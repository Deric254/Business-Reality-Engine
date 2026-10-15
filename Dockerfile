# One image = API + web app. Stage 1 builds the web app, stage 2 is the Python service that serves both,
# so there is a single URL, no CORS, and a single process to keep alive.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_BASE=""
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY --from=web /web/dist ./frontend_dist

# Run as an unprivileged user.
RUN useradd --system --uid 10001 --no-create-home bre
USER bre

EXPOSE 8000
# WEB_CONCURRENCY defaults to 1: each worker loads pandas (~150 MB), and a free 512 MB instance that runs 4 of them is
# killed by the host for running out of memory, then restarts, over and over. Raise it only on a bigger instance.
# RUN_MIGRATIONS=1 applies DB migrations before starting (single-service hosts such as Render).
CMD ["sh", "-c", "if [ \"$RUN_MIGRATIONS\" = \"1\" ]; then alembic upgrade head || exit 1; fi; exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --keep-alive 30 --timeout 120 --graceful-timeout 30 --access-logfile -"]
