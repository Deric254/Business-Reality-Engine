#!/bin/bash
# Pull the latest code and redeploy. Run from deploy/free on the server.
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
sleep 10 && docker compose ps
