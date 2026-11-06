"""Stable vacancy keys shared by notifications, reviews and application policy."""
import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from .models import NotificationSeen


def clean(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    # Keep meaningful technology punctuation (C++, C#, .NET).
    return " ".join(re.findall(r"[^\W_]+(?:[+#]+)?|(?<!\w)\.[^\W_]+", value))


def canonical_url(value: str) -> str:
    p = urlsplit(value.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.casefold().startswith("utm_") and k.casefold() not in {"source", "ref", "referrer", "gh_src"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def identity(job: dict[str, Any]) -> str:
    company, title = clean(job.get("company")), clean(job.get("title"))
    parts = ["role", company, title] if company and title else ["url", canonical_url(job.get("canonical_url", ""))]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def same_vacancy(first: dict[str, Any], second: dict[str, Any]) -> bool:
    a, b = first.get("canonical_url"), second.get("canonical_url")
    return bool(a and b and canonical_url(a) == canonical_url(b)) or identity(first) == identity(second)


def delivery_keys(job: dict[str, Any], channel: str = "telegram") -> set[str]:
    url = canonical_url(job.get("canonical_url", ""))
    keys = {hashlib.sha256(f"{channel}-vacancy-role-v2:{identity(job)}".encode()).hexdigest(),
            hashlib.sha256(f"{channel}-vacancy-url-v2:{url}".encode()).hexdigest()}
    if channel == "telegram":
        keys.add(hashlib.sha256(f"telegram-top-v1:{job.get('canonical_url', '')}".encode()).hexdigest())
    return keys


def claim_delivery(db: Session, job: dict[str, Any], channel: str = "telegram", *, legacy_keys: tuple[str, ...] = ()) -> bool:
    """Reserve all aliases atomically, including concurrent searches/review queues."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    keys = delivery_keys(job, channel) | set(legacy_keys)
    # One statement resolves concurrent reservations. Keep newly discovered
    # aliases even for a duplicate, so a later title/URL change cannot resend it.
    claimed = list(db.scalars(insert(NotificationSeen).values([{"id": key} for key in sorted(keys)])
                              .on_conflict_do_nothing().returning(NotificationSeen.id)))
    return len(claimed) == len(keys)
