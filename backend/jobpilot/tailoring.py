"""Evidence-preserving selection and ATS-readable application documents.

Vacancy text ranks candidate evidence only. It is never used as candidate facts,
and the source resume is retained in its entirety, in its original order.
"""

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .answers import NeedsAttention
from .domain import JobInput, Profile, _contains, evaluate
from .models import Candidate, Job, Resume
from .resumes import verified_resume
from .services import job_input


def _job_input(job: Job | JobInput) -> JobInput:
    return job_input(job) if isinstance(job, Job) else job


def resume_contexts(db: Session, profile: Profile) -> list[tuple[Resume, Profile]]:
    """Read and verify each upload once for a batch of vacancy evaluations."""
    shared = (
        "full_name", "email", "phone", "location", "search_locations", "portfolio",
        "linkedin", "github", "work_authorization", "relocation", "salary_expectation",
        "notice_period", "legal_answers",
    )
    contexts = []
    candidate = db.get(Candidate, 1)
    records = candidate.facts.get("resumes", {}) if candidate else {}
    for resume in db.scalars(select(Resume).order_by(Resume.created_at.desc(), Resume.id)):
        try:
            parsed = verified_resume(db, resume, records)
        except (OSError, ValueError):
            continue
        facts: dict[str, Any] = dict(parsed.facts)
        for field in shared:
            value = getattr(profile, field)
            if value not in (None, "", [], {}):
                facts[field] = value
        facts.update(active_resume_id=resume.id, resume_text=parsed.text,
                     resume_filename=resume.filename, resume_parse_warnings=parsed.warnings)
        context = Profile.model_validate(facts)
        contexts.append((resume, context))
    return contexts


def select_resume(db: Session, job: Job | JobInput, profile: Profile, *,
                  contexts: list[tuple[Resume, Profile]] | None = None) -> tuple[Resume, Profile]:
    """Rank intact uploads using document facts and verified shared preferences."""
    ranked: list[tuple[tuple[int, int, int, int], Resume, Profile]] = []
    vacancy = _job_input(job)
    for resume, context in resume_contexts(db, profile) if contexts is None else contexts:
        result = evaluate(vacancy, context)
        # A high textual score cannot override a missing mandatory qualification.
        # Prefer documents that can actually satisfy application policy first.
        qualified = bool(result.get("recommendable")) and result["eligibility"] == "eligible"
        rank = (int(qualified), int(result["score"]), int(bool(result.get("recommendable"))),
                int(resume.id == profile.active_resume_id))
        ranked.append((rank, resume, context))
    if not ranked:
        raise NeedsAttention("Нет доступного читаемого резюме с подтверждённой целостностью; загрузите файл повторно")
    _, resume, selected = max(ranked, key=lambda item: item[0])
    return resume, selected


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W)


def _node(parent: ET.Element, name: str, **attributes: str) -> ET.Element:
    return ET.SubElement(parent, f"{{{W}}}{name}", {f"{{{W}}}{k}": v for k, v in attributes.items()})


def _paragraph(body: ET.Element, text: str, style: str = "Normal") -> None:
    paragraph = _node(body, "p")
    properties = _node(paragraph, "pPr")
    _node(properties, "pStyle", val=style)
    run = _node(paragraph, "r")
    # w:br preserves multiline source sections without creating tables/text boxes.
    for i, line in enumerate(text.split("\n")):
        if i:
            _node(run, "br")
        value = _node(run, "t")
        value.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        value.text = line


def _write_docx(path: Path, paragraphs: list[tuple[str, str]]) -> Path:
    document = ET.Element(f"{{{W}}}document")
    body = _node(document, "body")
    for text, style in paragraphs:
        _paragraph(body, text, style)
    section = _node(body, "sectPr")
    _node(section, "pgSz", w="12240", h="15840")
    _node(section, "pgMar", top="1080", right="1080", bottom="1080", left="1080")
    styles = ET.Element(f"{{{W}}}styles")
    for name, size in (("Normal", "22"), ("Title", "36"), ("Heading1", "26")):
        style_node = _node(styles, "style", type="paragraph", styleId=name)
        if name == "Normal":
            style_node.set(f"{{{W}}}default", "1")
        _node(style_node, "name", val=name)
        if name != "Normal":
            _node(style_node, "basedOn", val="Normal")
        properties = _node(style_node, "pPr")
        _node(properties, "spacing", before="120" if name == "Heading1" else "0", after="100")
        if name != "Normal":
            _node(properties, "keepNext")
        _node(properties, "widowControl")
        run = _node(style_node, "rPr")
        _node(run, "rFonts", ascii="Arial", hAnsi="Arial", cs="Arial")
        _node(run, "color", val="000000")
        _node(run, "sz", val=size)
        if name != "Normal":
            _node(run, "b")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    document_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        # Approval binds document bytes, so regeneration after a restart must
        # not change the ZIP timestamp or depend on the host's file metadata.
        parts: list[tuple[str, str | bytes]] = [
            ("[Content_Types].xml", content_types), ("_rels/.rels", relationships),
            ("word/_rels/document.xml.rels", document_relationships),
            ("word/document.xml", ET.tostring(document, encoding="utf-8", xml_declaration=True)),
            ("word/styles.xml", ET.tostring(styles, encoding="utf-8", xml_declaration=True)),
        ]
        for name, data in parts:
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    temporary.replace(path)
    return path


def generate_resume(resume: Resume, profile: Profile, job: Job | JobInput, output_dir: Path) -> Path:
    """Create a plain DOCX with relevant proven skills and the complete source CV.

    This conservative customization deliberately retains dates, employers and all
    source sections verbatim. It does not let an LLM rewrite candidate history.
    """
    if profile.active_resume_id != resume.id or not profile.resume_text.strip():
        raise NeedsAttention("Выбранное резюме не соответствует данным для генерации")
    vacancy = _job_input(job)
    context = vacancy.title + "\n" + vacancy.description
    skills = sorted(dict.fromkeys(profile.skills), key=lambda value: not _contains(value, context))
    relevant = [value for value in skills if _contains(value, context)]
    russian = bool(re.search(r"[а-яё]", profile.resume_text[:500], re.IGNORECASE))
    paragraphs: list[tuple[str, str]] = []
    if profile.full_name:
        paragraphs.append((profile.full_name, "Title"))
    contact = [profile.email, profile.phone, profile.location, profile.portfolio, profile.linkedin, profile.github]
    if any(contact):
        paragraphs.append((" | ".join(str(value) for value in contact if value), "Normal"))
    if relevant:
        paragraphs.extend([("Релевантные навыки" if russian else "Relevant skills", "Heading1"),
                           (", ".join(relevant), "Normal")])
    paragraphs.append(("Резюме" if russian else "Resume", "Heading1"))
    # Keep *every* source line, including qualifications unrelated to this job.
    paragraphs.extend((line, "Normal") for line in profile.resume_text.splitlines())
    return _write_docx(Path(output_dir) / "resume.docx", paragraphs)


def generate_cover_letter(profile: Profile, job: Job | JobInput, output_dir: Path) -> Path:
    """Create a brief evidence-only letter without inventing personal motivation."""
    vacancy = _job_input(job)
    russian = bool(re.search(r"[а-яё]", vacancy.title, re.IGNORECASE))
    from .answers import AnswerEngine
    engine = AnswerEngine(profile, {})
    facts = engine.facts(russian)
    keys = engine.relevant_facts(facts, vacancy.title + "\n" + vacancy.description)
    if not keys:
        raise NeedsAttention("Нет подтверждённых фактов для сопроводительного письма")
    text = " ".join(facts[key] for key in keys)
    opening = "Здравствуйте! Прошу рассмотреть мою кандидатуру." if russian else "Hello! Please consider my application."
    closing = "Спасибо за рассмотрение моей кандидатуры." if russian else "Thank you for considering my application."
    paragraphs = [(("Сопроводительное письмо" if russian else "Cover letter"), "Title"),
                  (opening, "Normal"), (text, "Normal"), (closing, "Normal")]
    if profile.full_name:
        paragraphs.append((profile.full_name, "Normal"))
    return _write_docx(Path(output_dir) / "cover-letter.docx", paragraphs)
