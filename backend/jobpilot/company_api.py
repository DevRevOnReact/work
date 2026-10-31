from time import monotonic
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .companies import (
    COUNTRIES,
    company_key,
    discover_employers,
    save_company,
    seed_companies,
)
from .db import session
from .http import authenticate, serialize
from .models import Company, ReferralLead, Source

router = APIRouter(dependencies=[Depends(authenticate)])
_last_discovery = float("-inf")


@router.get("/companies")
def companies(q: str = Query(default="", max_length=200),
              signal: Literal["", "cis_profile", "referrals", "regional_presence", "resume_fit"] = "",
              db: Session = Depends(session)) -> list[dict[str, Any]]:
    sources = list(db.scalars(select(Source)))
    leads = list(db.scalars(select(ReferralLead)))
    # Include connected boards and legacy contacts without mutating them on GET.
    items = {company.key: {**serialize(company), "sources": [], "contacts": []}
             for company in db.scalars(select(Company).order_by(Company.name))}
    for source in sources:
        item = items.setdefault(company_key(source.company), {
            "id": source.id, "name": source.company, "careers_url": "", "signals": [], "sources": [], "contacts": [],
        })
        item["sources"].append(serialize(source))
    for lead in leads:
        item = items.setdefault(company_key(lead.company), {
            "id": lead.id, "name": lead.company, "careers_url": "", "signals": [], "sources": [], "contacts": [],
        })
        item["contacts"].append(serialize(lead))
    words = q.strip().casefold().split()
    return sorted([item for item in items.values()
                   if all(word in (item["name"] + " " + " ".join(
                       s.get("location", "") for s in item["signals"])).casefold() for word in words)
                   and (not signal or any(s["kind"] == signal for s in item["signals"]))],
                  key=lambda item: item["name"].casefold())


@router.post("/companies/starter")
def starter(db: Session = Depends(session)) -> dict[str, int]:
    added = seed_companies(db)
    db.commit()
    return {"added": added}


class DiscoverInput(BaseModel):
    country: str
    page: int = Field(default=1, ge=1, le=10)


@router.post("/companies/discover")
async def discover(body: DiscoverInput, db: Session = Depends(session)) -> dict[str, Any]:
    global _last_discovery
    if body.country not in COUNTRIES:
        raise HTTPException(422, "Выберите страну из списка")
    if monotonic() - _last_discovery < 30:
        raise HTTPException(429, "Подождите 30 секунд перед повторным поиском")
    _last_discovery = monotonic()
    try:
        found, errors = await discover_employers(body.country, body.page)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {403, 429}:
            raise HTTPException(429, "Лимит публичного API GitHub; повторите позже") from exc
        raise HTTPException(502, "GitHub временно недоступен") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Не удалось подключиться к GitHub") from exc
    added = 0
    for item in found:
        if not db.scalar(select(Company.id).where(Company.key == company_key(item["name"]))):
            added += 1
        save_company(db, item["name"], signal=item["signal"])
    db.commit()
    return {"added": added, "found": len({company_key(item["name"]) for item in found}),
            "errors": errors, "next_page": body.page + 1 if body.page < 10 else None}


class CompanyInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    careers_url: str = Field(default="", max_length=1000)


@router.post("/companies")
def add_company(body: CompanyInput, db: Session = Depends(session)) -> dict[str, Any]:
    if not company_key(body.name):
        raise HTTPException(422, "Укажите компанию")
    if body.careers_url:
        parsed = urlsplit(body.careers_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise HTTPException(422, "Укажите HTTPS-ссылку на карьерную страницу")
    company = save_company(db, body.name, body.careers_url)
    db.commit()
    return serialize(company)
