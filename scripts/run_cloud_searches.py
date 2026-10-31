"""Three-hour GitHub Actions job; works while the free web server is asleep."""
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main() -> None:
    from sqlalchemy import select, update
    from jobpilot.db import SessionLocal
    from jobpilot.models import SavedSearch
    from jobpilot.notifications import deliver_pending
    from jobpilot.search_runner import run_saved_search
    from jobpilot.schedule import due_searches
    from jobpilot.progress import advance
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql+psycopg://"):
        raise RuntimeError("Cloud search job requires a persistent PostgreSQL DATABASE_URL")
    with SessionLocal() as db:
        db.execute(update(SavedSearch).where(
            SavedSearch.status.in_(["queued", "running"]),
            SavedSearch.started_at < datetime.now(timezone.utc) - timedelta(minutes=20),
        ).values(status="failed", completed_at=datetime.now(timezone.utc), errors=["Предыдущий запуск прерван"],
                 progress=advance({}, "failed", "Предыдущий запуск прерван", "error")))
        db.commit()
        ids = due_searches(db)
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
    try:
        main()
    except Exception as exc:
        # A driver exception can contain fragments of a connection URL that
        # GitHub's whole-secret masking will not recognize.
        print(f"Cloud search failed: {type(exc).__name__}. Check database settings and app status.", flush=True)
        raise SystemExit(1)
