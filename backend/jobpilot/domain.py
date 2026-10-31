import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MATCHING_VERSION = "matching-v2.1"

class Profile(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    active_resume_id: str | None = None
    resume_text: str = ""
    resume_filename: str | None = None
    resume_parse_warnings: list[str] = Field(default_factory=list)
    location: str = ""
    search_locations: list[str] = Field(default_factory=list)
    years_experience: float = Field(default=0, ge=0)
    portfolio: str = ""
    linkedin: str | None = None
    github: str | None = None
    skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    employment: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    languages: dict[str, str] = Field(default_factory=dict)
    work_authorization: dict[str, bool] = Field(default_factory=dict)
    relocation: str = ""
    salary_expectation: str | None = None
    notice_period: str | None = None
    legal_answers: dict[str, str] = Field(default_factory=dict)


class JobInput(BaseModel):
    provider: Literal["greenhouse", "ashby", "lever", "remotive", "arbeitnow", "manual", "linkedin"]
    external_id: str
    canonical_url: str
    company: str
    title: str
    location: str
    description: str
    salary: str | None = None
    published_at: datetime | None = None


TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"NORMALIZED"},
    "NORMALIZED": {"REJECTED", "MATCHED"},
    "REJECTED": {"NORMALIZED"},
    "MATCHED": {"READY", "REJECTED", "NORMALIZED"},
    "READY": {"APPLYING", "REJECTED", "MATCHED"},
    "APPLYING": {"SUBMITTED", "SUBMISSION_UNCERTAIN", "FAILED"},
    "FAILED": {"READY", "REJECTED"},
    "SUBMISSION_UNCERTAIN": {"SUBMITTED"},
    "SUBMITTED": {"INTERVIEW", "REJECTED_BY_EMPLOYER", "OFFER"},
    "INTERVIEW": {"REJECTED_BY_EMPLOYER", "OFFER"},
    "REJECTED_BY_EMPLOYER": set(),
    "OFFER": set(),
}


def transition_allowed(before: str, after: str, has_intent: bool = False) -> bool:
    return after in TRANSITIONS.get(before, set()) and not (after == "READY" and has_intent)


def profile_fingerprint(profile: Profile) -> str:
    """Invalidate persisted scores when candidate facts or the active resume change."""
    return hashlib.sha256((MATCHING_VERSION + ":" + profile.model_dump_json()).encode()).hexdigest()


def _contains(term: str, text: str) -> bool:
    return bool(term.strip()) and bool(
        re.search(r"(?<!\w)" + re.escape(term.strip().lower()) + r"(?!\w)", text.lower())
    )


_STOP_WORDS = (
    "a an the and or of in on at to for with as is are be we you your our will have has "
    "experience years year work working role position required requirements skills knowledge "
    "senior junior middle lead principal staff engineer developer specialist manager remote "
    "worldwide company team about must plus us responsibilities looking ability strong "
    "и в на с по для от из опыт лет года работа требования обязанности знание навыки "
    "компания команда удаленно специалист разработчик старший"
)
_STOP = set(_STOP_WORDS.split())


# Shared with resume parsers without importing them here. Aliases are whole terms.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "React": ("react", "react.js", "reactjs"),
    "React Native": ("react native",),
    "Next.js": ("next.js", "nextjs"),
    "Vue": ("vue", "vue.js", "vuejs"),
    "PixiJS": ("pixijs", "pixi.js"), "WebGL": ("webgl",), "GSAP": ("gsap",),
    "RxJS": ("rxjs",), "NgRx": ("ngrx",),
    "Angular": ("angular", "angularjs"),
    "TypeScript": ("typescript",), "JavaScript": ("javascript",),
    "Python": ("python",), "Java": ("java",), "Go": ("go", "golang"),
    "Kotlin": ("kotlin",), "Ruby": ("ruby",), "Rust": ("rust",),
    "PHP": ("php",), "Laravel": ("laravel",), "C++": ("c++",),
    "C#": ("c#",), ".NET": (".net", "dotnet"), "Swift": ("swift",),
    "Dart": ("dart",), "Flutter": ("flutter",), "Scala": ("scala",),
    "R": ("r",), "SQL": ("sql",), "HTML": ("html", "html5"),
    "CSS": ("css", "css3"), "Node.js": ("node.js", "nodejs"),
    "Django": ("django",), "FastAPI": ("fastapi",), "Flask": ("flask",),
    "Spring": ("spring", "spring boot"), "Rails": ("rails", "ruby on rails"),
    "PostgreSQL": ("postgresql", "postgres"), "MySQL": ("mysql",),
    "Redis": ("redis",), "MongoDB": ("mongodb",), "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"), "AWS": ("aws", "amazon web services"),
    "Azure": ("azure",), "GCP": ("gcp", "google cloud"),
    "Terraform": ("terraform",), "Git": ("git",), "Linux": ("linux",),
    "CI/CD": ("ci/cd",), "REST": ("rest", "restful"), "GraphQL": ("graphql",),
    "WebSockets": ("websockets", "websocket"), "Kafka": ("kafka",),
    "SQLAlchemy": ("sqlalchemy",), "Pandas": ("pandas",), "NumPy": ("numpy",),
    "PyTorch": ("pytorch",), "TensorFlow": ("tensorflow",),
    "Redux": ("redux", "redux toolkit"), "Zustand": ("zustand",),
    "TanStack Query": ("tanstack query", "react query"),
    "Tailwind": ("tailwind", "tailwindcss"), "Storybook": ("storybook",),
    "Vite": ("vite",), "Jest": ("jest",), "Playwright": ("playwright",),
    "Excel": ("excel",), "Power BI": ("power bi",), "Tableau": ("tableau",),
    "Figma": ("figma",), "Photoshop": ("photoshop",), "AutoCAD": ("autocad",),
    "SAP": ("sap",), "Salesforce": ("salesforce",), "QuickBooks": ("quickbooks",),
}
KNOWN_SKILLS: tuple[str, ...] = tuple(SKILL_ALIASES)
DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "SaaS": ("saas",), "fintech": ("fintech", "финтех"),
    "payments": ("payments", "payment", "платежи", "платежей", "платежи", "платёжные", "платежных"),
    "subscriptions": ("subscriptions", "subscription", "подписки", "подписок"), "CRM": ("crm",),
    "analytics": ("analytics", "аналитика", "аналитическими"), "dashboards": ("dashboards", "dashboard", "дашборды"),
    "internal tools": ("internal tools", "внутренние инструменты"), "AI": ("ai", "artificial intelligence", "ии"),
    "LLM": ("llm", "large language models"), "observability": ("observability",),
    "healthcare": ("healthcare", "здравоохранение"),
    "ecommerce": ("ecommerce", "e-commerce"), "banking": ("banking", "банкинг"),
    "insurance": ("insurance", "страхование"), "education": ("edtech", "education"),
    "logistics": ("logistics", "логистика"), "retail": ("retail", "ритейл"),
    "manufacturing": ("manufacturing", "производство"),
    "recruiting": ("recruiting", "recruitment", "подбор персонала"),
}


def _detected(text: str, vocabulary: dict[str, tuple[str, ...]]) -> set[str]:
    return {key for key, aliases in vocabulary.items() if any(_contains(a, text) for a in aliases)}


def _salary_range(value: str) -> tuple[float, float, str, str] | None:
    text = value.lower().replace(",", "")
    currencies = {name for name, pattern in {
        "USD": r"\busd\b|\$", "EUR": r"\beur\b|€", "GBP": r"\bgbp\b|£",
        "RUB": r"\brub\b|₽|руб",
    }.items() if re.search(pattern, text)}
    periods = {name for name, pattern in {
        "year": r"\b(?:year|yearly|annual|annually|annum|yr)\b|год",
        "month": r"\b(?:month|monthly|mo)\b|мес",
        "hour": r"\b(?:hour|hourly|hr)\b|час",
    }.items() if re.search(pattern, text)}
    numbers = re.findall(r"(?<!\w)(\d+(?:\.\d+)?)\s*([kmкм]?)(?!\w)", text)
    if len(currencies) != 1 or len(periods) != 1 or not 1 <= len(numbers) <= 2:
        return None
    amounts = [float(n) * (1000 if suffix in {"k", "к"} else 1000000 if suffix in {"m", "м"} else 1)
               for n, suffix in numbers]
    # In '80-100k' the suffix applies to both endpoints.
    if len(numbers) == 2 and not numbers[0][1] and numbers[1][1] and amounts[0] < 1000:
        amounts[0] *= 1000 if numbers[1][1] in {"k", "к"} else 1000000
    if min(amounts) <= 0 or (len(amounts) == 2 and amounts[0] > amounts[1]):
        return None
    # Open-ended ranges need additional semantics; do not invent the other bound.
    if re.search(r"\+|\b(?:from|up to|starting|minimum|maximum)\b|от |до ", text):
        return None
    return min(amounts), max(amounts), next(iter(currencies)), next(iter(periods))


def _terms(text: str) -> set[str]:
    return {
        t for t in re.findall(r"\.net\b|[\w][\w+#]*(?:[./-][\w+#]+)*", text.lower())
        if t not in _STOP and not t.isdigit() and (len(t) > 1 or t in {"r", "c"})
    }


_COUNTRIES = {
    "united states": ("united states", "usa", "us", "u.s.", "сша"),
    "united kingdom": ("united kingdom", "uk", "britain", "великобритания"),
    "georgia": ("georgia", "грузия"),
    "serbia": ("serbia", "сербия", "сербии"),
    "germany": ("germany", "deutschland", "германия"),
    "canada": ("canada", "канада"),
    "poland": ("poland", "польша"),
    "france": ("france", "франция"),
    "netherlands": ("netherlands", "нидерланды"),
    "spain": ("spain", "испания"),
    "portugal": ("portugal", "португалия"),
    "india": ("india", "индия"),
    "brazil": ("brazil", "бразилия"),
    "australia": ("australia", "австралия"),
    "austria": ("austria", "австрия"), "belgium": ("belgium", "бельгия"),
    "bulgaria": ("bulgaria", "болгария"), "croatia": ("croatia", "хорватия"),
    "cyprus": ("cyprus", "кипр"), "czechia": ("czechia", "czech republic", "чехия"),
    "denmark": ("denmark", "дания"), "estonia": ("estonia", "эстония"),
    "finland": ("finland", "финляндия"), "greece": ("greece", "греция"),
    "hungary": ("hungary", "венгрия"), "ireland": ("ireland", "ирландия"),
    "italy": ("italy", "италия"), "latvia": ("latvia", "латвия"),
    "lithuania": ("lithuania", "литва"), "luxembourg": ("luxembourg", "люксембург"),
    "malta": ("malta", "мальта"), "romania": ("romania", "румыния"),
    "slovakia": ("slovakia", "словакия"), "slovenia": ("slovenia", "словения"),
    "sweden": ("sweden", "швеция"), "norway": ("norway", "норвегия"),
    "iceland": ("iceland", "исландия"), "liechtenstein": ("liechtenstein", "лихтенштейн"),
    "switzerland": ("switzerland", "швейцария"), "turkey": ("turkey", "türkiye", "турция"),
    "japan": ("japan", "япония"), "singapore": ("singapore", "сингапур"),
    "mexico": ("mexico", "мексика"), "argentina": ("argentina", "аргентина"),
    "israel": ("israel", "израиль"), "south africa": ("south africa", "юар"),
    "uae": ("united arab emirates", "uae", "оаэ"),
}
_EU = {"austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "denmark", "estonia", "finland",
       "france", "germany", "greece", "hungary", "ireland", "italy", "latvia", "lithuania", "luxembourg",
       "malta", "netherlands", "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden"}
_EEA = _EU | {"norway", "iceland", "liechtenstein"}
_EMEA = _EEA | {"united kingdom", "georgia", "serbia", "switzerland", "turkey", "israel", "uae", "south africa"}


def _countries(text: str) -> set[str]:
    result = {country for country, aliases in _COUNTRIES.items() if any(_contains(a, text) for a in aliases)}
    if "georgia" in result and ("united states" in result or _contains("atlanta", text)):
        result.discard("georgia")
        result.add("united states")
    return result


def _eligibility(job: JobInput, profile: Profile) -> tuple[str, list[str]]:
    if profile.search_locations:
        options = [(place, _eligibility(job, profile.model_copy(update={"location": place, "search_locations": []})))
                   for place in dict.fromkeys(profile.search_locations) if place.strip()]
        if options:
            best = min(options, key=lambda item: {"eligible": 0, "unknown": 1, "rejected": 2}[item[1][0]])
            return best[1][0], [f"Проверены варианты: {', '.join(place + ' — ' + result[0] for place, result in options)}",
                               *best[1][1]]
    text = f"{job.location}\n{job.description}".lower()
    location = job.location.lower()
    reasons: list[str] = []
    rejected = False
    unknown = False
    remote = bool(re.search(r"\bremote\b|удал[её]н", text))
    onsite = bool(re.search(r"\b(?:onsite|on-site|hybrid)\b|офис|гибрид", location)) or bool(
        re.search(r"(?:role|position) is (?:onsite|on-site|hybrid)", text)
    )
    sponsored = bool(re.search(
        r"(?:visa|relocation) sponsorship (?:available|provided)|"
        r"(?:provide|offer|provides|offers) (?:visa |relocation )?sponsorship", text
    ))
    if re.search(
        r"(?:no|without|not available for) (?:visa |relocation )?sponsorship|"
        r"(?:not|don't|cannot|unable to) (?:offer|provide|support|sponsor)|"
        r"(?:cannot|can't|do not|don't) sponsor|sponsorship (?:is )?not", text
    ):
        sponsored = False
    residence = _countries(profile.location)
    posted = _countries(location)
    restrictions = re.findall(
        r"(?:must|need to|have to) (?:be based|be|reside|live) in ([^.!\n]+)|"
        r"((?:[\w.]+[ -]){0,2}[\w.]+)[ -]only", text
    )
    allowed: set[str] = set()
    for groups in restrictions:
        allowed |= _countries(" ".join(groups))
    scope = allowed or posted
    worldwide = bool(re.search(r"worldwide|\bglobal\b|anywhere", location)) or bool(re.search(
        r"(?:work|working|hire|hiring) (?:remotely )?from anywhere|"
        r"remote[^.!\n]{0,25}worldwide", job.description.lower()
    ))
    regional = bool(re.search(r"\b(?:emea|eu|eea|europe)\b", location))
    if re.search(r"\b(?:eu|eea)[ -]only\b", text) or re.search(r"\b(?:eu|eea)\b", location):
        scope = _EEA if _contains("eea", location) or re.search(r"\beea[ -]only\b", text) else _EU
        worldwide = False
    elif _contains("emea", location):
        scope = scope or _EMEA
    if scope:
        if residence and not residence & scope and not (onsite and sponsored and not allowed):
            rejected = True
            reasons.append("Место проживания не соответствует явному ограничению вакансии")
        elif not residence and not (onsite and sponsored and not allowed):
            unknown = True
            reasons.append("Неизвестно: страна проживания кандидата не указана")
    elif (not (worldwide or sponsored) or (regional and not scope)) and not (
        profile.location.strip() and _contains(profile.location, location)
    ):
        unknown = True
        reasons.append("Неизвестно: доступность вакансии из места проживания не подтверждена")
    if onsite and not sponsored and not (
        profile.location.strip() and _contains(profile.location, location)
    ):
        rejected = True
        reasons.append("Офис/гибрид: местное проживание или визовая поддержка не подтверждены")
    if not remote and not onsite and not sponsored:
        unknown = True
        reasons.append("Неизвестно: формат работы не указан")
    if re.search(
        r"(?:security clearance|(?:us|u\.s\.|eu) citizenship) (?:is )?(?:required|mandatory)|"
        r"must (?:be a|have) .*citizen", text
    ):
        unknown = True
        reasons.append("Неизвестно: гражданство или допуск требуют отдельного подтверждения")
    if re.search(
        r"(?:must|required to) (?:have|be).*?(?:work authorization|authori[sz]ed to work)|"
        r"(?:work authorization|right to work) (?:is )?required", text
    ):
        clauses = " ".join(re.findall(
            r"[^.!\n]*(?:work authorization|right to work|authori[sz]ed to work)[^.!\n]*", text
        ))
        required_countries = _countries(clauses) or posted
        declarations = {
            country: permitted for country, permitted in profile.work_authorization.items()
            if (_countries(country) & required_countries) or _contains(country, clauses)
        }
        if len(required_countries) > 1 or len(declarations) != 1:
            unknown = True
            reasons.append("Неизвестно: право на работу требует явного подтверждения для страны")
        elif not next(iter(declarations.values())):
            rejected = True
            reasons.append("В профиле явно отсутствует требуемое право на работу")
    status = "rejected" if rejected else "unknown" if unknown else "eligible"
    if status == "eligible":
        reasons.append("Условия места и формата работы явно совместимы с профилем")
    return status, reasons


def evaluate(job: JobInput, profile: Profile, at: datetime | None = None) -> dict[str, Any]:
    from .matching import evaluate_match
    return evaluate_match(job, profile, at)


def resume_variant(title: str, description: str) -> str:
    # Legacy API helper only; active-resume selection never uses role variants.
    text = (title + " " + description).lower()
    if "python" in text and any(x in text for x in ["full stack", "full-stack", "ai", "llm"]):
        return "react-python-ai"
    if any(x in title.lower() for x in ["full stack", "full-stack", "fullstack"]):
        return "full-stack"
    return "frontend"
