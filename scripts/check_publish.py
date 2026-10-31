"""Audit prospective Git files without printing private values."""
from pathlib import Path
import json
import re
import sqlite3
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    command = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    tracked = subprocess.check_output(command + ["ls-files", "-z"], cwd=ROOT)
    untracked = subprocess.check_output(command + ["ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    paths = sorted(set(p.decode("utf-8") for p in (tracked + untracked).split(b"\0") if p))
    secrets = []
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            if any(word in key.upper() for word in ("TOKEN", "PASSWORD", "SECRET", "API_KEY")) and len(value) >= 12:
                secrets.append(value)
    failures = []
    private_values = []
    state = ROOT / "data/cloud-state.json"
    if state.exists():
        private_values.extend(str(v) for k, v in json.loads(state.read_text(encoding="utf-8")).items()
                              if k.endswith("_id") and v)
    database = ROOT / "data/jobpilot-local.db"
    if database.exists():
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
            row = db.execute("SELECT facts FROM candidates WHERE id=1").fetchone()
            if row:
                facts = json.loads(row[0])
                private_values.extend(str(facts[k]) for k in ("full_name", "email", "phone")
                                      if facts.get(k) and len(str(facts[k])) >= 6)
    for name in paths:
        path = Path(name)
        if ((path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower() in {".pdf", ".docx", ".db", ".pem", ".key", ".zip"}
            or path.parts[0] in {"data", ".venv", ".test-tmp"}
            or any(part in {"node_modules", ".next", "__pycache__"} for part in path.parts)):
            failures.append(f"{name}: private file or generated directory")
            continue
        content = (ROOT / path).read_text(encoding="utf-8")
        if any(value in content for value in secrets):
            failures.append(f"{name}: matches a configured secret")
        if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})", content):
            failures.append(f"{name}: possible GitHub credential")
        if re.search(r"postgres(?:ql)?(?:\+psycopg)?://[^\s:]+:[^\s@]{16,}@", content):
            failures.append(f"{name}: possible database credential")
        if any(value in content for value in private_values):
            failures.append(f"{name}: private workspace metadata")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Publish audit passed: {len(paths)} source/configuration files; no private files or configured secrets.")


if __name__ == "__main__":
    main()
