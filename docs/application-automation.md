# Application automation

The existing `Application`, `Job`, `Resume`, `Candidate`, `Control`, `Audit` and notification queue remain the system of record. Migration `0011_application_automation` adds preparation metadata and explicit submission policy without replacing history. Upgrading clears old `auto_apply` consent because the new autonomous policy requires exact destination domains. No application is submitted by installation or migration.

## Running

Native: install existing runtime dependencies, `pip install playwright==1.64.0`, then `python -m playwright install chromium`. Start JobPilot normally with `python scripts/run_local.py`; it migrates the SQLite workspace before startup. Docker Compose builds the existing browser-enabled backend target and uses headless Chromium. The unified cloud Dockerfile includes Chromium as well. Deployments need sufficient memory and either persistent `DATA_DIR` or `STORE_RESUME_FILES_IN_DB=true` for durable source documents, generated DOCX and screenshots. No deployment is performed by the implementation tests.

Use a single API process (`--workers 1`). Its independent browser thread owns up to three live review/intervention sessions. It observes active work every two seconds and polls every 30 seconds in idle (`APPLICATION_IDLE_POLL_SECONDS`). HTTP commands wake the executor after the database commit; other processes retain the bounded polling fallback. The existing search scheduler continues separately. A blocked or awaiting-approval application releases processing so other applications can progress, until the live-session limit is reached. Waiting sessions expire after 15 minutes; resuming reconstructs the form. Cookies and credentials are never persisted by this executor.

Temporary network/browser timeouts and HTTP 408/425/429/5xx before submit are retried at most twice, after at least 15 and 45 seconds. HTTP 429 waits at least 60 seconds. `Retry-After` accepts seconds or an HTTP date, with a seven-day ceiling on untrusted values; a longer delay wins over the backoff. HTTP 400/401/403/404/410 stop for review instead of looping. CAPTCHA and Cloudflare challenges are handed to the owner. The retry count, due time and captured partial answers are persisted, so restarting the dispatcher does not reset the budget. Unknown exceptions and missing candidate facts stop that application. No failure after a committed submit intent can be retried. Manual resume starts a new preparation retry budget. Headless sessions needing input close immediately; when capacity is full, a waiting browser can be released while its application and artifacts remain available for later reconstruction. Pausing also releases its browser.

At the daily limit, preparation is deferred until midnight in the configured search timezone. The dispatcher stops adding new autonomous applications until budget is available. Existing deferred applications resume automatically the next day, without clearing the daily submit fence or requiring a human action.

Autonomous selection re-evaluates saved vacancies against intact uploaded resumes, rather than trusting a stale search score. Documents are read once per selection batch. Clicking an individual application uses the saved automation mode too. Additional approval categories are preserved until explicitly changed; the settings provide a single action to use confirmed answers without additional review. Unsupported personal/legal declarations still require explicit facts. Navigation to the application form happens in the agent browser. The dashboard stays open in both modes; an explicit fallback link opens the actual reached employer form when human action is required.

The browser runtime only runs while the API service is running. Render Free sleeps on idle; scheduled vacancy searches in GitHub do not themselves provide an always-running application executor. Continuous unattended application processing needs an always-running host. The executor recovers known transient failures; it does not modify or deploy its own source code.

## Endpoints

All routes reuse the existing API authentication and frontend write-origin protection.

| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/automation/settings` | Mode, opt-in, threshold, daily limit, exact domains, excluded companies, required approval categories |
| POST | `/automation/applications` | Prepare a persisted JobPilot result by `canonical_url`; repeated calls open its existing attempt |
| GET | `/automation/applications` | Recent attempts; optional `canonical_url` filter |
| GET | `/automation/applications/{id}` | Progress, document links, answers, evidence and approval fingerprint |
| POST | `/automation/applications/{id}/action` | `approve`, `pause`, `resume`, `cancel`, `verify` |
| POST | `/automation/applications/{id}/answers` | Save several current-form answers atomically; optionally resume preparation |
| GET | `/automation/applications/{id}/files/{filename}` | Authenticated DOCX/screenshot retrieval from fixed artifact names |
| POST | `/answers` | Existing exact-question approved answer library |

`approve` requires the current `approval_token` returned with the preview. Approval binds candidate version, source selection/document bytes, answers, destination URL and actual field snapshot. A form mutation, changed profile/approved answer library or regenerated different document requires preparation again. Approved answers are treated as user facts; external page/job text cannot approve declarations. Salary units/currency, dates, per-technology years, authorization and personal declarations are never invented.

The application detail exposes `answer_fields` (opaque IDs, questions and choices) and an `answer_token` binding the current attempt, state, discovered fields and confirmed answers. The batch endpoint accepts `{answer_token, answers: [{id, answer, remember: false}], resume: true}`. Answers stay in that application's durable progress by default. Only explicitly selected `remember` answers enter the existing exact-question library. Unknown fields, invalid choices, stale tokens and changes after submission are rejected under the same lock as final submission. Saving invalidates approval and never changes global eligibility. The UI supports partial saving or saving and continuing preparation; required unknown facts remain blockers on the next pass.

Cost controls reuse the existing single service and database. Attempted vacancy aliases and excluded employers are discarded before reading CVs; outstanding prepared/queued work counts against selection capacity. Read-only public browser assets skip policy database reads, while navigation and writes retain live policy checks. Media and external fonts are blocked; challenge images and form scripts remain available. Each immutable answer engine caches repeated answers and unsupported questions and permits at most six model requests per preparation (`LLM_QUESTION_LIMIT`, including failed requests). Known questions and confirmed answers use no model call. C, C++ and C# stay distinct. The collapsed history makes no request; the open history polls a compact joined summary every 30 seconds (`?summary=true`). Detailed endpoints remain compatible.

The browser request guard requires public IP destinations and exact authorized HTTPS navigation hosts. Non-read requests can target approved domains and a small provider-specific set of ATS-owned APIs, only while that provider's application host remains approved. Arbitrary third-party mutation/upload destinations and WebSockets are blocked. `progress.blocked_host` identifies additional destinations needing explicit verification. Service workers are blocked; no stealth plugins, account automation or security bypasses are installed.

Daily limits count durable submit intents in the configured search timezone, including uncertain attempts. A PostgreSQL advisory transaction lock (or SQLite write lock) serializes duplicate/daily-limit checks and the compare-and-set submit fence. Browser submission occurs only after committing that fence. A browser exception afterward produces `SUBMISSION_UNCERTAIN`. Neither resume, cancel nor retry can cross that fence again. Verification requires a new success message observed after this adapter's click, with no remaining active Submit button; a redirect, HTTP 200 or old success message alone is insufficient.

Generated DOCX keeps the full original CV text, employment dates and ordering, with proven relevant skills and contact information highlighted first. This favors factual preservation over an unrestricted language-model rewrite. It does not fabricate accomplishments, translate credentials or make unsupported years-of-experience claims. Existing optional LLM answer generation is constrained to candidate evidence; common questions work offline. Resume generation emits DOCX and renders PDF when required by the upload field.

Apply discovery follows labelled Apply/Application links, tabs and buttons, including repeated links to one destination and links that would otherwise open a new tab. Cross-site navigation is limited to exact configured HTTPS hosts; all requests still pass public-IP and submission guards. Ambiguous links to different vacancies stop rather than picking a different job. The API persists the reached application URL and selected resume filename. After a provider change, the request guard permits that application's ATS-owned APIs rather than the original provider's APIs.

Forms accepting PDF only receive a PDF rendered from the prepared DOCX, preserving its factual contents. The actual uploaded document is available in the preview and uses the same authenticated artifact storage policy. Offline narrative answers now include documented project examples, role-fit questions and common contact-label variants. Long CVs without blank lines are chunked and the evidence relevant to the question/vacancy is selected across the document, instead of dropping the paragraph after its first 1000 characters. Binary technical questions use affirmative options only when the exact unqualified question is supported by recorded familiarity or concrete use; tenure and commercial-use requirements remain explicit facts.

## Verification

```sh
python -m pytest backend/tests -q
python -m mypy --config-file backend/pyproject.toml backend/jobpilot
cd frontend
npm run typecheck
npm run build
```

Local Playwright fixtures exercise real Chromium with controlled ATS pages; no test submits to an employer. Tests cover document integrity/selection, truthful answers, upload controls, dropdowns/radios, conditional fields, multistep validation, CAPTCHA/login blockers, stale approval, duplicate prevention and the durable uncertainty boundary. Browser UI fixtures cover approval, saved answers, pause/resume/cancel, uncertain verification and mobile layout. The explicit live ATS smoke test only reads public field metadata and blocks all writes/uploads/submissions. See its test docstring for environment variables; network access and currently open public postings are required. A successful local test does not establish compatibility with every live employer customization.

## Live discovery check, 1 November 2026

Fresh empty browser contexts read these public pages. No candidate information was entered, no documents were uploaded and no application was submitted. The smoke permits only named public Ashby GraphQL reads; all application writes remain blocked. These are form-discovery checks, not verified live submissions.

| Provider | Public posting | Observed result |
|---|---|---|
| Ashby | [G2i](https://jobs.ashbyhq.com/g2i/d9a764c2-b471-4006-8da1-42f67874fd04) | Resume input discovered |
| Workable | [Devsinc](https://apply.workable.com/devsinc-17/j/802A856918/) | Resume input discovered after declining optional cookies |
| Lever | [NFQ](https://jobs.lever.co/nfq/04427017-2544-40bb-bf82-864e5a66281d) | Resume input discovered on one run; later cookie/CAPTCHA intervention required |
| Greenhouse | [Modo Energy](https://job-boards.greenhouse.io/modoenergy/jobs/4954235101) | Visible human verification; stopped |
| SmartRecruiters | [Western Digital](https://jobs.smartrecruiters.com/WesternDigital/744000138717897) | DataDome challenge on some runs; conversational Winston flow did not expose an upload under the read-only policy; live completion unverified |

Application Telegram notifications identify the vacancy, selected CV and a direct authenticated `/?application={id}` link when `PUBLIC_APP_URL` is configured. Approval requests, human-action stops and failures include prepared answers and discovered unanswered fields with choices. Missing facts are marked for clarification. Long notifications are split into numbered messages without truncating answers. Only fields exposed before the interruption can be included; login, CAPTCHA, conditional questions, and later steps may prevent discovery. Delivery requires a configured Telegram bot/chat and an existing saved search, and runs through the existing durable notification queue. The release adds no schema migration, changes no saved automation consent and leaves application history intact.
