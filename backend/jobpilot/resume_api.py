import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import Response
from urllib.parse import quote
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import session
from .http import authenticate, serialize
from .models import (
    Candidate,
    Resume,
    uid,
)
from .resumes import (
    MAX_UPLOAD_BYTES,
    MEDIA_TYPES,
    activate_resume,
    parse_resume,
    verified_resume,
)
from .resume_storage import read_file, store_file
from .services import (
    audit,
)

router = APIRouter(dependencies=[Depends(authenticate)])


@router.get("/resumes")
def resumes(db: Session = Depends(session)) -> list[dict[str, Any]]:
    candidate = db.get(Candidate, 1)
    facts = candidate.facts if candidate else {}
    records = facts.get("resumes", {})
    return [
        {
            **serialize(r), "active": facts.get("active_resume_id") == r.id,
            "parse_warnings": records.get(r.id, {}).get("warnings", []),
            "text_length": len(records.get(r.id, {}).get("text", "")),
        }
        for r in db.scalars(select(Resume).order_by(Resume.created_at.desc()))
    ]


@router.post("/resumes")
async def upload_resume(
    variant: str = Form(default="general"), file: UploadFile = File(), db: Session = Depends(session)
) -> dict[str, Any]:
    if variant not in {"general", "frontend", "full-stack", "react-python-ai"}:
        raise HTTPException(422, "Неизвестный вариант резюме")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    filename = Path((file.filename or "resume.pdf").replace("\\", "/")).name
    if len(filename) > 255:
        raise HTTPException(422, "Имя файла резюме должно содержать не более 255 символов")
    try:
        parsed = parse_resume(content, filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    candidate = db.scalar(select(Candidate).where(Candidate.id == 1).with_for_update())
    if candidate is None:
        raise HTTPException(409, "Профиль кандидата отсутствует")
    key = uid() + Path(filename).suffix.lower()
    folder = settings().data_dir / "resumes"
    resume = Resume(
        variant=variant,
        filename=filename,
        storage_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    path = folder / key
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        db.add(resume)
        db.flush()
        store_file(db, resume, content)
        audit(db, "cv_uploaded", {"resume_id": resume.id, "variant": variant, "sha256": resume.sha256})
        count = activate_resume(db, candidate, resume, parsed)
        from .search_setup import ensure_resume_searches
        ensure_resume_searches(db)
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return {
        **serialize(resume), "active": True, "parsed_facts": parsed.facts,
        "parse_warnings": parsed.warnings, "text_length": len(parsed.text), "jobs_rescored": count,
    }


@router.post("/resumes/{resume_id}/activate")
def activate_uploaded_resume(resume_id: str, db: Session = Depends(session)) -> dict[str, Any]:
    candidate = db.scalar(select(Candidate).where(Candidate.id == 1).with_for_update())
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "Резюме не найдено")
    if candidate is None:
        raise HTTPException(409, "Профиль кандидата отсутствует")
    try:
        parsed = verified_resume(db, resume, candidate.facts.get("resumes", {}))
    except OSError as exc:
        raise HTTPException(409, "Файл резюме недоступен. Загрузите его повторно") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    count = activate_resume(db, candidate, resume, parsed)
    db.commit()
    return {
        **serialize(resume), "active": True, "parsed_facts": parsed.facts,
        "parse_warnings": parsed.warnings, "text_length": len(parsed.text), "jobs_rescored": count,
    }


@router.get("/resumes/{resume_id}/file")
def resume_file(resume_id: str, db: Session = Depends(session)) -> Response:
    r = db.get(Resume, resume_id)
    if not r:
        raise HTTPException(404, "Резюме не найдено")
    if settings().store_resume_files_in_db:
        try:
            content = read_file(db, r)
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "Файл резюме недоступен") from exc
        return Response(content, media_type=MEDIA_TYPES.get(Path(r.filename).suffix.lower(), "application/octet-stream"),
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(r.filename)}"})
    path = settings().data_dir / "resumes" / r.storage_key
    if not path.is_file():
        raise HTTPException(404, "Файл резюме недоступен")
    return FileResponse(
        path, filename=r.filename,
        media_type=MEDIA_TYPES.get(Path(r.filename).suffix.lower(), "application/octet-stream"),
    )


