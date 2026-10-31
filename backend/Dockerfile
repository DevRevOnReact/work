FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.runtime.lock ./
RUN pip install --no-cache-dir -r requirements.runtime.lock
COPY pyproject.toml ./
COPY jobpilot ./jobpilot
RUN pip install --no-deps --no-cache-dir .
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown -R app:app /data /app
USER app
ENV DATA_DIR=/data
CMD ["uvicorn", "jobpilot.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# Browser submission is opt-in and absent from the default local image.
FROM runtime AS automation
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN pip install --no-cache-dir 'celery[redis]==5.6.3' 'playwright==1.64.0' && playwright install --with-deps chromium
USER app
