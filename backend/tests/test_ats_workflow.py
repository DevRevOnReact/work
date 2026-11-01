"""Real Chromium interacting with local HTTP ATS pages; no employer submissions."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright

from jobpilot.answers import AnswerEngine, NeedsAttention
from jobpilot.ats import ATS_ADAPTERS, GenericFormAdapter
from jobpilot.domain import Profile


@pytest.fixture
def local_ats():
    class Handler(BaseHTTPRequestHandler):
        submissions: ClassVar[list[bytes]] = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            document = (Path(__file__).parent / "fixtures" / "ats-multistep.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(document)

        def do_POST(self):
            self.submissions.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Your application has been received</h1>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", Handler.submissions
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def resume_file(tmp_path):
    document = tmp_path / "resume.pdf"
    document.write_bytes(b"%PDF-1.4\nVerified local fixture resume\n%%EOF")
    return document


async def test_multistep_dynamic_fields_upload_controls_and_confirmation(local_ats, resume_file):
    url, submissions = local_ats
    progress = []

    async def report(step, detail):
        progress.append(step)

    profile = Profile(full_name="Test Candidate", email="candidate@example.invalid", skills=["React", "TypeScript"],
                      work_authorization={"USA": True}, legal_answers={"I agree to the privacy policy": "Yes"})
    engine = AnswerEngine(profile, {"Work arrangement": "Remote", "Remote timezone": "UTC+3",
                                    "Available date": "2026-12-01"})
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(url)
        adapter = GenericFormAdapter(progress=report)
        evidence = await adapter.prepare(page, resume_file, engine, "React TypeScript frontend")
        assert not submissions, "Preparation must never send an application"
        assert await page.locator('[name="timezone"]').input_value() == "UTC+3"
        assert await page.locator('[name="authorization"][value="yes"]').is_checked()
        assert await page.locator('[name="privacy"]').is_checked()
        assert await page.locator('[name="available"]').input_value() == "2026-12-01"
        assert "React" in await page.locator('[name="skills"]').input_value()
        assert "next_step" in progress and "uploading_resume" in progress
        assert any(row["question"] == "Remote timezone" for row in evidence)
        original = await adapter.snapshot(page)
        assert original == await adapter.snapshot(page)
        uploaded = next(row for row in original if row["type"] == "file")
        from hashlib import sha256
        assert uploaded["files"][0]["sha256"] == sha256(resume_file.read_bytes()).hexdigest()
        await page.locator('[name="skills"]').fill("Changed after approval")
        assert original != await adapter.snapshot(page)
        await page.locator('[name="skills"]').fill(next(row["value"] for row in original if row["question"] == "Technical skills"))
        assert original == await adapter.snapshot(page)
        await adapter.submit(page)
        assert await adapter.verify(page)
        assert len(submissions) == 1
        assert b"Verified local fixture resume" in submissions[0]
        with pytest.raises(NeedsAttention):
            await adapter.submit(page)
        assert len(submissions) == 1
        await browser.close()


async def test_missing_answers_preserves_known_evidence_and_never_submits(local_ats, resume_file):
    url, submissions = local_ats
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(url)
        engine = AnswerEngine(Profile(full_name="Test Candidate", email="candidate@example.invalid"), {})
        with pytest.raises(NeedsAttention) as stopped:
            await GenericFormAdapter().prepare(page, resume_file, engine, "")
        assert any(row["question"] == "Work arrangement" for row in stopped.value.questions)
        assert any(row["question"] == "Full name" for row in stopped.value.answers)
        assert await page.locator('[name="full_name"]').input_value() == "Test Candidate"
        assert not submissions
        await browser.close()


async def test_embedded_frame_and_custom_combobox(resume_file):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        # Cross-frame field discovery is required by embedded Greenhouse boards.
        await page.set_content('<iframe title="Application form" id="application"></iframe>')
        frame = page.frames[1]
        await frame.set_content('''<form>
          <label>Full name<input required name="name"></label>
          <label>Resume<input type="file" name="resume" required></label>
          <button type="button" role="combobox" aria-label="Work arrangement"
            onclick="document.querySelector('#options').hidden=false">Choose</button>
          <div id="options" role="listbox" hidden><div role="option" onclick="this.parentNode.hidden=true">Remote</div></div>
          <button type="submit">Submit application</button></form>''')
        adapter = ATS_ADAPTERS["greenhouse"]()
        engine = AnswerEngine(Profile(full_name="Test Candidate"), {"Work arrangement": "Remote"})
        fields = await adapter.inspect(page)
        assert len(fields) == 3
        await adapter.prepare(page, resume_file, engine, "")
        before = await adapter.snapshot(page)
        await frame.locator('[role="combobox"]').evaluate("el => el.innerText='Office'")
        assert before != await adapter.snapshot(page)
        assert await frame.locator('[name="name"]').input_value() == "Test Candidate"
        assert not await frame.locator('#options').is_visible()
        await browser.close()


@pytest.mark.parametrize("html", [
    "<h1>Complete this CAPTCHA</h1>", "<label>Password<input type=password></label>",
    "<h1>Enter your verification code</h1><input autocomplete=one-time-code>",
    '<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>',
])
async def test_human_verification_blockers(html):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.route("https://**/*", lambda route: route.abort())
        await page.set_content(html)
        with pytest.raises(NeedsAttention):
            await GenericFormAdapter().guard(page)
        await browser.close()


async def test_required_invalid_email_blocks_before_submission(resume_file):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('''<form onsubmit="event.preventDefault();window.sent=true">
          <label>Email<input type=email required></label><label>Resume<input type=file required></label>
          <button>Submit application</button></form>''')
        with pytest.raises(NeedsAttention):
            await GenericFormAdapter().prepare(page, resume_file, AnswerEngine(Profile(email="invalid"), {}), "")
        assert not await page.evaluate("window.sent || false")
        await browser.close()


async def test_inspect_does_not_click_upload_or_fill(resume_file):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('''<form onsubmit="event.preventDefault();window.sent=true">
          <label>Email<input type=email required></label><label>Resume<input type=file required></label>
          <button>Submit application</button></form>''')
        fields = await GenericFormAdapter().inspect(page)
        assert [field["question"] for field in fields] == ["Email", "Resume"]
        assert await page.locator('input[type=email]').input_value() == ""
        assert await page.locator('input[type=file]').evaluate("el => el.files.length") == 0
        assert not await page.evaluate("window.sent || false")
        await browser.close()


async def test_preexisting_confirmation_cannot_prove_current_submit(resume_file):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('''<p>Thank you for applying</p><form
          onsubmit="event.preventDefault();this.remove()">
          <label>Resume<input type=file required></label><button>Submit application</button></form>''')
        adapter = GenericFormAdapter()
        await adapter.prepare(page, resume_file, AnswerEngine(Profile(), {}), "")
        await adapter.submit(page)
        assert not await adapter.verify(page)
        await browser.close()


async def test_cookie_rejection_and_role_button_apply_link(local_ats):
    url, submissions = local_ats
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(url)
        await page.set_content('''<a role="button" href="/apply">Apply for this job</a>
          <div role="dialog" aria-label="Cookie Consent" data-ui="cookie-consent">
          <button type="button" onclick="window.accepted=true">Accept all</button>
          <button type="button" onclick="this.parentNode.remove();window.declined=true">Decline all</button></div>''')
        await GenericFormAdapter()._open(page)
        assert page.url.endswith("/apply")
        assert any(field["type"] == "file" for field in await GenericFormAdapter().inspect(page))
        assert not submissions
        await browser.close()


async def test_default_checked_optional_consent_requires_explicit_answer(resume_file):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('''<form onsubmit="event.preventDefault();window.sent=true">
          <label>Resume<input type=file required></label>
          <label>I agree to marketing communications<input type=checkbox checked></label>
          <button>Submit application</button></form>''')
        with pytest.raises(NeedsAttention) as stopped:
            await GenericFormAdapter().prepare(page, resume_file, AnswerEngine(Profile(), {}), "")
        assert any("marketing" in row["question"] for row in stopped.value.questions)
        assert not await page.evaluate("window.sent || false")
        await browser.close()


async def test_unavailable_radio_answer_cannot_preserve_default_choice(resume_file):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('''<form><label>Resume<input type=file required></label>
          <fieldset><legend>Work authorization</legend>
          <label>Yes<input type=radio name=auth value=yes required checked></label>
          <label>No<input type=radio name=auth value=no></label></fieldset>
          <button>Submit application</button></form>''')
        with pytest.raises(NeedsAttention):
            await GenericFormAdapter().prepare(page, resume_file,
                AnswerEngine(Profile(), {"Work authorization": "Ask me later"}), "")
        await browser.close()


@pytest.mark.skipif(not os.getenv("JOBPILOT_ATS_SMOKE_URLS"), reason="Set JSON provider->public ATS URL for read-only smoke")
@pytest.mark.parametrize("provider,url", list(json.loads(os.getenv("JOBPILOT_ATS_SMOKE_URLS", "{}" )).items()) or [("", "")])
async def test_supported_real_ats_pages_read_only(provider, url):
    """Explicit opt-in: read actual employer markup, block all mutation requests.

    Example environment: {"ashby":"https://jobs.ashbyhq.com/<board>/<job>"}.
    This smoke never calls prepare/submit, supplies candidate data, or uploads.
    Only named, read-only Ashby GraphQL queries may use POST; mutations are blocked.
    """
    assert provider in ATS_ADAPTERS
    assert isinstance(url, str) and url.startswith("https://")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(service_workers="block")
        page = await context.new_page()

        async def read_only(route):
            request = route.request
            target = urlsplit(request.url)
            operation = parse_qs(target.query).get("op", [""])[0]
            ashby_read = (provider == "ashby" and request.method == "POST"
                          and target.hostname == "jobs.ashbyhq.com" and target.path == "/api/non-user-graphql"
                          and operation in {"ApiJobPosting", "ApiOrganizationFromHostedJobsPageName", "ApiApplicationForm"})
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not ashby_read:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", read_only)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        assert response and response.ok, f"{provider} returned {response.status if response else 'no response'}"
        await page.wait_for_timeout(1000)
        adapter = ATS_ADAPTERS[provider]()
        try:
            await adapter.guard(page)
            await adapter._open(page)
            await page.wait_for_timeout(1000)
            await adapter.guard(page)
        except NeedsAttention as exc:
            pytest.skip(f"{provider}: site requires human verification: {exc}")
        fields = await adapter.inspect(page)
        assert any(field["type"] == "file" for field in fields), f"{provider} did not expose a resume upload field"
        assert await page.locator("body").inner_text(), f"{provider} returned an empty page"
        await page.close()
        await browser.close()
