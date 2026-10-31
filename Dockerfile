FROM node:22-bookworm-slim AS web
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1 NODE_OPTIONS=--max-old-space-size=1024 RAYON_NUM_THREADS=2
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY backend/requirements.runtime.lock /app/backend/requirements.runtime.lock
RUN pip install --no-cache-dir -r backend/requirements.runtime.lock
COPY backend/ /app/backend/
RUN pip install --no-deps --no-cache-dir /app/backend
COPY --from=web /build/.next/standalone /app/web/
COPY --from=web /build/.next/static /app/web/.next/static
COPY scripts/serve_cloud.py /app/scripts/serve_cloud.py
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app:app /app
USER app
ENV PYTHONUNBUFFERED=1 NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production DATA_DIR=/app/data
EXPOSE 10000
CMD ["python", "scripts/serve_cloud.py"]
