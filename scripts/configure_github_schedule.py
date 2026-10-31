"""Configure encrypted GitHub Actions secrets using existing Git credentials.

Deployment-only helper: pip install PyNaCl first. No credentials are printed.
"""
import argparse
import base64
import getpass
import os
import subprocess
import sys

import httpx
from nacl import public, encoding


def configure(repository: str, url: str, email: str, database: str, password: str) -> None:
    from sqlalchemy.engine import make_url
    parsed = make_url(database)
    if (parsed.drivername != "postgresql+psycopg" or not parsed.password or
            not parsed.host or not parsed.host.endswith(".neon.tech")):
        raise ValueError("Invalid Neon database URL")
    if len(password) < 16:
        raise ValueError("Application password is too short")
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                            text=True, capture_output=True, env=environment, check=True)
    credential = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    token = credential.get("password", "")
    if not token:
        raise RuntimeError("No Git credential")
    with httpx.Client(base_url="https://api.github.com", timeout=30,
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/vnd.github+json"}) as client:
        repo = client.get(f"/repos/{repository}")
        repo.raise_for_status()
        if not repo.json().get("permissions", {}).get("admin"):
            raise RuntimeError("Repository administration access is required")
        # Do not overwrite environment protection rules if it already exists.
        target = client.get(f"/repos/{repository}/environments/production")
        if target.status_code == 404:
            response = client.put(f"/repos/{repository}/environments/production", json={})
            response.raise_for_status()
        else:
            target.raise_for_status()
        response = client.get(f"/repos/{repository}/actions/secrets/public-key")
        response.raise_for_status()
        key = response.json()
        cipher = public.SealedBox(public.PublicKey(key["key"], encoding.Base64Encoder()))
        for name, value in {"DATABASE_URL": database, "LOGIN_PASSWORD": password}.items():
            encrypted = base64.b64encode(cipher.encrypt(value.encode())).decode()
            response = client.put(f"/repos/{repository}/actions/secrets/{name}",
                                  json={"encrypted_value": encrypted, "key_id": key["key_id"]})
            response.raise_for_status()
        variables = {"PUBLIC_APP_URL": url, "NOTIFICATION_EMAIL": email, "JOBPILOT_SCHEDULE_ENABLED": "true"}
        for name, value in variables.items():
            existing = client.get(f"/repos/{repository}/actions/variables/{name}")
            if existing.status_code == 404:
                response = client.post(f"/repos/{repository}/actions/variables", json={"name": name, "value": value})
            else:
                existing.raise_for_status()
                response = client.patch(f"/repos/{repository}/actions/variables/{name}", json={"name": name, "value": value})
            response.raise_for_status()
        response = client.get(f"/repos/{repository}/actions/secrets")
        response.raise_for_status()
        names = {item["name"] for item in response.json()["secrets"]}
        assert {"DATABASE_URL", "LOGIN_PASSWORD"} <= names
    print("GitHub schedule configured: encrypted DATABASE_URL and LOGIN_PASSWORD; daily job enabled.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--encoded-input", action="store_true",
                        help="Read base64-encoded secrets to avoid terminal special-character transformations")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("Interactive hidden input is required.")
    database = getpass.getpass("Database URL (hidden): ")
    password = getpass.getpass("Application login password (hidden): ")
    try:
        if args.encoded_input:
            database = base64.b64decode(database, validate=True).decode()
            password = base64.b64decode(password, validate=True).decode()
        configure(args.repository, args.url, args.email, database, password)
    except Exception as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        print(f"GitHub configuration failed: {type(exc).__name__}; HTTP status: {status}")
        raise SystemExit(1)
