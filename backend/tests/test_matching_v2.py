import pytest
import runpy
from pathlib import Path
from sqlalchemy import select

from jobpilot.domain import evaluate
from jobpilot.models import Candidate, SavedSearch, TopVacancy
from jobpilot.resumes import parse_resume
from jobpilot.searches import SearchFilters, matches
from jobpilot.services import bootstrap
from jobpilot.top_vacancies import qualifies, save_top_vacancies
from test_domain import resume_profile, vacancy
from test_saved_searches import setup_resumes


def frontend(**overrides):
    return resume_profile(**{
        "resume_text": "Senior Frontend Engineer\nReact TypeScript Next.js Jest REST. "
                       "Built CRM dashboards, forms and filters. API integrations, authentication, "
                       "performance optimization, caching, code reviews and testing. English B2. 5 years experience.",
        "languages": {"English": "B2"}, "search_locations": ["Serbia", "Georgia"], **overrides})


def result_for(description, **job_fields):
    job = vacancy(description=description, **job_fields)
    return job, evaluate(job, frontend())


def test_missing_angular_cannot_be_compensated_by_general_tasks_or_freshness():
    job, result = result_for("Angular RxJS NgRx required. 5 years experience. "
                             "Build CRM dashboards, forms, filters and REST API integrations. Jest. English B2.")
    assert not result["recommendable"] and any("Angular" in reason for reason in result["blockers"])
    assert not matches(job, SearchFilters(min_score=60), result)
    assert not qualifies({**job.model_dump(mode="json"), "score": 99, "explanation": result})


@pytest.mark.parametrize("optional", ["Nice to have: Angular RxJS.", "Angular is a bonus.", "Angular not required."])
def test_optional_framework_does_not_block_or_reduce_required_stack(optional):
    job, result = result_for("React and TypeScript required. 5 years experience. " + optional)
    stack = next(c for c in result["criteria"] if c["key"] == "stack")
    assert stack["score"] == stack["max_score"] and result["recommendable"]
    assert "Angular" in result["optional_skills"] and "Angular" not in result["missing_skills"]


def test_framework_alternatives_only_require_one_and_core_in_title_is_checked():
    _, result = result_for("React or Vue required. TypeScript. 5 years experience.")
    assert result["recommendable"]
    _, result = result_for("TypeScript, REST and Jest.", title="Senior Angular Frontend Engineer")
    assert not result["recommendable"]
    _, result = result_for("React, Vue or Angular required. TypeScript.")
    assert result["recommendable"]


def test_company_stack_and_optional_section_do_not_become_mandatory():
    _, result = result_for("Requirements: React TypeScript. Nice to have: Angular. "
                           "Responsibilities: build CRM dashboards. About us: We use Java and Go.")
    assert result["recommendable"]
    assert "Angular" not in result["missing_skills"] and "Java" not in result["missing_skills"]


@pytest.mark.parametrize("level,allowed", [("B1", True), ("B2", True), ("C1", False), ("C2", False)])
def test_explicit_english_level_is_enforced(level, allowed):
    _, result = result_for(f"React TypeScript. English {level} required. 5 years experience.")
    assert result["recommendable"] == allowed


def test_optional_inline_skill_does_not_hide_later_required_language():
    _, result = result_for("React TypeScript. Angular is a bonus. English C1 required.")
    assert not result["recommendable"] and any("C1" in reason for reason in result["blockers"])


def test_required_language_cannot_be_invented_when_unknown():
    job = vacancy(description="React TypeScript. German C1 required.")
    result = evaluate(job, frontend())
    assert not result["recommendable"] and any("German" in reason for reason in result["blockers"])


def test_staff_and_large_experience_gap_are_gates_but_small_gap_is_not():
    _, result = result_for("React TypeScript. 10 years experience.", title="Staff Frontend Engineer")
    assert not result["recommendable"] and len(result["blockers"]) >= 2
    _, result = result_for("React TypeScript. 7 years experience.")
    assert result["recommendable"]


@pytest.mark.parametrize("location,allowed", [("Remote EMEA", True), ("Remote Worldwide", True),
    ("Remote Serbia", True), ("Remote Georgia", True), ("Remote EU only", False),
    ("Remote US-only", False), ("Remote Canada", False), ("Remote Singapore", False)])
def test_two_countries_are_checked_as_alternatives(location, allowed):
    job, result = result_for("React TypeScript. 5 years experience.", location=location)
    assert result["recommendable"] == allowed
    assert matches(job, SearchFilters(min_score=60), result) == allowed


def test_relocation_preference_never_creates_work_authorization():
    _, result = result_for("React TypeScript. Must have work authorization in Serbia.", location="Remote Serbia")
    assert result["eligibility"] == "unknown"


def test_unknown_salary_education_language_and_date_do_not_dilute_professional_score():
    job, result = result_for("React TypeScript. Build CRM dashboards and forms. 5 years experience.")
    other = evaluate(job.model_copy(update={"salary": "competitive", "published_at": None}), frontend())
    assert result["score"] == other["score"] >= 60
    assert all(c["max_score"] == 0 for c in result["criteria"] if c["key"] in {
        "education", "languages", "location", "compensation", "freshness"})


def test_mandatory_degree_is_checked_but_equivalent_experience_is_preserved():
    _, result = result_for("React TypeScript. A PhD is required.")
    assert not result["recommendable"]
    _, result = result_for("React TypeScript. Bachelor or equivalent experience required.")
    assert result["recommendable"]


def test_russian_experience_pixi_and_language_are_extracted():
    parsed = parse_resume(("Тестовый Кандидат\nSenior Frontend Engineer\nБатуми, Грузия | Удалённо\n"
                           "Более 5 лет разрабатываю веб-приложения. Vue.js PixiJS WebSocket GSAP.\n"
                           "Английский: B2").encode(), "cv.txt")
    assert parsed.facts["years_experience"] == 5
    assert {"Vue", "PixiJS", "WebSockets", "GSAP"} <= set(parsed.facts["skills"])
    assert parsed.facts["location"] == "georgia" and parsed.facts["languages"]["English"] == "B2"


def test_bilingual_tasks_and_pixi_are_real_evidence():
    resume = "Senior Frontend Engineer\nVue.js PixiJS TypeScript. " \
             "Делал анимации, спрайты и текстуры. Добавил обработчики событий, " \
             "адаптацию под мобильные экраны, интеграции API и обработку ошибок."
    job = vacancy(title="Frontend Developer", description="Vue.js PixiJS TypeScript. "
                  "Build animations with sprites and textures. Handle pointer events, API integrations "
                  "and error states. Adapt mobile layouts.")
    result = evaluate(job, frontend(resume_text=resume))
    assert result["recommendable"] and result["score"] >= 80
    assert "PixiJS" in result["matched_skills"]


def test_refresh_invalidates_incompatible_top_without_sending_messages(db):
    first, _ = setup_resumes(db)
    first.variant = "frontend"
    candidate = db.get(Candidate, 1)
    value = SavedSearch(name="Frontend · основной поиск", resume_id=first.id,
                        filters={"title": "Frontend, Front-end, Front end"})
    db.add(value); db.flush()
    job = vacancy(description="Angular required. 5 years experience.")
    old = {**job.model_dump(mode="json"), "score": 95, "eligibility": "eligible"}
    save_top_vacancies(db, value, [old]); db.commit()
    candidate.facts = {k: v for k, v in candidate.facts.items() if k != "search_scoring_version"}
    bootstrap(db)
    assert db.scalar(select(TopVacancy)) is None
    assert "UI Engineer" in value.filters["title"]
    before = dict(candidate.facts)
    bootstrap(db)
    assert candidate.facts == before


def test_profile_update_preserves_old_artifact_and_encodes_untrusted_filename():
    from test_resumes import pdf_bytes
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/prepare_search_profile_update.py"))
    content = pdf_bytes()
    statements = module["prepare"](content, "cv';unsafe.pdf", "a" * 64, ["Serbia", "Georgia"])
    assert statements == module["prepare"](content, "cv';unsafe.pdf", "a" * 64, ["Serbia", "Georgia"])
    assert not any("cv';unsafe" in sql or "DELETE FROM" in sql for sql in statements)
    assert any("INSERT INTO resume_files" in sql and "ON CONFLICT" in sql for sql in statements)
    assert any("UPDATE saved_searches" in sql and "a" * 64 in sql for sql in statements)
    with pytest.raises(ValueError):
        module["prepare"](content, "cv.pdf", "invalid", ["Serbia"])
