"""Bounded public-feed I/O and persisted search execution, without Celery/browser imports."""
import asyncio
from collections import OrderedDict
from datetime import timedelta
from time import monotonic
from typing import Any

import httpx
from sqlalchemy import select, update

from .db import SessionLocal
from .models import SavedSearch, Source, now
from .searches import SearchFilters, make_results, phrases
from .sources import ADAPTERS

# The single local search thread shares a small five-minute feed cache across CVs.
_cache: OrderedDict[tuple[str, str, str], tuple[float, list[Any]]] = OrderedDict()


def run_saved_search(search_id: str, session_factory: Any = SessionLocal,
                     adapters: Any = ADAPTERS, use_cache: bool = True) -> None:
    with session_factory() as db:
        claimed = db.execute(update(SavedSearch).where(
            SavedSearch.id == search_id,
            (SavedSearch.status != "running") | (SavedSearch.started_at < now() - timedelta(minutes=20)),
        ).values(status="running", started_at=now()).returning(SavedSearch.id)).scalar_one_or_none()
        db.commit()
        if not claimed:
            return
        search = db.get(SavedSearch, search_id)
        if not search:
            return
        inputs: list[Any] = []
        errors: list[str] = []
        filters = SearchFilters.model_validate(search.filters)
        sources = [source for source in db.scalars(select(Source).where(Source.enabled.is_(True)))
                   if (not filters.provider or source.provider == filters.provider)
                   and (source.provider in {"remotive", "arbeitnow"} or not phrases(filters.company) or
                        any(term in source.company.casefold() for term in phrases(filters.company)))]

        async def fetch_all() -> None:
            semaphore = asyncio.Semaphore(3)
            async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
                async def fetch_one(source: Source) -> None:
                    adapter = adapters.get(source.provider)
                    if not adapter:
                        errors.append(f"{source.company}: неподдерживаемая платформа {source.provider}")
                        return
                    key = source.provider, source.board, source.company
                    cached = _cache.get(key) if use_cache else None
                    try:
                        if cached and monotonic() - cached[0] < 300:
                            jobs = cached[1]
                            _cache.move_to_end(key)
                        else:
                            async with semaphore:
                                jobs = await adapter(client).fetch(source.board, source.company)
                            if use_cache:
                                _cache[key] = monotonic(), jobs
                                while len(_cache) > 64 or sum(len(v[1]) for v in _cache.values()) > 15000:
                                    _cache.popitem(last=False)
                        inputs.extend(jobs)
                        source.error, source.synced_at = None, now()
                    except Exception as exc:
                        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                        source.error = f"HTTP {status}" if status else type(exc).__name__
                        errors.append(f"{source.company}: {source.error}")
                await asyncio.gather(*(fetch_one(source) for source in sources))

        try:
            asyncio.run(asyncio.wait_for(fetch_all(), timeout=480))
            if not sources:
                raise ValueError("Нет включённых источников, соответствующих компании и платформе")
            if len(errors) == len(sources):
                raise ValueError("Не удалось загрузить ни один источник")
            # Source sync metadata must not acquire a SQLite write lock while
            # thousands of vacancies are being scored.
            with db.no_autoflush:
                results, count = make_results(db, search, inputs)
            search.results, search.result_count = results, count
            search.errors, search.status, search.completed_at = errors, "complete", now()
            from .notifications import enqueue_digest
            try:
                with db.begin_nested():
                    enqueue_digest(db, search, results)
            except Exception:
                import logging
                logging.getLogger(__name__).error("Unable to queue search notifications")
        except Exception as exc:
            search.errors = errors + [str(exc) if isinstance(exc, ValueError) else type(exc).__name__]
            search.status = "failed"
        db.commit()
