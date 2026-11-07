from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from jobpilot.application_errors import ApplicationSiteError, check_response, retry_seconds
from jobpilot.ats import GenericFormAdapter


def test_retry_after_supports_seconds_dates_and_untrusted_headers():
    current = datetime(2026, 11, 7, 12, tzinfo=UTC)
    assert retry_seconds("90", current) == 90
    assert retry_seconds("Sat, 07 Nov 2026 12:02:00 GMT", current) == 120
    assert retry_seconds("Sat, 07 Nov 2026 11:02:00 GMT", current) == 0
    for value in ("", "invalid", "-5", "nan", "inf"):
        assert retry_seconds(value, current) == 0
    assert retry_seconds("9" * 10000, current) == 7 * 86400


@pytest.mark.parametrize("status,transient", [(400, False), (401, False), (403, False), (404, False),
                                           (410, False), (408, True), (429, True), (500, True), (503, True)])
def test_http_failures_have_explicit_classification(status, transient):
    with pytest.raises(ApplicationSiteError) as caught:
        check_response(SimpleNamespace(status=status, headers={"retry-after": "120"}))
    assert caught.value.status == status and caught.value.transient is transient
    assert caught.value.retry_after == 120
    check_response(SimpleNamespace(status=200, headers={}))
    check_response(None)


async def test_navigation_http_error_stops_before_form_and_successful_reload_clears_it():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        adapter = GenericFormAdapter()
        adapter.watch(page)
        status = 503

        async def response(route):
            await route.fulfill(status=status, content_type="text/html", body="<h1>Local fixture</h1>",
                                headers={"Retry-After": "90"})

        await page.route("**/*", response)
        await page.goto("https://fixture.invalid/application")
        with pytest.raises(ApplicationSiteError) as caught:
            await adapter.guard(page)
        assert caught.value.status == 503
        status = 200
        await page.reload()
        await adapter.guard(page)
        await browser.close()
