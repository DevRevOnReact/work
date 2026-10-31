# Personal cloud deployment

Prepared architecture: one Render **Free** Docker web service for Next.js + FastAPI,
with a persistent free Neon PostgreSQL database. The only public port is the web
server; the API binds to loopback. HTTP Basic authentication protects the app and
all browser API routes. Use HTTPS and the generated password, saved in your phone's
password manager. Health checks expose readiness only.

## Database

Create a Neon Free project near the web service, using a production database and
an isolated branch for validating schema migrations. Keep project and branch IDs
in your private deployment notes, outside the source repository.

The current schema revision is `0007_cloud_storage_notifications`.
Use the migration helper below to transfer a local workspace to an empty database.

## Publish

1. Put the source in a private GitHub repository. Never upload `.env`, `data/`,
   `.venv/`, `node_modules/`, or personal CV files; the ignore files exclude them.
2. Connect that repository to Render and create a Blueprint from `render.yaml`.
   It explicitly selects one free web service, without a Render database or paid disk.
3. Set `DATABASE_URL` to the pooled Neon connection string and
   `DATABASE_URL_UNPOOLED` to the direct connection string. Change the URL driver
   prefix to `postgresql+psycopg://` while retaining SSL query parameters.
4. Render generates `API_TOKEN` and `LOGIN_PASSWORD`. Keep them in Render secrets.
   The browser login is `jobpilot`; read the password from the Render dashboard.
5. Render supplies `RENDER_EXTERNAL_URL`; the launcher uses it for origin checks
   and notification links. Do not set credentials in frontend public variables.
6. Verify `/health` is ready, anonymous `/` and `/api/backend/profile` return
   401, then sign in and open results, a resume file and the companies shortlist.

New commits deploy automatically only after CI checks pass. Update the linked
production branch to release changes; deploy an earlier successful Render build to
roll back code. Database migrations remain managed by Alembic and run at startup.

## Telegram and email

Add these as **Render secrets**, not repository files:

- `TELEGRAM_BOT_TOKEN`: existing bot token from BotFather.
- `TELEGRAM_CHAT_ID`: your private numeric chat ID; send your bot `/start` first.
- `RESEND_API_KEY`: API key from Resend, only for sending mail.
- `NOTIFICATION_EMAIL`: your recipient email.
- `NOTIFICATION_FROM`: default `JobPilot <onboarding@resend.dev>`.

The default Resend sender can only send to the Resend account's own address. Register
Resend with the desired recipient address, or verify a sender domain. HTTP is used
because Render Free blocks outbound SMTP. In the app, open **Резюме и профиль** →
**Уведомления о вакансиях** to see status and queue a test.

Successful searches queue one digest per channel when new non-rejected vacancies
appear. It includes up to eight examples and a link to all results. URL fingerprints
prevent repeated alerts for the same search and channel. Pending deliveries and retry
state survive restarts. Resend delivery uses an idempotency key. Telegram does not
offer an idempotency key, so an interrupted network response can rarely cause a repeat.

## Daily searches while Render sleeps

Enable `.github/workflows/search-daily.yml` on the repository's default branch.
It stays inactive until the repository variable `JOBPILOT_SCHEDULE_ENABLED`
is set to `true`, after the database and notification secrets have been configured.
Set the **production** GitHub environment:

Secrets: `DATABASE_URL`, `LOGIN_PASSWORD`. Delivery credentials stay on Render.
Optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY` if delivery should also run directly in Actions.
Variables: `NOTIFICATION_EMAIL`, `NOTIFICATION_FROM`, `PUBLIC_APP_URL`.

The workflow runs daily at 00:17 UTC (03:17 Moscow), against the database directly.
It then wakes the private web app once to queue and deliver digests using Render's
notification credentials. It does not keep Render artificially awake.
It can also run manually from Actions.
GitHub scheduling can be delayed; this is not a precise delivery-time guarantee.
GitHub and Neon usage must remain within the account's free quotas.

The web runtime can also run searches manually and catches up daily runs while it
is active. Both paths atomically claim saved searches. A web restart leaves a live
cloud run alone; runs older than 20 minutes can be recovered.

## Durability and limits

Render Free sleeps after 15 minutes without inbound traffic; the first visit can
take longer. Local disk changes disappear on restart. CV files are therefore stored
in PostgreSQL along with profiles, company evidence, results and notification state.
Remotive's local six-hour cache is expendable and may refresh after a cloud restart.
The app does not send applications.

Neon currently includes 1 GB storage and 100 CU-hours per project on Free. Uploaded
files are capped at 10 MB each and saved results at 500 per search. Provider quotas,
email limits and free plans can change; never enable a paid upgrade automatically.

Reference documentation:

- https://render.com/docs/free
- https://render.com/docs/blueprint-spec
- https://neon.com/pricing
- https://resend.com/docs/knowledge-base/403-error-resend-dev-domain

## Migration helper

`scripts/migrate_cloud_data.py` accepts the database URL through hidden terminal
input, validates Alembic migrations, verifies resume hashes, and refuses to import
into a database with existing application rows. Local records are read only.
Credentials are never printed or committed. Do not rerun it against the already
imported production database.
