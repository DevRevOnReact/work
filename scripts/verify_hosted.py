"""Read-only hosted smoke check with hidden login input."""
import argparse
import getpass
import hashlib
import sys

import httpx


def verify(url: str, password: str) -> None:
    with httpx.Client(base_url=url, timeout=45) as anonymous:
        assert anonymous.get("/").status_code == 401
        assert anonymous.get("/api/backend/profile").status_code == 401
        assert anonymous.get("/health").status_code == 200
    with httpx.Client(base_url=url, timeout=45, auth=("jobpilot", password)) as client:
        assert client.get("/").status_code == 200
        sources = client.get("/api/backend/sources")
        sources.raise_for_status()
        assert len(sources.json()) >= 100
        searches = client.get("/api/backend/searches")
        searches.raise_for_status()
        assert searches.json()
        companies = client.get("/api/backend/companies?signal=resume_fit")
        companies.raise_for_status()
        assert len(companies.json()) == 100
        resumes = client.get("/api/backend/resumes")
        resumes.raise_for_status()
        for resume in resumes.json():
            response = client.get(f"/api/backend/resumes/{resume['id']}/file")
            response.raise_for_status()
            assert hashlib.sha256(response.content).hexdigest() == resume["sha256"]
        path = "/api/backend/companies/discover"
        assert client.post(path, headers={"Origin": "https://unrelated.example"},
                           json={"country": "invalid"}).status_code == 403
        assert client.post(path, headers={"Origin": url},
                           json={"country": "invalid"}).status_code == 422
        status = client.get("/api/backend/notifications/status")
        status.raise_for_status()
        print({"health": "ready", "private_login": True, "csrf": True,
               "sources": len(sources.json()), "top_companies": len(companies.json()),
               "resumes_verified": len(resumes.json()),
               "searches": [(s["status"], s["result_count"]) for s in searches.json()],
               "telegram": status.json()["telegram"], "email": status.json()["email"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("Interactive hidden input is required.")
    password = getpass.getpass("Application password (hidden): ")
    try:
        verify(args.url.rstrip("/"), password)
    except Exception as exc:
        print(f"Hosted verification failed: {type(exc).__name__}")
        raise SystemExit(1)
