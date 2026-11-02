"""Validate run counts and emit an idempotent INSERT into the Telegram outbox."""
import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_started_at: AwareDatetime
    checked: int = Field(ge=0, le=40, strict=True)
    verified: int = Field(ge=0, le=40, strict=True)
    inserted: int = Field(ge=0, le=40, strict=True)
    accepted: int = Field(ge=0, le=40, strict=True)
    pending: int = Field(ge=0, le=40, strict=True)
    error: Literal["none", "source_unavailable", "search_failed", "processing_failed"] = "none"

    @model_validator(mode="after")
    def consistent(self) -> "ResearchReport":
        if not 0 <= self.accepted + self.pending <= self.inserted <= self.verified <= self.checked:
            raise ValueError("Inconsistent research counts")
        if not datetime.now(UTC) - timedelta(hours=24) <= self.run_started_at <= datetime.now(UTC):
            raise ValueError("Run timestamp must be within the last 24 hours")
        return self


def prepare(record: dict) -> str:
    item = ResearchReport.model_validate(record)
    started = item.run_started_at.astimezone(UTC)
    identity = hashlib.sha256(f"telegram-linkedin-report-v1:{started.isoformat()}".encode()).hexdigest()
    errors = {"source_unavailable": "Часть страниц LinkedIn недоступна; покрытие проверки неполное.",
              "search_failed": "Ошибка публичного поиска; проверку не удалось завершить.",
              "processing_failed": "Ошибка обработки кандидатов; результат неполный."}
    lines = ["JobPilot · LinkedIn · отчёт за 3 дня",
             started.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M МСК"),
             f"Проверено вакансий: {item.checked}.",
             f"Со свежестью за 72 часа, проверенной на странице: {item.verified}.",
             f"Новых записей: {item.inserted}; повторов: {item.verified - item.inserted}.",
             f"Новых вакансий в топ (от 60/100): {item.accepted}."]
    if item.pending:
        lines.append(f"Ещё ожидают обработки: {item.pending}.")
    if item.error != "none":
        lines.append(errors[item.error])
    elif not item.accepted and not item.pending:
        lines.append("Новых подходящих вакансий не найдено.")
    if item.accepted:
        lines.append("Доставка карточек вакансий проверяется отдельно по очереди Telegram.")
    lines.append("Публичный поиск не охватывает все вакансии LinkedIn.")
    message = base64.b64encode("\n".join(lines).encode()).decode()
    return ("INSERT INTO search_notifications "
            "(id,search_id,channel,message,status,attempts,next_attempt,created_at) "
            f"SELECT '{identity}',id,'telegram',convert_from(decode('{message}','base64'),'UTF8'),"
            "'pending',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM saved_searches "
            "ORDER BY enabled DESC,id LIMIT 1 ON CONFLICT (id) DO NOTHING RETURNING id;")


if __name__ == "__main__":
    try:
        print(prepare(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))))
    except Exception as exc:
        print("Report rejected: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
