"""Idempotent defaults: existing searches and candidate preferences always win."""
import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Candidate, Resume, SavedSearch
from .searches import SearchFilters


def resume_filters(resume: Resume, record: dict[str, Any]) -> SearchFilters:
    text = record.get("text", "")
    headline = " ".join(text.splitlines()[:12]).casefold()
    variant = resume.variant
    title = ""
    if variant in {"full-stack", "react-python-ai"} or re.search(r"full[ -]?stack", headline):
        title = "Full Stack, Fullstack, Full-stack, Software Engineer, Product Engineer"
        if "Python" in record.get("facts", {}).get("skills", []):
            title += ", Backend, Back-end, Back end"
    elif variant == "frontend" or re.search(r"front[ -]?end", headline):
        title = "Frontend, Front-end, Front end, UI Engineer, Web Engineer, Web Developer, Product Engineer, Software Engineer"
    elif re.search(r"back[ -]?end", headline):
        title = "Backend, Back-end, Back end"
    # Skills are alternatives: requiring every skill in a CV hides good roles.
    skills = ", ".join(record.get("facts", {}).get("skills", []))[:300]
    return SearchFilters(title=title, skills=skills, skills_mode="any")


def ensure_resume_searches(db: Session) -> int:
    candidate = db.scalar(select(Candidate).where(Candidate.id == 1).with_for_update())
    if not candidate:
        return 0
    existing = set(db.scalars(select(SavedSearch.resume_id)))
    records = dict(candidate.facts.get("resumes", {}))
    created = 0
    for resume in db.scalars(select(Resume).order_by(Resume.created_at)):
        record = records.get(resume.id, {})
        if record.get("search_retired"):
            continue  # Retained historical artifacts must not recreate an automatic search.
        if resume.id not in existing and not record.get("text"):
            # Rehydrate pre-search uploads from their stored artifact, without activating them.
            from .config import settings
            from .resumes import MAX_UPLOAD_BYTES, parse_resume
            path = settings().data_dir / "resumes" / resume.storage_key
            try:
                with path.open("rb") as stream:
                    content = stream.read(MAX_UPLOAD_BYTES + 1)
                if hashlib.sha256(content).hexdigest() == resume.sha256:
                    parsed = parse_resume(content, resume.filename)
                    record = {"text": parsed.text, "facts": parsed.facts, "warnings": parsed.warnings}
                    records[resume.id] = record
            except (OSError, ValueError):
                pass
        if resume.id in existing or not record.get("text"):
            continue
        db.add(SavedSearch(
            name=f"{Path(resume.filename).stem} · основной поиск"[:120],
            resume_id=resume.id, filters=resume_filters(resume, record).model_dump(), enabled=True,
        ))
        existing.add(resume.id)
        created += 1
    candidate.facts = {**candidate.facts, "resumes": records}
    db.flush()
    return created
