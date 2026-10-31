"""Daily GitHub Actions job; works while the free web server is asleep."""
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import select, update
from jobpilot.db import SessionLocal
from jobpilot.models import SavedSearch
from jobpilot.notifications import deliver_pending
from jobpilot.search_runner import run_saved_search


def main() -> None:
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql+psycopg://"):
        raise RuntimeError("Cloud search job requires a persistent PostgreSQL DATABASE_URL")
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        db.execute(update(SavedSearch).where(
            SavedSearch.status.in_(["queued", "running"]),
            SavedSearch.started_at < datetime.now(timezone.utc) - timedelta(minutes=20),
        ).values(status="failed", errors=["Предыдущий запуск прерван"]))
        db.commit()
        ids = list(db.scalars(select(SavedSearch.id).where(
            SavedSearch.enabled.is_(True), SavedSearch.status.notin_(["queued", "running"]),
            (SavedSearch.started_at.is_(None)) | (SavedSearch.started_at < midnight),
        )))
    for search_id in ids:
        run_saved_search(search_id)
        with SessionLocal() as db:
            search = db.get(SavedSearch, search_id)
            print(f"{search_id}: {search.status if search else 'missing'}", flush=True)
    deliver_pending()
    with SessionLocal() as db:
        failed = list(db.scalars(select(SavedSearch.id).where(SavedSearch.id.in_(ids),
                                                             SavedSearch.status == "failed")))
    if failed:
        raise RuntimeError(f"{len(failed)} searches failed; check the application")


if __name__ == "__main__":
    main()
