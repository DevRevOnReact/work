"""Verify the built web app rejects anonymous access and fails closed."""
import base64
import os
from pathlib import Path
import secrets
import subprocess
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def check(password: str, port: int) -> None:
    env = dict(os.environ, HOSTED_MODE="true", LOGIN_USER="jobpilot",
               LOGIN_PASSWORD=password, PORT=str(port))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(["node", "scripts/start-local.mjs"], cwd=ROOT / "frontend",
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=flags)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
            for attempt in range(60):
                try:
                    response = client.get("/")
                    break
                except httpx.ConnectError:
                    if process.poll() is not None:
                        raise RuntimeError("Web test server failed to start")
                    time.sleep(0.2)
            else:
                raise RuntimeError("Web test server was not ready")
            if password:
                assert response.status_code == 401
                assert client.get("/api/backend/profile").status_code == 401
                assert client.get("/api/backend/profile", auth=("jobpilot", "wrong")).status_code == 401
                assert client.get("/", auth=("jobpilot", password)).status_code == 200
                assert client.get("/api/backend/searches", auth=("jobpilot", password)).status_code == 200
                assert client.get("/health").status_code == 200
            else:
                assert response.status_code == 503
                assert client.get("/api/backend/profile").status_code == 503
    finally:
        process.terminate()
        process.wait(timeout=15)


if __name__ == "__main__":
    check(secrets.token_urlsafe(32), 3001)
    check("", 3002)
    print("Cloud login verified: anonymous denied, valid login accepted, missing secret fails closed.")
