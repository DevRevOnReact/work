"""Conservative, explainable preferences inferred from explicit rejection reasons."""
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Audit


def preferences(db: Session) -> set[tuple[str, str]]:
    # The latest explanation for a vacancy replaces earlier explanations.
    latest = {}
    for row in db.scalars(select(Audit).where(Audit.kind == "vacancy_feedback").order_by(Audit.id)):
        latest[row.detail.get("review_id")] = row.detail
    rules = set()
    for detail in latest.values():
        reason = detail.get("reason", "").casefold()
        job = detail.get("job", {})
        # Only infer a reusable rule when the user explicitly names the dimension.
        if re.search(r"компан|работодател|employer|company", reason) and job.get("company"):
            rules.add(("company", job["company"].strip().casefold()))
        if re.search(r"должност|названи[ея]|роль|професси|position|job title", reason) and job.get("title"):
            rules.add(("title", job["title"].strip().casefold()))
    return rules


def adjust(job: Any, result: dict[str, Any], rules: set[tuple[str, str]]) -> dict[str, Any]:
    matched = [field for field, value in rules if str(getattr(job, field, "")).strip().casefold() == value]
    if not matched:
        return result
    score = max(0, result["score"] - min(30, 15 * len(matched)))
    reasons = list(result.get("reasons", []))
    reasons.append("Оценка снижена с учётом вашей обратной связи: " + ", ".join(
        "компания" if field == "company" else "название должности" for field in sorted(matched)))
    return {**result, "score": score, "reasons": reasons}
