"""Durable upload storage on hosted installations; preserve native file behavior."""
import hashlib

from sqlalchemy.orm import Session

from .config import settings
from .models import Resume, ResumeFile
from .resumes import MAX_UPLOAD_BYTES


def store_file(db: Session, resume: Resume, content: bytes) -> None:
    if settings().store_resume_files_in_db:
        existing = db.get(ResumeFile, resume.id)
        if existing:
            existing.content = content
        else:
            db.add(ResumeFile(resume_id=resume.id, content=content))


def read_file(db: Session, resume: Resume) -> bytes:
    stored = db.get(ResumeFile, resume.id) if settings().store_resume_files_in_db else None
    if stored:
        content = stored.content
    else:
        path = settings().data_dir / "resumes" / resume.storage_key
        with path.open("rb") as stream:
            content = stream.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES or hashlib.sha256(content).hexdigest() != resume.sha256:
        raise ValueError("Нарушена целостность файла резюме. Загрузите его повторно")
    return content
