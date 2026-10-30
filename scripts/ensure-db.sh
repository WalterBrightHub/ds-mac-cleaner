#!/bin/bash
# Best-effort PostgreSQL startup. Safe to call from the launcher.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DOCKER_BIN="$(command -v docker || true)"
if [ -z "$DOCKER_BIN" ] && [ -x /usr/local/bin/docker ]; then DOCKER_BIN=/usr/local/bin/docker; fi
if [ -z "$DOCKER_BIN" ] && [ -x /opt/homebrew/bin/docker ]; then DOCKER_BIN=/opt/homebrew/bin/docker; fi
if [ -z "$DOCKER_BIN" ] && [ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]; then
  DOCKER_BIN="/Applications/Docker.app/Contents/Resources/bin/docker"
fi
if [ -z "$DOCKER_BIN" ]; then
  echo "[db] docker not installed"
  exit 0
fi
if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  echo "[db] Docker Desktop is not running"
  exit 0
fi

echo "[db] starting compose service"
"$DOCKER_BIN" compose up -d db
for _ in $(seq 1 30); do
  if "$DOCKER_BIN" compose exec -T db pg_isready -U "${DSMC_DB_USER:-dsmac}" -d "${DSMC_DB_NAME:-dsmac}" >/dev/null 2>&1; then
    echo "[db] ready"
    exit 0
  fi
  sleep 1
done
echo "[db] timeout waiting for PostgreSQL"
exit 0
