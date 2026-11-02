"""Durable application commands and browser submission boundary regressions."""
import hashlib
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_domain import vacancy

from jobpilot import automation as flow
from jobpilot.answers import NeedsAttention
from jobpilot.application_runtime import ApplicationRuntime, BrowserSession
from jobpilot.automation_api import AutomationSettings, put_settings
from jobpilot.config import settings
from jobpilot.domain import Profile
from jobpilot.models import Application, Candidate, Control, Job, Resume, Source, now
from jobpilot.security import verified_application_url


@pytest.fixture
def workspace(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "data_dir", tmp_path)
    monkeypatch.setattr(settings(), "application_browser_headless", True)
    text = ("Jane Doe\nSenior Frontend Engineer\nReact Next.js TypeScript JavaScript Python.\n"
            "SaaS fintech payments analytics. 5 years experience.")
    directory = tmp_path / "resumes"
    directory.mkdir()
    (directory / "cv.txt").write_bytes(text.encode())
    resume = Resume(variant="general", filename="cv.txt", storage_key="cv.txt",
                    sha256=hashlib.sha256(text.encode()).hexdigest())
    source = Source(provider="ashby", board="test", company="Example")
    db.add_all([resume, source])
    db.flush()
    candidate = db.get(Candidate, 1)
    candidate.facts = Profile(full_name="Jane Doe", email="jane@example.invalid", location="Georgia",
                              active_resume_id=resume.id, resume_text=text, skills=["React", "Python"]).model_dump()
    control = db.get(Control, 1)
    control.authorized_domains = ["jobs.ashbyhq.com"]
    control.min_score = 0
    job = Job(source_id=source.id, **vacancy().model_dump())
    db.add(job)
    db.commit()
    runtime = ApplicationRuntime(sessionmaker(db.bind, expire_on_commit=False))
    runtime.last_selection = time.monotonic()
    return SimpleNamespace(runtime=runtime, job=job, resume=resume, candidate=candidate, control=control,
                           profile=Profile.model_validate(candidate.facts), root=tmp_path)


def mock_browser(monkeypatch, runtime, *, confirmed=True, preparation_error=None, submit_error=None):
    adapter = SimpleNamespace(prepare=AsyncMock(return_value=[{"question": "Email", "answer": "jane@example.invalid",
                                                               "evidence": ["profile.email"]}]),
                              guard=AsyncMock(), inspect=AsyncMock(return_value=[]), snapshot=AsyncMock(return_value=[]),
                              validate_submission=AsyncMock(),
                              submit=AsyncMock(), verify=AsyncMock(return_value=confirmed))
    if preparation_error:
        adapter.prepare.side_effect = preparation_error
    if submit_error:
        adapter.submit.side_effect = submit_error

    async def open_browser(application_id, profile, version, document_hash, attempt_id, answers_version):
        page = SimpleNamespace(url="https://jobs.ashbyhq.com/test/1/application", screenshot=AsyncMock())
        session = BrowserSession(SimpleNamespace(stop=AsyncMock()), SimpleNamespace(close=AsyncMock()),
                                 SimpleNamespace(close=AsyncMock()), page, adapter, profile, version,
                                 document_hash, attempt_id, time.monotonic() + 900, answers_version)
        runtime.sessions[application_id] = session
        return session

    monkeypatch.setattr(runtime, "open_browser", open_browser)
    return adapter


def test_request_idempotent_and_claim_only_once(db, workspace):
    first = flow.request_application(db, workspace.job)
    db.commit()
    second = flow.request_application(db, workspace.job)
    db.commit()
    assert first.id == second.id and first.attempt_id == second.attempt_id
    assert db.scalar(select(func.count()).select_from(Application)) == 1
    assert flow.claim(db, first.id)
    assert not flow.claim(db, first.id)


def test_unrecognized_client_job_url_cannot_inject_job_facts(db):
    with pytest.raises(ValueError, match="не найдена"):
        flow.resolve_job(db, "https://jobs.ashbyhq.com/injected/role?score=100")
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_tracking_variants_resolve_same_job_and_prevent_duplicate_attempt(db, workspace):
    url = workspace.job.canonical_url
    assert flow.resolve_job(db, url + "?utm_source=mail&ref=abc#application").id == workspace.job.id
    app = flow.request_application(db, workspace.job)
    app.submit_intent_at = now()
    app.state = "SUBMISSION_UNCERTAIN"
    db.commit()
    with pytest.raises(ValueError, match="повторная отправка"):
        flow.request_application(db, flow.resolve_job(db, url + "?utm_campaign=test"))


def test_peer_duplicate_company_role_and_location_block_after_intent(db, workspace):
    app = flow.request_application(db, workspace.job)
    app.submit_intent_at = now()
    other = Job(source_id=workspace.job.source_id, **vacancy(external_id="different",
                canonical_url="https://jobs.ashbyhq.com/test/different").model_dump())
    db.add(other)
    db.commit()
    with pytest.raises(ValueError, match="уже отправлен"):
        flow.request_application(db, other)


@pytest.mark.parametrize("url", ["http://jobs.ashbyhq.com/x", "https://jobs.ashbyhq.com.attacker.invalid/x",
                                 "https://user:pass@jobs.ashbyhq.com/x", "https://jobs.ashbyhq.com:8443/x",
                                 "https://api.ashbyhq.com/x"])
def test_upload_destination_requires_exact_authorized_https_domain(url):
    with pytest.raises(ValueError, match="HTTPS"):
        verified_application_url(url, ["jobs.ashbyhq.com"])


@pytest.mark.parametrize("domain", ["*.ashbyhq.com", "https://jobs.ashbyhq.com", "jobs.ashbyhq.com:443", "localhost"])
def test_settings_reject_wildcard_url_port_and_localhost(domain):
    with pytest.raises(ValidationError):
        AutomationSettings(authorized_domains=[domain])


@pytest.mark.parametrize("configuration", [{"mode": "autonomous", "auto_apply": False, "authorized_domains": ["jobs.ashbyhq.com"]},
                                           {"mode": "autonomous", "auto_apply": True, "authorized_domains": []},
                                           {"mode": "manual", "auto_apply": True}])
def test_autonomous_submission_requires_explicit_opt_in_and_domains(db, configuration):
    with pytest.raises(HTTPException) as error:
        put_settings(AutomationSettings(**configuration), db)
    assert error.value.status_code == 422


def test_recovery_requeues_preparation_but_fences_attempted_submissions(db, workspace):
    app = flow.request_application(db, workspace.job)
    app.state, app.approved_fingerprint = "FILLING_FORM", "old-approval"
    db.commit()
    flow.recover(db)
    db.refresh(app)
    assert app.state == "QUEUED" and app.approved_fingerprint is None
    app.state, app.submit_intent_at = "SUBMITTING", now()
    db.commit()
    flow.recover(db)
    db.refresh(app)
    assert app.state == "SUBMISSION_UNCERTAIN"
    assert not flow.claim(db, app.id)
    with pytest.raises(ValueError, match="началась"):
        flow.command(db, app, "resume")


async def test_manual_prepares_real_document_waits_for_current_approval_and_submits_once(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "AWAITING_APPROVAL" and app.submit_intent_at is None, ascii(app.attention)
    assert (workspace.root / app.generated_resume_key).is_file()
    assert app.answers[0]["answer"] == "jane@example.invalid"
    assert adapter.submit.await_count == 0
    with pytest.raises(ValueError, match="Предпросмотр"):
        flow.command(db, app, "approve", "stale-token")
    flow.command(db, app, "approve", app.prepared_fingerprint)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "SUBMITTED" and app.submitted_at and app.submit_intent_at
    await runtime.tick()
    assert adapter.submit.await_count == 1


@pytest.mark.parametrize("confirmed,timeout,expected", [(False, False, "SUBMISSION_UNCERTAIN"),
                                                      (True, True, "SUBMISSION_UNCERTAIN"),
                                                      (True, False, "SUBMITTED")])
async def test_outcome_requires_actual_confirmation_and_timeout_never_retries(db, workspace, monkeypatch, confirmed, timeout, expected):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime, confirmed=confirmed,
                           submit_error=TimeoutError("May already be accepted") if timeout else None)
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    flow.command(db, app, "approve", app.prepared_fingerprint)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == expected and app.submit_intent_at is not None
    assert (app.submitted_at is not None) == (expected == "SUBMITTED")
    await runtime.tick()
    assert adapter.submit.await_count == 1
    if expected == "SUBMISSION_UNCERTAIN":
        with pytest.raises(ValueError):
            flow.command(db, app, "resume")


async def test_missing_required_fact_pauses_without_crossing_boundary(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime, preparation_error=NeedsAttention("Unknown legal declaration"))
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "NEEDS_HUMAN_ACTION" and app.submit_intent_at is None
    assert adapter.submit.await_count == 0


async def test_autonomous_submit_respects_required_approval_categories(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    adapter.prepare.return_value = [{"question": "Are you authorized to work in Georgia?", "answer": "Yes",
                                     "evidence": ["work_authorization.Georgia"]}]
    workspace.control.mode, workspace.control.auto_apply = "autonomous", True
    app = flow.request_application(db, workspace.job, "autonomous")
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "AWAITING_APPROVAL" and adapter.submit.await_count == 0
    assert app.progress["approval_categories"] == ["legal"]


async def test_autonomous_can_submit_with_explicit_enabled_policy(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    workspace.control.mode, workspace.control.auto_apply = "autonomous", True
    app = flow.request_application(db, workspace.job, "autonomous")
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "SUBMITTED" and adapter.submit.await_count == 1


async def test_profile_mutation_invalidates_review_before_submit(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    flow.command(db, app, "approve", app.prepared_fingerprint)
    candidate = db.get(Candidate, 1)
    candidate.version += 1
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "NEEDS_HUMAN_ACTION" and app.submit_intent_at is None
    assert adapter.submit.await_count == 0


async def test_changed_form_or_file_invalidates_approval(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    adapter.snapshot.return_value = [{"question": "Email", "value": "changed@example.invalid"}]
    flow.command(db, app, "approve", app.prepared_fingerprint)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "NEEDS_HUMAN_ACTION" and app.submit_intent_at is None
    assert adapter.submit.await_count == 0


async def test_daily_limit_rechecked_at_submission(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    workspace.control.daily_limit = 1
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    other = Job(source_id=workspace.job.source_id,
                **vacancy(external_id="other", company="Other", title="Different role",
                          canonical_url="https://jobs.ashbyhq.com/other/2").model_dump())
    db.add(other)
    db.flush()
    db.add(Application(job_id=other.id, state="SUBMISSION_UNCERTAIN", submit_intent_at=now()))
    flow.command(db, app, "approve", app.prepared_fingerprint)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "QUEUED" and app.submit_intent_at is None
    assert app.progress["retry_at"] and app.progress["error_code"] == "daily_limit"
    assert adapter.submit.await_count == 0


def test_moscow_daily_limit_counts_intent_from_previous_utc_calendar_day(db, workspace, monkeypatch):
    from jobpilot.tailoring import select_resume

    # 00:30 Moscow on November 2; the local application's day starts at
    # 21:00 UTC on November 1, which SQLite must compare in UTC as well.
    monkeypatch.setattr(flow, "now", lambda: datetime(2026, 11, 1, 21, 30, tzinfo=UTC))
    monkeypatch.setattr(settings(), "search_timezone", "Europe/Moscow")
    workspace.control.daily_limit = 1
    app = flow.request_application(db, workspace.job)
    other = Job(source_id=workspace.job.source_id,
                **vacancy(external_id="midnight-other", company="Different employer", title="Different role",
                          canonical_url="https://jobs.ashbyhq.com/other/midnight").model_dump())
    db.add(other)
    db.flush()
    db.add(Application(job_id=other.id, state="SUBMISSION_UNCERTAIN",
                       submit_intent_at=datetime(2026, 11, 1, 21, 15, tzinfo=UTC)))
    db.commit()
    _, profile = select_resume(db, workspace.job, workspace.profile)
    with pytest.raises(NeedsAttention, match="дневной лимит"):
        flow.policy_check(db, app, workspace.job, profile, submitting=True)


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_stale_user_command_cannot_overwrite_committed_submission_boundary(db, workspace, action):
    app = flow.request_application(db, workspace.job)
    app.state = "NEEDS_HUMAN_ACTION"
    db.commit()
    # This user's ORM row predates another session committing the submit fence.
    with workspace.runtime.session_factory() as other_db:
        current = other_db.get(Application, app.id)
        current.state, current.submit_intent_at = "SUBMITTING", now()
        other_db.commit()
    with pytest.raises(ValueError, match="началась"):
        flow.command(db, app, action)
    db.rollback()
    db.expire_all()
    assert db.get(Application, app.id).state == "SUBMITTING"


async def test_dispatch_eventually_recovers_stale_orphan_preparation(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    app = flow.request_application(db, workspace.job)
    app.state = "PREPARING"
    app.updated_at = now() - timedelta(seconds=settings().application_timeout_seconds + 120)
    db.commit()
    runtime.last_selection = 0
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "AWAITING_APPROVAL" and app.submit_intent_at is None
    assert adapter.submit.await_count == 0


async def test_actual_browser_route_guard_blocks_untrusted_writes_and_navigation(db, workspace, monkeypatch):
    import playwright.async_api

    import jobpilot.application_runtime as runtime_module

    runtime = workspace.runtime
    app = flow.request_application(db, workspace.job)
    db.commit()
    page = SimpleNamespace(set_default_timeout=MagicMock(), goto=AsyncMock(), wait_for_timeout=AsyncMock())
    context = SimpleNamespace(route=AsyncMock(), new_page=AsyncMock(return_value=page), close=AsyncMock(),
                              route_web_socket=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)), stop=AsyncMock())
    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: SimpleNamespace(start=AsyncMock(return_value=pw)))

    async def public(url):
        return "127.0.0.1" not in url and "10.0.0.1" not in url

    monkeypatch.setattr(runtime_module, "public_url", public)
    await runtime.open_browser(app.id, workspace.profile, 1, "digest", app.attempt_id, "answers")
    guard = context.route.call_args.args[1]

    async def request(url, method="POST", navigation=False, body=None, frame_url=None):
        route = SimpleNamespace(request=SimpleNamespace(url=url, method=method,
                                headers={"content-type": "application/json"} if body else {}, post_data=body,
                                is_navigation_request=lambda: navigation), continue_=AsyncMock(), abort=AsyncMock())
        if frame_url:
            route.request.frame = SimpleNamespace(url=frame_url)
        await guard(route)
        return route

    allowed = await request("https://api.ashbyhq.com/upload")
    assert allowed.continue_.await_count == 1 and allowed.abort.await_count == 0
    # A user may complete authentication in the paused, authorized browser;
    # this exception cannot authorize an application submission endpoint.
    app.state = "NEEDS_HUMAN_ACTION"
    db.commit()
    login = await request("https://jobs.ashbyhq.com/login", "POST", True)
    assert login.continue_.await_count == 1
    app.state = "AWAITING_APPROVAL"
    db.commit()
    before_approval = await request("https://api.ashbyhq.com/submit")
    assert before_approval.abort.await_count == 1
    native_before_approval = await request(workspace.job.canonical_url, navigation=True)
    assert native_before_approval.abort.await_count == 1
    graphql_before_approval = await request("https://api.ashbyhq.com/graphql", body='{"operationName":"SubmitApplication"}')
    assert graphql_before_approval.abort.await_count == 1
    workspace.control.authorized_domains = ["jobs.ashbyhq.com", "job-boards.greenhouse.io"]
    db.commit()
    greenhouse_frame = "https://job-boards.greenhouse.io/fixture/jobs/123"
    redirected_upload = await request("https://boards-api.greenhouse.io/upload", frame_url=greenhouse_frame)
    assert redirected_upload.continue_.await_count == 1
    redirected_submit = await request("https://boards-api.greenhouse.io/submit", frame_url=greenhouse_frame)
    assert redirected_submit.abort.await_count == 1
    unrelated_provider = await request("https://api.ashbyhq.com/upload", frame_url=greenhouse_frame)
    assert unrelated_provider.abort.await_count == 1
    for url, method, navigation in [
        ("https://attacker.invalid/steal", "POST", False),
        ("https://127.0.0.1/admin", "GET", False),
        ("https://10.0.0.1/admin", "GET", True),
        ("https://jobs.ashbyhq.com.attacker.invalid/role", "GET", True),
        ("https://user:password@jobs.ashbyhq.com/role", "POST", False),
        ("http://jobs.ashbyhq.com/upload", "POST", False),
    ]:
        blocked = await request(url, method, navigation)
        assert blocked.abort.await_count == 1 and blocked.continue_.await_count == 0
    workspace.control.authorized_domains = []
    db.commit()
    revoked = await request("https://api.ashbyhq.com/upload")
    assert revoked.abort.await_count == 1
    workspace.control.authorized_domains = ["jobs.ashbyhq.com"]
    app.submit_intent_at = now()
    app.state = "SUBMITTING"
    db.commit()
    permitted_submit = await request("https://api.ashbyhq.com/submit")
    assert permitted_submit.continue_.await_count == 1 and permitted_submit.abort.await_count == 0
    duplicate_submit = await request("https://api.ashbyhq.com/submit")
    assert duplicate_submit.abort.await_count == 1 and duplicate_submit.continue_.await_count == 0
    await runtime.close(app.id)


def test_stored_generated_resume_survives_local_file_loss_and_requires_authentication(db, workspace, monkeypatch):
    from jobpilot import api
    from jobpilot.db import session
    from jobpilot.tailoring import generate_resume

    monkeypatch.setattr(settings(), "store_resume_files_in_db", True)
    monkeypatch.setattr(settings(), "auth_required", True)
    app = flow.request_application(db, workspace.job)
    db.commit()
    directory = workspace.root / "artifacts" / app.id / app.attempt_id
    document = generate_resume(workspace.resume, workspace.profile, workspace.job, directory)
    app.generated_resume_key = str(document.relative_to(workspace.root))
    expected = document.read_bytes()
    flow.store_artifact(db, app, document)
    db.commit()
    document.unlink()
    assert not document.exists()
    api.app.dependency_overrides[session] = lambda: db
    client = TestClient(api.app)
    url = f"/automation/applications/{app.id}/files/resume.docx"
    try:
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer incorrect"}).status_code == 401
        response = client.get(url, headers={"Authorization": "Bearer " + settings().api_token.get_secret_value()})
        assert response.status_code == 200 and response.content == expected
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert response.headers["cache-control"] == "private, no-store"
        missing = client.get(url.replace("resume.docx", "before.png"),
                             headers={"Authorization": "Bearer " + settings().api_token.get_secret_value()})
        assert missing.status_code == 404
    finally:
        client.close()
        api.app.dependency_overrides.clear()


async def test_browser_progress_keeps_submitting_state_after_boundary(db, workspace):
    app = flow.request_application(db, workspace.job)
    app.state, app.submit_intent_at = "SUBMITTING", now()
    db.commit()
    await workspace.runtime.record(app.id, "submitting", "Clicking confirmed application")
    db.expire_all()
    assert db.get(Application, app.id).state == "SUBMITTING"


async def test_uncertain_submission_can_be_verified_without_repeating_submit(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime, confirmed=False)
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    flow.command(db, app, "approve", app.prepared_fingerprint)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "SUBMISSION_UNCERTAIN"
    adapter.verify.return_value = True
    flow.command(db, app, "verify")
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "SUBMITTED" and app.submitted_at is not None
    assert adapter.submit.await_count == 1 and adapter.verify.await_count == 2


async def test_shutdown_during_approval_prevents_new_submission(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    app = flow.request_application(db, workspace.job)
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    flow.command(db, app, "approve", app.prepared_fingerprint)
    db.commit()
    runtime.stop()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.submit_intent_at is None and adapter.submit.await_count == 0


async def test_cancelled_queued_application_never_opens_browser(db, workspace, monkeypatch):
    runtime = workspace.runtime
    adapter = mock_browser(monkeypatch, runtime)
    app = flow.request_application(db, workspace.job)
    db.commit()
    flow.command(db, app, "cancel")
    db.commit()
    await runtime.tick()
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.state == "CANCELLED" and app.submit_intent_at is None
    assert adapter.prepare.await_count == adapter.submit.await_count == 0


@pytest.mark.parametrize("mutate", [False, True])
async def test_real_browser_preparation_approval_and_confirmation_no_external_requests(db, workspace, monkeypatch, mutate):
    """Run a real Chromium against an ATS URL entirely fulfilled by local HTML."""
    from playwright.async_api import async_playwright

    from jobpilot.ats import AshbyAdapter

    runtime = workspace.runtime
    observations = []
    requests = []
    form = '''<html><body><form onsubmit="event.preventDefault(); console.log('local-submission');
    document.body.innerHTML='<h1>Thank you for applying</h1>'">
    <label>Full name<input id="name" required></label>
    <label>Email<input id="email" type="email" required></label>
    <label>Resume<input id="resume" type="file" required></label>
    <label>Tell us about yourself<textarea required></textarea></label>
    <button type="submit">Submit application</button></form></body></html>'''

    async def open_browser(application_id, profile, version, document_hash, attempt_id, answers_version):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(service_workers="block")

        async def local_page(route):
            requests.append(route.request.url)
            assert route.request.url == workspace.job.canonical_url
            await route.fulfill(status=200, content_type="text/html", body=form)

        # Fulfill every request locally. This test cannot reach an employer.
        await context.route("**/*", local_page)
        page = await context.new_page()
        page.on("console", lambda message: observations.append(message.text))
        adapter = AshbyAdapter(progress=lambda step, detail: runtime.record(application_id, step, detail))
        session = BrowserSession(pw, browser, context, page, adapter, profile, version, document_hash,
                                 attempt_id, time.monotonic() + 900, answers_version)
        runtime.sessions[application_id] = session
        await page.goto(workspace.job.canonical_url)
        return session

    monkeypatch.setattr(runtime, "open_browser", open_browser)
    app = flow.request_application(db, workspace.job)
    db.commit()
    try:
        await runtime.tick()
        db.expire_all()
        app = db.get(Application, app.id)
        assert app.state == "AWAITING_APPROVAL", ascii(app.attention)
        page = runtime.sessions[app.id].page
        assert await page.locator("#name").input_value() == "Jane Doe"
        assert await page.locator("#email").input_value() == "jane@example.invalid"
        assert await page.locator("#resume").evaluate("el => el.files[0].name") == "resume.docx"
        assert observations == [] and app.submit_intent_at is None
        if mutate:
            await page.locator("#email").fill("unexpected@example.invalid")
        flow.command(db, app, "approve", app.prepared_fingerprint)
        db.commit()
        await runtime.tick()
        db.expire_all()
        app = db.get(Application, app.id)
        if mutate:
            assert app.state == "NEEDS_HUMAN_ACTION" and app.submit_intent_at is None
            assert observations == []
        else:
            assert app.state == "SUBMITTED" and app.submitted_at and app.submit_intent_at
            assert observations.count("local-submission") == 1
        assert requests == [workspace.job.canonical_url]
    finally:
        await runtime.close(app.id)


def test_supported_sites_opt_in_expands_real_domains(db, monkeypatch):
    from jobpilot.security import ATS_HOSTS
    monkeypatch.setattr("jobpilot.automation_api.browser_available", lambda: True)
    result = put_settings(AutomationSettings(mode="autonomous", auto_apply=True,
                                           use_supported_sites=True), db)
    assert set(result["authorized_domains"]) == set().union(*ATS_HOSTS.values())
    assert result["auto_apply"] is True
    assert db.get(Control, 1).mode == "autonomous"


def test_supported_sites_never_imply_auto_send_consent(db):
    with pytest.raises(HTTPException) as error:
        put_settings(AutomationSettings(mode="autonomous", use_supported_sites=True), db)
    assert error.value.status_code == 422


def test_selection_rechecks_stale_search_score_against_selected_resume(db, workspace, monkeypatch):
    from jobpilot import domain, tailoring, top_vacancies

    workspace.control.mode = "autonomous"
    workspace.control.enabled = workspace.control.auto_apply = True
    workspace.control.min_score = 80
    db.commit()
    stale = {"canonical_url": workspace.job.canonical_url, "company": workspace.job.company,
             "score": 10, "eligibility": "unknown"}
    monkeypatch.setattr(top_vacancies, "top_rows", lambda db: [SimpleNamespace(data=stale)])
    monkeypatch.setattr(tailoring, "select_resume", lambda db, job, profile, **kwargs: (workspace.resume, workspace.profile))
    monkeypatch.setattr(domain, "evaluate", lambda job, profile: {"score": 90, "eligibility": "eligible", "recommendable": True})

    workspace.runtime.select_eligible()

    db.expire_all()
    app = db.scalar(select(Application).where(Application.job_id == workspace.job.id))
    assert app is not None and app.state == "QUEUED"


async def test_transient_preparation_failure_retries_after_delay_without_submission(db, workspace, monkeypatch):
    adapter = mock_browser(monkeypatch, workspace.runtime)
    adapter.prepare.side_effect = [TimeoutError("Temporary connection loss"), []]
    app = flow.request_application(db, workspace.job)
    db.commit()
    await workspace.runtime.tick()
    db.expire_all()
    db.refresh(app)
    assert app.state == "QUEUED" and app.progress["retry_count"] == 1
    assert app.id not in workspace.runtime.sessions and app.submit_intent_at is None
    await workspace.runtime.tick()
    assert adapter.prepare.await_count == 1  # Backoff survives dispatch cycles.
    app.progress = {**app.progress, "retry_at": (now() - timedelta(seconds=1)).isoformat()}
    db.commit()
    await workspace.runtime.tick()
    db.expire_all()
    db.refresh(app)
    assert app.state == "AWAITING_APPROVAL" and adapter.prepare.await_count == 2
    assert adapter.submit.await_count == 0


async def test_transient_retries_are_bounded_and_manual_resume_resets_budget(db, workspace):
    app = flow.request_application(db, workspace.job)
    db.commit()
    for _ in range(3):
        await workspace.runtime.failure(app.id, TimeoutError("network"))
    db.expire_all()
    db.refresh(app)
    assert app.state == "FAILED" and app.progress["retry_count"] == 2
    flow.command(db, app, "resume")
    assert app.progress["retry_count"] == 0 and app.progress["retry_at"] is None


async def test_waiting_application_cannot_starve_queue(db, workspace, monkeypatch):
    monkeypatch.setattr(settings(), "application_session_limit", 1)
    adapter = mock_browser(monkeypatch, workspace.runtime)
    first = flow.request_application(db, workspace.job)
    db.commit()
    await workspace.runtime.tick()
    assert first.id in workspace.runtime.sessions
    second_job = Job(source_id=workspace.job.source_id, **vacancy(external_id="second",
                     canonical_url="https://jobs.ashbyhq.com/test/second").model_dump())
    db.add(second_job)
    db.flush()
    second = flow.request_application(db, second_job)
    db.commit()
    await workspace.runtime.tick()
    db.expire_all()
    db.refresh(first)
    db.refresh(second)
    assert first.state == second.state == "AWAITING_APPROVAL"
    assert first.progress["browser_status"] == "closed"
    assert second.id in workspace.runtime.sessions and first.id not in workspace.runtime.sessions
    assert adapter.prepare.await_count == 2 and adapter.submit.await_count == 0


def test_click_prepare_respects_saved_autonomous_mode(db, workspace):
    from jobpilot.automation_api import ApplicationInput, prepare

    workspace.control.mode = "autonomous"
    workspace.control.auto_apply = True
    db.commit()
    result = prepare(ApplicationInput(canonical_url=workspace.job.canonical_url), db)
    assert result["mode"] == "autonomous"


async def test_daily_limit_defers_instead_of_requesting_human_intervention(db, workspace, monkeypatch):
    from jobpilot.answers import RetryLater

    mock_browser(monkeypatch, workspace.runtime)
    app = flow.request_application(db, workspace.job, "autonomous")
    db.commit()
    reset_at = now() + timedelta(hours=5)
    await workspace.runtime.failure(app.id, RetryLater("Продолжим завтра", reset_at))
    db.expire_all()
    db.refresh(app)
    assert app.state == "QUEUED" and app.attention is None
    assert app.progress["retry_at"] == reset_at.isoformat()
    assert app.submit_intent_at is None
    await workspace.runtime.tick()
    assert app.id not in workspace.runtime.sessions
