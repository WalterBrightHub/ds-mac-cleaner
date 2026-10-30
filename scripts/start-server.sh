#!/bin/bash
# Start the local server in the foreground. The macOS app runs this via nohup.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p .runtime

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "python3 not found" >&2
  exit 1
fi

# Start PostgreSQL in the background. Never block the UI if Docker is asleep.
if [ "${DSMC_AUTOSTART_DB:-1}" = "1" ]; then
  "$ROOT/scripts/ensure-db.sh" >> "$ROOT/.runtime/docker.log" 2>&1 &
fi

export PYTHONPYCACHEPREFIX="$ROOT/.runtime/pycache"
exec "$PYTHON" "$ROOT/server/app.py" --host "${DSMC_HOST:-127.0.0.1}" --port "${DSMC_PORT:-8787}"
