"""Read-only top page UI smoke with controlled vacancy data."""
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright, expect


def main():
    stamp = datetime.now(UTC).isoformat()
    items = [{"id": str(i), "title": f"Frontend Engineer {i}", "company": "Example IT", "location": "Remote · Europe",
              "score": 90 if i == 0 else 60, "provider": "ashby", "canonical_url": f"https://example.com/job/{i}",
              "published_at": stamp, "salary": None, "eligibility": "unknown", "resume_filename": "frontend.txt", "search_name": "Frontend"} for i in range(21)]
    state = {"refreshed": False}
    def route_api(route):
        parsed = urlsplit(route.request.url)
        path = parsed.path.split('/api/backend/', 1)[1]
        if path == 'top-vacancies':
            params = parse_qs(parsed.query)
            offset = int(params.get('offset', ['0'])[0])
            selected = [dict(items[0], provider='linkedin', canonical_url='https://www.linkedin.com/jobs/view/1234567890')] if params.get('provider') == ['linkedin'] else items
            value = {"total": len(selected), "items": selected[offset:offset+20], "window_hours": 72, "min_score": 60}
        elif path == 'notifications/status':
            value = {"telegram": True, "telegram_counts": {"sent": 21}, "last_error": None}
        elif path == 'discover':
            state['refreshed'] = True
            value = {"status": "queued", "searches": 1}
        else:
            route.continue_(); return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(value))
    output = Path(__file__).resolve().parents[1] / 'data/ui-check'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/backend/**', route_api)
        page.goto('http://127.0.0.1:3000', wait_until='networkidle')
        page.get_by_role('button', name='Топ за 3 дня', exact=True).click()
        expect(page).to_have_url('http://127.0.0.1:3000/top')
        expect(page.get_by_role('heading', name='Топ за 3 дня', exact=True)).to_be_visible()
        expect(page.locator('.top-vacancy')).to_have_count(20)
        expect(page.locator('.top-vacancy').first.locator('.score')).to_contain_text('90')
        expect(page.locator('.top-vacancy').first.get_by_role('link', name='Открыть вакансию ↗')).to_have_attribute('href', 'https://example.com/job/0')
        page.screenshot(path=str(output / 'top-desktop.png'), full_page=True)
        page.get_by_role('button', name='Далее', exact=True).click()
        expect(page.locator('.top-vacancy')).to_have_count(1)
        page.get_by_label('Источник вакансий').select_option('linkedin')
        expect(page.locator('.top-vacancy')).to_have_count(1)
        expect(page.locator('.top-vacancy').first.get_by_role('link', name='Открыть вакансию ↗')).to_have_attribute('href', 'https://www.linkedin.com/jobs/view/1234567890')
        expect(page.get_by_text('Публичная выдача LinkedIn', exact=False)).to_be_visible()
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('heading', name='Топ за 3 дня', exact=True)).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.screenshot(path=str(output / 'top-mobile.png'), full_page=True)
        page.get_by_role('button', name='Обновить подборку', exact=True).click()
        expect(page.get_by_text('Обновление подборки запущено', exact=True)).to_be_visible()
        assert state['refreshed']
        page.get_by_role('button', name='Компании', exact=True).click()
        expect(page.get_by_role('heading', name='Компании', exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
    print(json.dumps({"top_route": True, "threshold": True, "links": True, "pagination": True,
                      "reload": True, "refresh": True, "mobile": True, "browser_errors": errors}))


if __name__ == '__main__':
    main()
