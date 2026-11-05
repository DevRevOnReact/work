import json
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from test_notifications import enable
from test_saved_searches import setup_resumes, vacancy

from jobpilot import domain, notifications, telegram, top_vacancies, vacancy_review
from jobpilot.models import Audit, Candidate, SavedSearch, SearchNotification, TopVacancy, VacancyReview, now


@pytest.fixture
def review_setup(db, monkeypatch):
    enable(monkeypatch)
    first, second = setup_resumes(db)
    search = SavedSearch(name="Python", resume_id=first.id, filters={})
    other = SavedSearch(name="Other", resume_id=second.id, filters={})
    db.add_all([search, other]); db.commit()
    job = {**vacancy().model_dump(mode="json"), "score": 85, "eligibility": "unknown",
           "explanation": {"score": 85, "eligibility": "unknown", "recommendable": True,
                           "reasons": ["Неизвестно: право на работу требует подтверждения"]}}
    search.results, search.result_count = [job], 1
    other.results, other.result_count = [job], 1
    vacancy_review.prepare_reviews(db, search, [job])
    vacancy_review.prepare_reviews(db, other, [job])
    db.commit()
    review = db.scalar(select(VacancyReview))
    monkeypatch.setattr(domain, "evaluate", lambda *args: job["explanation"])
    @contextmanager
    def factory():
        yield db
    monkeypatch.setattr(telegram, "SessionLocal", factory)
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    return search, other, job, review


def deliver_review(db, monkeypatch, review):
    original = httpx.Client
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    monkeypatch.setattr(notifications.httpx, "Client",
                        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    notifications.deliver_pending()
    db.refresh(review)
    return sent


def poll(monkeypatch, updates):
    requests = []
    def get(url, **kwargs):
        assert "callback_query" in kwargs["params"]["allowed_updates"]
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, json={"ok": True, "result": updates})
    def post(url, **kwargs):
        requests.append((url.rsplit("/", 1)[-1], kwargs["json"]))
        return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True})
    monkeypatch.setattr(telegram.httpx, "get", get)
    monkeypatch.setattr(telegram.httpx, "post", post)
    telegram.poll_commands()
    return requests


def callback(review, *, chat="123", message_id=42, action="a", update_id=1):
    return {"update_id": update_id, "callback_query": {"id": str(update_id), "data": f"vr:{action}:{review.id}",
            "message": {"chat": {"id": chat}, "message_id": message_id}}}


def test_approval_is_authenticated_deduplicated_and_does_not_change_eligibility(db, monkeypatch, review_setup):
    search, other, job, review = review_setup
    assert len(list(db.scalars(select(VacancyReview)))) == 1
    assert len(list(db.scalars(select(SearchNotification)))) == 1
    sent = deliver_review(db, monkeypatch, review)
    keyboard = sent[0]["reply_markup"]["inline_keyboard"][0]
    assert [button["callback_data"] for button in keyboard] == [f"vr:a:{review.id}", f"vr:r:{review.id}"]
    assert "отклик не отправляется" in sent[0]["text"] and "право на работу" in sent[0]["text"]
    poll(monkeypatch, [callback(review, chat="999"), callback(review, message_id=41, update_id=2)])
    assert review.status == "pending" and not list(db.scalars(select(TopVacancy)))
    responses = poll(monkeypatch, [callback(review, update_id=3)])
    assert review.status == "approved" and [row.data["eligibility"] for row in top_vacancies.top_rows(db)] == ["unknown"]
    assert search.results[0]["review_status"] == other.results[0]["review_status"] == "approved"
    assert any(method == "answerCallbackQuery" and "Отклик не отправлен" in payload["text"] for method, payload in responses)
    assert any(method == "editMessageText" and payload["reply_markup"] == {"inline_keyboard": []}
               for method, payload in responses)
    assert vacancy_review.prepare_reviews(db, search, [job])[0]["review_status"] == "approved"
    assert notifications.enqueue_top_telegram(db, search.id, vacancy_review.apply_decisions(db, [job])) == 0
    poll(monkeypatch, [callback(review, action="r", update_id=4)])
    assert review.status == "approved"
    assert len(list(db.scalars(select(Audit).where(Audit.kind == "vacancy_review")))) == 1
    candidate = db.get(Candidate, 1)
    candidate.facts = {**candidate.facts, "location": "Paris"}
    db.commit()
    assert top_vacancies.top_rows(db) == []  # A previous approval cannot outlive changed profile facts.
    assert "review_status" not in vacancy_review.apply_decisions(db, search.results)[0]


def test_rejection_is_saved_and_changed_vacancy_needs_new_review(db, monkeypatch, review_setup):
    search, other, job, review = review_setup
    deliver_review(db, monkeypatch, review)
    eligible = {**job, "eligibility": "eligible"}
    top_vacancies.save_top_vacancies(db, other, [eligible])
    assert notifications.enqueue_top_telegram(db, other.id, [eligible]) == 1
    db.flush()
    poll(monkeypatch, [callback(review, action="r")])
    assert review.status == "rejected"
    assert search.results == other.results == [] and search.result_count == other.result_count == 0
    assert vacancy_review.prepare_reviews(db, search, [job]) == []
    assert vacancy_review.prepare_reviews(db, search, [eligible]) == []
    assert list(db.scalars(select(TopVacancy))) == []
    assert len(list(db.scalars(select(SearchNotification).where(SearchNotification.status == "superseded")))) == 1
    old_notification = review.notification_id
    old_review_id = review.id
    changed = {**job, "description": job["description"] + " New eligibility requirements."}
    assert vacancy_review.prepare_reviews(db, search, [changed]) == [changed]
    db.commit()
    assert review.status == "pending" and review.notification_id != old_notification
    stale_click = callback(review, update_id=2)
    stale_click["callback_query"]["data"] = f"vr:a:{old_review_id}"
    poll(monkeypatch, [stale_click])
    assert review.status == "pending"
    changed_id = review.id
    vacancy_review.prepare_reviews(db, search, [job])
    db.commit()
    assert review.id not in {old_review_id, changed_id}
    assert review.notification_id != old_notification


def test_stale_profile_and_expired_or_changed_search_cannot_be_approved(db, monkeypatch, review_setup):
    search, _, _, review = review_setup
    deliver_review(db, monkeypatch, review)
    candidate = db.get(Candidate, 1)
    candidate.facts = {**candidate.facts, "location": "Paris"}
    db.commit()
    poll(monkeypatch, [callback(review)])
    assert review.status == "pending" and not list(db.scalars(select(TopVacancy)))
    candidate.facts = {**candidate.facts, "location": "Berlin"}
    search.filters = {"title": "Designer"}
    db.commit()
    poll(monkeypatch, [callback(review, update_id=2)])
    assert review.status == "pending"
    search.filters = {}
    review.data = {**review.data, "published_at": (now() - timedelta(days=4)).isoformat()}
    db.commit()
    poll(monkeypatch, [callback(review, update_id=3)])
    assert review.status == "pending"


def test_click_before_delivery_receipt_is_committed_is_retried(db, monkeypatch, review_setup):
    _, _, _, review = review_setup
    poll(monkeypatch, [callback(review)])
    assert review.status == "pending"
    assert not list(db.scalars(select(Audit).where(Audit.kind == "telegram_cursor")))
    deliver_review(db, monkeypatch, review)
    poll(monkeypatch, [callback(review)])
    assert review.status == "approved"


def test_old_success_summaries_are_suppressed_but_failures_remain(db, monkeypatch, review_setup):
    search, _, _, review = review_setup
    review.status = "rejected"
    db.get(SearchNotification, review.notification_id).status = "superseded"
    rows = [
        SearchNotification(id="1" * 64, search_id=search.id, channel="telegram",
                           message="JobPilot · отчёт поиска · топ за 3 дня\nУспех\nПоиск завершён."),
        SearchNotification(id="2" * 64, search_id=search.id, channel="telegram",
                           message="JobPilot · LinkedIn · отчёт за 3 дня\nПроверено вакансий: 40."),
        SearchNotification(id="3" * 64, search_id=search.id, channel="telegram",
                           message="JobPilot · LinkedIn · отчёт за 3 дня\nОшибка обработки кандидатов."),
    ]
    db.add_all(rows); db.commit()
    sent = deliver_review(db, monkeypatch, review)
    assert [row.status for row in rows] == ["superseded", "superseded", "sent"]
    assert len(sent) == 1 and "Ошибка" in sent[0]["text"]


def test_saved_uncertain_results_are_backfilled_only_once(db, monkeypatch):
    enable(monkeypatch)
    first, _ = setup_resumes(db)
    job = {**vacancy().model_dump(mode="json"), "score": 80, "eligibility": "unknown", "explanation": {}}
    search = SavedSearch(name="Saved", resume_id=first.id, filters={}, status="complete", results=[job], result_count=1)
    db.add(search); db.commit()
    vacancy_review.backfill_saved_reviews(db)
    vacancy_review.backfill_saved_reviews(db)
    db.commit()
    assert len(list(db.scalars(select(VacancyReview)))) == 1
    assert len(list(db.scalars(select(SearchNotification)))) == 1


def test_unqueued_reviews_continue_past_the_first_batch(db, monkeypatch):
    enable(monkeypatch)
    first, _ = setup_resumes(db)
    search = SavedSearch(name="Saved", resume_id=first.id, filters={})
    db.add(search); db.flush()
    job = {**vacancy().model_dump(mode="json"), "score": 80, "eligibility": "unknown", "explanation": {}}
    for index in range(201):
        data = {**job, "canonical_url": f"https://jobs.example/{index}"}
        db.add(VacancyReview(url_hash=vacancy_review.url_hash(data["canonical_url"]),
                             context_hash=vacancy_review.context_hash(db, data), search_id=search.id, data=data))
    db.commit()
    vacancy_review.enqueue_pending_reviews(db)
    db.commit()
    assert len(list(db.scalars(select(SearchNotification)))) == 200
    vacancy_review.enqueue_pending_reviews(db)
    db.commit()
    assert len(list(db.scalars(select(SearchNotification)))) == 201


def test_unsent_stale_reviews_are_retired_and_a_new_search_can_restore_them(db, monkeypatch):
    from pydantic import SecretStr

    from jobpilot.config import settings
    first, _ = setup_resumes(db)
    search = SavedSearch(name="Saved", resume_id=first.id, filters={})
    db.add(search); db.commit()
    job = {**vacancy().model_dump(mode="json"), "score": 80, "eligibility": "unknown", "explanation": {}}
    vacancy_review.prepare_reviews(db, search, [job]); db.commit()
    review = db.scalar(select(VacancyReview))
    old_id = review.id
    candidate = db.get(Candidate, 1)
    candidate.facts = {**candidate.facts, "location": "Paris"}
    enable(monkeypatch)
    vacancy_review.enqueue_pending_reviews(db); db.commit()
    assert review.status == "stale" and review.notification_id is None
    monkeypatch.setattr(settings(), "telegram_bot_token", SecretStr(""))
    vacancy_review.prepare_reviews(db, search, [job]); db.commit()
    assert review.status == "pending" and review.id != old_id
