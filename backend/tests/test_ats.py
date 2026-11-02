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
