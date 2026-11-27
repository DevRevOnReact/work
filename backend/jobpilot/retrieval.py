"""Fuse qualification score, explicit role fit and lexical relevance after eligibility filters."""
import re
from typing import Any

from .domain import Profile
from .evidence import rank_passages


def role(text: str) -> str:
    patterns = {
        "fullstack": r"full[ -]?stack",
        "frontend": r"front[ -]?end|\bui (?:engineer|developer)|\bweb (?:engineer|developer)",
        "backend": r"back[ -]?end|\bapi (?:engineer|developer)",
        "ai": r"applied ai|\bai engineer|\bml engineer|machine learning",
    }
    return next((key for key, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)), "")


def rank_candidates(results: list[dict[str, Any]], profile: Profile) -> None:
    header = "\n".join(profile.resume_text.splitlines()[:3])
    preferred = role(header)
    def adjusted(item: dict[str, Any]) -> float:
        return item["score"] + (15 if preferred and role(item["title"]) == preferred else 0)
    results.sort(key=lambda item: (-adjusted(item), item["canonical_url"]))
    query = header + " " + " ".join(profile.skills)
    lexical = rank_passages(query, [item["title"] + "\n" + item["description"] for item in results])
    ranks = {index: rank for rank, index in enumerate(lexical, 1)}
    for index, item in enumerate(results):
        item["ranking_score"] = 0.8 / (61 + index) + 0.2 / (60 + ranks[index])
        item["retrieval_rank"] = ranks[index]
        item["preferred_role_match"] = bool(preferred and role(item["title"]) == preferred)
    results.sort(key=lambda item: -item["ranking_score"])
