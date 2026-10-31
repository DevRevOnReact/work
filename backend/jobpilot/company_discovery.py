"""Durable status for short, bounded background company discovery."""
import asyncio
from typing import Any

import httpx
from sqlalchemy import select, update

from .companies import company_key, discover_employers, discover_global_catalog, save_company
from .models import Company, CompanyDiscovery, now
from .progress import advance


def run_discovery(discovery_id: str, session_factory: Any) -> None:
    progress: dict[str, Any] = {}

    def report(stage: str, message: str, **counts: Any) -> None:
        nonlocal progress
        progress = advance(progress, stage, message, **counts)
        with session_factory() as db:
            db.execute(update(CompanyDiscovery).where(CompanyDiscovery.id == discovery_id).values(progress=progress))
            db.commit()

    try:
        with session_factory() as db:
            job = db.get(CompanyDiscovery, discovery_id)
            if not job or job.status != "queued":
                return
            job.status = "running"
            country, page = job.country, job.page
            db.commit()
        if country == "Worldwide":
            with session_factory() as db:
                result = discover_global_catalog(db, report)
        else:
            async def discover() -> Any:
                return await asyncio.wait_for(discover_employers(country, page, report), timeout=180)
            found, errors = asyncio.run(discover())
            report("saving", "Сохраняем компании и ссылки на публичные профили")
            added = 0
            with session_factory() as db:
                for item in found:
                    if not db.scalar(select(Company.id).where(Company.key == company_key(item["name"]))):
                        added += 1
                    save_company(db, item["name"], signal=item["signal"])
                db.commit()
            result = {"added": added, "found": len({company_key(item["name"]) for item in found}),
                      "errors": errors, "next_page": page + 1 if page < 10 else None}
        report("complete", f"Готово: найдено компаний {result['found']}, новых {result['added']}")
        with session_factory() as db:
            db.execute(update(CompanyDiscovery).where(CompanyDiscovery.id == discovery_id).values(
                status="complete", result=result, completed_at=now()))
            db.commit()
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            message = ("Лимит публичного API GitHub; повторите позже" if exc.response.status_code in {403, 429}
                       else f"GitHub вернул HTTP {exc.response.status_code}")
        elif isinstance(exc, httpx.HTTPError):
            message = "Не удалось подключиться к GitHub"
        elif isinstance(exc, TimeoutError):
            message = "Истекло время поиска компаний; повторите запуск"
        else:
            message = f"Не удалось завершить поиск компаний: {type(exc).__name__}"
        with session_factory() as db:
            db.execute(update(CompanyDiscovery).where(CompanyDiscovery.id == discovery_id).values(
                status="failed", completed_at=now(), result={"errors": [message]},
                progress=advance(progress, "failed", message, "error")))
            db.commit()
