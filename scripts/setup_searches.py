"""Migrate, import explicitly provided CV files and configure missing searches.

Use --local for the native SQLite workspace; otherwise use the configured database.
Existing resumes, profile preferences and search filters are preserved.
"""
import argparse
import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def configure(local: bool, paths: list[Path]) -> None:
    if local:
        folder = ROOT / "data"
        folder.mkdir(exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite:///{(folder / 'jobpilot-local.db').as_posix()}"
        os.environ["DATA_DIR"] = str(folder)
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select
    from jobpilot.config import settings
    from jobpilot.db import SessionLocal
    from jobpilot.models import Candidate, Resume, uid
    from jobpilot.resumes import MAX_UPLOAD_BYTES, activate_resume, parse_resume
    from jobpilot.search_setup import ensure_resume_searches
    from jobpilot.companies import seed_companies
    from jobpilot.services import bootstrap

    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    command.upgrade(config, "head")
    imported = 0
    written: list[Path] = []
    with SessionLocal() as db:
        bootstrap(db)
        candidate = db.get(Candidate, 1)
        assert candidate is not None
        try:
            for path in paths:
                with path.open("rb") as stream:
                    content = stream.read(MAX_UPLOAD_BYTES + 1)
                digest = hashlib.sha256(content).hexdigest()
                if db.scalar(select(Resume.id).where(Resume.sha256 == digest)):
                    continue
                parsed = parse_resume(content, path.name)
                resume = Resume(variant="general", filename=path.name, sha256=digest,
                                storage_key=uid() + path.suffix.lower())
                destination = settings().data_dir / "resumes" / resume.storage_key
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                written.append(destination)
                db.add(resume); db.flush()
                if not candidate.facts.get("active_resume_id"):
                    activate_resume(db, candidate, resume, parsed)
                else:
                    records = dict(candidate.facts.get("resumes", {}))
                    records[resume.id] = {"text": parsed.text, "facts": parsed.facts, "warnings": parsed.warnings}
                    candidate.facts = {**candidate.facts, "resumes": records}
                imported += 1
            created = ensure_resume_searches(db)
            companies = seed_companies(db)
            db.commit()
        except Exception:
            db.rollback()
            for path in written:
                path.unlink(missing_ok=True)
            raise
    print(f"Imported resumes: {imported}; configured searches: {created}; new companies: {companies}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("resumes", type=Path, nargs="*")
    args = parser.parse_args()
    configure(args.local, args.resumes)
