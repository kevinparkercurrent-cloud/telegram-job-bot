FROM ghcr.io/astral-sh/uv:0.12.4 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends age ca-certificates sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home jobbot

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY README.md ./README.md
RUN chmod 0755 scripts/backup.sh scripts/smoke_test.py \
    && mkdir -p /data /backups \
    && chown -R jobbot:jobbot /app /data /backups

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1
USER jobbot
CMD ["python", "-m", "job_bot.app"]
