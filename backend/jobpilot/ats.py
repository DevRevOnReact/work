import re
from html import escape
from pathlib import Path
from typing import Any, Protocol
from playwright.async_api import Page
from .answers import AnswerEngine, NeedsAttention


class ATSAdapter(Protocol):
    async def prepare(
        self, page: Page, resume: Path, engine: AnswerEngine, context: str
    ) -> list[dict[str, Any]]: ...
    async def submit(self, page: Page) -> None: ...
    async def verify(self, page: Page) -> bool: ...


class GenericFormAdapter:
    submit_pattern = re.compile(r"^(?:submit application|submit|apply|send application|откликнуться|отправить|отправить отклик|отправить заявку)$", re.I)
    confirmation_pattern = re.compile(
        r"application (?:has been |was )?(?:submitted|received)|thank you for applying|thanks for applying|successfully applied|(?:отклик|заявка|резюме) (?:успешно )?(?:отправлен[ао]?|получен[ао]?)|спасибо за (?:отклик|заявку)",
        re.I,
    )

    async def guard(self, page: Page) -> None:
        for frame in page.frames:
            text = (await frame.locator("body").inner_text())[:100000]
            if re.search(
                r"verify (?:you are|your identity)|complete (?:the |this )?captcha|security check|verify.*human",
                text,
                re.I,
            ):
                raise NeedsAttention("Требуется пройти CAPTCHA или подтвердить личность")
            challenges = frame.locator(
                'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="challenges.cloudflare"], input[type="password"]'
            )
            for i in range(await challenges.count()):
                if await challenges.nth(i).is_visible():
                    raise NeedsAttention("Требуется вход или проверка защиты от ботов")

    async def cover_letter(self, page: Page, engine: AnswerEngine, context: str, question: str) -> tuple[bytes, dict[str, Any]]:
        """Render a factual cover letter as an ATS-compatible PDF in the existing browser."""
        answer = await engine.answer("Сопроводительное письмо" if re.search(r"[а-яё]", question, re.I) else "Cover letter", context)
        document = await page.context.new_page()
        try:
            await document.set_content(
                '<html><head><meta charset="utf-8"></head><body style="font:14px Arial;line-height:1.6;padding:36px">'
                + "".join(f"<p>{escape(p)}</p>" for p in answer["answer"].split("\n") if p.strip())
                + "</body></html>"
            )
            return await document.pdf(format="A4", print_background=True), answer
        finally:
            await document.close()

    async def prepare(
        self, page: Page, resume: Path, engine: AnswerEngine, context: str
    ) -> list[dict[str, Any]]:
        await self.guard(page)
        if await page.locator("input[type=file]").count() == 0:
            opener = page.get_by_role("button", name=re.compile(r"^apply(?: for this job| now)?$", re.I))
            if await opener.count() == 1:
                safe_opener = await opener.evaluate(
                    "el => el.type === 'button' && !el.form && !el.closest('form')"
                )
                if safe_opener:
                    await opener.click()
                    await page.wait_for_timeout(500)
        evidence: list[dict[str, Any]] = []
        uploads = 0
        for frame in page.frames:
            controls = frame.locator("input, textarea, select, [role=combobox]")
            for i in range(await controls.count()):
                control = controls.nth(i)
                meta = await control.evaluate("""el => ({tag:el.tagName.toLowerCase(), type:el.type || '', name:el.name || '',
                    role:el.getAttribute('role'), required:el.required || el.getAttribute('aria-required') === 'true',
                    label: Array.from(el.labels || []).map(x=>x.innerText).join(' ') || el.getAttribute('aria-label') ||
                    (el.getAttribute('aria-labelledby') || '').split(' ').map(id=>document.getElementById(id)?.innerText || '').join(' ') ||
                    el.placeholder || el.name || '', options:el.tagName === 'SELECT' ? Array.from(el.options).map(o=>({label:o.label,value:o.value})) : []})""")
                kind = meta["type"]
                if kind in ("hidden", "submit", "button", "reset") or not await control.is_enabled():
                    continue
                question = re.sub(r"\s*\*\s*$", "", meta["label"]).strip()
                if kind == "file":
                    if re.search(r"cover.?letter|сопроводительн", question, re.I):
                        letter, letter_answer = await self.cover_letter(page, engine, context, question)
                        await control.set_input_files({"name": "cover-letter.pdf", "mimeType": "application/pdf", "buffer": letter})
                        evidence.append(letter_answer)
                        continue
                    if re.search(r"photo|additional|other|фото|дополнительн|друг", question, re.I):
                        if meta["required"]:
                            raise NeedsAttention(f"Не загружен обязательный документ: {question}")
                        continue
                    if not re.search(r"resume|cv|резюме", question, re.I):
                        raise NeedsAttention(f"Не удалось определить назначение загрузки файла: {question}")
                    await control.set_input_files(str(resume))
                    uploads += 1
                    continue
                if not await control.is_visible():
                    continue
                if kind == "radio":
                    # Group label (fieldset legend) contains the question; individual label is the choice.
                    group = await control.evaluate(
                        "el=>el.closest('fieldset')?.querySelector('legend')?.innerText || ''"
                    )
                    if not group:
                        if meta["required"]:
                            raise NeedsAttention(f"У группы вариантов нет доступного текста вопроса: {question}")
                        continue
                    answer = await engine.answer(group, context)
                    if answer["answer"].strip().lower() == question.strip().lower():
                        await control.check()
                        evidence.append(answer)
                    continue
                try:
                    answer = await engine.answer(question, context)
                except NeedsAttention:
                    if meta["required"]:
                        raise
                    continue
                value = answer["answer"]
                if meta["tag"] == "select":
                    options = [
                        o for o in meta["options"] if o["label"].strip().lower() == value.strip().lower()
                    ]
                    if len(options) != 1:
                        raise NeedsAttention(f"Нет точного варианта ответа на вопрос: {question}")
                    await control.select_option(value=options[0]["value"])
                elif kind == "checkbox":
                    if value.lower() not in {"yes", "no", "true", "false", "да", "нет"}:
                        raise NeedsAttention(f"Нужен явный ответ «да» или «нет»: {question}")
                    await control.set_checked(value.lower() in {"yes", "true", "да"})
                elif meta["role"] == "combobox":
                    await control.fill(value)
                    option = frame.get_by_role("option", name=value, exact=True)
                    try:
                        await option.wait_for(state="visible", timeout=3000)
                    except Exception as exc:
                        raise NeedsAttention(f"Не удалось выбрать подтверждённый вариант: {question}") from exc
                    await option.click()
                else:
                    await control.fill(value)
                evidence.append(answer)
        if not uploads:
            raise NeedsAttention("В форме не найдено поле загрузки резюме")
        await self.guard(page)
        for frame in page.frames:
            invalid = await frame.locator("input:invalid, textarea:invalid, select:invalid").count()
            if invalid:
                raise NeedsAttention("Не все обязательные поля заполнены корректно; проверьте снимок формы")
        return evidence

    async def submit(self, page: Page) -> None:
        await self.guard(page)
        buttons = []
        for frame in page.frames:
            found = frame.get_by_role("button", name=self.submit_pattern)
            for i in range(await found.count()):
                item = found.nth(i)
                if await item.is_visible() and await item.is_enabled():
                    buttons.append(item)
        if len(buttons) != 1:
            raise NeedsAttention("Не удалось однозначно определить кнопку отправки отклика")
        # No retry here: click may send a request even if Playwright raises a timeout.
        await buttons[0].click(timeout=15000, no_wait_after=True)

    async def verify(self, page: Page) -> bool:
        for _ in range(20):
            for frame in page.frames:
                confirmation = frame.get_by_text(self.confirmation_pattern)
                for i in range(await confirmation.count()):
                    if await confirmation.nth(i).is_visible():
                        # Success must coexist with removal of the active submission form.
                        active = frame.get_by_role("button", name=self.submit_pattern)
                        if not any([await active.nth(n).is_visible() for n in range(await active.count())]):
                            return True
            await page.wait_for_timeout(500)
        return False


class GreenhouseAdapter(GenericFormAdapter):
    submit_pattern = GenericFormAdapter.submit_pattern


class AshbyAdapter(GenericFormAdapter):
    submit_pattern = GenericFormAdapter.submit_pattern


class LeverAdapter(GenericFormAdapter):
    submit_pattern = GenericFormAdapter.submit_pattern


ATS_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter, "lever": LeverAdapter}
