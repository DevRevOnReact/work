"""Queue all three-day Telegram hits in PostgreSQL and wake the app to deliver them."""
import sys
from verify_release import application_url, queue_top_alerts, require_database, wait_for_telegram, wake
import httpx


def main():
    require_database()
    url = application_url()
    print(f'Top Telegram alerts queued: {queue_top_alerts()}', flush=True)
    with httpx.Client(timeout=120) as client:
        wake(client, url)
        wait_for_telegram(client, url)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print('Notification delivery failed: ' + detail, flush=True)
        sys.exit(1)
