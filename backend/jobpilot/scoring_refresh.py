"""Re-evaluate stored snapshots once after a matching/parser upgrade."""
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import JobInput, evaluate
from .matching import SCORING_VERSION
from .models import Candidate, Resume, SavedSearch, SearchNotification, TopVacancy
from .resumes import extract_facts
from .search_setup import resume_filters
from .searches import SearchFilters, matches, search_profile
from .top_vacancies import qualifies


def refresh_search_scoring(db: Session, candidate: Candidate) -> None:
    if candidate.facts.get("search_scoring_version") == SCORING_VERSION:
        return
    records = dict(candidate.facts.get("resumes", {}))
    previous_skills = {key: ", ".join(record.get("facts", {}).get("skills", []))[:300]
                       for key, record in records.items()}
    updates = {}
    for resume_id, record in records.items():
        if not record.get("text"):
            continue
        facts, warnings = extract_facts(record["text"])
        if candidate.facts.get("active_resume_id") == resume_id:
            for field, value in facts.items():
                if candidate.facts.get(field) == record.get("facts", {}).get(field):
                    updates[field] = value
        records[resume_id] = {**record, "facts": facts, "warnings": warnings}
    candidate.facts = {**candidate.facts, **updates, "resumes": records, "search_scoring_version": SCORING_VERSION}
    db.flush()
    searches = {search.id: search for search in db.scalars(select(SavedSearch))}
    for search in searches.values():
        resume = db.get(Resume, search.resume_id)
        if resume and search.name.endswith("· основной поиск") and search.filters.get("title") in {
                "Frontend, Front-end, Front end", "Full Stack, Fullstack, Full-stack"}:
            defaults = resume_filters(resume, records.get(resume.id, {}))
            changed = {"title": defaults.title}
            if search.filters.get("skills") == previous_skills.get(resume.id) and search.filters.get("skills_mode") == "any":
                changed["skills"] = defaults.skills
            search.filters = {**search.filters, **changed}
        try:
            profile = search_profile(db, search.resume_id)
        except ValueError:
            continue
        refreshed = []
        for item in search.results:
            try:
                job = JobInput.model_validate(item)
                result = evaluate(job, profile)
                if matches(job, SearchFilters.model_validate(search.filters), result):
                    refreshed.append({**item, "score": result["score"], "eligibility": result["eligibility"], "explanation": result})
            except ValueError:
                continue
        if search.filters.get("sort") == "newest":
            refreshed.sort(key=lambda item: (item.get("published_at") or "", item["score"]), reverse=True)
        elif search.filters.get("sort") == "company":
            refreshed.sort(key=lambda item: (item["company"].casefold(), -item["score"]))
        else:
            refreshed.sort(key=lambda item: -item["score"])
        search.results = refreshed
        search.result_count = len(refreshed)
    for row in db.scalars(select(TopVacancy)):
        top_search = searches.get(row.search_id)
        try:
            if top_search is None:
                raise ValueError("Search missing")
            job = JobInput.model_validate(row.data)
            result = evaluate(job, search_profile(db, top_search.resume_id))
            data = {**row.data, "score": result["score"], "eligibility": result["eligibility"], "explanation": result}
            data.pop("review_status", None)
            from .vacancy_review import apply_decisions
            reviewed = apply_decisions(db, [data])
            if not reviewed:
                raise ValueError("Vacancy rejected by user")
            data = reviewed[0]
            filters = SearchFilters.model_validate(top_search.filters).model_copy(update={"posted_days": 0, "min_score": 60})
            if not qualifies(data) or not matches(job, filters, result):
                raise ValueError("No longer qualified")
            row.data, row.score = data, result["score"]
        except ValueError:
            key = hashlib.sha256(f"telegram-top-v1:{row.data.get('canonical_url', '')}".encode()).hexdigest()
            notification = db.get(SearchNotification, key)
            if notification and notification.status == "pending":
                notification.status = "superseded"
            db.delete(row)
