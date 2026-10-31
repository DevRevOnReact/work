"""Supervise the API and web server in one free Render web service."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if os.environ.get("HOSTED_MODE") != "true":
        raise RuntimeError("Cloud launcher requires HOSTED_MODE=true")
    if os.environ.get("AUTH_REQUIRED") != "true" or len(os.environ.get("API_TOKEN", "")) < 32:
        raise RuntimeError("Set AUTH_REQUIRED=true and a strong API_TOKEN")
    if len(os.environ.get("LOGIN_PASSWORD", "")) < 16:
        raise RuntimeError("Set LOGIN_PASSWORD to at least 16 characters")
    database = os.environ.get("DATABASE_URL", "")
    if not database.startswith("postgresql+psycopg://"):
        raise RuntimeError("Set a persistent postgresql+psycopg DATABASE_URL")
    public_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if public_url:
        os.environ.setdefault("APP_ORIGIN", public_url)
        os.environ.setdefault("PUBLIC_APP_URL", public_url)
    subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"),
                    "upgrade", "head"], cwd=ROOT / "backend", check=True)
    environment = dict(os.environ, HOSTNAME="0.0.0.0", PORT=os.environ.get("PORT", "10000"),
                       API_INTERNAL_URL="http://127.0.0.1:8000")
    children = []
    def stop(signum: int, frame: object) -> None:
        for child in children:
            if child.poll() is None:
                child.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "jobpilot.api:app",
            "--host", "127.0.0.1", "--port", "8000", "--workers", "1"], cwd=ROOT, env=environment))
        children.append(subprocess.Popen(["node", "server.js"], cwd=ROOT / "web", env=environment))
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
    finally:
        stop(0, None)
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
