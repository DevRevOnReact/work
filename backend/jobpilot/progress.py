"""Small persisted snapshots, shared by vacancy and employer searches."""
from typing import Any

from .models import now


def advance(previous: dict[str, Any] | None, stage: str, message: str,
            level: str = "info", **counts: Any) -> dict[str, Any]:
    previous = previous or {}
    timestamp = now().isoformat()
    return {**previous, **counts, "stage": stage, "message": message, "updated_at": timestamp,
            "events": [*previous.get("events", []),
                       {"at": timestamp, "message": message, "level": level}][-150:]}
