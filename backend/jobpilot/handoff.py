"""Question/answer handoffs omit identity, contact and upload fields."""
import re
from typing import Any

from .answers import normalize

CONTACT = re.compile(
    r"\b(?:full(?: legal)? name|legal name|first name|last name|middle name|preferred name|your name|e mail|email|phone|telephone|"
    r"mobile|address|postcode|postal code|zip code|linkedin|github|portfolio|date of birth|passport)\b|"
    r"^(?:name|имя|фамилия|отчество|фио)\b|(?:ваше|ваш|полное)\s+(?:имя|фио|фамилия)|"
    r"электронн.*почт|e?mail|телефон|адрес|дата рождения|паспорт|как вас зовут",
    re.IGNORECASE,
)
CONTACT_EVIDENCE = re.compile(r"^(?:profile\.)?(?:full_name|email|phone|linkedin|github|portfolio)(?:\.|$)")


def redact(value: Any, facts: dict[str, Any]) -> str:
    text = str(value or "")
    for key in ("full_name", "email", "phone", "linkedin", "github", "portfolio"):
        secret = facts.get(key)
        if isinstance(secret, str) and secret.strip():
            text = re.sub(re.escape(secret.strip()), "[контактные данные скрыты]", text, flags=re.IGNORECASE)
    return re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[email скрыт]", text, flags=re.IGNORECASE)


def questions_and_answers(answers: list[dict[str, Any]], fields: list[dict[str, Any]],
                          facts: dict[str, Any]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in [*answers, *fields]:
        question = str(item.get("question") or "").strip()
        key = normalize(question)
        evidence = item.get("evidence") or []
        if (not key or CONTACT.search(key) or item.get("type") in {"file", "email", "tel"}
                or any(CONTACT_EVIDENCE.search(str(source)) for source in evidence)):
            continue
        if key in result:
            continue
        result[key] = {"question": redact(question, facts),
                       "answer": redact(item.get("answer"), facts) if item.get("answer") is not None else None,
                       "options": [redact(option, facts) for option in item.get("options") or []]}
    return list(result.values())
