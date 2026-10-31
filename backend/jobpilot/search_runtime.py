"""Single-process local queue and daily scheduler. One API process owns this runtime."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update, true

from .db import SessionLocal
from .config import settings
from .models import SavedSearch, now
from .search_runner import run_saved_search

log = logging.getLogger(__name__)


class SearchRuntime:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        # A stopped process has no remaining local tasks. Preserve old results.
        with SessionLocal() as db:
            stale = (SavedSearch.started_at < now() - timedelta(minutes=20)) if settings().hosted_mode else true()
            db.execute(update(SavedSearch).where(SavedSearch.status.in_(["queued", "running"]), stale).values(
                status="failed", errors=["Локальный процесс перезапущен; запустите поиск снова"]))
            db.commit()
        self.task = asyncio.create_task(self.schedule())

    def enqueue(self, search_id: str) -> bool:
        with SessionLocal() as db:
            claimed = db.execute(update(SavedSearch).where(
                SavedSearch.id == search_id, SavedSearch.status.notin_(["queued", "running"]),
            ).values(status="queued", started_at=now()).returning(SavedSearch.id)).scalar_one_or_none()
            db.commit()
        if not claimed:
            return False
        try:
            self.executor.submit(run_saved_search, search_id)
        except RuntimeError:
            with SessionLocal() as db:
                db.execute(update(SavedSearch).where(SavedSearch.id == search_id).values(status="failed"))
                db.commit()
            raise
        return True

    async def schedule(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.schedule_due)
                from .notifications import deliver_pending
                await asyncio.to_thread(deliver_pending)
            except Exception:
                log.exception("Daily search scheduling failed")
            await asyncio.sleep(30)

    def schedule_due(self) -> None:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with SessionLocal() as db:
            ids = list(db.scalars(select(SavedSearch.id).where(
                SavedSearch.enabled.is_(True), SavedSearch.status.notin_(["queued", "running"]),
                (SavedSearch.started_at.is_(None)) | (SavedSearch.started_at < midnight),
            )))
        for search_id in ids:
            self.enqueue(search_id)

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
