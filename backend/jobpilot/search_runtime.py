"""Single-process local queue and three-hour scheduler."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import true, update

from .config import settings
from .db import SessionLocal
from .models import SavedSearch, now
from .progress import advance
from .schedule import due_searches
from .search_runner import run_saved_search

log = logging.getLogger(__name__)


class SearchRuntime:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
        self.task: asyncio.Task | None = None
        self.telegram_task: asyncio.Task | None = None

    def start(self) -> None:
        # A stopped process has no remaining local tasks. Preserve old results.
        with SessionLocal() as db:
            stale = (SavedSearch.started_at < now() - timedelta(minutes=20)) if settings().hosted_mode else true()
            recovered = list(db.scalars(update(SavedSearch).where(SavedSearch.status.in_(["queued", "running"]), stale).values(
                status="failed", completed_at=now(), errors=["Локальный процесс перезапущен; запустите поиск снова"],
                progress=advance({}, "failed", "Процесс перезапущен; предыдущий поиск прерван", "error"))
                .returning(SavedSearch.id)))
            from .notifications import enqueue_search_report
            for search_id in recovered:
                search = db.get(SavedSearch, search_id)
                if search:
                    enqueue_search_report(db, search)
            db.commit()
            try:
                from .vacancy_review import backfill_saved_reviews
                backfill_saved_reviews(db)
                db.commit()
            except Exception as exc:
                db.rollback()
                log.warning("Could not queue saved vacancies for review: %s", type(exc).__name__)
        self.task = asyncio.create_task(self.schedule())
        self.telegram_task = asyncio.create_task(self.poll_telegram())

    def enqueue(self, search_id: str) -> bool:
        with SessionLocal() as db:
            claimed = db.execute(update(SavedSearch).where(
                SavedSearch.id == search_id, SavedSearch.status.notin_(["queued", "running"]),
            ).values(status="queued", started_at=now(), completed_at=None, errors=[],
                     progress=advance({}, "queued", "В очереди. Ожидаем освобождения поискового потока", done=0, total=0))
              .returning(SavedSearch.id)).scalar_one_or_none()
            db.commit()
        if not claimed:
            return False
        try:
            self.executor.submit(run_saved_search, search_id)
        except RuntimeError:
            with SessionLocal() as db:
                db.execute(update(SavedSearch).where(SavedSearch.id == search_id).values(
                    status="failed", completed_at=now(), errors=["Не удалось запустить поисковый поток"],
                    progress=advance({}, "failed", "Не удалось запустить поисковый поток", "error")))
                db.commit()
            raise
        return True

    async def schedule(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.schedule_due)
                from .linkedin_jobs import process_candidates
                await asyncio.to_thread(process_candidates)
                from .notifications import deliver_pending
                await asyncio.to_thread(deliver_pending)
            except Exception:
                log.exception("Search scheduling failed")
            await asyncio.sleep(30)

    def schedule_due(self) -> None:
        if not settings().automatic_search_schedule:
            return
        with SessionLocal() as db:
            ids = due_searches(db)
        for search_id in ids:
            self.enqueue(search_id)

    async def poll_telegram(self) -> None:
        from .telegram import deliver_outbox, poll_commands
        while True:
            try:
                await asyncio.to_thread(poll_commands)
                await asyncio.to_thread(deliver_outbox)
            except Exception as exc:
                # HTTP errors can contain the bot token in their URL.
                log.warning("Telegram polling failed: %s", type(exc).__name__)
            await asyncio.sleep(5)

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
        if self.telegram_task:
            self.telegram_task.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
