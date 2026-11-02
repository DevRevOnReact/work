"""Read-only desktop/mobile smoke check for a running keyless local workspace."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "data/ui-check"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:3000", wait_until="networkidle")
        page.get_by_role("heading", name="Ваши поиски", exact=True).wait_for()
        page.get_by_text("Загружаем поиски…", exact=True).wait_for(state="hidden")
        assert page.locator('input[name="token"]').count() == 0
        blocked = page.request.post("http://127.0.0.1:3000/api/backend/searches/setup",
                                    headers={"Origin": "https://unrelated.example"}, data={})
        assert blocked.status == 403
        page.screenshot(path=str(output / "searches-desktop.png"), full_page=True)
        if page.locator(".search-row").count():
            page.get_by_role("button", name="Настроить", exact=True).first.click()
            page.get_by_role("heading", name="Настройки поиска", exact=True).wait_for()
            page.get_by_role("button", name="Удалённо", exact=True).click()
            expect(page.get_by_role("button", name="Удалённо", exact=True)).to_have_attribute("aria-pressed", "true")
            page.get_by_label("Убрать Формат: Удалённо", exact=True).click()
            expect(page.get_by_role("button", name="Любой формат", exact=True)).to_have_attribute("aria-pressed", "true")
            page.screenshot(path=str(output / "filters-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "filters-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.get_by_role("button", name="Отмена", exact=True).click()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="Компании", exact=True).click()
        page.locator(".company-card").first.wait_for()
        page.get_by_label("Поиск компаний", exact=True).fill("GitLab")
        expect(page.locator(".company-card")).to_have_count(1)
        page.get_by_label("Сигнал").select_option("referrals")
        expect(page.locator(".company-card")).to_have_count(1)
        page.screenshot(path=str(output / "companies-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "companies-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        for name in ["Резюме и профиль"]:
            page.get_by_role("button", name=name, exact=True).click()
            page.get_by_role("heading", name=name, exact=True).wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert not errors, errors
        browser.close()
    print(json.dumps({"pages": 4, "keyless": True, "csrf": True, "filters": True,
                      "company_search": True, "mobile": True, "browser_errors": errors}))


if __name__ == "__main__":
    main()
