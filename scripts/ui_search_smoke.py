"""UI regressions against controlled API fixtures; never changes user records."""
import json
from datetime import UTC, datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    stamp = datetime.now(UTC).isoformat()
    state = {"search_polls": 0, "discovery_polls": 0, "discovering": False, "company_runs": 0, "failed": False}
    base = {"id": "smoke-search", "name": "Проверка UI", "resume_id": "smoke-resume", "enabled": True,
            "filters": {"company": "Acme"}, "result_count": 0, "errors": [], "results": [],
            "started_at": stamp, "completed_at": None, "next_run_at": stamp, "schedule_timezone": "Europe/Moscow"}
    def progress(stage, message, done=0):
        return {"stage": stage, "message": message, "total": 2, "done": done, "updated_at": stamp,
                "events": [{"at": stamp, "message": "Acme: запрос к ashby", "level": "info"},
                           {"at": stamp, "message": message, "level": "error" if stage == "failed" else "info"}]}
    def search():
        if state["failed"]:
            return {**base, "status": "failed", "completed_at": stamp, "errors": ["Acme: HTTP 429"],
                    "progress": progress("failed", "Лимит источника", 1)}
        done = state["search_polls"] >= 3
        return {**base, "status": "complete" if done else "running", "completed_at": stamp if done else None,
                "progress": progress("complete" if done else "fetching", "Готово: 0 подходящих вакансий за сегодня" if done else "Загружаем источники: 2", 2 if done else 1)}
    def handler(route):
        path = route.request.url.split("/api/backend/", 1)[1].split("?", 1)[0]
        method = route.request.method
        if path == "resumes":
            result = [{"id": "smoke-resume", "filename": "smoke.txt", "variant": "general", "text_length": 100, "parse_warnings": []}]
        elif path == "searches":
            state["search_polls"] += 1
            result = [search()]
        elif path == "searches/smoke-search":
            result = search()
        elif path == "searches/smoke-search/run":
            state["company_runs"] += 1
            state["search_polls"] = 0
            result = {"queued": True}
        elif path == "companies":
            result = [{"id": "smoke-company", "name": "Acme", "careers_url": "https://example.com/careers", "signals": [], "contacts": [],
                       "sources": [{"id": "smoke-source", "company": "Acme", "provider": "ashby", "board": "acme", "enabled": True, "error": None}]}]
        elif path == "companies/discover/start":
            assert method == "POST" and route.request.post_data_json["country"] == "Worldwide"
            state["discovering"] = True
            result = {"id": "smoke-discovery", "status": "queued"}
        elif path == "companies/discoveries":
            if not state["discovering"]:
                result = []
            else:
                state["discovery_polls"] += 1
                done = state["discovery_polls"] >= 3
                result = [{"id": "smoke-discovery", "country": "Worldwide", "page": 1, "status": "complete" if done else "running",
                           "started_at": stamp, "completed_at": stamp if done else None,
                           "result": {"found": 2, "added": 1, "errors": [], "next_page": None} if done else {},
                           "progress": progress("complete" if done else "catalog", "Готово: найдено компаний 2, новых 1" if done else "Проверяем мировой каталог IT", 2 if done else 1)}]
        else:
            route.continue_()
            return
        route.fulfill(status=202 if path == "companies/discover/start" else 200, content_type="application/json", body=json.dumps(result))
    output = Path(__file__).resolve().parents[1] / "data/ui-check"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/backend/**", handler)
        page.goto("http://127.0.0.1:3000", wait_until="networkidle")
        expect(page.get_by_role("progressbar")).to_have_attribute("value", "1")
        expect(page.locator(".search-log")).to_contain_text("Acme: запрос к ashby")
        page.screenshot(path=str(output / "search-progress-desktop.png"), full_page=True)
        expect(page.get_by_role("progressbar")).to_have_attribute("value", "2", timeout=15000)
        state["failed"] = True
        page.reload(wait_until="networkidle")
        expect(page.locator(".search-progress .notice.error")).to_have_text("Acme: HTTP 429")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(output / "search-progress-mobile.png"), full_page=True)
        state["failed"] = False
        page.get_by_role("button", name="Компании", exact=True).click()
        expect(page.get_by_label("Регион поиска")).to_have_value("Worldwide")
        expect(page.get_by_label("Регион поиска").locator('option[value="United States"]')).to_have_count(1)
        expect(page.get_by_label("Регион поиска").locator('option[value="Japan"]')).to_have_count(1)
        page.get_by_role("button", name="Найти новые компании", exact=True).click()
        expect(page.locator(".search-progress > p[role=status]")).to_contain_text("Проверяем мировой каталог IT")
        expect(page.locator(".search-progress > p[role=status]")).to_contain_text("Готово: найдено компаний 2, новых 1", timeout=15000)
        page.screenshot(path=str(output / "company-discovery-mobile.png"), full_page=True)
        page.get_by_role("button", name="Найти вакансии", exact=True).click()
        expect(page.get_by_role("heading", name="Ваши поиски", exact=True)).to_be_visible()
        assert state["company_runs"] == 1
        assert not errors, errors
        browser.close()
    print(json.dumps({"live_progress": True, "errors_visible": True, "worldwide_regions": True,
                      "background_discovery": True, "company_job_search": True, "mobile": True, "browser_errors": errors}))


if __name__ == "__main__":
    main()
