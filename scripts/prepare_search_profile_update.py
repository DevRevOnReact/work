"""Prepare an idempotent profile/CV update for the existing database connector.

Keeps the old file and resume record; only searches selecting that exact old
SHA-256 are moved to the supplied replacement. SQL contains encoded document
data and must be kept outside Git.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def prepare(content: bytes, filename: str, old_sha: str, locations: list[str]) -> list[str]:
    from jobpilot.resumes import parse_resume
    if Path(filename).name != filename or Path(filename).suffix.lower() != ".pdf":
        raise ValueError("Expected a PDF filename without a directory")
    if not re.fullmatch(r"[a-f0-9]{64}", old_sha):
        raise ValueError("Expected the exact SHA-256 of the previously selected resume")
    if not locations or any(place not in {"Serbia", "Georgia"} for place in locations):
        raise ValueError("Pass only the locations explicitly confirmed for this update")
    parsed = parse_resume(content, filename)
    sha = hashlib.sha256(content).hexdigest()
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, "jobpilot-resume:" + sha))
    def json_value(value: object) -> str:
        encoded = base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()
        return f"convert_from(decode('{encoded}','base64'),'UTF8')::jsonb"
    record = {"filename": filename, "sha256": sha, "text": parsed.text, "facts": parsed.facts,
              "warnings": parsed.warnings, "media_type": "application/pdf"}
    chosen = f"(SELECT id FROM resumes WHERE sha256='{sha}' ORDER BY created_at,id LIMIT 1)"
    skills = ", ".join(parsed.facts["skills"])[:300]
    data = base64.b64encode(content).decode()
    return [
        "SELECT id FROM candidates WHERE id=1 FOR UPDATE",
        "INSERT INTO resumes (id,variant,filename,storage_key,sha256,created_at) "
        f"SELECT '{identity}','frontend',({json_value(filename)}) #>> '{{}}','{identity}.pdf','{sha}',CURRENT_TIMESTAMP "
        f"WHERE NOT EXISTS (SELECT 1 FROM resumes WHERE sha256='{sha}') ON CONFLICT (id) DO NOTHING",
        f"INSERT INTO resume_files (resume_id,content) SELECT {chosen},decode('{data}','base64') "
        "ON CONFLICT (resume_id) DO NOTHING",
        "UPDATE candidates SET facts=((facts::jsonb - 'search_scoring_version') || "
        f"jsonb_build_object('search_locations',{json_value(list(dict.fromkeys(locations)))},'resumes',"
        f"COALESCE(facts::jsonb->'resumes','{{}}'::jsonb)||jsonb_build_object({chosen},{json_value(record)})))::json,"
        "version=version+1 WHERE id=1",
        f"UPDATE saved_searches SET resume_id={chosen},filters=(CASE WHEN name LIKE '%· основной поиск' "
        f"AND filters->>'skills_mode'='any' THEN filters::jsonb||jsonb_build_object('skills',{json_value(skills)}) "
        "ELSE filters::jsonb END)::json "
        f"WHERE resume_id IN (SELECT id FROM resumes WHERE sha256='{old_sha}')",
        f"SELECT {chosen} AS selected_resume_id,(SELECT count(*) FROM saved_searches WHERE resume_id={chosen}) AS updated_searches",
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("resume", type=Path)
    parser.add_argument("--replace-searches-from-sha", required=True)
    parser.add_argument("--location", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    statements = prepare(args.resume.read_bytes(), args.resume.name, args.replace_searches_from_sha, args.location)
    args.output.write_text(json.dumps(statements, ensure_ascii=False), encoding="utf-8")
    print("Prepared profile update. Old resume artifacts remain available; no database changes performed.")
