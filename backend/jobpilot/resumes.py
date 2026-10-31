"""Local resume extraction and activation. No inferred identity or legal declarations."""

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Application, Candidate, Job, Resume
from .services import audit, rescore, scoring_context

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
PROVENANCE_FIELDS = {
    "resumes", "active_resume_id", "resume_text", "resume_filename", "resume_parse_warnings"
}

# These are recognition terms, never default candidate skills.
SKILLS = (
    "React Native", "React", "Next.js", "TypeScript", "JavaScript", "Python", "FastAPI",
    "Flask", "Django", "PostgreSQL", "SQLAlchemy", "Redis", "Docker", "Kubernetes",
    "TanStack Query", "Redux Toolkit", "Redux", "Zustand", "Tailwind", "Storybook", "Vite",
    "REST", "WebSockets", "CI/CD", "Node.js", "Express", "GraphQL", "HTML", "CSS",
    "SQL", "MongoDB", "MySQL", "AWS", "Azure", "GCP", "Git", "Java", "Kotlin",
    "Go", "Golang", "Rust", "C++", "C#", ".NET", "PHP", "Laravel", "Ruby", "Rails",
    "Angular", "Vue", "Svelte", "Swift", "Flutter", "Pandas", "NumPy", "PyTorch",
    "TensorFlow", "OpenAI", "LangChain", "LLM", "Figma", "Jest", "Playwright", "Cypress",
    "PixiJS", "WebGL", "GSAP", "RxJS", "NgRx", "Svelte",
)
DOMAINS = (
    "SaaS", "fintech", "payments", "subscriptions", "CRM", "dashboards", "analytics",
    "internal tools", "AI", "LLM", "observability", "e-commerce", "healthcare", "education",
)
ALIASES = {
    "React": r"react(?:\.js|js)?", "Next.js": r"next\.?js",
    "Node.js": r"node\.?js", "PostgreSQL": r"postgres(?:ql)?",
    "Tailwind": r"tailwind(?:\s*css)?", "Vue": r"vue(?:\.js|js)?",
    "PixiJS": r"pixi\.?js", "WebSockets": r"websockets?",
}


@dataclass
class ParsedResume:
    text: str
    facts: dict[str, Any]
    warnings: list[str]


class ResumeParseError(ValueError):
    """A localized, safe error that can be returned to the client."""


def _recognized(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if re.search(
        r"(?<![\w+#])(?:" + ALIASES.get(term, re.escape(term)) + r")(?![\w+#])", text, re.IGNORECASE
    )]


def extract_facts(text: str) -> tuple[dict[str, Any], list[str]]:
    """Conservative facts with explicit empty values for unsupported fields.

    Unstructured sections retain their source text instead of inventing employers,
    dates or degrees. Authorization and other legal answers are never extracted.
    """
    facts: dict[str, Any] = {
        "full_name": None, "email": None, "phone": None, "location": "",
        "years_experience": 0, "portfolio": "", "linkedin": None, "github": None,
        "skills": _recognized(text, SKILLS), "domains": _recognized(text, DOMAINS),
        "employment": [], "projects": [], "education": [], "languages": {},
    }
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    email = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text)
    if email:
        facts["email"] = email.group()
    for field, label in (("full_name", r"(?:full\s+)?name|имя|фио"),
                         ("location", r"location|based in|местоположение|город"),
                         ("phone", r"phone|tel(?:ephone)?|телефон"),
                         ("portfolio", r"portfolio|website|портфолио")):
        match = re.search(rf"^(?:{label})\s*:\s*([^\n|]+)", text, re.IGNORECASE | re.MULTILINE)
        if match:
            facts[field] = match.group(1).strip()
    # A name on the first line is accepted only when it consists of 2-4 name-like words.
    if not facts["full_name"] and lines:
        first = lines[0]
        words = first.split()
        role_words = (r"engineer|developer|resume|curriculum|vitae|designer|manager|skills|experience|"
                      r"резюме|разработчик|инженер|менеджер|дизайнер|аналитик|навыки|опыт|программист|специалист|руководитель")
        if (2 <= len(words) <= 4 and not re.search(role_words, first, re.IGNORECASE)
                and all(re.fullmatch(r"[^\W\d_][^\W\d_'-]*[’'\-]?[^\W\d_]*", w)
                        and w[0].isupper() for w in words)):
            facts["full_name"] = first
    if not facts["phone"]:
        phone = re.search(r"(?<!\w)\+\d[\d ()-]{7,23}\d", text)
        if phone and 8 <= len(re.sub(r"\D", "", phone.group())) <= 15:
            facts["phone"] = phone.group().strip()
    for field in ("github", "linkedin"):
        match = re.search(rf"(?:https?://)?(?:www\.)?{field}\.com/[\w./%-]+", text, re.IGNORECASE)
        if match:
            value = match.group().rstrip("./")
            facts[field] = value if value.lower().startswith("http") else "https://" + value
    years = re.search(
        r"\b(\d{1,2}(?:\.\d)?)\+?\s*(?:years?|yrs?)\s+(?:of\s+)?"
        r"(?:(?:professional|commercial|software|development|engineering)\s+){0,3}experience\b",
        text, re.IGNORECASE,
    )
    if not years:
        years = re.search(
            r"\b(\d{1,2}(?:[.,]\d)?)\+?\s*(?:лет|года?|год)\s+"
            r"(?:(?:коммерческого|профессионального|общего)\s+)?опыта\b",
            text, re.IGNORECASE,
        )
    if not years:
        years = re.search(r"\b(?:более|свыше)\s+(\d{1,2}(?:[.,]\d)?)\+?\s+лет\s+"
                          r"(?:разрабатываю|разработки|работаю|работы|опыта)", text, re.IGNORECASE)
    if not years:
        years = re.search(
            r"\b(?:опыт(?:\s+работы)?|experience)\s*[:—-]\s*"
            r"(\d{1,2}(?:[.,]\d)?)\+?\s*(?:лет|года?|год|years?|yrs?)\b",
            text, re.IGNORECASE,
        )
    if years:
        facts["years_experience"] = float(years.group(1).replace(",", "."))
    if not facts["location"]:
        # Only read an explicit country in the contact/header area, never an employer's address.
        from .domain import _countries
        for line in lines[:6]:
            countries = _countries(line)
            if countries and re.search(r"remote|удал[её]н|based in|\||·|,", line, re.I):
                facts["location"] = ", ".join(sorted(countries))
                break
    language_labels = {
        "English": r"english|английский", "Russian": r"russian|русский",
        "German": r"german|немецкий", "French": r"french|французский",
        "Spanish": r"spanish|испанский", "Georgian": r"georgian|грузинский",
        "Ukrainian": r"ukrainian|украинский", "Portuguese": r"portuguese|португальский",
        "Chinese": r"chinese|китайский", "Japanese": r"japanese|японский",
    }
    level = (r"[ABC][12]|native(?:\s+speaker)?|fluent|advanced|upper[ -]intermediate|"
             r"intermediate|elementary|beginner|basic|professional(?:\s+working)?(?:\s+proficiency)?|"
             r"родной|свободн(?:ый|о)|продвинутый|средний|начальный|базовый")
    for language, labels in language_labels.items():
        match = re.search(
            rf"\b(?:{labels})\s*(?:[:—–-]\s*|\(\s*)?({level})\b", text, re.IGNORECASE
        )
        if match:
            facts["languages"][language] = match.group(1)
    sections = {
        "experience": "employment", "work experience": "employment",
        "professional experience": "employment", "employment": "employment",
        "projects": "projects", "selected projects": "projects", "education": "education",
        "опыт работы": "employment", "проекты": "projects", "образование": "education",
    }
    headings = set(sections) | {
        "skills", "technical skills", "summary", "profile", "languages", "certifications",
        "contact", "interests", "навыки", "языки",
    }
    current: str | None = None
    section_lines: list[str] = []

    def finish() -> None:
        if current and section_lines:
            facts[current].append({"summary": "\n".join(section_lines)})

    for line in lines:
        heading = line.rstrip(":").casefold()
        if heading in headings:
            finish()
            current = sections.get(heading)
            section_lines = []
        elif current:
            section_lines.append(line)
    finish()
    warnings = ["Проверьте извлечённые факты: разрешение на работу и юридические ответы не определяются автоматически."]
    if not facts["full_name"]:
        warnings.append("Не удалось достоверно определить полное имя.")
    if not years:
        warnings.append("Стаж не указан явно: 0 означает неизвестный стаж. Даты работы не пересчитывались.")
    if any(facts[field] for field in ("employment", "projects", "education")):
        warnings.append("Опыт работы, проекты и образование сохранены исходным текстом для проверки.")
    return facts, warnings


def parse_resume(content: bytes, filename: str) -> ParsedResume:
    suffix = Path(filename).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise ResumeParseError("Загрузите резюме в формате PDF, DOCX или TXT")
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ResumeParseError("Загрузите непустое резюме размером не более 10 МБ")
    try:
        if suffix == ".pdf":
            if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
                raise ResumeParseError("Загрузите полный PDF-файл")
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise ResumeParseError("Загрузите PDF без пароля и шифрования")
            if len(reader.pages) > 100:
                raise ResumeParseError("Резюме должно содержать не более 100 страниц")
            parts = []
            total = 0
            for page in reader.pages:
                stream = page.get_contents()
                if stream is not None and len(stream.get_data()) > 20 * 1024 * 1024:
                    raise ResumeParseError("Содержимое страницы PDF слишком велико")
                part = page.extract_text() or ""
                total += len(part)
                if total > MAX_TEXT_CHARS:
                    raise ResumeParseError("Текст резюме слишком велик")
                parts.append(part)
            text = "\n".join(parts)
        elif suffix == ".docx":
            with ZipFile(BytesIO(content)) as archive:
                infos = archive.infolist()
                if len(infos) > 1000 or sum(i.file_size for i in infos) > 20 * 1024 * 1024:
                    raise ResumeParseError("Распакованное содержимое DOCX слишком велико")
                if "[Content_Types].xml" not in archive.namelist():
                    raise ResumeParseError("Загрузите корректный документ DOCX")
                names = ["word/document.xml"] + sorted(
                    n for n in archive.namelist() if re.fullmatch(r"word/(?:header|footer)\d+\.xml", n)
                )
                paragraphs = []
                ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                for name in names:
                    xml = archive.read(name)
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                        raise ResumeParseError("DOCX содержит неподдерживаемые XML-объявления")
                    root = ElementTree.fromstring(xml)
                    for paragraph in root.iter(ns + "p"):
                        paragraphs.append("".join(
                            element.text or "" if element.tag == ns + "t" else
                            "\t" if element.tag == ns + "tab" else
                            "\n" if element.tag in {ns + "br", ns + "cr"} else ""
                            for element in paragraph.iter()
                        ))
                text = "\n".join(paragraphs)
        else:
            encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = content.decode(encoding)
    except (BadZipFile, KeyError, ElementTree.ParseError, UnicodeError) as exc:
        raise ValueError("Не удалось прочитать резюме. Загрузите корректный PDF, DOCX или TXT в UTF-8/UTF-16") from exc
    except ResumeParseError:
        raise
    except Exception as exc:
        # Parser exceptions vary between PDF versions; do not leak document content.
        raise ValueError("Не удалось прочитать резюме. Загрузите корректный документ без шифрования") from exc
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Текст резюме слишком велик")
    if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
        raise ValueError("Резюме содержит двоичные данные или неподдерживаемый текст")
    text = "\n".join(line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
    if not text or not any(c.isalnum() for c in text):
        raise ValueError("Читаемый текст не найден. Распознайте сканированный PDF (OCR) перед загрузкой")
    facts, warnings = extract_facts(text)
    return ParsedResume(text, facts, warnings)


def rescore_pending(db: Session) -> int:
    """Use existing audited transitions, without touching queued or attempted work."""
    rows = db.execute(
        select(Job, Application).join(Application).where(
            Application.state.in_({"DISCOVERED", "NORMALIZED", "MATCHED", "REJECTED"}),
            Application.submit_intent_at.is_(None), Application.submitted_at.is_(None),
            Application.attempt_id.is_(None),
        ).with_for_update(of=Application)
    ).all()
    candidate_profile, active_resume_valid = scoring_context(db)
    for job, application in rows:
        attention = application.attention
        # A preflight selection on an unattempted application may belong to the
        # previous active resume. Let readiness bind the current selection again.
        application.resume_id = None
        rescore(db, job, application, candidate_profile, active_resume_valid)
        if job.duplicate_of and attention:
            application.attention = attention
    return len(rows)


def activate_resume(db: Session, candidate: Candidate, resume: Resume, parsed: ParsedResume) -> int:
    records = dict(candidate.facts.get("resumes", {}))
    records[resume.id] = {
        "filename": resume.filename, "sha256": resume.sha256,
        "media_type": MEDIA_TYPES[Path(resume.filename).suffix.lower()],
        "text": parsed.text, "facts": parsed.facts, "warnings": parsed.warnings,
    }
    candidate.facts = {
        **candidate.facts, **parsed.facts, "resumes": records,
        "active_resume_id": resume.id, "resume_text": parsed.text,
        "resume_filename": resume.filename, "resume_parse_warnings": parsed.warnings,
        "location": candidate.facts.get("location") or parsed.facts.get("location", ""),
        "relocation": candidate.facts.get("relocation", ""),
    }
    candidate.version += 1
    count = rescore_pending(db)
    audit(db, "cv_activated", {
        "resume_id": resume.id, "sha256": resume.sha256,
        "profile_version": candidate.version, "jobs_rescored": count,
    })
    return count
