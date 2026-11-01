# Application automation

The existing `Application`, `Job`, `Resume`, `Candidate`, `Control`, `Audit` and notification queue remain the system of record. Migration `0011_application_automation` adds preparation metadata and explicit submission policy without replacing history. Upgrading clears old `auto_apply` consent because the new autonomous policy requires exact destination domains. No application is submitted by installation or migration.

## Running

Native: install existing runtime dependencies, `pip install playwright==1.64.0`, then `python -m playwright install chromium`. Start JobPilot normally with `python scripts/run_local.py`; it migrates the SQLite workspace before startup. Docker Compose builds the existing browser-enabled backend target and uses headless Chromium. The unified cloud Dockerfile includes Chromium as well. Deployments need sufficient memory and either persistent `DATA_DIR` or `STORE_RESUME_FILES_IN_DB=true` for durable source documents, generated DOCX and screenshots. No deployment is performed by the implementation tests.

Use a single API process (`--workers 1`). Its independent browser thread owns up to three live review/intervention sessions and observes persisted commands every two seconds. The existing search scheduler continues separately. A blocked or awaiting-approval application releases processing so other applications can progress, until the live-session limit is reached. Waiting sessions expire after 15 minutes; resuming reconstructs the form. Cookies and credentials are never persisted by this executor.

## Endpoints

All routes reuse the existing API authentication and frontend write-origin protection.

| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/automation/settings` | Mode, opt-in, threshold, daily limit, exact domains, excluded companies, required approval categories |
| POST | `/automation/applications` | Prepare a persisted JobPilot result by `canonical_url`; repeated calls open its existing attempt |
| GET | `/automation/applications` | Recent attempts; optional `canonical_url` filter |
| GET | `/automation/applications/{id}` | Progress, document links, answers, evidence and approval fingerprint |
| POST | `/automation/applications/{id}/action` | `approve`, `pause`, `resume`, `cancel`, `verify` |
| GET | `/automation/applications/{id}/files/{filename}` | Authenticated DOCX/screenshot retrieval from fixed artifact names |
| POST | `/answers` | Existing exact-question approved answer library |

`approve` requires the current `approval_token` returned with the preview. Approval binds candidate version, source selection/document bytes, answers, destination URL and actual field snapshot. A form mutation, changed profile/approved answer library or regenerated different document requires preparation again. Approved answers are treated as user facts; external page/job text cannot approve declarations. Salary units/currency, dates, per-technology years, authorization and personal declarations are never invented.

The browser request guard requires public IP destinations and exact authorized HTTPS navigation hosts. Non-read requests can target approved domains and a small provider-specific set of ATS-owned APIs, only while that provider's application host remains approved. Arbitrary third-party mutation/upload destinations and WebSockets are blocked. `progress.blocked_host` identifies additional destinations needing explicit verification. Service workers are blocked; no stealth plugins, account automation or security bypasses are installed.

Daily limits count durable submit intents in the configured search timezone, including uncertain attempts. A PostgreSQL advisory transaction lock (or SQLite write lock) serializes duplicate/daily-limit checks and the compare-and-set submit fence. Browser submission occurs only after committing that fence. A browser exception afterward produces `SUBMISSION_UNCERTAIN`. Neither resume, cancel nor retry can cross that fence again. Verification requires a new success message observed after this adapter's click, with no remaining active Submit button; a redirect, HTTP 200 or old success message alone is insufficient.

Generated DOCX keeps the full original CV text, employment dates and ordering, with proven relevant skills and contact information highlighted first. This favors factual preservation over an unrestricted language-model rewrite. It does not fabricate accomplishments, translate credentials or make unsupported years-of-experience claims. Existing optional LLM answer generation is constrained to candidate evidence; common questions work offline. Resume generation currently emits DOCX; PDF-only resume inputs pause for manual intervention.

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
