# API image. Not build-tested: the authoring sandbox could not pull base images. Boot path verified directly with the same worker command.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Run as an unprivileged user.
RUN useradd --system --uid 10001 --no-create-home bre
USER bre

EXPOSE 8000
# WEB_CONCURRENCY = number of worker processes. Shared state (rate limits,
# scheduler lock) lives in Redis, so any number of workers/containers is safe.
# RUN_MIGRATIONS=1 (set on single-service hosts such as Render) applies DB migrations before starting.
# docker-compose uses its own migrate service and leaves it unset.
CMD ["sh", "-c", "if [ \"$RUN_MIGRATIONS\" = \"1\" ]; then alembic upgrade head || exit 1; fi; exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-4} --keep-alive 30 --timeout 60 --graceful-timeout 30 --access-logfile -"]
