from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_tailoring import add_resume

from jobpilot import reply_runtime as runtime
from jobpilot.api import app
from jobpilot.config import settings
from jobpilot.db import session
from jobpilot.models import Candidate, Outbox, ReplyDraft, now
from jobpilot.reply_api import remove
from jobpilot.reply_assistant import AssistantError, DraftInput, generate, prepare


@pytest.fixture
def configured(db, monkeypatch):
    monkeypatch.setattr(settings(), "assistant_model_url", "http://model.invalid")
    monkeypatch.setattr(runtime, "SessionLocal", sessionmaker(db.get_bind(), expire_on_commit=False))
    db.commit()


def question(value="Tell me about your React experience", **kwargs):
    return DraftInput(id=uuid4(), question=value, **kwargs)


def test_verified_resume_is_used_without_switching_active(db, tmp_path, monkeypatch):
    frontend = add_resume(db, tmp_path, "Jane Doe\nFrontend Engineer\nReact TypeScript CSS", "web.txt", monkeypatch)
    add_resume(db, tmp_path, "Jane Doe\nBackend Engineer\nPython PostgreSQL REST", "api.txt", monkeypatch)
    original = db.get(Candidate, 1).facts.copy()
    data = prepare(db, question(resume_id=frontend.id))
    assert data["resume"]["id"] == frontend.id and "Python" not in str(data["facts"])
    assert db.get(Candidate, 1).facts == original
    with pytest.raises(AssistantError, match="недоступно"):
        prepare(db, question(resume_id=uuid4()))


def test_private_declaration_uses_fixed_answer_without_model(db, configured, monkeypatch, tmp_path):
    add_resume(db, tmp_path, "Jane Doe\nReact engineer", "resume.txt", monkeypatch)
    body = question("Can you guarantee visa sponsorship?", language="en")
    result = generate(body, prepare(db, body))
    assert result["variants"] == {"brief": "I'll check the details and get back to you with an accurate answer."}


def test_model_writes_only_one_answer(configured, monkeypatch):
    calls = []
    def post(url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        assert body["format"]["required"] == ["reply", "fact_ids"]
        assert list(body["format"]["properties"]) == ["reply", "fact_ids"]
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"message": {"content": '{"reply":"Thanks, what time suits you?"}'}})
    monkeypatch.setattr(httpx, "post", post)
    result = generate(question(), {"resume": {}, "facts": {}, "policy": ""})
    assert result["variants"] == {"professional": "Thanks, what time suits you?"}
    assert len(calls) == 1


def test_same_input_reuses_one_inference_and_changed_context_regenerates(db, configured, monkeypatch):
    facts = {"stack": "React"}
    context = {"resume": {"id": "test"}, "facts": facts, "policy": ""}
    calls = []
    monkeypatch.setattr(runtime, "prepare", lambda *_: context)

    def generate_once(*args):
        calls.append(1)
        return {"resume": context["resume"], "variants": {"professional": "Hello."}}

    monkeypatch.setattr(runtime, "generate", generate_once)
    for index in range(4):
        if index == 3:
            facts["stack"] = "TypeScript"
        row = runtime.enqueue(db, question(instruction="Friendly" if index >= 2 else ""), "app")
        db.commit()
        assert runtime.process_one()
        db.refresh(row)
        assert row.status == "ready"
    assert len(calls) == 3


def test_duplicate_and_worker_restart(db, configured, monkeypatch):
    body = question()
    row = runtime.enqueue(db, body, "bot")
    assert runtime.enqueue(db, body, "bot").id == row.id
    db.commit()
    first = runtime.claim(db)
    db.commit()
    assert first and not runtime.claim(db)
    db.refresh(row)
    row.claimed_at = now() - runtime.LEASE - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(runtime, "prepare", lambda *_: {})
    monkeypatch.setattr(runtime, "generate", lambda *_: {"resume": {"filename": "cv.pdf"}, "variants":{"brief":"Good day."}})
    assert runtime.process_one() and not runtime.process_one()
    db.refresh(row)
    assert row.status == "ready" and row.attempts == 2 and db.get(Outbox, row.id)
    row.created_at = now() - timedelta(days=8)
    db.commit()
    runtime.clean_expired(db)
    db.commit()
    assert db.scalar(select(func.count()).select_from(ReplyDraft)) == 0
    assert db.scalar(select(func.count()).select_from(Outbox)) == 0


def test_delete_during_inference_does_not_restore_private_data(db, configured, monkeypatch):
    row = runtime.enqueue(db, question(), "bot")
    db.commit()
    def finish(*_):
        remove(UUID(row.id), db)
        return {"resume":{"filename":"cv.pdf"}, "variants":{"brief":"Hello."}}
    monkeypatch.setattr(runtime, "prepare", lambda *_: {})
    monkeypatch.setattr(runtime, "generate", finish)
    assert runtime.process_one()
    assert db.scalar(select(func.count()).select_from(ReplyDraft)) == 0
    assert not db.scalar(select(Outbox))


def test_bound_queue_and_attachment_validation(db, configured):
    assert "до одной минуты" in runtime.bot_request(db, {"voice":{"duration":61}}, 10)
    assert runtime.bot_request(db, {"text":"/assistant"}, 11) == runtime.BOT_HELP
    for _ in range(6):
        runtime.enqueue(db, question(), "app")
    with pytest.raises(AssistantError, match="Очередь"):
        runtime.enqueue(db, question(), "app")


def test_web_and_mini_use_separate_authentication(db, configured, monkeypatch):
    monkeypatch.setattr(settings(), "auth_required", True)
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        headers = {"Authorization":"Bearer "+settings().api_token.get_secret_value()}
        assert client.get("/assistant").status_code == 401
        assert client.get("/telegram-mini/assistant", headers=headers).status_code == 401
        assert client.post("/telegram-mini/assistant/media/image", content=b"invalid").status_code == 401
        body = question().model_dump(mode="json")
        assert client.post("/assistant", headers=headers, json=body).status_code == 202
        assert client.post("/assistant", headers=headers, json=body).status_code == 202
        history = client.get("/assistant", headers=headers).json()["drafts"]
        assert len(history) == 1 and history[0]["question"] == body["question"]
        assert client.get(f"/assistant/{body['id']}/file", headers=headers).status_code == 404
        assert client.post("/assistant", headers=headers, json={**body, "chat_id":"other"}).status_code == 422
    finally:
        app.dependency_overrides.clear()
