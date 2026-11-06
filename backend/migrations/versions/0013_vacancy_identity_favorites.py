"""Keep favorites and retain semantic delivery history from existing messages."""
import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op

revision = "0013_vacancy_identity_favorites"
down_revision = "0012_vacancy_reviews"
branch_labels = None
depends_on = None


def role_key(company, title):
    def clean(value):
        text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
        return " ".join(re.findall(r"[^\W_]+(?:[+#]+)?|(?<!\w)\.[^\W_]+", text))
    return hashlib.sha256(json.dumps(["role", clean(company), clean(title)], ensure_ascii=False).encode()).hexdigest()


def url_key(value, channel="telegram"):
    p = urlsplit(value.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.casefold().startswith("utm_") and k.casefold() not in {"source", "ref", "referrer", "gh_src"}]
    url = urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))
    return hashlib.sha256(f"{channel}-vacancy-url-v2:{url}".encode()).hexdigest()


def upgrade() -> None:
    op.add_column("top_vacancies", sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_top_vacancies_favorite", "top_vacancies", ["favorite"])
    op.add_column("vacancy_reviews", sa.Column("identity_key", sa.String(64), nullable=False, server_default=""))
    op.create_index("ix_vacancy_reviews_identity_key", "vacancy_reviews", ["identity_key"])
    connection = op.get_bind()
    seen = sa.table("notification_seen", sa.column("id", sa.String(64)))
    reviews = sa.table("vacancy_reviews", sa.column("id", sa.String(36)), sa.column("data", sa.JSON()),
                       sa.column("identity_key", sa.String(64)), sa.column("notification_id", sa.String(64)),
                       sa.column("status", sa.String(20)))
    aliases = set()
    records = {}
    rejected = set()
    for row in connection.execute(sa.select(reviews)).mappings():
        key = role_key(row["data"].get("company"), row["data"].get("title"))
        connection.execute(reviews.update().where(reviews.c.id == row["id"]).values(identity_key=key))
        if row["notification_id"]:
            aliases.add(hashlib.sha256(f"telegram-vacancy-role-v2:{key}".encode()).hexdigest())
            records[row["notification_id"]] = row["data"]
        if row["status"] == "rejected":
            rejected.add(key)
    existing = set(connection.scalars(sa.select(seen.c.id)))
    # Recover full names from retained snapshots, including messages whose
    # descriptive header was truncated to fit Telegram's limit.
    for table_name, column_name in (("top_vacancies", "data"), ("saved_searches", "results")):
        table = sa.table(table_name, sa.column("id", sa.String()), sa.column(column_name, sa.JSON()))
        for snapshot in connection.execute(sa.select(table)).mappings():
            value = snapshot[column_name]
            for job in value if isinstance(value, list) else [value]:
                if not isinstance(job, dict):
                    continue
                old_id = hashlib.sha256(f"telegram-top-v1:{job.get('canonical_url', '')}".encode()).hexdigest()
                records[old_id] = job
                if old_id in existing:
                    key = role_key(job.get("company"), job.get("title"))
                    aliases.add(hashlib.sha256(f"telegram-vacancy-role-v2:{key}".encode()).hexdigest())
                    aliases.add(url_key(job.get("canonical_url", "")))
                if table_name == "saved_searches":
                    old_email = hashlib.sha256(f"email:{snapshot['id']}:{job.get('canonical_url', '')}".encode()).hexdigest()
                    if old_email in existing:
                        key = role_key(job.get("company"), job.get("title"))
                        aliases.add(hashlib.sha256(f"email-vacancy-role-v2:{key}".encode()).hexdigest())
                        aliases.add(url_key(job.get("canonical_url", ""), "email"))
    notifications = sa.table("search_notifications", sa.column("id", sa.String(64)), sa.column("message", sa.Text()),
                             sa.column("channel", sa.String()), sa.column("status", sa.String()),
                             sa.column("created_at", sa.DateTime()))
    occupied = set()
    query = sa.select(notifications).where(notifications.c.channel == "telegram").order_by(
        sa.case((notifications.c.status == "pending", 1), else_=0), notifications.c.created_at, notifications.c.id)
    for row in connection.execute(query).mappings():
        message = row["message"]
        if not message.startswith(("JobPilot · топ за 3 дня ·", "JobPilot · вакансия на проверке ·")):
            continue
        job = records.get(row["id"])
        if not job:
            lines = message.splitlines()
            if len(lines) > 2 and " · " in lines[2]:
                company, title = lines[2].split(" · ", 1)
                urls = re.findall(r"https://\S+", message)
                job = {"company": company, "title": title, "canonical_url": urls[-1] if urls else ""}
        if not job:
            continue
        key = role_key(job.get("company"), job.get("title"))
        keys = {hashlib.sha256(f"telegram-vacancy-role-v2:{key}".encode()).hexdigest()}
        if job.get("canonical_url"):
            keys.add(url_key(job["canonical_url"]))
        if row["status"] == "pending" and (keys & occupied or key in rejected):
            connection.execute(notifications.update().where(notifications.c.id == row["id"]).values(status="superseded"))
            connection.execute(reviews.update().where(reviews.c.notification_id == row["id"],
                                                      reviews.c.status == "pending").values(status="stale"))
        occupied.update(keys)
        aliases.update(keys)
    # Email digest headers retain role/company even after the search snapshot expires.
    for message in connection.scalars(sa.select(notifications.c.message).where(notifications.c.channel == "email")):
        lines = message.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"^\d+/100 · (.+?) · (.+)$", line)
            if match:
                key = role_key(*match.groups())
                aliases.add(hashlib.sha256(f"email-vacancy-role-v2:{key}".encode()).hexdigest())
                if index + 2 < len(lines) and lines[index + 2].startswith("https://"):
                    aliases.add(url_key(lines[index + 2], "email"))
    if aliases - existing:
        connection.execute(seen.insert(), [{"id": key} for key in aliases - existing])


def downgrade() -> None:
    op.drop_index("ix_vacancy_reviews_identity_key", table_name="vacancy_reviews")
    op.drop_column("vacancy_reviews", "identity_key")
    op.drop_index("ix_top_vacancies_favorite", table_name="top_vacancies")
    op.drop_column("top_vacancies", "favorite")
