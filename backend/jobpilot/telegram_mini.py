import hashlib
import hmac
import json
import time
from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from .config import settings
from .db import session
from .top_vacancies import FavoriteInput, set_favorite, top_vacancies

router = APIRouter()


def authenticate_telegram(init_data: str = Header(default="", alias="X-Telegram-Init-Data")) -> None:
    config = settings()
    token, chat_id = config.telegram_bot_token.get_secret_value(), config.telegram_chat_id
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        if len(values) != len(pairs) or not token or not chat_id or chat_id.startswith("-"):
            raise ValueError()
        supplied = values.pop("hash")
        check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        signature = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        age = time.time() - int(values["auth_date"])
        user = json.loads(values["user"])
        if not hmac.compare_digest(signature, supplied) or not 0 <= age <= 86400 or str(user["id"]) != chat_id:
            raise ValueError()
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, "Откройте мини-приложение из своего Telegram-бота") from None


@router.get("/telegram-mini/top-vacancies", dependencies=[Depends(authenticate_telegram)])
def mini_top(db: Annotated[Session, Depends(session)], offset: int = Query(default=0, ge=0),
             limit: int = Query(default=20, ge=1, le=100), favorites: bool = False) -> dict[str, Any]:
    return top_vacancies(offset=offset, limit=limit, provider="", favorites=favorites, db=db)


@router.put("/telegram-mini/top-vacancies/{vacancy_id}/favorite", dependencies=[Depends(authenticate_telegram)])
def mini_favorite(vacancy_id: str, body: FavoriteInput, db: Annotated[Session, Depends(session)]) -> dict[str, Any]:
    return set_favorite(db, vacancy_id, body.favorite)
