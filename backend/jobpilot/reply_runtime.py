import hashlib
import json
import logging
from datetime import timedelta
from threading import Event, Thread
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Candidate, Outbox, ReplyDraft, now
from .reply_assistant import (
    MEDIA_LIMIT,
    AssistantError,
    DraftInput,
    generate,
    prepare,
    recognize,
    telegram_media,
)

log = logging.getLogger(__name__)
RETENTION = timedelta(days=7)
LEASE = timedelta(minutes=8)
BOT_HELP = "Пришлите вопрос рекрутера текстом, голосом до минуты или скриншотом. Помощник подберёт резюме и подготовит ответ."


def enqueue(db: Session, body: DraftInput, source: str, attachment: dict | None = None) -> ReplyDraft:
    if not settings().assistant_model_url:
        raise AssistantError("Помощник ещё не настроен на сервере.")
    # Single-owner installation. Serialize admission so parallel requests share limits and deduplication.
    db.execute(select(Candidate.id).where(Candidate.id == 1).with_for_update()).one()
    existing = db.get(ReplyDraft, str(body.id))
    if existing:
        return existing
    if body.notify and not (settings().telegram_chat_id.isdecimal() and settings().telegram_bot_token.get_secret_value()):
        raise AssistantError("Для уведомления нужен подключённый личный чат с ботом.")
    count = db.scalar(select(func.count()).select_from(ReplyDraft).where(
        ReplyDraft.created_at >= now() - timedelta(minutes=10))) or 0
    if count >= 18:
        raise AssistantError("Достигнут лимит запросов. Попробуйте через десять минут.")
    pending = db.scalar(select(func.count()).select_from(ReplyDraft).where(ReplyDraft.status.in_(["queued", "working"])))
    if pending and pending >= 6:
        raise AssistantError("Очередь заполнена. Дождитесь готового ответа.")
    payload = body.model_dump(mode="json")
    if source == "bot" or body.notify:
        payload["recipient"] = settings().telegram_chat_id
    if attachment:
        payload["attachment"] = attachment
    draft = ReplyDraft(id=str(body.id), status="queued", source=source, request=payload)
    db.add(draft)
    db.flush()
    return draft


def bot_request(db: Session, message: dict, update_id: int) -> str:
    text = (message.get("text") or message.get("caption") or "").strip()
    forwarded = bool(message.get("forward_origin") or message.get("forward_date"))
    if not forwarded and text.split(maxsplit=1)[:1] == ["/assistant"]:
        text = text[len("/assistant"):].strip()
    attachment = None
    if voice := message.get("voice"):
        if voice.get("duration", 61) > 60 or voice.get("file_size", 0) > MEDIA_LIMIT:
            return "Пришлите запись до одной минуты и до 4 МБ."
        attachment = {"kind": "audio", "id": voice.get("file_id")}
    elif photos := message.get("photo"):
        eligible = [p for p in photos if p.get("file_size", 0) <= MEDIA_LIMIT]
        if not eligible:
            return "Пришлите скриншот размером до 4 МБ."
        attachment = {"kind": "image", "id": eligible[-1].get("file_id")}
    if not text and not attachment:
        return BOT_HELP
    if len(text) > 4000:
        return "Пришлите сообщение длиной до 4000 символов."
    try:
        request = DraftInput(id=uuid5(NAMESPACE_URL, f"jobpilot:telegram:{update_id}"), question=text or "…")
        enqueue(db, request, "bot", attachment)
    except AssistantError as exc:
        return str(exc)
    return "Готовлю ответ. Он появится здесь и в истории помощника."


def clean_expired(db: Session) -> None:
    expired = select(ReplyDraft.id).where(ReplyDraft.created_at < now() - RETENTION)
    db.execute(delete(Outbox).where(Outbox.id.in_(expired)))
    db.execute(delete(ReplyDraft).where(ReplyDraft.id.in_(expired)))


def claim(db: Session) -> tuple[str, str, dict, int] | None:
    eligible = or_(ReplyDraft.status == "queued",
                   (ReplyDraft.status == "working") & (ReplyDraft.claimed_at < now() - LEASE))
    row = db.scalar(select(ReplyDraft).where(eligible).order_by(ReplyDraft.created_at)
                    .limit(1).with_for_update(skip_locked=True))
    if not row:
        return None
    lease = str(uuid4())
    changed = db.scalar(update(ReplyDraft).where(ReplyDraft.id == row.id, eligible)
                         .values(status="working", claimed_at=now(), attempts=ReplyDraft.attempts + 1, lease=lease)
                         .returning(ReplyDraft.id)
                         .execution_options(synchronize_session=False))
    if not changed:
        return None
    return row.id, lease, dict(row.request), row.attempts + 1


def reuse_key(request: DraftInput, context: dict) -> str:
    # Context includes the selected resume, verified facts and declaration policy.
    inputs = [request.model_dump(mode="json", exclude={"id", "notify"}), context]
    encoded = json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(b"grounded-english-v2\0" + encoded).hexdigest()


def process_one() -> bool:
    with SessionLocal.begin() as db:
        work = claim(db)
    if not work:
        return False
    draft_id, lease, payload, attempt = work
    attachment = payload.pop("attachment", None)
    recipient = payload.pop("recipient", None)
    try:
        if attempt > 3:
            raise AssistantError("Обработка была прервана. Повторите запрос.")
        if attachment:
            kind = attachment["kind"]
            transcript = recognize(kind, telegram_media(kind, attachment["id"]))
            caption = payload["question"]
            payload["question"] = ((caption + "\n" if caption != "…" else "") + transcript)[:4000]
        request = DraftInput.model_validate(payload)
        with SessionLocal() as db:
            context = prepare(db, request)
            key = reuse_key(request, context)
            previous = db.scalar(select(ReplyDraft.result).where(
                ReplyDraft.status == "ready", ReplyDraft.created_at >= now() - timedelta(minutes=10),
                ReplyDraft.request["reuse_key"].as_string() == key).limit(1))
        result = previous if previous is not None else generate(request, context)
        payload["reuse_key"] = key
        status = "ready"
    except AssistantError as exc:
        result, status = {"error": str(exc)}, "failed"
    except Exception as exc:  # noqa: BLE001 - raw decoder, SQL and model errors may contain private text.
        log.warning("Assistant request failed: %s", type(exc).__name__)
        result, status = {"error": "Не удалось подготовить ответ. Попробуйте ещё раз."}, "failed"
    with SessionLocal.begin() as db:
        row = db.scalar(select(ReplyDraft).where(ReplyDraft.id == draft_id, ReplyDraft.lease == lease)
                        .with_for_update())
        if row:  # A deletion or a new lease wins over late results.
            if recipient:
                payload["recipient"] = recipient
            row.status, row.request, row.result, row.lease = status, payload, result, None
            if row.source == "bot" or payload.get("notify"):
                if status == "ready":
                    variants = result["variants"]
                    msg = variants.get("professional") or variants.get("brief") or variants["warm"]
                    msg += "\n\nРезюме · " + result["resume"]["filename"]
                else:
                    msg = result["error"]
                db.add(Outbox(id=row.id, message=msg))
    return True


class ReplyWorker:
    def __init__(self) -> None:
        self.stop_event = Event()
        self.thread = Thread(target=self.run, name="reply-assistant", daemon=True)

    def run(self) -> None:
        next_cleanup = now()
        while not self.stop_event.is_set():
            try:
                if now() >= next_cleanup:
                    with SessionLocal.begin() as db:
                        clean_expired(db)
                    next_cleanup = now() + timedelta(hours=1)
                if process_one():
                    continue
            except Exception as exc:  # noqa: BLE001 - keep the queue alive across transient dependency failures.
                log.warning("Assistant worker unavailable: %s", type(exc).__name__)
            self.stop_event.wait(2)

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
