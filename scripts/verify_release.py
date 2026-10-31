"""Verify a release through public health and the existing protected database outbox."""
import json
import os
from pathlib import Path
import sys
import time
import traceback
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def application_url() -> str:
    url = os.environ.get('PUBLIC_APP_URL', '').rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError('Set an HTTPS PUBLIC_APP_URL in GitHub')
    return url


def require_database() -> None:
    if not os.environ.get('DATABASE_URL', '').startswith('postgresql+psycopg://'):
        raise RuntimeError('Set the existing persistent PostgreSQL DATABASE_URL secret in GitHub')


def wake(client: httpx.Client, url: str) -> dict:
    response = client.get(url + '/health')
    response.raise_for_status()
    return response.json()


def queue_top_alerts() -> int:
    from jobpilot.db import SessionLocal
    from jobpilot.models import SavedSearch
    from jobpilot.notifications import enqueue_top_telegram
    from jobpilot.top_vacancies import top_rows
    queued = 0
    with SessionLocal() as db:
        for row in top_rows(db):
            if db.get(SavedSearch, row.search_id) is not None:
                queued += enqueue_top_telegram(db, row.search_id, [row.data], enqueue_without_credentials=True)
        db.commit()
    return queued


def wait_for_telegram(client: httpx.Client, url: str, *, identity: str | None = None, seconds: int = 1200) -> dict:
    from sqlalchemy import func, select
    from jobpilot.db import SessionLocal
    from jobpilot.models import SearchNotification
    deadline = time.monotonic() + seconds
    while True:
        with SessionLocal() as db:
            if identity:
                item = db.get(SearchNotification, identity)
                if item and item.status == 'sent' and item.provider_message_id:
                    return {'telegram_test': item.status, 'telegram_message_id': item.provider_message_id}
                if item and item.error:
                    raise RuntimeError('Telegram delivery error: ' + item.error)
                if item and item.status == 'failed':
                    raise RuntimeError('Telegram rejected the deployment test')
            else:
                rows = db.execute(select(SearchNotification.status, func.count()).where(
                    SearchNotification.channel == 'telegram').group_by(SearchNotification.status)).all()
                counts = dict(rows)
                print(json.dumps({'telegram_counts': counts}), flush=True)
                if not counts.get('pending', 0):
                    return counts
        if time.monotonic() >= deadline:
            raise RuntimeError('Telegram remains pending; check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on Render')
        try:
            wake(client, url)
        except httpx.HTTPError:
            pass
        time.sleep(10)


def main() -> None:
    require_database()
    url, expected = application_url(), os.environ.get('EXPECTED_COMMIT', '')
    if not expected:
        raise RuntimeError('Set EXPECTED_COMMIT')
    with httpx.Client(timeout=120) as client:
        deadline = time.monotonic() + 900
        while True:
            try:
                health = wake(client, url)
                if health.get('commit') == expected:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Expected deployment did not become ready')
            time.sleep(10)
        from sqlalchemy import select
        from jobpilot.db import SessionLocal
        from jobpilot.models import SavedSearch
        from jobpilot.notifications import queue_deployment_test
        from jobpilot.search_runner import run_saved_search
        from jobpilot.top_vacancies import top_rows
        with SessionLocal() as db:
            test = queue_deployment_test(db, expected)
            identity = test.id
            db.commit()
        wake(client, url)
        print(json.dumps({'deployment': expected, 'telegram_test': 'queued'}), flush=True)
        with SessionLocal() as db:
            ids = list(db.scalars(select(SavedSearch.id).where(SavedSearch.enabled.is_(True))))
        for search_id in ids:
            run_saved_search(search_id)
            with SessionLocal() as db:
                search = db.get(SavedSearch, search_id)
                if search and search.status == 'failed':
                    raise RuntimeError('Search backfill failed; inspect the application search log')
            print(json.dumps({'search_refreshed': True}), flush=True)
            wake(client, url)
        print(json.dumps({'top_alerts_queued': queue_top_alerts()}), flush=True)
        with SessionLocal() as db:
            print(json.dumps({'top_vacancies': len(top_rows(db))}), flush=True)
        print(json.dumps(wait_for_telegram(client, url, identity=identity, seconds=180)), flush=True)
        wait_for_telegram(client, url)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        frames = [f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}' for frame in traceback.extract_tb(exc.__traceback__)]
        print('Release verification failed: ' + detail + '; locations=' + ' -> '.join(frames), flush=True)
        sys.exit(1)
