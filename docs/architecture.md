# Architecture

## Runtime

Next.js provides the UI and same-origin backend proxy. FastAPI owns PostgreSQL (Docker) or SQLite (native local development). One API process starts `SearchRuntime`, which holds a single search executor thread and checks due three-hour searches every 30 seconds. No Redis, Celery or browser imports are required by the default search runtime.

Searches are claimed with a conditional database UPDATE before entering the queue, so duplicate manual/scheduled requests cannot queue the same search twice. Queue/running state and results live in `saved_searches`; process restart marks interrupted local work as failed while preserving the last results. A running search fetches feeds with concurrency three and an eight-minute I/O bound. A five-minute in-process cache is bounded to 64 feeds or 15,000 jobs and shares successful feeds across resume searches. It is only an optimization, not the source of truth.

`schedule.py` supplies the same three-hour UTC slots to the embedded scheduler, GitHub Actions runner and optional Celery scheduler. A failed run does not loop within its slot. Freshness uses publication time and the Moscow calendar day; unknown dates and timestamps after evaluation time are excluded before scoring. Network I/O holds no database transaction. Progress commits independently from feed loading and results, so polling stays available in SQLite. Each snapshot retains at most 150 events, stage, timestamp and counters; starting a new run clears old errors and completion time. Failed runs retain previous results with an explicit UI label. Multiple Uvicorn processes must not own the embedded runtime; use one API process as configured. SQLite remains a local development database, not a migration of the user's Docker data.

Company discovery returns HTTP 202 from `/companies/discover/start` and runs a bounded background task. `company_discoveries` stores the region, progress and result; `/companies/discoveries` restores the visible state after navigation/reload and marks jobs older than five minutes as interrupted. The worldwide mode connects curated IT boards; country mode inspects public GitHub profiles, with at most three concurrent profile requests and a three-minute deadline. Existing synchronous `/companies/discover` clients remain compatible. Official referral evidence, catalog membership and self-reported profiles have separate signal kinds.

## Modules

- `api.py`: application assembly, lifespan and health checks.
- `profile_api.py`, `resume_api.py`, `source_api.py`, `job_api.py`, `history_api.py`: focused route groups; existing history remains available.
- `http.py`: shared HTTP serialization and optional server authentication, without circular API imports.
- `resumes.py`: document parsing, per-document provenance, activation and rescoring. Shared preferences survive upload/activation.
- `search_setup.py`: idempotent setup of missing searches, including recovery of older stored resume text when its file hash matches. Existing searches are never overwritten.
- `searches.py`: validated filters, explicit any/all matching, workplace classification, resume-specific scoring and result sorting. Cheap filters run before scoring.
- `search_runner.py`: bounded public feed fetches, cache, source errors and durable result writes.
- `search_runtime.py`: local queue and three-hour scheduling, separate from HTTP handlers.
- `schedule.py`, `progress.py`: shared schedule slots and bounded activity snapshots.
- `company_discovery.py`: durable background discovery and user-visible failure recovery.
- `companies.py`: employer identity normalization, scoped evidence, starter catalog and bounded public-profile discovery.
- `company_api.py`: directory search, manual addition, evidence filters, discovery limits and errors.
- `workers.py`: optional legacy Celery adapter; it delegates saved searches to the same runner.

Frontend `app/page.tsx` composes a workspace. `components/` contains separate search editor, filters, results, progress, companies, contacts, resumes and sources views. `hooks/` owns queries, action state and directory discovery/search state. `lib/` owns API requests, DTO types and filter metadata. Search-list polling excludes full job descriptions, polls every two seconds while busy and every 15 seconds while idle to detect scheduled starts. Only the selected search fetches full results. Company discovery polls its durable state and invalidates the directory at completion. Regions come from the API to keep backend validation and UI options in sync. Results are rendered 20 at a time.

## Persistence and safety boundaries

Candidate JSON retains shared preferences and per-resume extracted facts. Saved searches reference the exact resume. Employer evidence has a kind, source URL, check date, scope note and confidence (`official` or `self_reported`). Existing boards and manually reviewed referral contacts appear in the company directory without being deleted or reclassified as verified evidence.

Low-scoring legacy jobs remain available for reevaluation after resume changes. Filtering does not drop audit triggers or erase job/application history. The legacy submission readiness threshold and durable submit-intent boundary remain in place. Default search workflows never submit applications.

The default development stack has no browser login. Compose publishes only the web port on loopback; native servers also bind to loopback. Same-origin checks still reject cross-origin mutations, and the proxy preserves path/body limits. Optional server authentication is isolated in `http.py`. Uploaded files retain random storage names, size limits and SHA-256 integrity checks.

## Resource configuration

Default Docker services: database, API, web. The production image uses a pinned runtime-only dependency lock; browser binaries and Celery dependencies are optional. PostgreSQL shared buffers, connection count and work memory are bounded; service memory/CPU ceilings are explicit. No old database or artifact volume is removed. Upgrade with `docker compose up -d --build --remove-orphans` to stop old idle services too. Image/build-cache cleanup is deliberately separate from changing the running app.
