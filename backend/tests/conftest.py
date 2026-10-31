import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["API_TOKEN"] = "test-token-" + "a" * 40
os.environ["LLM_API_KEY"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from jobpilot.db import Base
from jobpilot.services import bootstrap


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        bootstrap(session)
        yield session
    engine.dispose()


@pytest.fixture
def active_candidate(db, tmp_path, monkeypatch):
    """A real selected artifact and explicit facts for submission workflow tests."""
    from jobpilot.config import settings
    from jobpilot.domain import Profile
    from jobpilot.models import Candidate, Control, Resume

    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "cv.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    resume = Resume(variant="general", filename="cv.pdf", storage_key="cv.pdf", sha256="f" * 64)
    db.add(resume)
    db.flush()
    candidate = db.get(Candidate, 1)
    candidate.facts = Profile(
        full_name="Test Candidate", email="test@example.invalid", location="Georgia",
        skills=["React", "Next.js", "TypeScript", "JavaScript", "Python"],
        years_experience=5, domains=["SaaS", "fintech", "payments", "analytics"],
        active_resume_id=resume.id, resume_filename=resume.filename,
        resume_text="Test Candidate\nSenior Frontend Engineer. React Next.js TypeScript JavaScript Python. "
                    "SaaS fintech payments analytics. 5 years experience.",
    ).model_dump()
    db.get(Control, 1).min_score = 0
    db.get(Control, 1).auto_apply = True
    db.commit()
    monkeypatch.setattr(settings(), "data_dir", tmp_path)
    return candidate
