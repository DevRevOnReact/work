"""Exercise application controls against isolated API fixtures; no employer requests.

Run the web app, then: python frontend/scripts/ui-automation-smoke.py [base-url]
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3000"
    stamp = datetime.now(UTC).isoformat()
    settings = {
        "enabled": True, "mode": "manual", "auto_apply": False, "min_score": 80,
        "daily_limit": 5, "authorized_domains": ["jobs.ashbyhq.com"], "excluded_companies": [],
        "required_approval_categories": ["legal", "salary", "personal"],
        "browser_available": True, "browser_headless": False, "supported_domains": ["jobs.ashbyhq.com"],
    }
    jobs = [{
        "id": str(i), "title": f"Frontend Engineer {i}", "company": "Fixture Employer",
        "location": "Remote worldwide", "score": 90, "provider": "ashby",
        "canonical_url": f"https://jobs.ashbyhq.com/fixture/{i}", "published_at": stamp,
        "salary": None, "eligibility": "eligible", "resume_filename": "fixture.txt", "search_name": "Frontend",
    } for i in range(4)]
    apps = {}
    actions = []
    approved_answers = []
    errors = []
    profile = {"full_name": "Fixture Candidate", "email": "fixture@example.invalid", "location": "Georgia", "work_authorization": {}}

    def create(index):
        state = ["PREPARING", "NEEDS_HUMAN_ACTION", "SUBMISSION_UNCERTAIN", "AWAITING_APPROVAL"][index]
        return {
            "id": f"fixture-{index}", "job_id": str(index), "state": state, "mode": "manual",
            "attention": "Подтвердите право на работу" if index == 1 else None,
            "progress": {"step": "preparing", "message": "Проверяем форму",
                         "questions": ["Are you authorized to work in Georgia?"] if index == 1 else [],
                         "events": [{"at": stamp, "message": "Открыта форма работодателя"}]},
            "resume_id": None, "generated_resume_url": None, "screenshot_url": None,
            "answers": [{"question": "Your React experience?", "answer": "React projects in my uploaded resume.", "evidence": ["resume.0"]}],
            "job": {key: jobs[index][key] for key in ("company", "title", "canonical_url")},
            "submit_intent_at": stamp if index == 2 else None, "submitted_at": None,
            "approval_token": "prepared-version-1", "reads": 0,
        }

    def route_api(route):
        path = urlsplit(route.request.url).path.split("/api/backend/", 1)[1]
        method = route.request.method
        body = route.request.post_data_json if method in {"POST", "PUT"} and route.request.post_data else {}
        if path == "top-vacancies":
            value = {"total": len(jobs), "items": jobs, "window_hours": 72, "min_score": 60}
        elif path == "automation/settings":
            if method == "PUT":
                settings.update(body)
            value = settings
        elif path == "automation/applications":
            if method == "POST":
                index = next(i for i, job in enumerate(jobs) if job["canonical_url"] == body["canonical_url"])
                identity = f"fixture-{index}"
                apps.setdefault(identity, create(index))
                value = apps[identity]
            else:
                value = list(apps.values())
        elif path.startswith("automation/applications/"):
            identity = path.split("/")[2]
            app = apps[identity]
            if path.endswith("/action"):
                action = body["action"]
                actions.append((identity, action))
                if action == "approve":
                    assert body["approval_token"] == app["approval_token"]
                    app.update(state="SUBMITTED", submit_intent_at=stamp, submitted_at=stamp)
                elif action == "pause":
                    app["state"] = "PAUSED"
                elif action == "resume":
                    app.update(state="AWAITING_APPROVAL", attention=None)
                elif action == "cancel":
                    app["state"] = "CANCELLED"
            elif app["state"] == "PREPARING":
                app["reads"] += 1
                if app["reads"] >= 2:
                    app["state"] = "AWAITING_APPROVAL"
            value = app
        elif path == "answers":
            approved_answers.append(body)
            value = body
        elif path == "notifications/status":
            value = {"telegram": False, "email": False, "telegram_counts": {}, "counts": {}, "last_error": None}
        elif path == "profile":
            if method == "PUT":
                profile.update(body)
            value = profile
        elif path in {"searches", "resumes"}:
            value = []
        else:
            raise AssertionError(f"Unexpected API call: {method} {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

    output = Path(__file__).resolve().parents[2] / "data" / "ui-check"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/backend/**", route_api)
        page.goto(f"{base}/top", wait_until="networkidle")
        first = page.locator(".top-vacancy").nth(0)
        first.get_by_role("button", name="Auto Apply", exact=True).click()
        expect(first.get_by_text("Ожидает разрешения на отправку", exact=True)).to_be_visible(timeout=10000)
        expect(first.get_by_text("React projects in my uploaded resume.", exact=True)).to_be_visible()
        first.get_by_role("button", name="Разрешить отправку работодателю", exact=True).click()
        expect(first.get_by_text("Отправка подтверждена", exact=True)).to_be_visible()
        expect(first.get_by_role("button", name="Разрешить отправку работодателю", exact=True)).to_have_count(0)

        second = page.locator(".top-vacancy").nth(1)
        second.get_by_role("button", name="Auto Apply", exact=True).click()
        expect(second.get_by_label("Точный вопрос работодателя")).to_have_value("Are you authorized to work in Georgia?")
        second.get_by_label("Ваш подтверждённый ответ").fill("Yes, confirmed for Georgia.")
        second.get_by_role("button", name="Сохранить подтверждённый ответ", exact=True).click()
        expect(second.get_by_text("Ответ сохранён.", exact=False)).to_be_visible()
        second.get_by_role("button", name="Продолжить подготовку", exact=True).click()
        expect(second.get_by_text("Ожидает разрешения на отправку", exact=True)).to_be_visible()
        second.get_by_role("button", name="Пауза", exact=True).click()
        expect(second.get_by_text("На паузе", exact=True)).to_be_visible()
        second.get_by_role("button", name="Продолжить подготовку", exact=True).click()
        second.get_by_role("button", name="Отменить отклик", exact=True).click()
        expect(second.get_by_text("Отменено", exact=True)).to_be_visible()

        third = page.locator(".top-vacancy").nth(2)
        third.get_by_role("button", name="Auto Apply", exact=True).click()
        expect(third.get_by_text("Результат отправки не подтверждён", exact=True)).to_be_visible()
        expect(third.get_by_role("button", name="Разрешить отправку работодателю", exact=True)).to_have_count(0)
        expect(third.get_by_role("button", name="Продолжить подготовку", exact=True)).to_have_count(0)
        third.get_by_role("button", name="Проверить подтверждение", exact=True).click()
        expect(third.get_by_text("Результат отправки не подтверждён", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "automation-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(output / "automation-mobile.png"), full_page=True)
        page.reload(wait_until="networkidle")
        page.locator(".top-vacancy").nth(0).get_by_role("button", name="Auto Apply", exact=True).click()
        expect(page.locator(".top-vacancy").nth(0).get_by_text("Отправка подтверждена", exact=True)).to_be_visible()
        assert sum(action == "approve" for _, action in actions) == 1

        page.get_by_role("button", name="Резюме и профиль", exact=True).click()
        expect(page.get_by_role("heading", name="Отклики за вас", exact=True)).to_be_visible()
        toggle = page.get_by_role("switch", name="Отправлять подходящие отклики автоматически")
        expect(toggle).not_to_be_checked()
        toggle.check()
        page.get_by_role("button", name="Использовать подтверждённые ответы без дополнительных вопросов").click()
        page.get_by_role("button", name="Включить автоотправку", exact=True).click()
        expect(page.get_by_text("● Автоотправка включена", exact=True)).to_be_visible()
        assert settings["mode"] == "autonomous" and settings["auto_apply"] is True
        assert settings["use_supported_sites"] is True
        assert settings["required_approval_categories"] == []
        page.screenshot(path=str(output / "settings-simple.png"), full_page=True)
        toggle.uncheck()
        page.get_by_role("button", name="Сохранить ручной режим", exact=True).click()
        expect(page.get_by_text("○ Ручной режим", exact=True)).to_be_visible()
        assert settings["auto_apply"] is False
        page.get_by_label("Страна", exact=True).fill("Georgia")
        page.get_by_label("Подтверждённый статус", exact=False).select_option("yes")
        page.get_by_role("button", name="Сохранить статус", exact=True).click()
        expect(page.get_by_text("Право на работу подтверждено", exact=True)).to_be_visible()
        assert profile["work_authorization"] == {"Georgia": True}
        assert approved_answers == [{"question": "Are you authorized to work in Georgia?", "answer": "Yes, confirmed for Georgia."}]
        assert ("fixture-0", "approve") in actions and ("fixture-2", "verify") in actions

        # Navigation belongs to the agent's browser. Blockers leave the dashboard
        # open; explicit fallback links point to the actual discovered form.
        apps["fixture-3"] = create(3)
        direct_form = jobs[3]["canonical_url"] + "/application"
        apps["fixture-3"].update(state="PREPARING", mode="autonomous", reads=-100, application_url=direct_form)
        page.goto(f"{base}/top", wait_until="networkidle")
        fourth = page.locator(".top-vacancy").nth(3)
        fourth.get_by_role("button", name="Auto Apply", exact=True).click()
        expect(fourth.get_by_text("Подготовка", exact=True)).to_be_visible()
        apps["fixture-3"]["state"] = "NEEDS_HUMAN_ACTION"
        fourth.get_by_role("button", name="Обновить состояние", exact=True).click()
        expect(fourth.get_by_text("Нужна ваша помощь", exact=True)).to_be_visible()
        assert page.url == f"{base}/top"
        expect(fourth.get_by_role("link", name="Перейти к вакансии", exact=False)).to_have_attribute("href", direct_form)
        expect(fourth.get_by_text("Подтвердить ответ для этой и будущих заявок", exact=True)).to_have_count(0)
        apps["fixture-3"].update(state="PREPARING", mode="manual")
        fourth.get_by_role("button", name="Обновить состояние", exact=True).click()
        expect(fourth.get_by_text("Подготовка", exact=True)).to_be_visible()
        page.route("https://jobs.ashbyhq.com/**", lambda route: route.fulfill(
            content_type="text/html", body="<h1>Employer application fixture</h1>"))
        apps["fixture-3"]["state"] = "NEEDS_HUMAN_ACTION"
        fourth.get_by_role("button", name="Обновить состояние", exact=True).click()
        expect(fourth.get_by_text("Нужна ваша помощь", exact=True)).to_be_visible()
        assert page.url == f"{base}/top"
        fourth.get_by_role("link", name="Перейти к вакансии", exact=False).click()
        page.wait_for_url(direct_form)
        assert not errors, errors
        browser.close()
    print(json.dumps({"polling": True, "approval": True, "missing_answer": True, "pause_resume_cancel": True,
                      "uncertain_no_resubmit": True, "reload_existing": True, "autonomous_opt_in": True,
                      "verified_authorization": True, "mobile": True, "browser_errors": errors}))


if __name__ == "__main__":
    main()
