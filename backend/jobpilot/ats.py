"""Conservative, accessible form automation shared by the supported ATS adapters.

The adapter only handles the page. URL authorization, durable submit intent and
browser isolation belong to the application executor. External labels are data.
"""

import re
import time
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from playwright.async_api import Frame, Locator, Page

from .answers import LEGAL, AnswerEngine, NeedsAttention, normalize

Progress = Callable[[str, str], Awaitable[None]]


class ATSAdapter(Protocol):
    async def prepare(
        self, page: Page, resume: Path, engine: AnswerEngine, context: str
    ) -> list[dict[str, Any]]: ...
    async def submit(self, page: Page) -> None: ...
    async def verify(self, page: Page) -> bool: ...


_META = """el => {
    const labelText = node => {
      if (!node) return '';
      const copy = node.cloneNode(true);
      copy.querySelectorAll('input,textarea,select,button,[role=combobox],[role=listbox]').forEach(n => n.remove());
      return (copy.textContent || '').trim();
    };
    const labelled = (el.getAttribute('aria-labelledby') || '').split(' ')
      .map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
    const labels = Array.from(el.labels || []).map(labelText).join(' ').trim();
    const container = el.closest('fieldset,[role=group],[role=radiogroup]');
    const groupLabel = container?.querySelector('legend')?.innerText ||
      container?.getAttribute('aria-label') ||
      (container?.getAttribute('aria-labelledby') || '').split(' ')
        .map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
    const wrapper = el.closest('[data-qa],.application-question,.field,.form-field,.ashby-application-form-field-entry');
    const radios = el.type === 'radio' ? Array.from((el.form || document).querySelectorAll('input[type=radio]'))
      .filter(radio => radio.name === el.name) : [];
    return {tag:el.tagName.toLowerCase(), type:el.type || '', name:el.name || '',
      id:el.id || '', role:el.getAttribute('role'), value:el.value ?? (el.isContentEditable ? el.innerText : ''),
      displayValue:el.getAttribute('aria-valuetext') || el.getAttribute('data-value') ||
        (el.getAttribute('role') === 'combobox' ? el.innerText : '') || '',
      checked:!!el.checked || el.getAttribute('aria-checked') === 'true',
      selectedValues:el.tagName === 'SELECT' ? Array.from(el.selectedOptions).map(o=>o.value) : [],
      files: Array.from(el.files || []).map(f => ({name:f.name,size:f.size,type:f.type,lastModified:f.lastModified})),
      required:el.required || el.getAttribute('aria-required') === 'true' || container?.getAttribute('aria-required') === 'true',
      label:labels || el.getAttribute('aria-label') || labelled ||
        labelText(wrapper?.querySelector('label')) || el.placeholder || el.name || '',
      group:groupLabel || '', accept:el.accept || '',
      options:el.tagName === 'SELECT' ? Array.from(el.options)
        .filter(o => !o.disabled).map(o => ({label:o.label,value:o.value})) : radios
        .map(r => ({label:Array.from(r.labels || []).map(labelText).join(' ') || r.getAttribute('aria-label') || '',value:r.value}))};
}"""


class GenericFormAdapter:
    submit_pattern = re.compile(
        r"^(?:submit application|submit your application|submit|apply|apply now|"
        r"send application|send your application|откликнуться|отправить|отправить отклик|отправить заявку)$",
        re.IGNORECASE,
    )
    next_pattern = re.compile(
        r"^(?:next(?: step)?|continue|continue application|review(?: application)?|"
        r"далее|продолжить|следующий шаг|проверить заявку)$", re.IGNORECASE,
    )
    opener_pattern = re.compile(
        r"^\s*(?:apply(?: (?:for|to) (?:this |the )?(?:job|position|role)| now| for job| online| here| externally|"
        r" on (?:company|employer)(?:'s)? (?:website|site))?|start (?:your |an? )?application|"
        r"application|откликнуться(?: на вакансию)?|подать заявку|заполнить анкету)[\s!›→↗»]*$", re.IGNORECASE,
    )
    confirmation_pattern = re.compile(
        r"application (?:has been |was )?(?:successfully )?(?:submitted|received)|"
        r"thank you for applying|thanks for applying|successfully applied|"
        r"(?:отклик|заявка|резюме) (?:успешно )?(?:отправлен[ао]?|получен[ао]?)|"
        r"спасибо за (?:отклик|заявку)", re.IGNORECASE,
    )
    control_selector = "input, textarea, select, [role=combobox], [role=checkbox], [contenteditable=true][role=textbox]"
    max_steps = 12

    def __init__(self, progress: Progress | None = None, navigation_hosts: set[str] | None = None) -> None:
        self.progress = progress
        self.navigation_hosts = navigation_hosts or set()
        self._opened_urls: set[str] = set()
        self.resume_path: Path | None = None
        self._before_confirmation: set[str] | None = None
        self._submit_started = False
        self.prepared_answers: list[dict[str, Any]] = []
        self.answer_engine: AnswerEngine | None = None
        self.answer_context = ""

    async def report(self, step: str, detail: str) -> None:
        if self.progress:
            await self.progress(step, detail)

    async def guard(self, page: Page) -> None:
        for frame in page.frames:
            if frame.is_detached() or not await frame.locator("body").count():
                continue
            text = (await frame.locator("body").inner_text())[:100000]
            if re.search(
                r"verify (?:you are|your identity)|complete (?:the |this )?captcha|"
                r"security check|verify.{0,30}human|enter.{0,25}(?:verification|authentication) code|"
                r"two.factor authentication|multi.factor authentication|"
                r"please enable js and disable any ad blocker|"
                r"подтвердите.{0,25}(?:личность|человек)|введите.{0,20}код подтверждения",
                text, re.IGNORECASE,
            ):
                raise NeedsAttention("Требуется CAPTCHA, MFA или подтверждение личности в браузере")
            challenges = frame.locator(
                'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
                'iframe[src*="challenges.cloudflare"], input[type="password"], '
                'iframe[src*="captcha-delivery.com"], iframe[src*="arkoselabs.com"], '
                'iframe[src*="funcaptcha"], iframe[title*="captcha" i], '
                'input[autocomplete="one-time-code"]'
            )
            for i in range(await challenges.count()):
                if await challenges.nth(i).is_visible():
                    raise NeedsAttention("Требуется вход или проверка защиты от ботов в браузере")

    async def cover_letter(
        self, page: Page, engine: AnswerEngine, context: str, question: str
    ) -> tuple[bytes, dict[str, Any]]:
        """Render factual cover-letter evidence using the current Chromium."""
        answer = await engine.answer(
            "Сопроводительное письмо" if re.search(r"[а-яё]", question, re.IGNORECASE) else "Cover letter", context
        )
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

    async def resume_upload(self, page: Page, resume: Path, accept: str) -> Path:
        """Use a PDF of the prepared document when the form only accepts PDF."""
        accepted = {item.strip().lower() for item in accept.split(",") if item.strip()}
        pdf = {".pdf", "application/pdf"}
        docx = {".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "*/*", "application/*"}
        if resume.suffix.lower() != ".docx" or not accepted.intersection(pdf) or accepted.intersection(docx):
            return resume
        from .resumes import parse_resume
        text = parse_resume(resume.read_bytes(), resume.name).text
        document = await page.context.new_page()
        try:
            await document.set_content(
                '<html><head><meta charset="utf-8"></head><body style="font:11pt Arial;line-height:1.5">'
                + "".join(f'<p style="margin:0 0 6pt">{escape(line)}</p>' for line in text.splitlines())
                + '</body></html>'
            )
            path = resume.with_suffix(".pdf")
            await document.pdf(path=str(path), format="A4", margin={"top": "18mm", "bottom": "18mm", "left": "18mm", "right": "18mm"})
            return path
        finally:
            await document.close()

    @staticmethod
    def question(meta: dict[str, Any]) -> str:
        return re.sub(r"\s*(?:\*|\(required\)|обязательное поле)\s*$", "", meta["label"], flags=re.IGNORECASE).strip()[:2000]

    async def inspect(self, page: Page) -> list[dict[str, Any]]:
        """Read field metadata without writing, clicking or uploading anything."""
        fields = []
        for frame in page.frames:
            if frame.is_detached():
                continue
            controls = frame.locator(self.control_selector)
            for i in range(await controls.count()):
                control = controls.nth(i)
                meta = await control.evaluate(_META)
                if meta["type"] in {"hidden", "submit", "button", "reset"} and meta["role"] not in {"combobox", "checkbox"}:
                    continue
                if meta["type"] != "file" and not await control.is_visible():
                    continue
                if meta["type"] == "file" and await control.evaluate(
                    "el => !!el.closest('[hidden],[aria-hidden=true],fieldset[disabled]')"
                ):
                    continue
                fields.append({
                    "question": self.question(meta), "type": meta["type"] or meta["role"] or meta["tag"],
                    "required": meta["required"], "options": [o["label"] for o in meta["options"]],
                    "optionValues": [o["value"] for o in meta["options"]],
                    "identifier": meta["id"] or meta["name"] or f"field-{i}", "frame_url": frame.url,
                    "value": meta["value"], "checked": meta["checked"],
                    "displayValue": meta["displayValue"],
                    "selectedValues": meta["selectedValues"], "files": meta["files"],
                })
        return fields

    async def snapshot(self, page: Page) -> list[dict[str, Any]]:
        """Capture approval-relevant values, including content hashes of uploads.

        Call again just before the durable submit intent and require equality.
        Hidden infrastructure inputs (CSRF tokens, ATS tracking) are excluded.
        """
        from hashlib import sha256

        fields = []
        for frame_index, frame in enumerate(page.frames):
            if frame.is_detached():
                continue
            controls = frame.locator(self.control_selector)
            for index in range(await controls.count()):
                control = controls.nth(index)
                meta = await control.evaluate(_META)
                if meta["type"] in {"hidden", "submit", "button", "reset"} and meta["role"] not in {"combobox", "checkbox"}:
                    continue
                if meta["type"] != "file" and not await control.is_visible():
                    continue
                files = []
                for file_index, descriptor in enumerate(meta["files"]):
                    # Files are already selected in this local browser. Keep bytes
                    # in-process and persist only hashes, never their content.
                    digest = await control.evaluate("""async (el, i) => {
                      if (!globalThis.crypto?.subtle) return null;
                      const digest = await crypto.subtle.digest('SHA-256', await el.files[i].arrayBuffer());
                      return Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');
                    }""", file_index)
                    if digest is None:
                        # about:blank fixtures lack Web Crypto. Bound memory even
                        # there rather than serializing a whole 10 MB document.
                        fallback = sha256()
                        for offset in range(0, descriptor["size"], 65536):
                            chunk = await control.evaluate("""async (el, args) => Array.from(new Uint8Array(
                              await el.files[args[0]].slice(args[1], args[1] + 65536).arrayBuffer()))""",
                                                           [file_index, offset])
                            fallback.update(bytes(chunk))
                        digest = fallback.hexdigest()
                    files.append({key: value for key, value in {**descriptor, "sha256": digest}.items()
                                  if key != "lastModified"})
                fields.append({
                    "frame": frame_index, "frame_url": frame.url,
                    "identifier": meta["id"] or meta["name"] or f"field-{index}",
                    "question": self.question(meta), "type": meta["type"] or meta["role"] or meta["tag"],
                    "required": meta["required"], "disabled": not await control.is_enabled(),
                    "value": meta["value"] if meta["type"] != "file" else "",
                    "displayValue": meta["displayValue"],
                    "checked": meta["checked"], "selectedValues": meta["selectedValues"],
                    "options": meta["options"], "files": files,
                })
        return fields

    async def _buttons(self, page: Page, pattern: re.Pattern[str]) -> list[Locator]:
        buttons = []
        for frame in page.frames:
            if frame.is_detached():
                continue
            found = frame.get_by_role("button", name=pattern)
            for i in range(await found.count()):
                item = found.nth(i)
                if await item.is_visible() and await item.is_enabled():
                    buttons.append(item)
        return buttons

    def can_navigate(self, destination: str, source: str) -> bool:
        target, origin = urlsplit(destination), urlsplit(source)
        try:
            valid = (not target.username and not target.password and target.port in (None, 443))
            same_origin = target.scheme == origin.scheme and target.netloc == origin.netloc
            # Local HTTP fixtures may navigate within their own origin. Production
            # requests also pass through the executor's HTTPS/public-IP guard.
            return ((same_origin and target.scheme in {"http", "https"} and not target.username and not target.password)
                    or (valid and target.scheme == "https" and target.hostname in self.navigation_hosts))
        except ValueError:
            return False

    async def _open(self, page: Page) -> bool:
        await self._decline_optional_cookies(page)
        fields = await self.inspect(page)
        if any(field["type"] == "file" for field in fields) or (fields and await self._buttons(page, self.next_pattern)):
            return False
        if fields:
            for button in await self._buttons(page, self.submit_pattern):
                if await button.evaluate("el => !!(el.form || el.closest('form'))"):
                    return False
        links: dict[str, Locator] = {}
        for frame in page.frames:
            # A link may be exposed as a button/tab by the ATS accessibility tree.
            found = frame.get_by_role("link", name=self.opener_pattern).or_(
                frame.locator("a[href]").filter(has_text=self.opener_pattern))
            for i in range(await found.count()):
                item = found.nth(i)
                if not await item.is_visible():
                    continue
                href = await item.evaluate("el => el.href")
                if self.can_navigate(href, frame.url) and href not in self._opened_urls:
                    links.setdefault(href, item)
        if len(links) == 1:
            href = next(iter(links))
            self._opened_urls.add(href)
            await self.report("opening_form", "Открытие страницы формы отклика")
            # Keep target=_blank application links in the guarded agent session.
            await page.goto(href, wait_until="domcontentloaded")
            await page.wait_for_timeout(300)
            return True
        if len(links) > 1:
            raise NeedsAttention("На странице несколько разных ссылок отклика; нельзя однозначно выбрать вакансию")
        openers = await self._buttons(page, self.opener_pattern)
        for frame in page.frames:
            tabs = frame.get_by_role("tab", name=self.opener_pattern)
            for index in range(await tabs.count()):
                if await tabs.nth(index).is_visible() and await tabs.nth(index).is_enabled():
                    openers.append(tabs.nth(index))
        safe_openers = [item for item in openers if await item.evaluate(
            "el => !el.matches('a[href]') && !el.form && !el.closest('form')")]
        names = {normalize(await item.inner_text()) for item in safe_openers}
        if safe_openers and len(names) == 1:
            await self.report("opening_form", "Открытие формы отклика")
            previous_pages = set(page.context.pages)
            await safe_openers[0].click()
            await page.wait_for_timeout(300)
            opened = [item for item in page.context.pages if item not in previous_pages]
            if opened:
                try:
                    if len(opened) != 1:
                        raise NeedsAttention("Apply открыл несколько страниц; выберите форму вручную")
                    popup = opened[0]
                    await popup.wait_for_load_state("domcontentloaded", timeout=10000)
                    target = popup.url
                    if not self.can_navigate(target, page.url) or target in self._opened_urls:
                        raise NeedsAttention("Разрешите домен формы из Apply в настройках откликов")
                    self._opened_urls.add(target)
                    await page.goto(target, wait_until="domcontentloaded")
                finally:
                    for popup in opened:
                        await popup.close()
            return True
        return False

    async def _decline_optional_cookies(self, page: Page) -> None:
        """Reject optional tracking without accepting unapproved declarations."""
        for frame in page.frames:
            banners = frame.locator(
                '[role="dialog"][aria-label*="cookie" i], [data-ui="cookie-consent"], #onetrust-banner-sdk'
            )
            for index in range(await banners.count()):
                banner = banners.nth(index)
                if not await banner.is_visible():
                    continue
                reject = banner.get_by_role("button", name=re.compile(
                    r"^(?:decline all|reject all|reject optional(?: cookies)?|only necessary(?: cookies)?|"
                    r"отклонить все|только необходимые)$", re.IGNORECASE
                ))
                if await reject.count() == 1 and await reject.is_visible() and await reject.is_enabled():
                    await self.report("declining_optional_cookies", "Отклоняем необязательные cookies")
                    await reject.click(timeout=5000)
                else:
                    raise NeedsAttention("Закройте диалог cookies в браузере; согласие автоматически не предоставляется")

    async def wait_for_form(self, page: Page) -> None:
        """Wait for a hydrated form instead of inspecting an empty ATS shell."""
        from playwright.async_api import TimeoutError as BrowserTimeout

        deadline = time.monotonic() + 10
        transitions = 0
        await self.report("waiting_for_form", "Ждём загрузки формы работодателя")
        while True:
            await self.guard(page)
            if await self._open(page):
                transitions += 1
                if transitions > 5:
                    raise NeedsAttention("Форма не найдена после нескольких переходов; проверьте страницу вакансии")
                deadline = time.monotonic() + 10
            fields = await self.inspect(page)
            if any(field["type"] == "file" for field in fields):
                return
            if fields and await self._buttons(page, self.next_pattern):
                return
            # Text-only forms are useful for a manual handoff too. Never click
            # their Apply/Submit button as if it opened another page.
            if fields and await self._buttons(page, self.submit_pattern):
                return
            if time.monotonic() >= deadline:
                if await self._buttons(page, self.submit_pattern):
                    raise NeedsAttention("На странице есть отправка, но форма резюме недоступна; требуется проверка")
                raise BrowserTimeout("Форма работодателя не загрузилась вовремя")
            await page.wait_for_timeout(250)

    @staticmethod
    def _attention(message: str, missing: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> NeedsAttention:
        error = NeedsAttention(message)
        # Public executor can preserve already generated answers and all unanswered questions.
        error.questions = missing  # type: ignore[attr-defined]
        error.answers = evidence  # type: ignore[attr-defined]
        return error

    async def _fill(
        self, page: Page, frame: Frame, control: Locator, meta: dict[str, Any],
        resume: Path, engine: AnswerEngine, context: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        kind = meta["type"]
        question = self.question(meta)
        if not question:
            if meta["required"]:
                raise NeedsAttention("Обязательное поле не имеет доступной подписи")
            return None, False
        if kind == "file":
            if re.search(r"cover.?letter|сопроводительн", question, re.IGNORECASE):
                letter, answer = await self.cover_letter(page, engine, context, question)
                await control.set_input_files({"name": "cover-letter.pdf", "mimeType": "application/pdf", "buffer": letter})
                await self.report("uploading_document", "Загружено сопроводительное письмо")
                return answer, False
            if re.search(r"photo|additional|other|фото|дополнительн|друг", question, re.IGNORECASE):
                if meta["required"]:
                    raise NeedsAttention(f"Не загружен обязательный документ: {question}")
                return None, False
            if not re.search(r"resume|curriculum|\bcv\b|резюме", question, re.IGNORECASE):
                raise NeedsAttention(f"Не удалось определить назначение загрузки файла: {question}")
            self.resume_path = await self.resume_upload(page, resume, meta["accept"])
            await control.set_input_files(str(self.resume_path))
            await self.report("uploading_resume", "Загружено выбранное резюме")
            return None, True
        if kind == "radio":
            group = meta["group"]
            if not group:
                if meta["required"]:
                    raise NeedsAttention(f"У группы вариантов нет доступного текста вопроса: {question}")
                return None, False
            answer = await engine.choice_answer(group.strip(), context, [option["label"] for option in meta["options"]])
            if not any(normalize(option["label"]) == normalize(str(answer["answer"])) for option in meta["options"]):
                raise NeedsAttention(f"Нет точного варианта ответа на вопрос: {group}")
            if normalize(str(answer["answer"])) == normalize(question):
                await control.check()
                return answer, False
            return None, False
        answer = await engine.choice_answer(question, context, [option["label"] for option in meta["options"]])
        value = str(answer["answer"])
        if meta["tag"] == "select":
            options = [o for o in meta["options"] if normalize(o["label"]) == normalize(value) and o["value"]]
            if len(options) != 1:
                raise NeedsAttention(f"Нет точного варианта ответа на вопрос: {question}")
            await control.select_option(value=options[0]["value"])
        elif kind == "checkbox" or meta["role"] == "checkbox":
            if normalize(value) not in {"yes", "no", "true", "false", "да", "нет"}:
                raise NeedsAttention(f"Нужен явный ответ «да» или «нет»: {question}")
            await control.set_checked(normalize(value) in {"yes", "true", "да"})
        elif meta["role"] == "combobox":
            if await control.evaluate("el => el.matches('input,textarea') || el.isContentEditable"):
                await control.fill(value)
            else:
                await control.click()
            option = frame.get_by_role("option", name=value, exact=True)
            try:
                await option.wait_for(state="visible", timeout=3000)
                await option.click()
            except Exception as exc:
                raise NeedsAttention(f"Не удалось выбрать подтверждённый вариант: {question}") from exc
        else:
            if kind == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise NeedsAttention(f"Нужна подтверждённая дата в формате YYYY-MM-DD: {question}")
            if kind == "number" and not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                raise NeedsAttention(f"Нужен точный числовой ответ: {question}")
            await control.fill(value)
        await self.report("filling_field", f"Заполнено поле: {question[:160]}")
        return answer, False

    async def _validate(self, page: Page, evidence: list[dict[str, Any]]) -> None:
        missing = []
        for frame in page.frames:
            invalid = frame.locator("input:invalid, textarea:invalid, select:invalid, [aria-invalid=true]")
            for i in range(await invalid.count()):
                item = invalid.nth(i)
                meta = await item.evaluate(_META)
                if meta["type"] == "hidden" or not await item.is_enabled():
                    continue
                if meta["type"] != "file" and not await item.is_visible():
                    continue
                missing.append({"question": self.question(meta), "type": meta["type"], "required": True,
                                "options": [o["label"] for o in meta["options"]]})
        if missing:
            raise self._attention("Не все обязательные поля заполнены корректно", missing, evidence)

    async def prepare(
        self, page: Page, resume: Path, engine: AnswerEngine, context: str
    ) -> list[dict[str, Any]]:
        self.prepared_answers = []
        self.answer_engine, self.answer_context = engine, context
        await self.guard(page)
        self._before_confirmation = await self._confirmations(page)
        self._opened_urls.clear()
        await self.wait_for_form(page)
        evidence = self.prepared_answers
        uploads = 0
        optional_missing: list[dict[str, Any]] = []
        # Track DOM node identity, not indexes: ATS components can insert new controls
        # after a select, or replace the entire current step.
        completed: set[tuple[str, str]] = set()
        for step in range(self.max_steps):
            await self.guard(page)
            await self.report("filling_form", f"Заполнение шага {step + 1}")
            missing: list[dict[str, Any]] = []
            # Multiple passes pick up conditional fields without clicking final submit.
            for _ in range(8):
                fresh = 0
                for frame in list(page.frames):
                    if frame.is_detached():
                        continue
                    controls = frame.locator(self.control_selector)
                    for i in range(await controls.count()):
                        control = controls.nth(i)
                        meta = await control.evaluate(_META)
                        if (meta["type"] in {"hidden", "submit", "button", "reset"}
                                and meta["role"] not in {"combobox", "checkbox"}) or not await control.is_enabled():
                            continue
                        if meta["type"] != "file" and not await control.is_visible():
                            continue
                        # A hidden <input type=file> is often the real upload target.
                        # Skip uploads only when their entire field is intentionally hidden.
                        if meta["type"] == "file" and await control.evaluate(
                            "el => !!el.closest('[hidden],[aria-hidden=true],fieldset[disabled]')"
                        ):
                            continue
                        identity = await control.evaluate("""el => {
                          if (!el.__jobpilotID) el.__jobpilotID = Date.now() + ':' + Math.random();
                          return el.__jobpilotID;
                        }""")
                        key = (str(id(frame)), identity)
                        if key in completed:
                            continue
                        completed.add(key)
                        fresh += 1
                        try:
                            answer, uploaded = await self._fill(page, frame, control, meta, resume, engine, context)
                            if answer and answer not in evidence:
                                evidence.append(answer)
                            uploads += int(uploaded)
                        except NeedsAttention as exc:
                            question = meta["group"] or self.question(meta)
                            populated = (meta["checked"] if meta["type"] in {"checkbox", "radio"}
                                         else bool(str(meta["value"]).strip() or meta["displayValue"].strip()))
                            declaration = bool(LEGAL.search(question) or re.search(
                                r"salary|compensation|зарплат|оклад|вознагражд", question, re.IGNORECASE
                            ))
                            field = {"question": question,
                                     "type": meta["type"] or meta["role"] or meta["tag"],
                                     "required": meta["required"],
                                     "options": [o["label"] for o in meta["options"]],
                                     "reason": str(exc)}
                            if meta["required"] or meta["type"] == "file" or (declaration and populated):
                                missing.append(field)
                            else:
                                optional_missing.append(field)
                if not fresh:
                    break
                await page.wait_for_timeout(150)
            if missing:
                raise self._attention("; ".join(dict.fromkeys(m["reason"] for m in missing))[:2000], missing + optional_missing, evidence)
            await self._validate(page, evidence)
            next_buttons = await self._buttons(page, self.next_pattern)
            if not next_buttons:
                if not uploads:
                    raise self._attention("В форме не найдено поле загрузки резюме", [], evidence)
                await self.guard(page)
                await self.validate_submission(page)
                return evidence
            if len(next_buttons) != 1:
                raise self._attention("Не удалось однозначно определить следующий шаг формы", [], evidence)
            # A Next button with native submit semantics could send the application.
            # Only unambiguous in-browser step controls are safe during preparation.
            if not await next_buttons[0].evaluate("el => el.type === 'button'"):
                raise self._attention("Кнопка следующего шага может отправить форму; требуется ручное действие", [], evidence)
            await self.report("next_step", f"Переход к шагу {step + 2}")
            await next_buttons[0].click()
            await page.wait_for_timeout(300)
        raise self._attention("Превышен предел шагов формы; требуется ручная проверка", [], evidence)

    async def handoff(self, page: Page) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read remaining questions after a failure without changing the page."""
        from .handoff import questions_and_answers
        engine = self.answer_engine
        if engine is None:
            return self.prepared_answers, []
        fields = await self.inspect(page)
        answers = list(self.prepared_answers)
        answered = {normalize(item.get("question", "")) for item in answers}
        for item in questions_and_answers([], fields, engine.profile.model_dump()):
            if normalize(item["question"]) in answered:
                continue
            try:
                answer = await engine.choice_answer(item["question"], self.answer_context, item["options"])
                answers.append(answer)
            except NeedsAttention:
                pass  # Keep an explicit unanswered question; never invent a declaration.
        return answers, fields

    async def validate_submission(self, page: Page) -> Locator:
        """Preflight before the executor commits its durable submit fence."""
        await self.guard(page)
        await self._validate(page, [])
        if self._submit_started:
            raise NeedsAttention("Отправка уже была начата; автоматический повтор запрещён")
        buttons = await self._buttons(page, self.submit_pattern)
        if len(buttons) != 1:
            raise NeedsAttention("Не удалось однозначно определить кнопку отправки отклика")
        return buttons[0]

    async def submit(self, page: Page) -> None:
        button = await self.validate_submission(page)
        self._before_confirmation = await self._confirmations(page)
        self._submit_started = True
        await self.report("submitting", "Нажатие кнопки отправки")
        # One click only. The executor has already committed the durable uncertainty boundary.
        await button.click(timeout=15000, no_wait_after=True)

    async def _confirmations(self, page: Page) -> set[str]:
        seen = set()
        for frame in page.frames:
            if frame.is_detached():
                continue
            found = frame.get_by_text(self.confirmation_pattern)
            for i in range(await found.count()):
                item = found.nth(i)
                if await item.is_visible():
                    seen.add((await item.inner_text()).strip()[:2000])
        return seen

    async def verify(self, page: Page) -> bool:
        # A redirect, HTTP 200, disappeared button, or old success text alone is
        # insufficient. Require fresh visible success evidence after our click.
        if not self._submit_started:
            return False
        for _ in range(20):
            fresh = await self._confirmations(page) - (self._before_confirmation or set())
            if fresh and not await self._buttons(page, self.submit_pattern):
                await self.report("submission_confirmed", "Сайт подтвердил получение отклика")
                return True
            await page.wait_for_timeout(500)
        return False


class GreenhouseAdapter(GenericFormAdapter):
    """Greenhouse accessible labels, native uploads and embedded board forms."""


class AshbyAdapter(GenericFormAdapter):
    """Ashby client-rendered fields and conditional screening questions."""

class LeverAdapter(GenericFormAdapter):
    """Lever application modal and standard candidate/application fields."""


class WorkableAdapter(GenericFormAdapter):
    submit_pattern = re.compile(
        r"^(?:submit application|submit your application|submit|apply|send application)$", re.IGNORECASE
    )


class SmartRecruitersAdapter(GenericFormAdapter):
    opener_pattern = re.compile(rf"(?:{GenericFormAdapter.opener_pattern.pattern}|^i.m interested$)", re.IGNORECASE)
    submit_pattern = re.compile(r"^(?:submit application|submit|send application|apply|apply now)$", re.IGNORECASE)


ATS_ADAPTERS = {
    "greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter, "lever": LeverAdapter,
    "workable": WorkableAdapter, "smartrecruiters": SmartRecruitersAdapter,
    "manual": GenericFormAdapter, "generic": GenericFormAdapter,
}
