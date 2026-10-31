"""Wake the private app and send queued digests after the daily database search."""
import os
import time
from urllib.parse import urlsplit

import httpx


def main() -> None:
    url = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
    password = os.environ.get("LOGIN_PASSWORD", "")
    if not url or not password:
        raise RuntimeError("PUBLIC_APP_URL and LOGIN_PASSWORD must be configured in GitHub")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("PUBLIC_APP_URL must be an HTTPS application URL")
    for attempt in range(3):
        try:
            response = httpx.post(url + "/api/backend/notifications/dispatch",
                auth=(os.environ.get("LOGIN_USER", "jobpilot"), password), timeout=120)
            response.raise_for_status()
            result = response.json()
            print(f"Notification channels: telegram={result['telegram']}, email={result['email']}; counts={result['counts']}")
            return
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}:
                raise RuntimeError("Application login rejected; update the GitHub LOGIN_PASSWORD secret") from None
            if attempt == 2:
                raise RuntimeError("Notification delivery endpoint remained unavailable") from None
            time.sleep(20)


if __name__ == "__main__":
    main()
