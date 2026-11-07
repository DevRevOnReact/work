from types import SimpleNamespace

from jobpilot.feedback import adjust, preferences
from jobpilot.services import audit


def test_feedback_uses_latest_reason_and_adjusts_only_matching_dimension(db):
    audit(db, "vacancy_feedback", {"review_id": "one", "reason": "Не подходит компания",
                                  "job": {"company": "Acme", "title": "Designer"}})
    db.flush()
    rules = preferences(db)
    assert adjust(SimpleNamespace(company="ACME", title="Engineer"), {"score": 70}, rules)["score"] == 55
    assert adjust(SimpleNamespace(company="Other", title="Designer"), {"score": 70}, rules)["score"] == 70
    audit(db, "vacancy_feedback", {"review_id": "one", "reason": "Не подходит должность",
                                  "job": {"company": "Acme", "title": "Designer"}})
    db.flush()
    rules = preferences(db)
    assert adjust(SimpleNamespace(company="Acme", title="Engineer"), {"score": 70}, rules)["score"] == 70
    assert adjust(SimpleNamespace(company="Other", title="Designer"), {"score": 70}, rules)["score"] == 55
