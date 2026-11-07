"""Resume extraction, provenance, activation and submission-boundary regression tests."""

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select

from jobpilot import api
from jobpilot.config import settings
from jobpilot.db import session
from jobpilot.models import Application, Audit, Candidate, Job, Outbox, Resume, Source, now
from jobpilot.resumes import MAX_UPLOAD_BYTES, parse_resume


@pytest.fixture
def client(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "data_dir", tmp_path)
    api.app.dependency_overrides[session] = lambda: db
    test_client = TestClient(api.app, headers={
        "Authorization": "Bearer " + settings().api_token.get_secret_value()
    })
    try:
        # No context manager/lifespan: use the fixture's isolated database.
        yield test_client
    finally:
        test_client.close()
        api.app.dependency_overrides.clear()


def pdf_bytes(text="Jane Doe\nReact TypeScript\n5 years of experience"):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
        })
    })
    stream = DecodedStreamObject()
    lines = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").splitlines()
    stream.set_data(("BT /F1 12 Tf 50 740 Td 14 TL " +
                     " ".join(f"({line}) Tj T*" for line in lines) + " ET").encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def docx_bytes(text="Jane Doe\nPython Django"):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' +
            "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in text.splitlines()) +
            '</w:body></w:document>')
    return buffer.getvalue()


def upload(client, text, filename="resume.txt"):
    return client.post("/resumes", data={"variant": "frontend"},
                       files={"file": (filename, text.encode("utf-8"), "text/plain")})


def add_job(db, state="MATCHED", **application_fields):
    source = Source(provider="ashby", board="example", company="Example")
    db.add(source)
    db.flush()
    job = Job(source_id=source.id, provider="ashby", external_id=source.id,
              canonical_url=f"https://jobs.ashbyhq.com/example/{source.id}",
              company="Example", title="Frontend React Developer", location="Remote worldwide",
              description="React TypeScript Python FastAPI", score=1, eligibility="unknown")
    db.add(job)
    db.flush()
    application = Application(job_id=job.id, state=state, **application_fields)
    db.add(application)
    db.commit()
    return job, application


@pytest.mark.parametrize("filename,content", [
    ("cv.pdf", pdf_bytes()), ("cv.docx", docx_bytes("Jane Doe\nReact TypeScript\n5 years of experience")),
    ("cv.txt", b"Jane Doe\nReact TypeScript\n5 years of experience"),
], ids=lambda value: f"bytes-{len(value)}" if isinstance(value, bytes) else None)
def test_extract_actual_document_text(filename, content):
    parsed = parse_resume(content, filename)
    assert parsed.facts["full_name"] == "Jane Doe"
    assert parsed.facts["skills"] == ["React", "TypeScript"]
    assert parsed.facts["years_experience"] == 5
    assert parsed.facts["location"] == ""
    assert parsed.facts["portfolio"] == ""
    assert parsed.facts["domains"] == []
    assert "Jane Doe" in parsed.text


def test_explicit_facts_and_sections_without_invented_details():
    parsed = parse_resume(b"""Name: Jane Doe
Email: jane@example.invalid
Location: Berlin, Germany
Phone: +49 123 456 7890
Portfolio: https://jane.example
https://github.com/jane
https://linkedin.com/in/jane
7.5 years of professional experience
Skills
Python, Django, PostgreSQL
Experience
Engineer at Actual Company, 2020-2024
Education
Actual University
Projects
Built a payments service
""", "cv.txt")
    facts = parsed.facts
    assert facts["email"] == "jane@example.invalid"
    assert facts["location"] == "Berlin, Germany"
    assert facts["years_experience"] == 7.5
    assert facts["phone"] == "+49 123 456 7890"
    assert facts["github"] == "https://github.com/jane"
    assert facts["linkedin"] == "https://linkedin.com/in/jane"
    assert facts["employment"] == [{"summary": "Engineer at Actual Company, 2020-2024"}]
    assert facts["education"] == [{"summary": "Actual University"}]
    assert facts["projects"] == [{"summary": "Built a payments service"}]
    assert "work_authorization" not in facts


def test_unicode_and_no_seeded_facts():
    text = "Имя: Иван Петров\nPython\nТелефон: +995 555 123 456"
    parsed = parse_resume(text.encode("utf-16"), "cv.TXT")
    assert parsed.facts["full_name"] == "Иван Петров"
    assert parsed.facts["skills"] == ["Python"]
    assert parsed.facts["years_experience"] == 0
    assert parsed.facts["email"] is None
    assert parsed.warnings


@pytest.mark.parametrize("statement,years", [
    ("5 лет опыта", 5), ("Опыт работы: 5 лет", 5),
    ("3,5 года коммерческого опыта", 3.5), ("Опыт: 7+ лет", 7),
    ("Worked from 2019 to 2025", 0),
])
def test_russian_explicit_experience(statement, years):
    parsed = parse_resume(("Иван Петров\n" + statement).encode(), "cv.txt")
    assert parsed.facts["years_experience"] == years
    assert all(any("а" <= c.lower() <= "я" for c in warning) for warning in parsed.warnings)


def test_languages_only_explicit_levels():
    parsed = parse_resume("""Jane Doe
Languages
English: C1
Русский — родной
German (B2)
French
Spanish is used by customers
""".encode(), "cv.txt")
    assert parsed.facts["languages"] == {"English": "C1", "Russian": "родной", "German": "B2"}


def test_activation_keeps_user_legal_answers_and_clears_stale_selection(client, db):
    first = upload(client, "Jane Doe\nReact").json()
    candidate = db.get(Candidate, 1)
    candidate.facts = {**candidate.facts, "work_authorization": {"Germany": True},
                       "legal_answers": {"Require sponsorship?": "Yes"}}
    db.commit()
    _, application = add_job(db, resume_id=first["id"])
    second = upload(client, "Jane Doe\nPython").json()
    assert second["jobs_rescored"] == 1
    assert application.resume_id is None
    assert candidate.facts["work_authorization"] == {"Germany": True}
    assert candidate.facts["legal_answers"] == {"Require sponsorship?": "Yes"}


def test_legacy_resume_activation_parses_existing_file(client, db, tmp_path):
    import hashlib

    content = b"Legacy Candidate\nPython"
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "legacy.txt").write_bytes(content)
    resume = Resume(variant="frontend", filename="legacy.txt", storage_key="legacy.txt",
                    sha256=hashlib.sha256(content).hexdigest())
    db.add(resume)
    db.commit()
    response = client.post(f"/resumes/{resume.id}/activate")
    assert response.status_code == 200, response.text
    assert db.get(Candidate, 1).facts["skills"] == ["Python"]
    assert db.get(Candidate, 1).facts["active_resume_id"] == resume.id


@pytest.mark.parametrize("content,filename", [
    (b"", "cv.txt"), (b"not a PDF", "cv.pdf"), (b"%PDF-1.4\n%%EOF", "cv.pdf"),
    (b"broken zip", "cv.docx"), (b"abc", "cv.exe"), (b"\x00binary", "cv.txt"),
    (b"\xffinvalid", "cv.txt"), (b" \n\t", "cv.txt"), (b"x" * (MAX_UPLOAD_BYTES + 1), "cv.txt"),
], ids=lambda value: f"bytes-{len(value)}" if isinstance(value, bytes) else None)
def test_reject_invalid_documents(content, filename):
    with pytest.raises(ValueError):
        parse_resume(content, filename)


def test_image_only_and_encrypted_pdf_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError, match="OCR"):
        parse_resume(buffer.getvalue(), "scan.pdf")
    writer.encrypt("secret")
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError, match="без пароля"):
        parse_resume(buffer.getvalue(), "encrypted.pdf")


def test_upload_activates_rescores_and_audits_without_queueing(client, db):
    job, application = add_job(db)
    response = upload(client, "Jane Doe\njane@example.invalid\nReact TypeScript\n5 years of experience")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active"] is True and body["jobs_rescored"] == 1
    facts = client.get("/profile").json()
    assert facts["active_resume_id"] == body["id"]
    assert facts["resume_filename"] == "resume.txt"
    assert facts["skills"] == ["React", "TypeScript"]
    assert facts["resumes"][body["id"]]["text"] == facts["resume_text"]
    assert facts["work_authorization"] == {}
    assert job.score > 1
    assert application.state == "MATCHED"
    assert application.resume_id is None
    assert not db.scalars(select(Outbox)).all()
    events = db.scalars(select(Audit).order_by(Audit.id)).all()
    assert {e.kind for e in events} >= {"cv_uploaded", "cv_activated", "state_changed"}
    assert all("jane@example.invalid" not in str(e.detail) for e in events)


@pytest.mark.parametrize("filename,content,media", [
    ("cv.pdf", pdf_bytes(), "application/pdf"),
    ("cv.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("cv.txt", b"Jane Doe\nPython", "text/plain"),
], ids=lambda value: f"bytes-{len(value)}" if isinstance(value, bytes) else None)
def test_upload_download_types(client, filename, content, media):
    response = client.post("/resumes", files={"file": (filename, content, media)})
    assert response.status_code == 200, response.text
    assert response.json()["variant"] == "general"
    download = client.get(f"/resumes/{response.json()['id']}/file")
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-type"].startswith(media)


@pytest.mark.parametrize("variant", ["general", "frontend", "full-stack", "react-python-ai"])
def test_upload_accepts_frontend_general_and_legacy_variants(client, variant):
    response = client.post("/resumes", data={"variant": variant},
                           files={"file": ("cv.txt", b"Jane Doe\nPython", "text/plain")})
    assert response.status_code == 200, response.text
    assert response.json()["variant"] == variant


def test_activation_replaces_previous_resume_facts(client, db):
    first = upload(client, "Jane Doe\nReact TypeScript\n5 years of experience").json()
    second = upload(client, "John Smith\nPython Django", "other.txt").json()
    facts = client.get("/profile").json()
    assert facts["active_resume_id"] == second["id"]
    assert facts["skills"] == ["Python", "Django"]
    assert facts["years_experience"] == 0
    response = client.post(f"/resumes/{first['id']}/activate")
    assert response.status_code == 200
    facts = client.get("/profile").json()
    assert facts["active_resume_id"] == first["id"]
    assert facts["skills"] == ["React", "TypeScript"]
    assert set(facts["resumes"]) == {first["id"], second["id"]}
    listed = client.get("/resumes").json()
    assert {r["id"] for r in listed if r["active"]} == {first["id"]}


def test_activation_and_selection_reuse_verified_extraction(client, db, monkeypatch):
    from jobpilot import resumes
    from jobpilot.domain import Profile
    from jobpilot.tailoring import resume_contexts

    first = upload(client, "Jane Doe\nReact TypeScript").json()
    second = upload(client, "Jane Doe\nPython Django", "other.txt").json()

    def unexpected_parse(*args, **kwargs):
        raise AssertionError("A cached resume was parsed again")

    monkeypatch.setattr(resumes, "parse_resume", unexpected_parse)
    assert client.post(f"/resumes/{first['id']}/activate").status_code == 200
    contexts = {resume.id: profile for resume, profile in resume_contexts(db, Profile())}
    assert contexts[first["id"]].skills == ["React", "TypeScript"]
    assert contexts[second["id"]].skills == ["Python", "Django"]


def test_profile_partial_save_protects_provenance_and_rescores(client, db):
    resume = upload(client, "Jane Doe\nReact").json()
    before = client.get("/profile").json()
    job, _ = add_job(db)
    result = client.put("/profile", json={
        "skills": ["Python"], "work_authorization": {"Germany": True},
        "active_resume_id": "forged", "resume_text": "forged", "resume_filename": "forged.txt",
        "resume_parse_warnings": [], "resumes": {},
    })
    assert result.status_code == 200, result.text
    after = result.json()
    for key in ("active_resume_id", "resume_text", "resume_filename", "resume_parse_warnings", "resumes"):
        assert after[key] == before[key]
    assert after["skills"] == ["Python"]
    assert after["full_name"] == "Jane Doe"
    assert after["work_authorization"] == {"Germany": True}
    assert job.score > 1
    assert after["resumes"][resume["id"]]["facts"]["skills"] == ["React"]


@pytest.mark.parametrize("state,fields", [
    ("READY", {}), ("APPLYING", {"attempt_id": "attempt"}),
    ("SUBMISSION_UNCERTAIN", {"submit_intent_at": now()}),
    ("SUBMITTED", {"submitted_at": now(), "submit_intent_at": now()}),
    ("FAILED", {"attempt_id": "attempt"}), ("REJECTED", {"attempt_id": "attempt"}),
    ("MATCHED", {"submit_intent_at": now()}), ("INTERVIEW", {}),
])
def test_upload_and_profile_save_preserve_attempted_applications(client, db, state, fields):
    job, application = add_job(db, state, attention="Keep evidence", **fields)
    before = api.serialize(application)
    explanation = dict(job.explanation)
    assert upload(client, "Jane Doe\nReact TypeScript").json()["jobs_rescored"] == 0
    assert client.put("/profile", json={"skills": ["Python"]}).status_code == 200
    assert api.serialize(application) == before
    assert job.score == 1 and job.explanation == explanation
    assert not db.scalars(select(Audit).where(Audit.application_id == application.id)).all()


def test_legacy_profile_get_merges_defaults(client, db):
    db.get(Candidate, 1).facts = {"full_name": "Legacy Candidate", "custom": "retained"}
    db.commit()
    facts = client.get("/profile").json()
    assert facts["active_resume_id"] is None
    assert facts["resume_text"] == ""
    assert facts["resume_filename"] is None
    assert facts["resume_parse_warnings"] == []
    assert facts["custom"] == "retained"


def test_invalid_upload_is_atomic(client, db, tmp_path):
    before = dict(db.get(Candidate, 1).facts)
    response = client.post("/resumes", files={"file": ("bad.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
    assert response.status_code == 422
    assert db.get(Candidate, 1).facts == before
    assert not db.scalars(select(Resume)).all()
    assert not db.scalars(select(Audit)).all()
    assert not (tmp_path / "resumes").exists()


def test_failed_rescore_rolls_back_upload_and_removes_file(client, db, tmp_path, monkeypatch):
    import jobpilot.resumes as resume_service

    def fail(_db):
        raise RuntimeError("injected rescore failure")

    monkeypatch.setattr(resume_service, "rescore_pending", fail)
    before = dict(db.get(Candidate, 1).facts)
    with pytest.raises(RuntimeError, match="injected"):
        upload(client, "Jane Doe\nReact")
    assert db.get(Candidate, 1).facts == before
    assert not db.scalars(select(Resume)).all()
    assert not db.scalars(select(Audit)).all()
    assert not list((tmp_path / "resumes").iterdir())


def test_missing_or_tampered_resume_activation_does_not_change_profile(client, db, tmp_path):
    resume = upload(client, "Jane Doe\nReact").json()
    before = dict(db.get(Candidate, 1).facts)
    stored = db.get(Resume, resume["id"])
    (tmp_path / "resumes" / stored.storage_key).write_bytes(b"tampered")
    assert client.post(f"/resumes/{resume['id']}/activate").status_code == 409
    assert client.post("/resumes/missing/activate").status_code == 404
    assert db.get(Candidate, 1).facts == before


def test_resume_routes_require_auth_when_explicitly_enabled(client, monkeypatch):
    monkeypatch.setattr(settings(), "auth_required", True)
    client.headers.pop("Authorization")
    assert client.get("/resumes").status_code == 401
    assert upload(client, "Jane Doe").status_code == 401
    assert client.post("/resumes/missing/activate").status_code == 401
    assert client.get("/resumes/missing/file").status_code == 401
