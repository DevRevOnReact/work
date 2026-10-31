"""Verify a published commit, send its idempotent Telegram test, and populate the top."""
import json
import os
import sys
import time
from urllib.parse import urlsplit

import httpx


def main() -> None:
    url = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
    password = os.environ.get("LOGIN_PASSWORD", "")
    expected = os.environ.get("EXPECTED_COMMIT", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or not password:
        raise RuntimeError("Set an HTTPS PUBLIC_APP_URL and LOGIN_PASSWORD in GitHub")
    with httpx.Client(base_url=url, auth=(os.environ.get("LOGIN_USER", "jobpilot"), password), timeout=120) as client:
        def api(path: str, method: str = "GET") -> dict | list:
            response = client.request(method, "/api/backend/" + path)
            if response.status_code in {401, 403}:
                raise RuntimeError("Application login rejected; check the GitHub LOGIN_PASSWORD secret")
            if response.status_code == 409:
                raise RuntimeError("Telegram or the resume search is not configured on the hosting service")
            response.raise_for_status()
            return response.json()

        deadline = time.monotonic() + 900
        while True:
            try:
                release = api("release")
                if release.get("commit") == expected:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Expected deployment did not become ready")
            time.sleep(10)
        client.get("/top").raise_for_status()
        receipt = api("notifications/deployment-test", "POST")
        while receipt["status"] == "pending" and time.monotonic() < deadline:
            time.sleep(10)
            receipt = api("notifications/deployment-test", "POST")
        if receipt["status"] != "sent" or not receipt.get("provider_message_id"):
            raise RuntimeError("Telegram did not acknowledge the deployment test; check notification status")
        print(json.dumps({"deployment": expected, "telegram_test": receipt["status"],
                          "telegram_message_id": receipt["provider_message_id"]}), flush=True)
        previous_failures = api("notifications/status")["telegram_counts"].get("failed", 0)

        queued = api("discover", "POST")
        print(json.dumps({"searches_queued": queued.get("searches", 0)}), flush=True)
        deadline = time.monotonic() + 1200
        while True:
            searches = api("searches")
            active = [item for item in searches if item["status"] in {"queued", "running"}]
            if not active:
                if any(item["enabled"] and item["status"] == "failed" for item in searches):
                    raise RuntimeError("Search backfill failed; check the application's search log")
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Searches are still running; inspect the application's search log")
            print(json.dumps({"searches_running": len(active)}), flush=True)
            time.sleep(10)
        top = api("top-vacancies")
        assert top["window_hours"] == 72 and top["min_score"] == 60
        assert all(job["score"] >= 60 and job["canonical_url"].startswith("https://") for job in top["items"])
        print(json.dumps({"top_vacancies": top["total"]}), flush=True)
        deadline = time.monotonic() + 1200
        while True:
            status = api("notifications/dispatch", "POST")
            counts = status["telegram_counts"]
            print(json.dumps({"telegram_counts": counts}), flush=True)
            if not counts.get("pending", 0):
                if counts.get("failed", 0) > previous_failures:
                    raise RuntimeError("Some Telegram messages failed; inspect notification status")
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Telegram delivery is still pending; the durable queue will keep retrying")
            time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # HTTP request URLs and authentication must never appear in workflow logs.
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print("Release verification failed: " + detail, flush=True)
        sys.exit(1)
