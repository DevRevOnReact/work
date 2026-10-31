from datetime import timedelta
from typing import Any

from rapidfuzz.fuzz import ratio
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .domain import JobInput, Profile, evaluate, profile_fingerprint, transition_allowed
from .models import (
    Application,
    Audit,
    Candidate,
    Control,
    Job,
    Outbox,
    Resume,
    Source,
    now,
)

MINIMUM_QUALIFICATION_SCORE = 60


def bootstrap(db: Session) -> None:
    if not db.get(Candidate, 1):
        db.add(Candidate(id=1, facts=Profile().model_dump()))
    if not db.get(Control, 1):
        db.add(Control(id=1))
    db.flush()
    candidate = db.get(Candidate, 1)
    assert candidate is not None
    from .scoring_refresh import refresh_search_scoring
    refresh_search_scoring(db, candidate)
    candidate_profile, active_resume_valid = scoring_context(db, candidate)
    fingerprint = profile_fingerprint(candidate_profile)
    # Rebuild legacy scores locally, preserving all stored candidate facts and attempts.
    rows = db.execute(select(Application, Job).join(Job).where(
        Application.state.in_(["DISCOVERED", "NORMALIZED", "MATCHED", "REJECTED", "READY"]),
        Application.submit_intent_at.is_(None),
        Application.submitted_at.is_(None), Application.attempt_id.is_(None),
        (Job.explanation["profile_fingerprint"].as_string().is_(None)) |
        (Job.explanation["profile_fingerprint"].as_string() != fingerprint),
    )).all()
    for app, job in rows:
        if app.state == "READY":
            transition(db, app, "MATCHED", "Revalidate active resume and current profile")
            app.resume_id = None
        rescore(db, job, app, candidate_profile, active_resume_valid)
    db.commit()


def audit(db: Session, kind: str, detail: dict[str, Any], app: Application | None = None) -> None:
    db.add(Audit(application_id=app.id if app else None, kind=kind, detail=detail))


def transition(db: Session, app: Application, state: str, reason: str | None = None) -> None:
    if not transition_allowed(app.state, state, app.submit_intent_at is not None):
        raise ValueError(f"Invalid transition {app.state} → {state}")
    before = app.state
    app.state = state
    app.attention = reason
    app.updated_at = now()
    audit(db, "state_changed", {"from": before, "to": state, "reason": reason}, app)


def job_input(job: Job) -> JobInput:
    return JobInput.model_validate({field: getattr(job, field) for field in JobInput.model_fields})


def scoring_context(db: Session, candidate: Candidate | None = None) -> tuple[Profile, bool]:
    candidate = candidate or db.get(Candidate, 1)
    if candidate is None:
        raise ValueError("Profile missing")
    profile = Profile.model_validate(candidate.facts)
    resume = db.get(Resume, profile.active_resume_id) if profile.active_resume_id else None
    valid_resume = bool(resume and (not profile.resume_filename or profile.resume_filename == resume.filename))
    return profile, valid_resume


def rescore(
    db: Session, job: Job, app: Application,
    candidate_profile: Profile | None = None, active_resume_valid: bool | None = None,
) -> None:
    if app.submit_intent_at or app.state not in {"DISCOVERED", "NORMALIZED", "MATCHED", "REJECTED"}:
        raise ValueError("Cannot rescore after queueing or attempting an application")
    if candidate_profile is None or active_resume_valid is None:
        candidate_profile, active_resume_valid = scoring_context(db)
    if app.state != "NORMALIZED":
        transition(db, app, "NORMALIZED")
    scoring_profile = candidate_profile
    if not active_resume_valid:
        scoring_profile = candidate_profile.model_copy(update={"resume_text": ""})
    result = evaluate(job_input(job), scoring_profile)
    result["profile_fingerprint"] = profile_fingerprint(candidate_profile)
    job.score, job.eligibility, job.explanation = result["score"], result["eligibility"], result
    state = "REJECTED" if job.eligibility == "rejected" else "MATCHED"
    transition(db, app, state, "; ".join(result["reasons"])
               if job.eligibility != "eligible" or not scoring_profile.resume_text.strip() else None)


def ingest(db: Session, source: Source, inputs: list[JobInput]) -> int:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(804221)"))
    if not inputs:
        source.synced_at = now()
        source.error = None
        return 0

    candidate = db.get(Candidate, 1)
    profile, active_resume_valid = scoring_context(db, candidate)
    fingerprint = profile_fingerprint(profile)
    scoring_profile = profile if active_resume_valid else profile.model_copy(update={"resume_text": ""})

    # Fetch existing identities once per feed instead of issuing one query per vacancy.
    existing_rows = db.execute(
        select(Job.provider, Job.external_id, Job.canonical_url).where(
            ((Job.provider == source.provider) & Job.external_id.in_([item.external_id for item in inputs]))
            | Job.canonical_url.in_([item.canonical_url for item in inputs])
        )
    ).all()
    known_external = {(provider, external_id) for provider, external_id, _ in existing_rows}
    known_urls = {url for _, _, url in existing_rows}

    scored: list[tuple[JobInput, dict[str, Any]]] = []
    for item in inputs:
        identity = (item.provider, item.external_id)
        if identity in known_external or item.canonical_url in known_urls:
            continue
        known_external.add(identity)
        known_urls.add(item.canonical_url)
        result = evaluate(item, scoring_profile)
        scored.append((item, result))

    # Only load possible duplicate candidates for companies in this feed.
    companies = {item.company.lower() for item, _ in scored}
    peers_by_company: dict[str, list[Job]] = {}
    if companies:
        peers = db.scalars(select(Job).where(func.lower(Job.company).in_(companies))).all()
        for peer in peers:
            peers_by_company.setdefault(peer.company.lower(), []).append(peer)

    count = 0
    for item, result in scored:
        job = Job(source_id=source.id, **item.model_dump())
        duplicate = next(
            (
                peer
                for peer in peers_by_company.get(item.company.lower(), [])
                if ratio(peer.title.lower(), item.title.lower()) >= 93
                and ratio(peer.location.lower(), item.location.lower()) >= 85
            ),
            None,
        )
        if duplicate:
            job.duplicate_of = duplicate.id
        db.add(job)
        db.flush()
        app = Application(job_id=job.id)
        db.add(app)
        db.flush()
        result["profile_fingerprint"] = fingerprint
        job.score, job.eligibility, job.explanation = result["score"], result["eligibility"], result
        transition(db, app, "NORMALIZED")
        transition(
            db,
            app,
            "REJECTED" if job.eligibility == "rejected" else "MATCHED",
            "; ".join(result["reasons"])
            if job.eligibility != "eligible" or not profile.resume_text.strip()
            else None,
        )
        if duplicate:
            app.attention = f"Potential duplicate of {duplicate.id}; review before applying"
        audit(db, "job_discovered", {"provider": source.provider, "job_id": job.id}, app)
        if job.score >= 80 and job.eligibility == "eligible" and not duplicate:
            db.add(Outbox(message=f"🔥 {job.score}/100 · {job.title} at {job.company}\n{job.canonical_url}"))
        count += 1
        # Later vacancies in the same feed must also see newly inserted jobs for deduplication.
        peers_by_company.setdefault(item.company.lower(), []).append(job)
    source.synced_at = now()
    source.error = None
    return count


def readiness(
    db: Session, job: Job, app: Application, candidate_profile: Profile | None = None,
    active_resume: Resume | None = None, context_loaded: bool = False,
) -> str | None:
    if not context_loaded:
        candidate = db.get(Candidate, 1)
        candidate_profile = Profile.model_validate(candidate.facts) if candidate else None
    profile = candidate_profile
    if profile is None:
        return "Complete candidate profile"
    if not profile.active_resume_id:
        return "Upload and select an active resume"
    resume = active_resume if context_loaded else db.get(Resume, profile.active_resume_id)
    if not resume:
        return "Active resume is missing; upload or select it again"
    if not profile.resume_text.strip():
        return "Active resume has no parsed text; upload a readable resume"
    if profile.resume_filename and profile.resume_filename != resume.filename:
        return "Active resume filename does not match the parsed profile"
    if app.resume_id and app.resume_id != resume.id:
        return "Application resume does not match the active resume; revalidate before applying"
    if app.submit_intent_at:
        return "Submission already attempted; automatic resubmission is disabled"
    if job.explanation.get("profile_fingerprint") != profile_fingerprint(profile):
        return "Profile changed; rescore against the active resume before applying"
    if job.score < MINIMUM_QUALIFICATION_SCORE:
        return f"Vacancy score is below the {MINIMUM_QUALIFICATION_SCORE}-point qualification threshold"
    if not job.explanation.get("recommendable", True):
        return "Required qualifications not confirmed: " + "; ".join(job.explanation.get("blockers", []))
    if job.eligibility != "eligible":
        return "Work eligibility needs confirmation for your profile"
    if job.duplicate_of:
        return "Potential duplicate requires review"
    if not profile.full_name or not profile.email:
        return "Add your full name and email in Profile"
    app.resume_id = resume.id
    return None


def queue_ready(db: Session) -> list[str]:
    candidate = db.get(Candidate, 1)
    candidate_profile: Profile | None = None
    active_resume: Resume | None = None
    if candidate:
        candidate_profile, active_resume_valid = scoring_context(db, candidate)
        active_resume = db.get(Resume, candidate_profile.active_resume_id) if candidate_profile.active_resume_id else None
        fingerprint = profile_fingerprint(candidate_profile)
        # Re-evaluate saved matches when candidate facts or the active resume change.
        rows = db.execute(select(Application, Job).join(Job).where(
            Application.state.in_(["MATCHED", "REJECTED", "NORMALIZED", "READY"]),
            Application.submit_intent_at.is_(None),
            Application.submitted_at.is_(None), Application.attempt_id.is_(None),
        )).all()
        for app, job in rows:
            if job.explanation.get("profile_fingerprint") == fingerprint:
                continue
            if app.state == "READY":
                transition(db, app, "MATCHED", "Profile changed; revalidate active resume")
            app.resume_id = None
            rescore(db, job, app, candidate_profile, active_resume_valid)
    control = db.get(Control, 1)
    if not control or not control.enabled or not control.auto_apply:
        return []
    rows = db.execute(
        select(Application, Job)
        .join(Job)
        .where(Application.state == "MATCHED", Application.submit_intent_at.is_(None),
               Job.provider != "manual",
               Job.score >= max(MINIMUM_QUALIFICATION_SCORE, control.min_score))
        .order_by(Job.score.desc(), Job.published_at.desc())
    ).all()
    ids = []
    for app, job in rows:
        reason = readiness(db, job, app, candidate_profile, active_resume, context_loaded=True)
        if reason:
            if reason != app.attention:
                app.attention = reason
                audit(db, "attention_required", {"reason": reason}, app)
                db.add(Outbox(message=f"⚠️ {job.title} · {job.company}: {reason}"))
            continue
        transition(db, app, "READY")
        ids.append(app.id)
    return ids


def recover_stale(db: Session, timeout: int) -> None:
    for app in db.scalars(
        select(Application).where(
            Application.state == "APPLYING", Application.updated_at < now() - timedelta(seconds=timeout)
        )
    ):
        target = "SUBMISSION_UNCERTAIN" if app.submit_intent_at else "FAILED"
        transition(db, app, target, "Worker stopped before completion; inspect evidence")
        db.add(Outbox(message=f"⚠️ Application {app.id}: {target}"))
