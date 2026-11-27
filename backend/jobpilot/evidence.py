"""Bounded source passages and lexical retrieval shared by assistant evaluation."""
import math
import re
from collections import Counter

from .domain import Profile
from .resumes import SKILLS, _recognized

STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "for", "with", "on", "at", "from", "as", "is", "are", "your", "you", "our", "we", "my", "me", "i", "have", "has", "how", "what", "tell", "about", "experience", "years"}


def tokens(text: str) -> list[str]:
    return [word for word in re.findall(r"[\w+#]+", text.casefold()) if len(word) > 1 and word not in STOP]


def rank_passages(query: str, passages: list[str]) -> list[int]:
    """BM25 with deterministic tie-breaking, without learned claims or network calls."""
    if not passages:
        return []
    docs = [Counter(tokens(p)) for p in passages]
    lengths = [sum(doc.values()) for doc in docs]
    average = max(1, sum(lengths) / len(lengths))
    terms = set(tokens(query))
    frequency = {term: sum(term in doc for doc in docs) for term in terms}
    def score(index: int) -> float:
        result = 0.0
        for term in terms:
            count = docs[index][term]
            idf = math.log(1 + (len(docs) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            result += idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * lengths[index] / average))
        return result
    return sorted(range(len(passages)), key=lambda i: (-score(i), i))


def source_facts(profile: Profile, query: str, limit: int = 6) -> dict[str, str]:
    passages = []
    # Split before ranking so the end of a long employment section remains retrievable.
    for line in profile.resume_text.splitlines():
        line = line.strip(" •\t")
        if not line or re.search(r"@|https?://|(?:\+?\d[\s().-]*){8,}", line):
            continue
        if re.search(r"ignore.*instruct|system prompt|pretend|claim you|disregard|игнорируй", line, re.IGNORECASE):
            continue
        if re.search(r"no.*experience|never used|нет.*опыта|не работал|want to learn", line, re.IGNORECASE):
            continue
        for part in re.split(r"(?<=[.!?])\s+(?=[A-ZА-Я])", line):
            while part:
                end = len(part) if len(part) <= 480 else part.rfind(" ", 0, 481)
                end = end if end > 0 else 480
                piece, part = part[:end].strip(), part[end:].strip()
                if len(tokens(piece)) >= 3 and piece not in passages:
                    passages.append(piece)
    ranked = rank_passages(query, passages)
    selected = {f"source.{i}": passages[i] for i in ranked[:limit]}
    if profile.skills:
        selected["skills"] = "Recorded skills: " + ", ".join(profile.skills)
    if profile.years_experience > 0:
        selected["experience"] = f"Explicitly stated total experience: {profile.years_experience:g} years."
    return selected


def unsupported_claims(reply: str, facts: dict[str, str], fact_ids: list[str]) -> list[str]:
    cited = " ".join(facts[key] for key in fact_ids if key in facts)
    claims = " ".join(part for part in re.split(r"(?<=[.!?])\s+", reply) if not part.endswith("?"))
    numbers = set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?%?", claims))
    known = set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?%?", cited))
    technologies = set(_recognized(claims, SKILLS)) - set(_recognized(cited, SKILLS))
    missing = (numbers - known) | technologies
    for sentence in re.split(r"(?<=[.!?])\s+", claims):
        durations = re.findall(r"\b(\d+(?:\.\d+)?)\s+years?\b", sentence, re.IGNORECASE)
        for skill in _recognized(sentence, SKILLS):
            for duration in durations:
                link = rf"(?:{re.escape(duration)}\s+years?[^.\n]{{0,45}}{re.escape(skill)}|" \
                       rf"{re.escape(skill)}[^.\n]{{0,30}}{re.escape(duration)}\s+years?)"
                if not any(re.search(link, facts[key], re.IGNORECASE) for key in fact_ids if key in facts):
                    missing.add("unverified tenure in " + skill)
    for company in re.findall(r"\b(?:at|for)\s+([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)", claims):
        if company.casefold() not in cited.casefold():
            missing.add(company)
    for pattern in [r"\bled\b|leadership|team lead", r"manag(?:ed|ing)\s+(?:a\s+)?team",
                    r"mentor(?:ed|ing)", r"commercial experience"]:
        if re.search(pattern, claims, re.IGNORECASE) and not re.search(pattern, cited, re.IGNORECASE):
            missing.add(pattern)
    if re.search(r"\bI (?:have|built|developed|led|managed|worked|implemented|designed|delivered|improved|reduced)\b",
                 claims, re.IGNORECASE) and not fact_ids:
        missing.add("missing evidence")
    return sorted(missing)
