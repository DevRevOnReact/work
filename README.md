# JobPilot

Local, single-user resume search and employer discovery. No access key or login is needed in the default development configuration.

## Lightweight Docker start

```sh
python scripts/init_env.py
docker compose up -d --build --remove-orphans
```

Open http://localhost:3000. The default stack is **PostgreSQL + API + web**. Database migrations run before API startup. One API process owns a single background search thread and the three-hour scheduler. Redis, Celery workers, a separate scheduler and Chromium are absent from the default stack. `--remove-orphans` stops old worker/scheduler/Redis containers from the previous configuration; existing database and artifact volumes are preserved. Do not use `down -v`.

Steady-state memory limits total **1,088 MiB**: database 256 MiB, API 512 MiB, web 320 MiB. CPU limits total 2 cores. These are container ceilings, not measured usage or a limit on Docker Desktop's VM/build cache. Frontend builds run separately and can temporarily use more memory. The database keeps its existing named volume.

## Run without Docker

Requires Python 3.11+ and Node 22+. Install dependencies and prepare the local SQLite workspace:

```sh
pip install -r backend/requirements.runtime.lock
pip install --no-deps -e backend
python scripts/setup_searches.py --local resume.pdf
python scripts/run_local.py
```

In another terminal:

```sh
cd frontend
npm ci
npm run build
npm start
```

Both native servers bind to `127.0.0.1`. SQLite data lives in `data/jobpilot-local.db` and uploaded files in `data/resumes`. This is a separate workspace from the PostgreSQL/artifact volumes used by Docker; it does not import data from those volumes automatically. Repeat `setup_searches.py --local file1.pdf file2.docx` to import more files. Matching file hashes are not imported twice. Omit `--local` only when intentionally targeting the configured database.

## Workflow

### Worldwide sources and frontend shortlist

The starter catalog contains 100 verified employer boards plus Remotive's
international remote feed and Arbeitnow's European multi-company feed. Existing
sources are retained. There is no country restriction unless the saved search
adds one; remote roles may still restrict the candidate's country.

The local **Весь мир · Frontend / Vue / React / PixiJS** search uses the uploaded
resume and all enabled sources. In **Компании**, select **Топ-100 по вашему стеку**
to see the numbered shortlist, current vacancy examples and matching technologies.
This ranks public vacancy evidence, not guaranteed eligibility or an offer.
Some shortlist employers are retained for future openings.

To revalidate candidate boards and rebuild the local shortlist:

```sh
python scripts/load_global_sources.py
```

This script explicitly targets the local SQLite workspace, preserves existing
search filters and sources, and writes its validation report to
`data/global-source-report.json`. It currently targets the uploaded frontend
resume's Vue/React/TypeScript/PixiJS stack. Remotive responses are cached on disk
for six hours and retain attribution and links to Remotive. Arbeitnow reads up
to five pages per run. These feeds extend coverage; they are not an index of
every company worldwide.

1. Upload PDF, DOCX or TXT resumes (maximum 10 MB). A search is created automatically for every readable new resume. Existing uploads are configured at API startup, and **Настроить поиски для всех резюме** fills any gaps. Existing searches retain every filter, schedule and selected resume. Common location, relocation, salary and authorization preferences are retained. Each search uses the skills and experience of its own selected resume. Scanned PDFs require OCR before upload.
2. In **Источники вакансий**, connect public employer boards on Greenhouse, Ashby or Lever. Existing boards remain connected. The optional starter list expands coverage. Board errors are shown on both sources and affected searches.
3. Configure searches with grouped controls and removable filter chips. Comma-separated titles, companies and locations are alternatives; different fields combine with AND. Choose any/all skills and keywords. Skills respect word boundaries. All previous filter fields remain available under the grouped or advanced controls. Sort by resume score, publication date or company. Saving changed filters clears the previous filter results; run the search to refresh them.
4. Enabled searches update **every three hours**, at 00:00, 03:00, … UTC (03:00, 06:00, … Moscow), with one catch-up attempt per slot when the local API next runs. Hosted GitHub Actions uses minute 17 of each three-hour slot and works while Render sleeps. Manual and automatic searches include only vacancies published on the **current Moscow calendar day** (`SEARCH_TIMEZONE=Europe/Moscow`); undated and future-dated records are excluded. The UI shows the stage, source counts, activity time, errors and a bounded event log. Only one local search executes at a time. Failed runs preserve previous results, explicitly labeled in the UI, and are retried manually or in the next slot. No search sends applications. Keep the API running for local scheduled searches; the hosted workflow requires `JOBPILOT_SCHEDULE_ENABLED=true` and its existing secrets.
5. **Компании** replaces the old recommendations tab. Search companies by name/region, filter evidence signals, open career pages and find people on LinkedIn. Existing referral contacts are retained inside company cards. Initial referral evidence is linked to official GitLab, Wise and Atlassian pages with the scope of each program stated. JetBrains has a separately labeled regional-office signal.
6. **Найти новые компании** defaults to **Весь мир · IT-компании**, which imports the curated international employer catalog and connects its public vacancy boards without re-enabling boards you disabled. Country selection covers North America, Europe, Asia-Pacific, Israel, India, Latin America, Africa and the CIS, and examines a bounded page of public GitHub profiles for that country. Discovery runs in the background with persisted progress that survives page reloads; interrupted jobs show an explicit error. Company cards can launch **Найти вакансии**, using the selected resume and the company's connected boards. Existing country profile signals are preserved. Public profile fields do not establish nationality, current employment, hiring eligibility or willingness to refer. Official referral evidence is labeled separately from profile and catalog evidence. The catalog and GitHub pages are bounded sources, not an exhaustive internet index.

Salary/employment filters match employer text; currencies and pay periods are not normalized. Publication-age filters exclude unknown dates. Unknown workplace format is not treated as onsite. Explicit location format wins over company-office text in a description. Results persist up to 500 entries in the selected sort order, with the full count and 20 results rendered per page.

## Top vacancies and Telegram

`/top` is a dedicated page for vacancies published within the last **72 hours**
with a resume score of **60 or higher**. Each search scores this three-day window
in addition to keeping its normal today-only results. Top entries persist across
runs and are deduplicated by vacancy URL using the best resume score. The top
uses each search's other filters, with its own 60-point threshold and 72-hour
publication window. Unknown and future publication dates are excluded.

Telegram receives **one message per qualifying vacancy**, including the complete
job URL. A shared persistent key prevents re-enqueueing the same URL across
resumes and searches. Messages wait in the durable queue, respect chat pacing and
Telegram's `retry_after`, and expire when the vacancy leaves the 72-hour window.
Email digests remain available. Telegram delivery is at-least-once across a crash
after Telegram accepts a message but before the database commits the receipt.

After successful CI on `main`, **Verify deployment and Telegram** waits for the
exact Render commit, calls the idempotent deployment-test endpoint, records the
Telegram message ID, refreshes searches to populate the top immediately and
drains Telegram's queue. It uses the existing GitHub `DATABASE_URL` secret and
`PUBLIC_APP_URL` variable to write the protected database outbox and wake the
public health endpoint; bot credentials remain on Render and browser passwords
are not needed by the scheduled jobs. One test message is
sent per deployed commit. The workflow can also be dispatched manually.

`python scripts/ui_top_smoke.py` verifies the dedicated route, links, pagination,
reload, refresh and mobile layout without changing user records.

## Development checks

```sh
pip install -r backend/requirements.lock
pip install --no-deps -e backend
python -m pytest backend/tests -q
python -m mypy --config-file backend/pyproject.toml backend/jobpilot
cd frontend
npm run typecheck
npm run build
```

For a running local app: `python scripts/ui_smoke.py` checks navigation, keyless entry, cross-origin write rejection, company search, filter chips and desktop/mobile layout. `python scripts/ui_search_smoke.py` uses controlled API fixtures to check live progress, errors, worldwide discovery and launching company searches without modifying user records. These require the development Playwright dependency and its browser. `python scripts/live_sources.py --catalog --companies` checks all catalog feeds and GitHub discovery in the US, Germany and Singapore without changing database records, writing `data/source-api-audit.json`. PostgreSQL integration tests require a disposable `TEST_POSTGRES_URL`.

Legacy application history and APIs remain compatible; legacy application automation is not scheduled or installed in the default runtime. Its Python dependencies are available through the optional `automation` extra and Docker `automation` target. Server-to-server authentication can be explicitly enabled with `AUTH_REQUIRED=true` and a strong `API_TOKEN`; there is no browser key-entry flow. This default keyless configuration is intended for local development.

See [architecture](docs/architecture.md) and [filter research](docs/filter-research.md).
