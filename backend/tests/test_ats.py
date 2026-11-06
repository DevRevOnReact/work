import pytest
from playwright.async_api import async_playwright

from jobpilot.answers import AnswerEngine, NeedsAttention
from jobpilot.ats import (
    AshbyAdapter,
    GenericFormAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    SmartRecruitersAdapter,
    WorkableAdapter,
)
from jobpilot.domain import Profile

FORM = """<html><body><form onsubmit="event.preventDefault(); window.submissions=(window.submissions||0)+1; document.body.innerHTML='<h1>Thank you for applying</h1>'">
<label>Full name<input name="name" required></label>
<label>Email<input type="email" name="email" required></label>
<label>Resume<input type="file" name="resume" required></label>
<label>Tell us about yourself<textarea required></textarea></label>
<button type="submit">Submit application</button></form></body></html>"""


@pytest.mark.parametrize("adapter", [GenericFormAdapter, GreenhouseAdapter, AshbyAdapter, LeverAdapter,
                                     WorkableAdapter, SmartRecruitersAdapter])
async def test_real_browser_fill_submit_verify(adapter, tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(FORM)
        engine = AnswerEngine(Profile(full_name="Test Candidate", email="test@example.invalid", skills=["React"], years_experience=5), {})
        instance = adapter()
        evidence = await instance.prepare(page, resume, engine, "React role")
        assert len(evidence) == 3
        assert await page.locator("input[type=file]").evaluate("el=>el.files.length") == 1
        await instance.submit(page)
        assert await instance.verify(page)
        assert await page.evaluate("window.submissions") == 1
        await browser.close()


async def test_unknown_required_question_and_captcha_block(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(
            FORM.replace("Tell us about yourself", "Are you authorized to work in the USA?")
        )
        with pytest.raises(NeedsAttention):
            await LeverAdapter().prepare(
                page, resume, AnswerEngine(Profile(full_name="Test", email="t@example.invalid"), {}), ""
            )
        assert await page.evaluate("window.submissions||0") == 0
        await page.set_content("<body>Verify you are human</body>")
        with pytest.raises(NeedsAttention):
            await LeverAdapter().guard(page)
        await browser.close()


async def test_delayed_form_hydration_waits_before_reporting_missing_resume(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content("<body><p>Loading application…</p></body>")
        await page.evaluate("html => setTimeout(() => {document.body.innerHTML = html}, 1000)", FORM)
        adapter = GreenhouseAdapter()
        answers = await adapter.prepare(page, resume, AnswerEngine(
            Profile(full_name="Test Candidate", email="test@example.invalid", skills=["React"]), {}), "React")
        assert answers and await page.locator('input[type=file]').evaluate("el => el.files.length") == 1
        assert await page.evaluate("window.submissions || 0") == 0
        await browser.close()


async def test_apply_links_to_authorized_ats_stay_in_same_page_and_fill(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    destination = "https://job-boards.greenhouse.io/fixture/jobs/123#application"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=(
            FORM if route.request.url.startswith(destination.split('#')[0]) else
            f'<a href="{destination}" target="_blank">Apply on company website</a>'
            f'<a href="{destination}" target="_blank">Apply now</a>')))
        await page.goto("https://careers.example.invalid/job/123")
        adapter = GenericFormAdapter(navigation_hosts={"job-boards.greenhouse.io"})
        await adapter.prepare(page, resume, AnswerEngine(Profile(full_name="Jane Doe",
            email="jane@example.invalid", skills=["Python"]), {}), "Python role")
        assert page.url == destination and len(page.context.pages) == 1
        assert await page.get_by_label("Email", exact=True).input_value() == "jane@example.invalid"
        assert await page.evaluate("window.submissions || 0") == 0
        await browser.close()


async def test_application_tab_reveals_hidden_resume_input(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('<button role="tab" onclick="document.getElementById(\'apply\').hidden=false">Application</button>'
                               + '<div id="apply" hidden>' + FORM + '</div>')
        await AshbyAdapter().prepare(page, resume, AnswerEngine(Profile(full_name="Jane Doe",
            email="jane@example.invalid", skills=["Python"]), {}), "Python role")
        assert await page.locator('input[type=file]').evaluate("el => el.files.length") == 1
        assert await page.evaluate("window.submissions || 0") == 0
        await browser.close()


def test_application_navigation_rejects_unapproved_and_malformed_destinations():
    adapter = GenericFormAdapter(navigation_hosts={"job-boards.greenhouse.io"})
    source = "https://careers.example.invalid/job/123"
    for target in ["https://evil.invalid/apply", "https://job-boards.greenhouse.io.evil.invalid/apply",
                   "https://user:pass@job-boards.greenhouse.io/apply", "javascript:alert(1)",
                   "https://job-boards.greenhouse.io:bad/apply"]:
        assert not adapter.can_navigate(target, source)


async def test_russian_form_and_generated_required_cover_letter(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    form = FORM.replace("Full name", "ФИО").replace("Email", "Электронная почта")
    form = form.replace("Resume", "Резюме").replace("Tell us about yourself", "Расскажите о себе")
    form = form.replace('<button type="submit">Submit application</button>',
                        '<label>Сопроводительное письмо<input type="file" name="cover" required accept="application/pdf"></label>'
                        '<button type="submit">Откликнуться</button>')
    form = form.replace("Thank you for applying", "Спасибо за отклик")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_content(form)
        engine = AnswerEngine(Profile(full_name="Тест Кандидат", email="test@example.invalid",
                                      skills=["Python", "SQL"], years_experience=3), {})
        instance = LeverAdapter()
        evidence = await instance.prepare(page, resume, engine, "Python Developer. Python SQL.")
        assert len(evidence) == 4
        assert await page.locator('input[name="cover"]').evaluate("el => el.files[0].type") == "application/pdf"
        assert await page.locator('input[name="cover"]').evaluate("el => el.files[0].size") > 500
        assert await page.locator('input[name="name"]').input_value() == "Тест Кандидат"
        await instance.submit(page)
        assert await instance.verify(page)
        assert await page.evaluate("window.submissions") == 1
        await browser.close()


async def test_false_confirmation_does_not_count():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(FORM + "<p>Thank you for applying</p>")
        # Still has an active submit button, so never treat description text as a successful submission.
        assert not await LeverAdapter().verify(page)
        await browser.close()


async def test_prepare_never_clicks_a_submit_opener(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(
            """<form onsubmit="event.preventDefault();window.submissions=1"><button>Apply</button></form>"""
        )
        with pytest.raises(NeedsAttention):
            await LeverAdapter().prepare(page, resume, AnswerEngine(Profile(), {}), "")
        assert await page.evaluate("window.submissions||0") == 0
        await browser.close()


async def test_apply_follows_multiple_links_including_accessible_icon_link(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        pages = {"https://board.example/role": '<a href="https://employer.example/job" aria-label="Apply"><span>↗</span></a>',
                 "https://employer.example/job": '<a href="https://jobs.ashbyhq.com/test/application" target="_blank">Apply now</a>',
                 "https://jobs.ashbyhq.com/test/application": FORM}
        await page.route("**/*", lambda route: route.fulfill(body=pages[route.request.url], content_type="text/html"))
        await page.goto("https://board.example/role")
        adapter = GenericFormAdapter(navigation_hosts={"employer.example", "jobs.ashbyhq.com"})
        await adapter.prepare(page, resume, AnswerEngine(Profile(full_name="Jane Doe",
            email="jane@example.invalid", skills=["React"]), {}), "React role")
        assert page.url == "https://jobs.ashbyhq.com/test/application"
        assert await page.evaluate("window.submissions || 0") == 0
        await browser.close()


async def test_apply_button_popup_is_kept_in_guarded_session(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        await context.route("https://employer.example/**", lambda route: route.fulfill(body=FORM, content_type="text/html"))
        page = await context.new_page()
        await page.set_content('<button type="button" onclick="window.open(\'https://employer.example/apply\')">Apply</button>')
        adapter = GenericFormAdapter(navigation_hosts={"employer.example"})
        await adapter.prepare(page, resume, AnswerEngine(Profile(full_name="Jane Doe",
            email="jane@example.invalid", skills=["React"]), {}), "React role")
        assert page.url == "https://employer.example/apply" and len(context.pages) == 1
        assert await page.evaluate("window.submissions || 0") == 0
        await browser.close()


async def test_text_only_form_returns_answers_without_following_apply_or_submitting(tmp_path):
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(FORM.replace('<label>Resume<input type="file" name="resume" required></label>', '')
                               + '<a href="https://elsewhere.example/apply">Apply</a>')
        adapter = GenericFormAdapter(navigation_hosts={"elsewhere.example"})
        with pytest.raises(NeedsAttention, match="загрузки резюме"):
            await adapter.prepare(page, resume, AnswerEngine(Profile(full_name="Jane Doe",
                email="jane@example.invalid", skills=["React"]), {}), "React role")
        answers, fields = await adapter.handoff(page)
        assert any(answer["question"] == "Tell us about yourself" for answer in answers)
        assert any(field["question"] == "Email" for field in fields)
        assert page.url == "about:blank" and await page.evaluate("window.submissions || 0") == 0
        await browser.close()
