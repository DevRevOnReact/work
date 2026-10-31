"""Fault injection at the durable submit boundary; real browser coverage is in test_ats.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy.orm import sessionmaker
from jobpilot.answers import NeedsAttention
from jobpilot.config import settings
from jobpilot.models import Application, Source
from jobpilot.services import ingest, queue_ready
from jobpilot import workers
from test_domain import vacancy


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("click_timeout", "SUBMISSION_UNCERTAIN"),
        ("no_confirmation", "SUBMISSION_UNCERTAIN"),
        ("unknown_field", "FAILED"),
        ("success", "SUBMITTED"),
    ],
)
def test_submit_boundary_survives_failures(db, active_candidate, monkeypatch, tmp_path, fault, expected):
    source = Source(provider="ashby", board="test", company="Example")
    db.add(source)
    db.flush()
    ingest(db, source, [vacancy()])
    db.commit()
    ids = queue_ready(db)
    db.commit()
    assert len(ids) == 1
    monkeypatch.setattr(settings(), "data_dir", tmp_path)
    monkeypatch.setattr(workers, "SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    page = AsyncMock()
    page.url = vacancy().canonical_url
    page.set_default_timeout = MagicMock()
    page.get_by_text = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=0)))
    browser = AsyncMock()
    browser_context = AsyncMock()
    browser_context.new_page.return_value = page
    browser.new_context.return_value = browser_context
    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    pw.stop = AsyncMock()
    monkeypatch.setattr(workers, "async_playwright", lambda: MagicMock(start=AsyncMock(return_value=pw)))
    adapter = MagicMock()
    adapter.prepare = AsyncMock(return_value=[])
    adapter.submit = AsyncMock()
    adapter.verify = AsyncMock(return_value=fault == "success")
    if fault == "click_timeout":
        adapter.submit.side_effect = TimeoutError("Request may have reached ATS")
    if fault == "unknown_field":
        adapter.prepare.side_effect = NeedsAttention("Unknown required fact")
    monkeypatch.setitem(workers.ATS_ADAPTERS, "ashby", lambda: adapter)
    assert workers.claim(ids[0])
    asyncio.run(workers.execute(ids[0]))
    db.expire_all()
    app = db.get(Application, ids[0])
    assert app.state == expected
    assert (app.submit_intent_at is not None) == (fault != "unknown_field")
    assert (app.submitted_at is not None) == (fault == "success")
    # Simulate task redelivery: failed/uncertain/submitted claims cannot execute again.
    assert not workers.claim(app.id)
    assert adapter.submit.await_count == (0 if fault == "unknown_field" else 1)
