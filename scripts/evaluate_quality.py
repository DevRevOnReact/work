"""Reproducible, synthetic gold set. Run with --output for an auditable JSON report."""
import argparse
import json
import math
from pathlib import Path

from jobpilot.domain import JobInput, Profile, evaluate
from jobpilot.evidence import rank_passages, source_facts
from jobpilot.retrieval import rank_candidates
from jobpilot.resumes import extract_facts

CASES = [
    ("Explicit years", "Jane Doe\nFrontend Engineer\nReact TypeScript\n5 years of experience", ["React", "TypeScript"], 5),
    ("No inferred years", "Jane Doe\nBackend Engineer\nPython PostgreSQL", ["Python", "PostgreSQL"], 0),
    ("Negated skill", "Jane Doe\nReact\nNo commercial experience with Python.", ["React"], 0),
    ("Aliases", "Jane Doe\nReact.js NextJS Postgres", ["React", "Next.js", "PostgreSQL"], 0),
    ("Overlapping names", "Jane Doe\nJavaScript TypeScript", ["JavaScript", "TypeScript"], 0),
    ("Sections", "Jane Doe\nEmployment History\nBuilt React interfaces.\nProfessional Summary\nPython", ["React", "Python"], 0),
]
ROLES = [
    ("Frontend React Engineer", "React TypeScript CSS accessible components rendering performance"),
    ("Python Backend Engineer", "Python FastAPI PostgreSQL APIs database optimization testing"),
    ("Full Stack Engineer", "React TypeScript Python FastAPI PostgreSQL API components"),
]
JOBS = [
    ("React UI Engineer", "Requirements: React TypeScript CSS. Build accessible components and optimize rendering performance.", [3,0,2]),
    ("Frontend Developer", "Requirements: React TypeScript. Build reusable components and responsive interfaces.", [3,0,2]),
    ("Web Engineer", "Requirements: React CSS. Deliver responsive components and test interfaces.", [2,0,1]),
    ("Python Backend Engineer", "Requirements: Python FastAPI PostgreSQL. Build APIs and optimize database queries.", [0,3,2]),
    ("Backend Developer", "Requirements: Python PostgreSQL. Build tested API integrations.", [0,3,2]),
    ("API Engineer", "Requirements: Python FastAPI. Implement APIs and database integrations.", [0,2,2]),
    ("Full Stack Engineer", "Requirements: React TypeScript Python FastAPI PostgreSQL. Build components and APIs.", [0,0,3]),
    ("Fullstack Developer", "Requirements: React Python PostgreSQL. Deliver end to end components and database features.", [0,0,3]),
    ("Product Engineer", "Requirements: React Python. Own product components and APIs.", [0,0,2]),
    ("Angular Engineer", "Requirements: Angular. Must have Angular production experience.", [0,0,0]),
    ("Engineering Manager", "Manage an engineering team and hiring. Requirements: leadership.", [0,0,0]),
    ("Java Engineer", "Requirements: Java Spring. Must have Java experience.", [0,0,0]),
]


def metrics(order: list[int], relevance: list[int], k: int = 3) -> dict:
    chosen = order[:k]
    hits = sum(relevance[i] > 0 for i in chosen)
    relevant = sum(v > 0 for v in relevance)
    dcg = sum((2 ** relevance[index] - 1) / math.log2(rank + 2) for rank, index in enumerate(chosen))
    ideal = sum((2 ** value - 1) / math.log2(rank + 2) for rank, value in enumerate(sorted(relevance, reverse=True)[:k]))
    return {"precision_at_3": round(hits / k, 4), "recall_at_3": round(hits / relevant, 4) if relevant else 0,
            "ndcg_at_3": round(dcg / ideal, 4) if ideal else 0, "returned": len(order)}


def measure() -> dict:
    expected = predicted = correct = 0
    years_correct = 0
    parsing = []
    for name, text, skills, years in CASES:
        facts, _ = extract_facts(text)
        target, found = set(skills), set(facts["skills"])
        expected += len(target)
        predicted += len(found)
        correct += len(target & found)
        years_correct += facts["years_experience"] == years
        parsing.append({"case": name, "expected": sorted(target), "actual": sorted(found),
                        "years_expected": years, "years_actual": facts["years_experience"]})
    retrieval = []
    for pindex, (title, body) in enumerate(ROLES):
        text = "Jane Doe\n" + title + "\nSkills\n" + body + "\nExperience\n" + body
        facts, _ = extract_facts(text)
        profile = Profile.model_validate({**facts, "active_resume_id": "synthetic", "resume_text": text, "location": "Georgia"})
        jobs = [JobInput(provider="manual", external_id=str(i), canonical_url=f"https://example.invalid/{i}",
                         company="Fixture", title=jt, description=jd, location="Remote worldwide")
                for i, (jt, jd, _) in enumerate(JOBS)]
        results = [evaluate(job, profile) for job in jobs]
        eligible = [i for i, r in enumerate(results) if r["recommendable"] and r["eligibility"] != "rejected"]
        baseline = sorted(eligible, key=lambda i: -results[i]["score"])
        candidates = [{**jobs[i].model_dump(), "score": results[i]["score"], "index": i} for i in eligible]
        rank_candidates(candidates, profile)
        ranked = [item["index"] for item in candidates]
        relevance = [grades[pindex] for _, _, grades in JOBS]
        retrieval.append({"resume": title, "baseline": metrics(baseline, relevance), "hybrid": metrics(ranked, relevance),
                          "top": [{"title": jobs[i].title, "score": results[i]["score"], "gold_grade": relevance[i]} for i in ranked[:3]]})
    text = "\n".join(["Built React components."] * 40 + ["Reduced dashboard rendering latency by 37% using virtualization."])
    evidence = source_facts(Profile(resume_text=text, skills=["React"]), "dashboard rendering latency virtualization")
    return {"dataset": "Synthetic expert-authored fixtures, 6 extraction cases, 3 resumes x 12 vacancies",
            "limitations": "Fixture results do not estimate real-world precision. Production evaluation needs owner-labelled relevant and irrelevant vacancies.",
            "extraction": {"skill_precision": correct / predicted if predicted else 0,
                           "skill_recall": correct / expected if expected else 0, "years_exact_match": years_correct / len(CASES),
                           "cases": parsing},
            "evidence": {"late_achievement_retrieved": any("37%" in value for value in evidence.values())},
            "retrieval": retrieval}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = measure()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    assert result["extraction"]["skill_precision"] == 1
    assert result["extraction"]["skill_recall"] == 1
    assert result["evidence"]["late_achievement_retrieved"]
