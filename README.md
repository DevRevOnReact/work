# JobPilot

Обновлённый алгоритм автооткликов, обработки вопросов и ошибок: [описание на русском](docs/auto-apply-workflow.ru.md). API и правила совместимости описаны в [application-automation.md](docs/application-automation.md).

Local, single-user resume search and employer discovery. No access key or login is needed in the default development configuration.

## Lightweight Docker start

```sh
python scripts/init_env.py
docker compose up -d --build --remove-orphans
```

Open http://localhost:3000. The default stack is **PostgreSQL + API + web**. Database migrations run before API startup. One API process owns the search thread, three-hour scheduler and application browser runtime. Chromium starts on demand. Redis, Celery workers and a separate scheduler are unnecessary. `--remove-orphans` stops old worker/scheduler/Redis containers from the previous configuration; existing database and artifact volumes are preserved. Do not use `down -v`.

Memory limits total **1,600 MiB**: database 256 MiB, API including Chromium 1,024 MiB, web 320 MiB. CPU limits total 2 cores. These are container ceilings, not measured usage or a limit on Docker Desktop's VM/build cache. Limit concurrent review sessions with `APPLICATION_SESSION_LIMIT` (default 3). Frontend builds run separately and can temporarily use more memory. The database keeps its existing named volume.

## Run without Docker

Requires Python 3.11+ and Node 22+. Install dependencies and prepare the local SQLite workspace:

```sh
pip install -r backend/requirements.runtime.lock
pip install --no-deps -e backend
pip install playwright==1.64.0
python -m playwright install chromium
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
2. Searches use the connected employer boards and show any relevant board errors.
3. Configure searches with grouped controls and removable filter chips. Comma-separated titles, companies and locations are alternatives; different fields combine with AND. Choose any/all skills and keywords. Skills respect word boundaries. All previous filter fields remain available under the grouped or advanced controls. Sort by resume score, publication date or company. Saving changed filters clears the previous filter results; run the search to refresh them.
4. Enabled searches update **every three hours**, at 00:00, 03:00, … UTC (03:00, 06:00, … Moscow), with one catch-up attempt per slot when the local API next runs. Hosted GitHub Actions uses minute 17 of each three-hour slot and works while Render sleeps. Manual and automatic searches include only vacancies published on the **current Moscow calendar day** (`SEARCH_TIMEZONE=Europe/Moscow`); undated and future-dated records are excluded. The UI shows the stage, source counts, activity time, errors and a bounded event log. Only one local search executes at a time. Failed runs preserve previous results, explicitly labeled in the UI, and are retried manually or in the next slot. No search sends applications. Keep the API running for local scheduled searches; the hosted workflow requires `JOBPILOT_SCHEDULE_ENABLED=true` and its existing secrets.
5. **Компании** replaces the old recommendations tab. Search companies by name/region, filter evidence signals, open career pages and find people on LinkedIn. Existing referral contacts are retained inside company cards. Initial referral evidence is linked to official GitLab, Wise and Atlassian pages with the scope of each program stated. JetBrains has a separately labeled regional-office signal.
6. **Найти новые компании** defaults to **Весь мир · IT-компании**, which imports the curated international employer catalog and connects its public vacancy boards without re-enabling boards you disabled. Country selection covers North America, Europe, Asia-Pacific, Israel, India, Latin America, Africa and the CIS, and examines a bounded page of public GitHub profiles for that country. Discovery runs in the background with persisted progress that survives page reloads; interrupted jobs show an explicit error. Company cards can launch **Найти вакансии**, using the selected resume and the company's connected boards. Existing country profile signals are preserved. Public profile fields do not establish nationality, current employment, hiring eligibility or willingness to refer. Official referral evidence is labeled separately from profile and catalog evidence. The catalog and GitHub pages are bounded sources, not an exhaustive internet index.

Salary/employment filters match employer text; currencies and pay periods are not normalized. Publication-age filters exclude unknown dates. Unknown workplace format is not treated as onsite. Explicit location format wins over company-office text in a description. Results persist up to 500 entries in the selected sort order, with the full count and 20 results rendered per page.

## Top vacancies and Telegram

Open the Telegram Mini App with `/top` or `/start` in the existing bot, then tap **Открыть топ вакансий**. This requires an HTTPS `PUBLIC_APP_URL` (automatically set from `RENDER_EXTERNAL_URL` on Render) and a private `TELEGRAM_CHAT_ID` belonging to the owner. Telegram signs the launch data, which the backend validates for every read and favorite update; the main site's password is not required for this limited view. The top groups matching company and job title across sources and locations and retains the highest-scored result. Notification deduplication uses both the normalized URL and normalized company/title, across searches and resumes. Capitalization, spacing and ordinary punctuation do not create a new role; meaningful technology punctuation such as C++ and C# remains distinct.

Matching v2 scores professional evidence with stack 40%, tasks 25%, role 15%,
experience 10% and domain 10%. Missing requirements redistribute their weight;
salary, education, language and geography are separate checks. Missing core or
explicitly mandatory technologies, incompatible CEFR levels, a major experience
gap, unconfirmed Staff/Principal scope and rejected geography block top delivery
regardless of the professional score. Optional skills and explicit alternatives
are handled separately. Task concepts recognize Russian and English wording;
this is an explainable rule system, not an external semantic model.
Unconfirmed geography remains visible in ordinary searches for review. A fresh,
otherwise qualifying vacancy with uncertain eligibility is also sent to Telegram
for an explicit decision; it does not enter the top automatically.

`search_locations` stores explicitly confirmed alternative locations. Each is
checked independently; it never declares work authorization. Startup reparses
stored CV text and rescales stored results once per algorithm version. Custom
filters remain intact; untouched primary role defaults gain related role titles.

`/top` is a dedicated page for vacancies published within the last **72 hours**
with a resume score of **60 or higher**. Each search scores this three-day window
in addition to keeping its normal today-only results. Top entries persist across
runs and are deduplicated by vacancy URL using the best resume score. The top
uses each search's other filters, with its own 60-point threshold and 72-hour
publication window. Unknown and future publication dates are excluded.

Both the main top page and Telegram Mini App have **Все вакансии** and
**Избранное** tabs. The heart saves an explicit, persistent favorite state.
Favorites survive the 72-hour cleanup and remain visible in the top and favorites
tab; removing the heart returns them to normal expiry. New source/resume
representatives of the same vacancy inherit the favorite. Retaining a favorite
does not extend Telegram notification freshness or automatically apply to an old
vacancy. Rejected vacancies are removed even if previously favorited.

Telegram receives **one message per qualifying vacancy**, including the complete
job URL. Persistent URL and company/title aliases prevent re-enqueueing the same
vacancy across sources, resumes, searches and review/top messages. Email digests
also deduplicate across searches by URL and company/title. Messages wait in the durable queue, respect chat pacing and
Telegram's `retry_after`, and expire when the vacancy leaves the 72-hour window.
Email digests remain available. Telegram delivery is at-least-once across a crash
after Telegram accepts a message but before the database commits the receipt.

Vacancies with uncertain geography, work authorization or workplace availability
receive a separate card with **Подтвердить** and **Отклонить** buttons. A
confirmation adds the vacancy to the top for its remaining 72-hour window; a
rejection permanently hides matching URLs or company/title pairs, including
changed descriptions, locations and links. Decisions are deduplicated across
searches. Changed vacancy/profile facts invalidate an approval and its old buttons,
without sending the same vacancy again.
They do not change the underlying eligibility assessment, assert work
authorization, or approve or send an application. An approved uncertain vacancy
may have its form prepared in the configured assisted/autonomous workflow, or
through Auto Apply. Its questions and supported answers are handed back for manual
completion while legal eligibility remains unknown; automatic submission still
requires verified eligibility and the existing submission policy.
Saved, still-fresh uncertain results are queued when the API starts. Buttons are
handled only for the configured Telegram chat, while the API or Celery worker
is running; a sleeping hosted service must be awake to process them.

Form preparation follows unambiguous Apply links and buttons (including a new
tab) through the authorized HTTPS domains until the actual form is available.
It does not follow an opener when an application form is already present and
does not click a final submit during preparation. If automation cannot finish,
Telegram and the application panel show a numbered question/answer handoff,
including unanswered questions and options. Identity, contact and upload fields
are excluded, contact details are redacted from free-text answers, and prepared
answers can be copied individually in the panel. A review confirmation is required
for uncertain vacancies before the browser can prepare their forms. Duplicate,
rejected, cancelled and already attempted applications are blocked by URL or
company/title regardless of location. Migration `0013_vacancy_identity_favorites`
preserves existing history and suppresses duplicate pending Telegram cards.

Successful searches no longer send count-only reports. Search failures and
partial source errors still produce alerts, combined where possible. Older
successful search and LinkedIn research reports already in the queue are also
suppressed. Vacancy cards, application alerts and error reports remain available.
The cloud runner queues alerts without bot credentials and wakes Render for
delivery. `/stats` and `/today` provide application counts on request.

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

### Public LinkedIn research

The top page can filter by LinkedIn. A separate Codex heartbeat researches
public job pages every three hours while the computer and Codex are running;
coverage is partial and is not LinkedIn's complete vacancy feed. It does not
connect a mailbox or use a logged-in LinkedIn session. Publication freshness
is checked on the live job page. Absolute dates are preferred; a fresh English
relative label can establish a conservative lower publication bound (one extra
time unit plus five minutes). Ambiguous ages of two or more days are excluded.
The original relative label is retained and displayed as an estimate in Telegram
and the top page. Relative ages in cached search snippets are never accepted.
Each run searches up to 12 queries and checks up to 40 distinct job pages,
rotating across React, Vue/PixiJS, full-stack and worldwide remote roles.

Research records contain the job URL, title, employer, location, factual summary,
publication timestamp, date evidence and verification timestamp. Run
`python scripts/prepare_linkedin_candidate.py vacancy.json` to validate a record
and generate a safely encoded, idempotent INSERT for the existing database.
The API processes this inbox, applies enabled searches and their selected
resumes, and computes scores itself. Records cannot supply a score. Only scores
of at least 60 enter the top and the existing Telegram outbox; messages include
a short description and the complete LinkedIn URL. `/linkedin/jobs/status`
reports processing counts behind the same authentication as other app APIs.
The normal hosted employer-board schedule remains independent of this heartbeat.
The heartbeat can queue research reports using `scripts/prepare_linkedin_report.py`;
normal count-only reports are suppressed on delivery while errors remain visible.
Its counts must come from the actual check and database processing state; pending
candidates are not reported as delivered. The same run timestamp makes retries
idempotent.

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

Application preparation runs in the API process without Redis/Celery. Existing application history is retained; old apply endpoints/tasks now queue preparation for review. Server-to-server authentication can be explicitly enabled with `AUTH_REQUIRED=true` and a strong `API_TOKEN`; there is no browser key-entry flow. This default keyless configuration is intended for local development.

## Automatic job applications

In **Резюме и профиль → Автоматические отклики**, enter your verified contact details, location, work authorization and exact authorized employer/ATS domains (for example `jobs.ashbyhq.com`, `jobs.lever.co`, `job-boards.greenhouse.io`). Wildcard trust is unsupported. A source's presence in the job catalog alone does not authorize sharing your CV. The default mode is manual and autonomous submission is disabled.

Click **Auto Apply** on a saved search result or top vacancy. The agent selects an intact uploaded resume using existing matching, creates a deterministic ATS-friendly DOCX, opens Chromium, fills recognized fields and uploads the document. The card shows answers, the selected source resume, generated DOCX, browser screenshot and action log. **Разрешить отправку работодателю** approves the current document, answers, profile and filled form. Changes invalidate approval. Only a fresh visible success confirmation after the actual click marks a submission as complete.

- **Manual:** prepare a selected vacancy, then wait for approval.
- **Assisted:** periodically select compatible saved results above the threshold and prepare them for individual approval.
- **Autonomous:** requires a separate explicit opt-in, compatible location/employment conditions and authorized domains. Daily limits, excluded companies and required approval categories apply immediately. Legal/personal/salary answers still require verified facts or exact previously approved answers. Default sensitive categories require individual application approval.

Greenhouse, Ashby, Lever, Workable and SmartRecruiters adapters share accessible-form handling; a conservative fallback handles custom authorized domains. Native controls, accessible dropdowns, conditional fields, uploads and bounded multi-step forms are supported. Unrecognized controls, PDF-only CV requirements, protected upload hosts or ambiguous submit buttons pause for help instead of guessing. Arbitrary website compatibility is not guaranteed. Public posting URLs from aggregators may need the employer's direct application link added through the existing manual-job API; the agent never invents a destination.

Locally Chromium is visible (`APPLICATION_BROWSER_HEADLESS=false`). Complete login/CAPTCHA/MFA in that exact agent browser, then resume the application. Browser sessions stay alive up to `APPLICATION_SESSION_SECONDS=900`. Opening the employer link in another browser does not share its session. Containers/cloud use headless Chromium; this version has no remote browser control, so interactive authentication there requires an operator-accessible browser deployment. No CAPTCHA bypass or stored plaintext credentials are used. Browser cookies are memory-only; after session expiry/restart you may need to sign in again.

Pause, resume and cancel are durable. Pre-submit work can be reconstructed after a restart. A committed submit intent permanently blocks repeated Submit, even on timeout/crash; **Проверить подтверждение** only observes the surviving session. Without it, check the employer's confirmation or email manually. Existing outcome recording accepts explicitly documented manual evidence. Telegram uses the existing durable, rate-limit aware notification queue. Documents/screenshots use the existing artifact directory and authenticated endpoints. With `STORE_RESUME_FILES_IN_DB=true`, generated DOCX and screenshots are also retained in the database and remain downloadable after ephemeral disk loss, like source CVs.

See [application automation details](docs/application-automation.md) for API and test commands.

See [architecture](docs/architecture.md) and [filter research](docs/filter-research.md).
