#!/bin/bash
# Run once on a fresh Ubuntu/Debian free VM:  curl -fsSL <raw url>/setup-server.sh | bash   (or copy and run)
set -euo pipefail
sudo apt-get update -y && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# 2 GB swap: the e2-micro has only 1 GB RAM and image builds need headroom.
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
echo "Done. LOG OUT and back in (so the docker group applies), then continue with docs/FREE_DEPLOY.md step 5."
