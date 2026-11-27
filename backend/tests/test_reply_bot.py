from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select
from test_tailoring import add_resume

from jobpilot import telegram
from jobpilot.config import settings
from jobpilot.models import Control, Outbox, ReplyDraft
from jobpilot.reply_assistant import DraftInput, prepare, role_variant


@pytest.mark.parametrize(("query", "variant"), [
    ("Experience with React and TypeScript in a frontend project?", "frontend"),
    ("Applied AI Engineer", "react-python-ai"),
    ("Full Stack Developer (LLM, React)", "full-stack"),
    ("Расскажите про стек Vue и Python", "full-stack"),
    ("When can we speak?", None),
])
def test_specialization(query, variant):
    assert role_variant(query) == variant


def test_vacancy_context_does_not_turn_salary_in_job_description_into_personal_declaration(db, tmp_path, monkeypatch):
    from uuid import uuid4
    resume = add_resume(db, tmp_path, "Jane Doe\nReact TypeScript", "frontend.txt", monkeypatch)
    resume.variant = "frontend"
    db.flush()
    data = prepare(db, DraftInput(id=uuid4(), question="Can you introduce yourself?",
                                 vacancy="Frontend engineer\nOur salary range is 50-80k."))
    assert data["resume"]["id"] == resume.id and data["policy"] == ""


def test_forwarded_command_only_generates_a_draft_for_owner(db, monkeypatch):
    from pydantic import SecretStr
    config = settings()
    monkeypatch.setattr(config, "assistant_model_url", "http://model.invalid")
    monkeypatch.setattr(config, "telegram_bot_token", SecretStr("test"))
    monkeypatch.setattr(config, "telegram_chat_id", "123")
    manager = MagicMock()
    manager.__enter__.return_value = db
    monkeypatch.setattr(telegram, "SessionLocal", lambda: manager)
    def item(i, sender, chat, kind):
        return {"update_id":i, "message":{"from":{"id":sender}, "chat":{"id":chat,"type":kind},
                                          "text":"/pause", "forward_origin":{"type":"user"}}}
    updates = [item(10,123,123,"private"),item(11,456,123,"group"),item(12,456,456,"private")]
    response = httpx.Response(200,json={"ok":True,"result":updates},request=httpx.Request("GET","https://example.invalid"))
    monkeypatch.setattr(telegram.httpx, "get", lambda *a, **k: response)
    enabled = db.get(Control,1).enabled
    telegram.poll_commands()
    rows = list(db.scalars(select(ReplyDraft)))
    assert len(rows)==1 and rows[0].source=="bot" and rows[0].request["question"]=="/pause"
    assert db.get(Control,1).enabled == enabled
    assert len(list(db.scalars(select(Outbox)))) == 1


def test_pdf_handoff_uses_only_the_verified_private_chat(db, configured_recipient, monkeypatch, tmp_path):
    from uuid import UUID, uuid4
    from jobpilot.reply_api import send_document

    resume = add_resume(db, tmp_path, "Verified example resume", "test.txt", monkeypatch)
    draft = ReplyDraft(id=str(uuid4()), source="app", status="ready", request={},
                       result={"resume":{"id":resume.id}})
    db.add(draft)
    db.flush()
    post = MagicMock(return_value=httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", "https://example.invalid")))
    monkeypatch.setattr(httpx, "post", post)
    assert send_document(UUID(draft.id), db) == {"ok": True}
    sent = post.call_args.kwargs
    assert sent["data"] == {"chat_id":"123"} and sent["files"]["document"][1] == b"Verified example resume"


@pytest.fixture
def configured_recipient(monkeypatch):
    from pydantic import SecretStr
    monkeypatch.setattr(settings(), "telegram_chat_id", "123")
    monkeypatch.setattr(settings(), "telegram_bot_token", SecretStr("test"))


def test_changed_recipient_blocks_pending_reply(db, configured_recipient, monkeypatch):
    from uuid import uuid4
    draft = ReplyDraft(id=str(uuid4()), source="app", status="ready", request={"recipient":"456"}, result={})
    db.add_all([draft, Outbox(id=draft.id, message="Private draft")])
    db.commit()
    manager = MagicMock()
    manager.__enter__.return_value = db
    monkeypatch.setattr(telegram, "SessionLocal", lambda: manager)
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)
    telegram.deliver_outbox()
    post.assert_not_called()
    assert db.get(Outbox, draft.id).error == "AssistantRecipientChanged"
