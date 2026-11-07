"""Review uncertain vacancies without changing verified profile or submission facts."""
import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Candidate, SavedSearch, SearchNotification, TopVacancy, VacancyReview, now, uid
from .services import audit
from .top_vacancies import MIN_SCORE, WINDOW_HOURS, publication, save_top_vacancies
from .vacancy_identity import canonical_url, claim_delivery, identity, same_vacancy


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def context_hash(db: Session, job: dict[str, Any]) -> str:
    candidate = db.get(Candidate, 1)
    facts = candidate.facts if candidate else {}
    context = {"url": job.get("canonical_url"), "location": job.get("location"),
               "description": job.get("description"),
               "profile": {key: facts.get(key) for key in ("location", "search_locations", "work_authorization")}}
    return hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def eligible_for_review(job: dict[str, Any]) -> bool:
    published = publication(job.get("published_at"))
    current = now()
    return bool(job.get("eligibility") == "unknown" and job.get("score", 0) >= MIN_SCORE
                and job.get("explanation", {}).get("recommendable", True)
                and job.get("canonical_url", "").startswith("https://")
                and published and current - timedelta(hours=WINDOW_HOURS) <= published <= current)


def matching_reviews(db: Session, job: dict[str, Any]) -> list[VacancyReview]:
    rows = db.scalars(select(VacancyReview).where(
        (VacancyReview.identity_key == identity(job)) |
        (VacancyReview.url_hash == url_hash(job.get("canonical_url", ""))) |
        (VacancyReview.identity_key == "")))
    return [row for row in rows if same_vacancy(row.data, job)]


def is_rejected(db: Session, job: dict[str, Any]) -> bool:
    return any(row.status == "rejected" for row in matching_reviews(db, job))


def approved_for_preparation(db: Session, job: dict[str, Any]) -> bool:
    """A review permits form preparation, never asserts legal eligibility."""
    return any(row.status == "approved" and row.context_hash == context_hash(db, row.data)
               and canonical_url(row.data.get("canonical_url", "")) == canonical_url(job.get("canonical_url", ""))
               and all(row.data.get(key) == job.get(key) for key in ("location", "description"))
               for row in matching_reviews(db, job))


def _queue(db: Session, review: VacancyReview, *, enqueue_without_credentials: bool = False) -> bool:
    from .notifications import channels
    if review.status != "pending" or (not enqueue_without_credentials and "telegram" not in channels()):
        return False
    if not eligible_for_review(review.data) or db.get(SavedSearch, review.search_id) is None:
        return False
    notification_key = hashlib.sha256(f"telegram-review-v1:{review.id}:{review.context_hash}".encode()).hexdigest()
    if db.get(SearchNotification, notification_key) is not None:
        review.notification_id = notification_key
        return False
    job = review.data
    reasons = [str(reason) for reason in job.get("explanation", {}).get("reasons", [])
               if str(reason).startswith("Неизвестно")]
    reason = "\n".join("• " + " ".join(line.split())[:220] for line in reasons[:3])
    text = (f"JobPilot · вакансия на проверке · {job['score']}/100\n\n"
            f"{job['company'][:120]} · {job['title'][:240]}\n"
            f"{job.get('location') or 'Местоположение не указано'}\n\n"
            f"{reason or 'Доступность вакансии из вашего местоположения не подтверждена.'}\n\n"
            "Подтвердить — добавить в подборку; отклик не отправляется.\n"
            f"{job['canonical_url']}")
    published = publication(job["published_at"])
    assert published is not None
    # Leave room for the final decision and Telegram's UTF-16 limit.
    if len(text.encode("utf-16-le")) // 2 > 3800:
        text = f"JobPilot · вакансия на проверке · {job['score']}/100\n\n{job['canonical_url']}"
    if len(text.encode("utf-16-le")) // 2 > 3800:
        return False
    review.identity_key = identity(job)
    if not claim_delivery(db, job):
        review.status = "stale"
        return False
    review.notification_id = notification_key
    db.add(SearchNotification(id=notification_key, search_id=review.search_id, channel="telegram", message=text,
                              expires_at=published + timedelta(hours=WINDOW_HOURS)))
    return True


def prepare_reviews(db: Session, search: SavedSearch, jobs: list[dict[str, Any]], *,
                    enqueue_without_credentials: bool = False) -> list[dict[str, Any]]:
    """Register and deduplicate candidates, then apply existing decisions."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    for job in jobs:
        if not eligible_for_review(job):
            continue
        digest, context = url_hash(job["canonical_url"]), context_hash(db, job)
        peers = matching_reviews(db, job)
        if any(peer.status == "rejected" for peer in peers):
            continue
        if peers and all(peer.url_hash != digest for peer in peers):
            continue  # Same employer and role on another board is not a new review.
        db.execute(insert(VacancyReview).values(id=uid(), url_hash=digest, context_hash=context,
                   identity_key=identity(job), search_id=search.id, data=job, status="pending", updated_at=now())
                   .on_conflict_do_nothing(index_elements=[VacancyReview.url_hash]))
        review = db.scalar(select(VacancyReview).where(VacancyReview.url_hash == digest).with_for_update())
        assert review is not None
        previous_search = db.get(SavedSearch, review.search_id)
        if review.context_hash != context or (review.status == "stale" and not review.notification_id):
            old = db.get(SearchNotification, review.notification_id) if review.notification_id else None
            if old and old.status == "pending":
                old.status = "superseded"
            # A new identifier prevents an old Telegram button becoming valid again
            # if the vacancy or profile later returns to an earlier version.
            review.id = uid()
            # Invalidate old buttons without sending the same vacancy again.
            review.context_hash, review.status = context, "stale" if review.notification_id else "pending"
            review.data, review.search_id, review.updated_at = job, search.id, now()
        elif job["score"] > review.data.get("score", 0) or not previous_search or not previous_search.enabled:
            review.data, review.search_id, review.updated_at = job, search.id, now()
            notification = db.get(SearchNotification, review.notification_id) if review.notification_id else None
            if notification and notification.status == "pending":
                notification.search_id = search.id
        _queue(db, review, enqueue_without_credentials=enqueue_without_credentials)
    return apply_decisions(db, jobs)


def apply_decisions(db: Session, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    urls = {url_hash(job.get("canonical_url", "")) for job in jobs}
    reviews = {review.url_hash: review for review in db.scalars(select(VacancyReview).where(VacancyReview.url_hash.in_(urls)))}
    result = []
    rejected = list(db.scalars(select(VacancyReview).where(VacancyReview.status == "rejected")))
    for job in jobs:
        if any(same_vacancy(review.data, job) for review in rejected):
            continue
        if "review_status" in job:
            job = {key: value for key, value in job.items() if key != "review_status"}
        review = reviews.get(url_hash(job.get("canonical_url", "")))
        if not review or review.context_hash != context_hash(db, job):
            result.append(job)
        elif review.status == "rejected":
            continue
        elif review.status == "approved" and job.get("eligibility") == "unknown":
            result.append({**job, "review_status": "approved"})
        else:
            result.append(job)
    return result


def enqueue_pending_reviews(db: Session) -> None:
    for review in db.scalars(select(VacancyReview).where(VacancyReview.status == "pending",
                                                        VacancyReview.notification_id.is_(None))
                             .order_by(VacancyReview.updated_at, VacancyReview.id).limit(200)):
        search = db.get(SavedSearch, review.search_id)
        if (not search or not search.enabled or not eligible_for_review(review.data)
                or review.context_hash != context_hash(db, review.data)):
            review.status = "stale"
        else:
            _queue(db, review)


def backfill_saved_reviews(db: Session) -> None:
    """Offer already saved, still-fresh uncertain results after upgrading."""
    for search in db.scalars(select(SavedSearch).where(SavedSearch.enabled.is_(True),
                                                       SavedSearch.status == "complete")):
        original = search.results or []
        updated = prepare_reviews(db, search, original)
        if updated != original:
            search.results = updated
            search.result_count = max(0, search.result_count - (len(original) - len(updated)))


def is_approved(db: Session, job: dict[str, Any]) -> bool:
    if job.get("eligibility") != "unknown" or job.get("review_status") != "approved":
        return False
    review = db.scalar(select(VacancyReview).where(VacancyReview.url_hash == url_hash(job.get("canonical_url", ""))))
    return bool(review and review.status == "approved" and review.context_hash == context_hash(db, job))


def decide(db: Session, review_id: str, message_id: str, decision: str) -> tuple[str, SearchNotification | None]:
    review = db.scalar(select(VacancyReview).where(VacancyReview.id == review_id).with_for_update())
    notification = db.get(SearchNotification, review.notification_id) if review and review.notification_id else None
    if not review or not notification or notification.provider_message_id != message_id:
        return "Эта кнопка больше не действует.", None
    if review.status not in {"pending", "top_pending"}:
        return "Решение по вакансии уже сохранено.", notification
    was_top = review.status == "top_pending"
    if was_top and decision != "rejected":
        return "Неизвестное действие.", None
    if is_rejected(db, review.data):
        return "Эта вакансия уже отклонена.", notification
    if (review.context_hash != context_hash(db, review.data) or (not was_top and not eligible_for_review(review.data))
            or db.get(SavedSearch, review.search_id) is None):
        return "Данные изменились или вакансия устарела. Дождитесь обновления поиска.", None
    if decision not in {"approved", "rejected"}:
        return "Неизвестное действие.", None
    if decision == "approved":
        # Search filters, resume and matching rules can change after the message was sent.
        from .domain import JobInput, evaluate
        from .searches import SearchFilters, matches, search_profile
        search = db.get(SavedSearch, review.search_id)
        assert search is not None
        try:
            job_input = JobInput.model_validate(review.data)
            current = evaluate(job_input, search_profile(db, search.resume_id))
            filters = SearchFilters.model_validate(search.filters).model_copy(
                update={"posted_days": 0, "min_score": MIN_SCORE, "sort": "score"})
            if (not search.enabled or current["eligibility"] != "unknown" or
                    not matches(job_input, filters, current)):
                return "Условия поиска изменились. Обновите подборку перед подтверждением.", None
            review.data = {**review.data, "score": current["score"], "eligibility": current["eligibility"],
                           "explanation": current}
        except ValueError:
            return "Не удалось проверить актуальность вакансии. Обновите поиск.", None
    review.status, review.updated_at = decision, now()
    job = review.data
    if decision == "approved":
        search = db.get(SavedSearch, review.search_id)
        assert search is not None
        save_top_vacancies(db, search, [{**job, "review_status": "approved"}])
    else:
        for row in db.scalars(select(TopVacancy)):
            if same_vacancy(row.data, job):
                key = hashlib.sha256(f"telegram-top-v1:{row.data['canonical_url']}".encode()).hexdigest()
                queued_top = db.get(SearchNotification, key)
                if queued_top and queued_top.status == "pending":
                    queued_top.status = "superseded"
                db.delete(row)
        for peer in matching_reviews(db, job):
            if peer.id != review.id:
                peer.status = "rejected"
            queued = db.get(SearchNotification, peer.notification_id) if peer.notification_id else None
            if queued and queued.status == "pending":
                queued.status = "superseded"
    for search in db.scalars(select(SavedSearch)):
        previous = search.results or []
        updated = apply_decisions(db, previous)
        if updated != previous:
            search.results = updated
            search.result_count = max(0, search.result_count - (len(previous) - len(updated)))
    audit(db, "vacancy_review", {"review_id": review.id, "decision": decision, "actor": "telegram"})
    return ("Добавлено в подборку. Отклик не отправлен." if decision == "approved"
            else "Вакансия отклонена. Ответьте на эту карточку: почему она вам не подходит?"), notification
