from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.domain import JobInput, Profile, evaluate, resume_variant, transition_allowed


def vacancy(**overrides):
    fields = {
        "provider": "ashby",
        "external_id": "test:1",
        "canonical_url": "https://jobs.ashbyhq.com/test/1/application",
        "company": "Example",
        "title": "Senior Frontend Engineer",
        "location": "Remote · Worldwide",
        "description": "React Next.js TypeScript JavaScript Python. SaaS fintech payments analytics. 5 years experience.",
        "published_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return JobInput(**fields)


def resume_profile(**overrides):
    fields = {
        "active_resume_id": "resume-1", "resume_filename": "cv.pdf", "location": "Georgia",
        "years_experience": 5,
        "resume_text": "Senior Frontend Engineer. React Next.js TypeScript JavaScript Python. "
                    "SaaS fintech payments analytics. 5 years experience.",
        "skills": ["React", "Next.js", "TypeScript", "JavaScript", "Python"],
        "domains": ["SaaS", "fintech", "payments", "analytics"],
    }
    fields.update(overrides)
    return Profile(**fields)


@pytest.mark.parametrize(
    "location",
    [
        "Remote — US-only",
        "Remote United States",
        "Remote Canada",
        "Remote Germany",
        "Atlanta, Georgia USA",
        "Hybrid · London",
    ],
)
def test_location_hard_filters(location):
    assert evaluate(vacancy(location=location), resume_profile())["eligibility"] == "rejected"


@pytest.mark.parametrize("location", ["Remote · Worldwide", "Remote · EMEA", "Remote · Georgia"])
def test_allowed_locations(location):
    assert evaluate(vacancy(location=location), resume_profile())["eligibility"] == "eligible"


def test_generic_remote_is_not_assumed_eligible():
    assert evaluate(vacancy(location="Remote"), Profile())["eligibility"] == "unknown"


def test_7_years_penalized_not_rejected():
    result = evaluate(vacancy(description=vacancy().description.replace("5 years", "7 years")), resume_profile())
    assert result["eligibility"] == "eligible"
    assert result["breakdown"]["experience"] == 6


@pytest.mark.parametrize(
    "title", ["Senior React Native Engineer", "Vue Developer", "Backend Java Engineer", "Technical Recruiter"]
)
def test_arbitrary_roles_are_not_hard_excluded(title):
    profile = resume_profile(resume_text=title + ". " + vacancy().description)
    result = evaluate(vacancy(title=title), profile)
    assert result["eligibility"] == "eligible"
    criterion = next(c for c in result["criteria"] if c["key"] == "role")
    assert criterion["score"] == criterion["max_score"]


def test_unknown_authorization_blocks():
    result = evaluate(
        vacancy(description="Must have work authorization in Germany. React TypeScript"), Profile()
    )
    assert result["eligibility"] != "eligible"


def test_unknown_publication_is_not_fresh():
    assert evaluate(vacancy(published_at=None), Profile())["breakdown"]["freshness"] == 0
    assert (
        evaluate(vacancy(published_at=datetime.now(UTC) - timedelta(days=60)), Profile())[
            "breakdown"
        ]["freshness"]
        == 0
    )


def test_max_score_and_weights():
    result = evaluate(vacancy(), resume_profile())
    assert 60 <= result["score"] <= 100
    assert result["score"] == sum(result["breakdown"].values())
    assert len(result["breakdown"]) == len(result["criteria"]) == 10
    assert {c["key"]: c["score"] for c in result["criteria"]} == result["breakdown"]
    assert all(0 <= c["score"] <= c["max_score"] and c["label"] and c["reason"] for c in result["criteria"])
    assert sum(c["max_score"] for c in result["criteria"]) == 100


def test_uncertain_never_automatically_retried():
    assert not transition_allowed("SUBMISSION_UNCERTAIN", "READY", True)
    assert not transition_allowed("FAILED", "READY", True)
    assert transition_allowed("FAILED", "READY", False)
    assert transition_allowed("SUBMISSION_UNCERTAIN", "SUBMITTED", True)


def test_cv_strategy():
    assert resume_variant("Frontend Engineer", "React") == "frontend"
    assert resume_variant("Full Stack Engineer", "React Python AI") == "react-python-ai"
    assert resume_variant("Full Stack Engineer", "React Node") == "full-stack"


def test_negated_sponsorship_does_not_allow_onsite():
    assert (
        evaluate(
            vacancy(location="Onsite London", description="We do not offer visa sponsorship. React"),
            Profile(),
        )["eligibility"]
        == "rejected"
    )


def test_worldwide_marketing_does_not_override_residence():
    assert (
        evaluate(vacancy(location="US Remote", description="We have customers worldwide. React"), resume_profile())[
            "eligibility"
        ]
        == "rejected"
    )
    assert (
        evaluate(
            vacancy(location="Remote Europe", description="We have customers worldwide. React"), resume_profile()
        )["eligibility"]
        == "unknown"
    )


def test_explicit_sponsored_relocation():
    assert (
        evaluate(
            vacancy(location="Onsite Germany", description="We provide visa sponsorship. React"), Profile()
        )["eligibility"]
        == "eligible"
    )


def test_empty_profile_has_no_seeded_facts_or_scores():
    profile = Profile()
    assert profile.skills == profile.domains == profile.resume_parse_warnings == []
    assert profile.location == profile.portfolio == ""
    assert profile.years_experience == 0
    result = evaluate(vacancy(), profile)
    assert result["score"] == 0
    assert all(c["score"] == 0 and "Неизвестно" in c["reason"] for c in result["criteria"])


def test_legacy_skills_without_resume_do_not_score():
    result = evaluate(vacancy(), resume_profile(resume_text="  "))
    assert result["score"] == 0
    assert result["matched_skills"] == []


def test_stale_resume_text_without_active_selection_does_not_score():
    result = evaluate(vacancy(), resume_profile(active_resume_id=None))
    assert result["score"] == 0


def test_authorization_for_another_country_cannot_satisfy_requirement():
    job = vacancy(location="Remote US", description="Must have work authorization in Germany.")
    profile = resume_profile(location="USA", work_authorization={"United States": True})
    assert evaluate(job, profile)["eligibility"] == "unknown"
    job = vacancy(description="Must have work authorization in Germany.")
    profile = resume_profile(work_authorization={"Germany": True})
    assert evaluate(job, profile)["eligibility"] == "eligible"
    profile.work_authorization["Germany"] = False
    assert evaluate(job, profile)["eligibility"] == "rejected"


def test_missing_job_skills_and_domains_are_independent_of_candidate():
    job = vacancy(title="Backend Java Engineer", description="Java Kotlin AWS. Banking insurance.")
    narrow = resume_profile(resume_text="Backend Java Engineer. Java banking.", skills=["Java"])
    broad = narrow.model_copy(update={"skills": ["Java", "Python", "React", "Docker"]})
    result = evaluate(job, narrow)
    criterion = next(c for c in result["criteria"] if c["key"] == "stack")
    assert criterion["score"] == round(criterion["max_score"] / 3)
    assert result["missing_skills"] == ["AWS", "Kotlin"]
    criterion = next(c for c in result["criteria"] if c["key"] == "domain")
    assert criterion["score"] == round(criterion["max_score"] / 2)
    assert "insurance" in next(c["reason"] for c in result["criteria"] if c["key"] == "domain")
    assert evaluate(job, broad)["breakdown"]["stack"] == result["breakdown"]["stack"]


def test_non_software_resume_and_russian_title():
    job = vacancy(title="Специалист по подбору персонала", description="Excel recruiting")
    profile = resume_profile(resume_text="Специалист по подбору персонала. Excel recruiting.")
    result = evaluate(job, profile)
    assert all(c["score"] == c["max_score"] for c in result["criteria"] if c["key"] in {"role", "stack"})
    assert result["eligibility"] == "eligible"


def test_experience_is_explicit_not_maximum_number_in_resume():
    profile = resume_profile(years_experience=0, resume_text="Java. 20 years education. 2 years project.")
    result = evaluate(vacancy(), profile)
    assert result["breakdown"]["experience"] == 0
    assert "Неизвестно" in result["criteria"][2]["reason"]


@pytest.mark.parametrize("location,candidate,expected", [
    ("Remote Germany", "Germany", "eligible"),
    ("Remote Canada", "Canada", "eligible"),
    ("Remote US-only", "USA", "eligible"),
    ("Remote Georgia", "Georgia, USA", "rejected"),
    ("Remote Germany", "", "unknown"),
    ("Remote Japan", "Japan", "eligible"),
])
def test_location_uses_candidate_residence(location, candidate, expected):
    assert evaluate(vacancy(location=location), resume_profile(location=candidate))["eligibility"] == expected


def test_restriction_in_description_overrides_worldwide():
    job = vacancy(description="Must reside in Canada. React", location="Remote Worldwide")
    assert evaluate(job, resume_profile())["eligibility"] == "rejected"


@pytest.mark.parametrize("offer,expectation,score,unknown", [
    ("USD 80k-100k/year", "USD 90k/year", 10, False),
    ("USD 80-100k/year", "$90000 annually", 10, False),
    ("EUR 80k-100k/year", "USD 90k/year", 0, True),
    ("USD 80k-100k/year", "USD 7500/month", 0, True),
    ("USD 80k-100k/year", "USD 110k/year", 0, False),
    ("competitive", "competitive", 0, True),
    ("USD 90k", "USD 90k", 0, True),
    ("from USD 80k/year", "USD 90k/year", 0, True),
])
def test_salary_comparison_is_conservative(offer, expectation, score, unknown):
    result = evaluate(vacancy(salary=offer), resume_profile(salary_expectation=expectation))
    criterion = next(c for c in result["criteria"] if c["key"] == "compensation")
    assert criterion["score"] == criterion["max_score"] == 0
    assert ("Неизвестно" in criterion["reason"]) == unknown
    if not unknown:
        assert bool(result["blockers"]) == (score == 0)


def test_all_ten_criteria_can_reach_ten_with_explicit_evidence():
    description = "Java banking bachelor English. 5 years experience."
    job = vacancy(title="Java Engineer", description=description, salary="USD 80k-100k/year")
    profile = resume_profile(resume_text="Java Engineer. " + description,
                             salary_expectation="USD 90k/year")
    result = evaluate(job, profile)
    assert result["score"] == 100
    assert all(c["score"] == c["max_score"] for c in result["criteria"])
    assert result["breakdown"]["stack"] == 40 and result["breakdown"]["responsibilities"] == 25


def test_eligibility_gate_does_not_erase_evidence_scores():
    result = evaluate(vacancy(location="Remote US-only"), resume_profile())
    assert result["eligibility"] == "rejected"
    assert result["score"] > 0
    assert result["score"] == sum(result["breakdown"].values())


def test_parse_warnings_are_exposed():
    result = evaluate(vacancy(), resume_profile(resume_parse_warnings=["Часть страниц не распознана"]))
    assert any("Часть страниц" in reason for reason in result["reasons"])
