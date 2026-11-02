"""Validate a public research record and emit SQL with safely encoded public data.

Usage: python scripts/prepare_linkedin_candidate.py vacancy.json
Run the resulting single INSERT through the existing Neon connector. No secrets needed.
"""
import base64
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def prepare(record: dict) -> str:
    from jobpilot.linkedin_jobs import LinkedInJob, candidate_id
    from jobpilot.models import now
    from datetime import timedelta
    item = LinkedInJob.model_validate(record)
    current = now()
    assert item.published_at is not None
    if not current - timedelta(hours=72) <= item.published_at <= item.verified_at <= current:
        raise ValueError("Publication is not verified within the current 72-hour window")
    if item.verified_at < current - timedelta(hours=24):
        raise ValueError("Verification is stale")
    payload = base64.b64encode(json.dumps(item.model_dump(mode="json"), ensure_ascii=False).encode()).decode()
    return ("INSERT INTO linkedin_candidates (id, data, status, result, created_at) VALUES ("
            f"'{candidate_id(item.canonical_url)}', convert_from(decode('{payload}', 'base64'), 'UTF8')::json, "
            "'pending', '{}'::json, CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING RETURNING id;")


if __name__ == "__main__":
    try:
        record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
        print(prepare(record))
    except Exception as exc:
        print("Candidate rejected: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
