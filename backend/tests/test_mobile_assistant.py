import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from jobpilot.api import app
from jobpilot.config import settings
from jobpilot.db import session
from jobpilot.models import AssistantDevice, ReplyDraft, now


@pytest.fixture
def clients(db, monkeypatch):
    monkeypatch.setattr(settings(), "auth_required", True)
    monkeypatch.setattr(settings(), "assistant_model_url", "http://model.invalid")
    app.dependency_overrides[session] = lambda: db
    try:
        yield TestClient(app), {"Authorization": "Bearer " + settings().api_token.get_secret_value()}
    finally:
        app.dependency_overrides.pop(session)


def credentials():
    secret = "jp1_" + secrets.token_urlsafe(32)
    return hashlib.sha256(secret.encode()).hexdigest(), {"Authorization": "Bearer " + secret}


def test_only_owner_pairs_phone_and_revocation_is_immediate(clients, db):
    client, owner = clients
    fingerprint, phone = credentials()
    body = {"fingerprint": fingerprint}
    assert client.post("/mobile-devices", json=body).status_code == 401
    assert client.post("/mobile-devices", headers=phone, json=body).status_code == 401
    assert client.get("/mobile-assistant", headers=phone).status_code == 401
    assert client.post("/mobile-devices", headers=owner, json=body).status_code == 200
    assert client.get("/mobile-assistant", headers=phone).status_code == 200
    assert client.get("/mobile-assistant", headers={"Authorization":"Bearer "+fingerprint}).status_code == 401
    assert client.get("/profile", headers=phone).status_code == 401
    assert client.get("/mobile-devices", headers=phone).status_code == 401
    draft = {"id":str(uuid4()), "question":"What is the next step?"}
    for _ in range(2):
        assert client.post("/mobile-assistant", headers=phone, json=draft).status_code == 202
    assert client.get("/mobile-assistant/"+draft["id"]+"/status", headers=phone).json()=={"status":"queued"}
    path = "/mobile-assistant/"+draft["id"]+"/answers"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=phone).json() == {"status":"queued","variants":{},"note":"","error":""}
    row = db.get(ReplyDraft, draft["id"])
    variants = {"brief": "Yes.", "professional": "Yes, I can.", "warm": "Happy to help!"}
    row.status = "ready"
    row.result = {"variants": variants, "note": "Review before sending.", "resume_id": "private"}
    db.commit()
    assert client.get(path, headers=phone).json() == {
        "status": "ready", "variants": variants, "note": "Review before sending.", "error": "",
    }
    assert client.get(f"/mobile-assistant/{uuid4()}/answers", headers=phone).status_code == 404
    assert len(client.get("/assistant", headers=owner).json()["drafts"]) == 1
    assert client.delete("/mobile-assistant/"+draft["id"], headers=phone).status_code == 404
    assert client.delete("/mobile-devices/"+fingerprint, headers=owner).is_success
    assert client.get("/mobile-assistant", headers=phone).status_code == 401
    assert client.get(path, headers=phone).status_code == 401


def test_expired_and_unapproved_devices(clients, db):
    client, owner = clients
    fp, phone = credentials()
    client.post("/mobile-devices", headers=owner, json={"fingerprint":fp}).raise_for_status()
    row = db.get(AssistantDevice, fp)
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert client.get("/mobile-assistant", headers=phone).status_code==401
    assert client.get("/mobile-devices", headers=owner).json()==[]
    assert client.get("/mobile-assistant", headers=owner).status_code==401
    assert client.get("/mobile-assistant/"+str(uuid4())+"/file", headers=phone).status_code==401
