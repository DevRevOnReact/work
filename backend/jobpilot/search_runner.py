"""Bounded feed I/O with short transactions and independently pollable progress."""
import asyncio
from collections import OrderedDict
from datetime import timedelta
from time import monotonic
from typing import Any

import httpx
from sqlalchemy import select, update

from .db import SessionLocal
from .models import SavedSearch, Source, now
from .progress import advance
from .searches import SearchFilters, make_results, phrases, published_today
from .sources import ADAPTERS
from .top_vacancies import publication

_cache: OrderedDict[tuple[str, str, str], tuple[float, list[Any]]] = OrderedDict()


def run_saved_search(search_id: str, session_factory: Any = SessionLocal,
                     adapters: Any = ADAPTERS, use_cache: bool = True, *,
                     enqueue_without_credentials: bool = False) -> None:
    started = now()
    progress = advance({}, "starting", "Начинаем поиск вакансий за сегодня", done=0, total=0)
    with session_factory() as db:
        claimed = db.execute(update(SavedSearch).where(
            SavedSearch.id == search_id,
            (SavedSearch.status != "running") | (SavedSearch.started_at < started - timedelta(minutes=20)),
        ).values(status="running", started_at=started, completed_at=None, errors=[], progress=progress)
          .returning(SavedSearch.id)).scalar_one_or_none()
        db.commit()
    if not claimed:
        return
    inputs: list[Any] = []
    errors: list[str] = []
    sources: list[Source] = []

    def report(stage: str, message: str, level: str = "info", **counts: Any) -> None:
        nonlocal progress
        progress = advance(progress, stage, message, level, **counts)
        with session_factory() as db:
            db.execute(update(SavedSearch).where(
                SavedSearch.id == search_id, SavedSearch.started_at == started, SavedSearch.status == "running",
            ).values(progress=progress))
            db.commit()

    try:
        with session_factory() as db:
            search = db.get(SavedSearch, search_id)
            if not search:
                return
            filters = SearchFilters.model_validate(search.filters)
            sources = [source for source in db.scalars(select(Source).where(Source.enabled.is_(True)))
                       if (not filters.provider or source.provider == filters.provider)
                       and (source.provider in {"remotive", "arbeitnow"} or not phrases(filters.company) or
                            any(term in source.company.casefold() for term in phrases(filters.company)))]
            db.commit()
        if not sources:
            raise ValueError("Нет включённых источников, соответствующих компании и платформе")
        report("fetching", f"Загружаем источники: {len(sources)}", total=len(sources), done=0)

        async def fetch_all() -> None:
            semaphore = asyncio.Semaphore(3)
            done = 0
            async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
                async def fetch_one(source: Source) -> None:
                    nonlocal done
                    async with semaphore:
                        key = source.provider, source.board, source.company
                        cached = _cache.get(key) if use_cache else None
                        try:
                            adapter = adapters.get(source.provider)
                            if not adapter:
                                raise ValueError(f"Неподдерживаемая платформа {source.provider}")
                            report("fetching", f"{source.company}: запрос к {source.provider}")
                            if cached and monotonic() - cached[0] < 300:
                                jobs = cached[1]
                                _cache.move_to_end(key)
                            else:
                                jobs = await adapter(client).fetch(source.board, source.company)
                                if use_cache:
                                    _cache[key] = monotonic(), jobs
                                    while len(_cache) > 64 or sum(len(v[1]) for v in _cache.values()) > 15000:
                                        _cache.popitem(last=False)
                            inputs.extend(jobs)
                            source.error, source.synced_at = None, now()
                            message = f"{source.company}: получено {len(jobs)} вакансий"
                        except Exception as exc:
                            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                            source.error = f"HTTP {status}" if status else (
                                str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
                            errors.append(f"{source.company}: {source.error}")
                            message = errors[-1]
                        done += 1
                        report("fetching", message, "error" if source.error else "info",
                               done=done, fetched=len(inputs), failures=len(errors))
                await asyncio.gather(*(fetch_one(source) for source in sources))

        asyncio.run(asyncio.wait_for(fetch_all(), timeout=480))
        with session_factory() as db:
            for source in sources:
                db.execute(update(Source).where(Source.id == source.id).values(
                    error=source.error, synced_at=source.synced_at))
            db.commit()
        if len(errors) == len(sources):
            raise ValueError("Не удалось загрузить ни один источник")
        checked_at = now()
        fresh = sum(published_today(job, checked_at) for job in inputs)
        unknown = sum(job.published_at is None for job in inputs)
        report("scoring", f"За сегодня: {fresh}; без даты: {unknown}. Применяем фильтры и оцениваем по резюме",
               fresh=fresh, unknown_dates=unknown)
        with session_factory() as db:
            search = db.get(SavedSearch, search_id, populate_existing=True)
            if not search or search.status != "running":
                return
            results, count = make_results(db, search, inputs, today_only=True, at=checked_at)
            report("scoring", "Формируем топ за последние 72 часа · оценка от 60")
            top_filters = SearchFilters.model_validate(search.filters).model_copy(
                update={"posted_days": 0, "min_score": 60, "sort": "score"})
            top_results, _ = make_results(db, search, inputs, at=checked_at,
                                         since=checked_at - timedelta(days=3), filters_override=top_filters, limit=None)
            from .top_vacancies import qualifies, save_top_vacancies
            from .vacancy_review import apply_decisions, prepare_reviews
            top_results = prepare_reviews(db, search, top_results,
                                          enqueue_without_credentials=enqueue_without_credentials)
            filtered_results = apply_decisions(db, results)
            count = max(0, count - (len(results) - len(filtered_results)))
            results = filtered_results
            review_count = sum(job.get("eligibility") == "unknown" and job.get("review_status") != "approved"
                               for job in top_results)
            top_results = [job for job in top_results if qualifies(job, checked_at)]
            save_top_vacancies(db, search, top_results)
            search.results, search.result_count = results, count
            search.errors, search.status, search.completed_at = errors, "complete", now()
            search.progress = advance(progress, "complete", f"Готово: {count} подходящих вакансий за сегодня",
                                      matched=count, top_matched=len(top_results), top_review=review_count)
            from .notifications import enqueue_digest, enqueue_top_telegram, enqueue_search_report
            try:
                with db.begin_nested():
                    enqueue_digest(db, search, results)
                    queued = enqueue_top_telegram(db, search.id, top_results,
                                                  enqueue_without_credentials=enqueue_without_credentials)
                    search.progress = {**search.progress, "telegram_queued": queued}
            except Exception:
                import logging
                logging.getLogger(__name__).error("Unable to queue search notifications")
                search.progress = {**search.progress, "notification_error": True}
            enqueue_search_report(db, search, enqueue_without_credentials=enqueue_without_credentials)
            db.commit()
    except Exception as exc:
        message = (str(exc) if isinstance(exc, ValueError) else
                   "Истекло время загрузки источников" if isinstance(exc, TimeoutError) else type(exc).__name__)
        with session_factory() as db:
            db.rollback()
            db.execute(update(SavedSearch).where(SavedSearch.id == search_id, SavedSearch.started_at == started).values(
                errors=errors + [message], status="failed", completed_at=now(),
                progress=advance(progress, "failed", message, "error")))
            search = db.get(SavedSearch, search_id, populate_existing=True)
            if search and publication(search.started_at) == publication(started):
                from .notifications import enqueue_search_report
                enqueue_search_report(db, search, enqueue_without_credentials=enqueue_without_credentials)
            db.commit()
