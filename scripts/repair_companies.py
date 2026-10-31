"""Repair local discovery evidence; back up SQLite before changing records."""
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
database = ROOT / "data/jobpilot-local.db"
os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"

from sqlalchemy import select
from jobpilot.companies import matches_country
from jobpilot.db import SessionLocal
from jobpilot.models import Company, ReferralLead, Source


def repair_text(value: str) -> str:
    try:
        return value.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


if __name__ == "__main__":
    backup = database.with_name(f"companies-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db")
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    removed = fixed = 0
    with SessionLocal() as db:
        protected = {s.company.casefold() for s in db.scalars(select(Source))}
        protected.update(s.company.casefold() for s in db.scalars(select(ReferralLead)))
        for company in db.scalars(select(Company)):
            signals = []
            for signal in company.signals:
                location = signal.get("location", "")
                if (signal.get("kind") == "cis_profile" and
                    (signal.get("country") == "Georgia" or "georgia" in location.casefold()) and
                    not matches_country(location, "Georgia")):
                    removed += 1
                    continue
                updated = {**signal, "note": repair_text(signal.get("note", ""))}
                fixed += updated != signal
                signals.append(updated)
            if company.signals and not signals and not company.careers_url and company.name.casefold() not in protected:
                db.delete(company)
            else:
                company.signals = signals
        db.commit()
    print(f"Backup: {backup}; invalid signals removed: {removed}; text notes repaired: {fixed}")
