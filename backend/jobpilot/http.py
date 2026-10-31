"""Shared HTTP concerns, independent of the application entry point."""
import hmac
from typing import Any

from fastapi import Header, HTTPException

from .config import settings


def authenticate(authorization: str = Header(default="")) -> None:
    if not settings().auth_required:
        return
    token = settings().api_token.get_secret_value()
    if len(token) < 32 or not hmac.compare_digest(authorization, "Bearer " + token):
        raise HTTPException(401, "Authentication required")


def serialize(obj: Any) -> dict[str, Any]:
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}
