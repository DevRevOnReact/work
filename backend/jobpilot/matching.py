"""Explainable weighted matching with bilingual evidence and qualification gates.

No external model or inferred candidate credentials. Unknown job requirements do
not dilute the professional score; explicit incompatible requirements block top
recommendations independently from the score.
"""
import math
import re
from datetime import UTC, datetime
from typing import Any

from .domain import (DOMAIN_ALIASES, SKILL_ALIASES, JobInput, Profile, _contains,
                     _detected, _eligibility, _salary_range, _terms, profile_fingerprint)

SCORING_VERSION = "matching-v2"
WEIGHTS = {"stack": 40, "responsibilities": 25, "role": 15, "experience": 10, "domain": 10}
LABELS = {"stack": "Стек и обязательные технологии", "role": "Направление роли",
          "experience": "Продолжительность опыта", "domain": "Предметная область",
          "responsibilities": "Подтверждённые рабочие задачи", "education": "Образование",
          "languages": "Языки и уровень", "location": "География и формат",
          "compensation": "Условия оплаты", "freshness": "Дата публикации"}
OPTIONAL = r"nice[ -]to[ -]have|preferred|bonus|optional|would be a plus|желательно|будет плюсом|преимуществом"
MANDATORY = r"\bmust\b|\brequir(?:ed|ements?)\b|essential|mandatory|обязател|необходим|требован|требуется"
FRAMEWORKS = {"Angular", "React", "Vue", "Svelte", "React Native", "Flutter"}
BACKEND_LANGUAGES = {"Python", "Java", "Go", "Kotlin", "Ruby", "Rust", "PHP", "C#", "C++", "Scala"}
TASKS = {
    "api": r"\bapi\b|интеграц|typed contracts|контракт.*api",
    "forms": r"\bforms?\b|\bформ(?:а|ы|у|ами|ах)\b|валидаци|validation",
    "tables": r"\btables?\b|таблиц|pagination|пагинаци|filtering|filters|фильтр",
    "performance": r"performance|optimization|оптимизац|производительност|code splitting|code[ -]split|рендер|rendering",
    "caching": r"caching|cache|кеш|кэш|deduplication|дедупликац|отмен.*запрос|request cancellation",
    "testing": r"\btests?\b|testing|тест|jest|playwright|cypress",
    "components": r"components|компонент|design systems|дизайн.систем|storybook",
    "authentication": r"authentication|authorization|permissions|аутентификац|авторизац|прав[ао].доступ",
    "payments": r"payments?|checkout|подпис|плат[её]ж|paywall|subscription",
    "async": r"asynchronous|async|асинхрон|background processing|фоновые|progress|loading|загрузк|error states|состояни.*ошиб",
    "monitoring": r"monitoring|logging|tracing|sentry|мониторинг|логирован|логи|идентификатор.*запрос",
    "delivery": r"deployments?|production|releases?|релиз|продакш|выпуск",
    "reviews": r"code reviews?|ревью|рев[ьъ]ю|провер.*код",
    "mentoring": r"mentor|ментор|наставни|помогал.*разработ|поддерж.*разработ",
    "animation": r"animations?|анимаци|interactive scenes|интерактивн.*сцен|sprites|спрайт|textures|текстур",
    "events": r"pointer events|input events|event handlers|обработ.*событ|обработчик|cleanup|очистк.*сцен",
    "responsive": r"responsive|mobile screens|mobile layouts|адаптац|адаптив|мобильн",
    "databases": r"query optimization|database|баз[аыу].данных|sqlalchemy|postgresql",
    "ai": r"ai integrations?|ai service|generation|генераци|интеграц.*ии",
    "ownership": r"own features|ownership|from requirements|от требований|полный цикл|end.to.end",
}


def _task_set(text: str) -> set[str]:
    return {key for key, pattern in TASKS.items() if re.search(pattern, text, re.I)}


def _segments(text: str) -> list[tuple[str, bool]]:
    # Preserve section scope even when a feed flattened the original HTML lists.
    text = re.sub(r"(?i)(nice[ -]to[ -]have|preferred qualifications|requirements|what you bring|responsibilities|"
                  r"about us|what we offer|benefits|обязательные требования|обязанности|желательно|будет плюсом)\s*:?",
                  r"\n\1: ", text)
    optional = False
    result = []
    for part in re.split(r"\n|[•◆]|(?<=[.!?;])\s+(?=[A-ZА-Я])", text):
        if re.match(r"\s*(?:" + OPTIONAL + r")\b", part, re.I):
            optional = True
        if re.match(r"\s*(?:about us|what we offer|benefits)\b", part, re.I):
            optional = True
        if re.search(r"requirements|what you bring|responsibilities|обязательные требования|обязанности", part, re.I):
            optional = False
        if part.strip():
            result.append((part, optional or bool(re.search(OPTIONAL + r"|not required|не требуется", part, re.I))))
    return result


def _role_types(text: str) -> set[str]:
    rules = {"frontend": r"front[ -]?end|фронтенд|frontend|\bui (?:engineer|developer)|\bweb (?:engineer|developer)",
             "backend": r"back[ -]?end|бэкенд|бекенд|серверн",
             "fullstack": r"full[ -]?stack|фулл?ст[еэ]к",
             "mobile": r"react native|flutter|android|ios|mobile|мобильн.*разработ",
             "data": r"data (?:engineer|scientist)|machine learning|ml engineer",
             "devops": r"devops|site reliability|\bsre\b",
             "recruiting": r"recruit|подбор.*персонал"}
    return {key for key, pattern in rules.items() if re.search(pattern, text, re.I)}


def _ratio(required: set[str], evidence: set[str]) -> float:
    return len(required & evidence) / len(required) if required else 0


def _evidence(required: set[str], actual: set[str]) -> str:
    found, missing = sorted(required & actual), sorted(required - actual)
    return (("Подтверждено: " + ", ".join(found)) if found else "Совпадения не подтверждены") + (
        "; не подтверждено: " + ", ".join(missing) if missing else "")


def _language_requirements(text: str, profile: Profile, resume: str) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    notes: list[str] = []
    names = {"English": r"english|английск\w*", "Russian": r"russian|русск\w*",
             "German": r"german|немецк\w*", "French": r"french|французск\w*",
             "Spanish": r"spanish|испанск\w*"}
    ranks = {level: i for i, level in enumerate(("A1", "A2", "B1", "B2", "C1", "C2"))}
    for language, pattern in names.items():
        for segment, optional in _segments(text):
            if optional or not re.search(pattern, segment, re.I):
                continue
            required = re.search(rf"(?:{pattern})[^.;\n]{{0,35}}?\b([ABC][12])\b|"
                                 rf"\b([ABC][12])\b\s+(?:{pattern})", segment, re.I)
            if not required:
                if re.search(MANDATORY, segment, re.I) and not profile.languages.get(language) and not re.search(pattern, resume, re.I):
                    blockers.append(f"Обязательный язык не подтверждён: {language}")
                else:
                    notes.append(f"{language}: требуемый уровень не указан")
                continue
            required_level = next(group.upper() for group in required.groups() if group)
            actual_text = profile.languages.get(language, "")
            actual = re.search(r"\b[ABC][12]\b", actual_text, re.I)
            if not actual:
                actual = re.search(rf"(?:{pattern})\s*[:—–-]?\s*([ABC][12])\b", resume, re.I)
            if actual:
                actual_level = actual.group(1).upper() if actual.lastindex else actual.group().upper()
            elif re.search(r"native|родной", actual_text, re.I):
                actual_level = "C2"
            else:
                blockers.append(f"{language}: требуется {required_level}; уровень в резюме не подтверждён")
                continue
            message = f"{language}: требуется {required_level}, в резюме {actual_level}"
            (blockers if ranks[actual_level] < ranks[required_level] else notes).append(message)
    return blockers, notes


def evaluate_match(job: JobInput, profile: Profile, at: datetime | None = None) -> dict[str, Any]:
    resume = profile.resume_text.strip().lower() if profile.active_resume_id else ""
    text = f"{job.title}\n{job.description}"
    eligibility, reasons = _eligibility(job, profile)
    blockers: list[str] = []
    vocabulary = dict(SKILL_ALIASES)
    for skill in profile.skills:
        if skill.strip() and not any(_contains(skill, alias) for aliases in vocabulary.values() for alias in aliases):
            vocabulary[skill] = (skill,)
    segments = _segments(text)
    required = set().union(*(_detected(part, vocabulary) for part, optional in segments if not optional))
    optional_skills = set().union(*(_detected(part, vocabulary) for part, optional in segments if optional)) - required
    actual = _detected(resume, vocabulary)
    for skill in list(actual):
        if any(re.search(r"(?:no experience (?:with|in)|нет опыта (?:с|в))\s+" + re.escape(alias) + r"(?!\w)", resume, re.I)
               for alias in vocabulary[skill]):
            actual.discard(skill)
    # Treat explicit alternatives as one requirement, rather than requiring both.
    alternatives: list[set[str]] = []
    skill_atom = r"(?:" + "|".join(re.escape(alias) for alias in sorted(
        {alias for aliases in vocabulary.values() for alias in aliases}, key=len, reverse=True)) + r")"
    for part, optional in segments:
        if optional:
            continue
        for match in re.finditer(r"(?<!\w)" + skill_atom + r"(?:\s*(?:,\s*|\s+(?:or|или)\s+)" + skill_atom + r")+(?!\w)", part, re.I):
            group = _detected(match.group(), vocabulary)
            if len(group) >= 2 and re.search(r"\b(?:or|или)\b", match.group(), re.I):
                alternatives.append(group)
    groups = [group for group in alternatives if group <= required]
    grouped = set().union(*groups)
    groups.extend([{skill} for skill in sorted(required - grouped)])
    stack_ratio = sum(bool(group & actual) for group in groups) / len(groups) if groups else 0
    job_roles = _role_types(job.title)
    header = " ".join(resume.splitlines()[:12])[:1200]
    resume_roles = _role_types(header)
    def infer_software_role(skills: set[str]) -> set[str]:
        front = bool(skills & FRAMEWORKS)
        back = bool(skills & BACKEND_LANGUAGES)
        return {"fullstack"} if front and back else {"frontend"} if front else {"backend"} if back else set()
    if not job_roles and re.search(r"engineer|developer|разработчик|инженер", job.title, re.I):
        job_roles = infer_software_role(required)
    if not resume_roles and re.search(r"engineer|developer|разработчик|инженер", header, re.I):
        resume_roles = infer_software_role(_detected(header, vocabulary))
    # Full-stack evidence can cover either half when the corresponding stack is present.
    compatible_roles = set(resume_roles)
    if "fullstack" in resume_roles:
        compatible_roles |= {"frontend", "backend"}
    if job_roles:
        role_ratio = float(bool(job_roles & compatible_roles))
    else:
        role_ratio = _ratio(_terms(job.title), _terms(header))
        if re.search(r"engineer|developer|разработчик|инженер", job.title, re.I) and compatible_roles:
            role_ratio = 1
    core = set(_detected(job.title, vocabulary))
    core |= required & (FRAMEWORKS if "frontend" in job_roles or "mobile" in job_roles else set())
    if "backend" in job_roles or "fullstack" in job_roles:
        core |= required & BACKEND_LANGUAGES
    if "fullstack" in job_roles:
        core |= required & FRAMEWORKS
    for part, optional in segments:
        if not optional and re.search(MANDATORY, part, re.I):
            core |= _detected(part, vocabulary)
    missing_core = []
    for skill in sorted(core - actual):
        if not any(skill in group and group & actual for group in alternatives):
            missing_core.append(skill)
    if missing_core:
        blockers.append("Не подтверждены основные/обязательные технологии: " + ", ".join(missing_core))
    if required and not required & actual:
        blockers.append("В резюме нет подтверждённого совпадения стека вакансии")
    if job_roles and not job_roles & compatible_roles:
        blockers.append("Направление вакансии не соответствует выбранному резюме")

    years = [float(match.replace(",", ".")) for part, optional in segments if not optional for match in re.findall(
        r"\b(\d{1,2}(?:[.,]\d+)?)\+?\s*(?:years?|yrs?|лет|года?)\b", part, re.I)]
    required_years = max(years, default=0)
    experience_ratio = max(0, 1 - max(0, required_years - profile.years_experience) * .2)
    if required_years and profile.years_experience and required_years - profile.years_experience >= 3:
        blockers.append(f"Требуется {required_years:g} лет; подтверждено {profile.years_experience:g}")
    if re.search(r"\b(?:staff|principal|director|head of)\b", job.title, re.I) and not re.search(
            r"\b(?:staff|principal|director|head of)\b", header, re.I):
        blockers.append("Уровень Staff/Principal/руководителя не подтверждён резюме")
    task_required, task_actual = _task_set(job.description), _task_set(resume)
    task_ratio = _ratio(task_required, task_actual) if task_required else _ratio(_terms(job.description), _terms(resume))
    domains, domain_actual = _detected(text, DOMAIN_ALIASES), _detected(resume, DOMAIN_ALIASES)
    ratios = {"stack": stack_ratio, "role": role_ratio, "experience": experience_ratio,
              "responsibilities": task_ratio, "domain": _ratio(domains, domain_actual)}
    applicable = {"stack": bool(required), "role": bool(job.title.strip()),
                  "experience": bool(required_years and profile.years_experience),
                  "responsibilities": bool(task_required or _terms(job.description)), "domain": bool(domains)}
    total_weight = sum(weight for key, weight in WEIGHTS.items() if applicable[key])
    maxima = {key: (100 * weight / total_weight if total_weight and applicable[key] else 0)
              for key, weight in WEIGHTS.items()}
    points = {key: math.floor(value) for key, value in maxima.items()}
    for key in sorted(points, key=lambda key: maxima[key] - points[key], reverse=True)[:100 - sum(points.values())]:
        if total_weight:
            points[key] += 1
    scores = {key: round(points[key] * ratios[key]) if resume else 0 for key in WEIGHTS}
    language_blockers, language_notes = _language_requirements(text, profile, resume)
    blockers.extend(language_blockers)
    if eligibility == "rejected":
        blockers.extend(reasons)
    offered, expected = _salary_range(job.salary or ""), _salary_range(profile.salary_expectation or "")
    comparable = bool(offered and expected and offered[2:] == expected[2:])
    salary_note = "Неизвестно: совместимость суммы, валюты и периода оплаты не подтверждена"
    if comparable and offered and expected:
        if max(offered[0], expected[0]) <= min(offered[1], expected[1]):
            salary_note = "Диапазон оплаты пересекается с ожиданиями"
        else:
            salary_note = "Диапазон оплаты не пересекается с ожиданиями"
            blockers.append(salary_note)
    current = at or datetime.now(UTC)
    current = current.replace(tzinfo=UTC) if current.tzinfo is None else current
    published = job.published_at
    if published and published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    age = (current - published).days if published else None
    notes = {"stack": _evidence(required, actual) + ("; желательные: " + ", ".join(sorted(optional_skills)) if optional_skills else ""),
             "role": f"Направление вакансии: {', '.join(sorted(job_roles)) or job.title}; резюме: {', '.join(sorted(resume_roles)) or 'по тексту роли'}",
             "experience": f"Требуется {required_years:g} лет; подтверждено {profile.years_experience:g}" if applicable["experience"]
                           else "Неизвестно: стаж или требование не указаны; вес перераспределён",
             "domain": _evidence(domains, domain_actual) if domains else "Сфера не указана; вес перераспределён",
             "responsibilities": _evidence(task_required, task_actual) if task_required else "Совпадение формулировок задач",
             "education": "Проверяется отдельно; отсутствие требования не уменьшает оценку",
             "languages": "; ".join(language_blockers + language_notes) or "Требование к языку не указано",
             "location": "; ".join(reasons), "compensation": salary_note,
             "freshness": f"Опубликована {age} дн. назад; окно топа проверяется отдельно" if age is not None else "Неизвестно: дата публикации"}
    education = bool(re.search(r"bachelor|master|phd|degree|высшее|бакалавр|магистр", job.description, re.I))
    if education:
        notes["education"] = "Требование к образованию требует отдельной проверки; диплом и эквивалентный опыт не приравниваются автоматически"
        for segment, optional in segments:
            degrees = set(re.findall(r"bachelor|master|phd|бакалавр|магистр", segment.lower()))
            if degrees and not optional and re.search(MANDATORY, segment, re.I):
                if not any(_contains(degree, resume) for degree in degrees) and not re.search(
                        r"equivalent (?:professional |work )?experience|эквивалентн.*опыт", segment, re.I):
                    blockers.append("Обязательное образование не подтверждено: " + ", ".join(sorted(degrees)))
    criteria = [{"key": key, "label": label, "score": scores.get(key, 0), "max_score": points.get(key, 0),
                 "reason": notes[key] if resume else "Неизвестно: резюме не загружено или текст не извлечён",
                 "status": "scored" if key in WEIGHTS and applicable[key] else "check"}
                for key, label in LABELS.items()]
    if not resume:
        blockers = ["Резюме не загружено или его текст недоступен"]
    reasons += blockers + ["Предупреждение разбора резюме: " + warning for warning in profile.resume_parse_warnings]
    return {"score": sum(scores.values()), "eligibility": eligibility, "recommendable": not blockers,
            "blockers": list(dict.fromkeys(blockers)), "scoring_version": SCORING_VERSION,
            "breakdown": {item["key"]: item["score"] for item in criteria}, "criteria": criteria,
            "matched_skills": sorted(required & actual), "missing_skills": sorted(required - actual),
            "optional_skills": sorted(optional_skills), "reasons": reasons, "freshness_days": age,
            "compensation_note": salary_note, "active_resume_id": profile.active_resume_id,
            "profile_fingerprint": profile_fingerprint(profile)}
