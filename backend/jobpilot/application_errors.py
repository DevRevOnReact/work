"""Classify employer HTTP failures without retrying a submitted application."""
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .answers import NeedsAttention


class ApplicationSiteError(NeedsAttention):
    def __init__(self, status: int, retry_after: str = "") -> None:
        self.status = status
        self.transient = status in {408, 425, 429} or status >= 500
        self.retry_after = retry_seconds(retry_after)
        self.code = f"employer_http_{status}"
        if status == 429:
            message = "Сайт работодателя ограничил частоту запросов (HTTP 429)."
        elif self.transient:
            message = f"Сайт работодателя временно недоступен (HTTP {status})."
        elif status in {401, 403}:
            message = f"Сайт требует входа или проверки защиты (HTTP {status}). Откройте форму вручную."
        elif status in {404, 410}:
            message = f"Форма вакансии недоступна или вакансия закрыта (HTTP {status})."
        else:
            message = f"Сайт отклонил запрос (HTTP {status}). Проверьте форму и данные."
        super().__init__(message)


def retry_seconds(value: str, current: datetime | None = None) -> float:
    """Retry-After accepts either integral seconds or an HTTP date."""
    if value.strip().isdigit():
        # Ignore pathological untrusted integers rather than overflowing a date.
        return float(min(int(value.strip()[:12]), 7 * 86400))
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return min(7 * 86400, max(0, (deadline - (current or datetime.now(UTC))).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return 0


def check_response(response: Any) -> None:
    if response is not None and response.status >= 400:
        raise ApplicationSiteError(response.status, response.headers.get("retry-after", ""))
