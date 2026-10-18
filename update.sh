#!/bin/sh
# Pull the latest code from GitHub and rebuild the container.
# Your settings are in .env (not in git), so nothing here overwrites them.
set -e
cd "$(dirname "$0")"
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
