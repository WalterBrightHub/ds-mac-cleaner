#!/bin/bash
# LaunchAgent watchdog: start the server only if it isn't already healthy.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${DSMC_PORT:-8787}"
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  PORT="${DSMC_PORT:-8787}"
fi
mkdir -p "$ROOT/.runtime"
if curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  exit 0
fi
nohup "$ROOT/scripts/start-server.sh" >> "$ROOT/.runtime/agent.log" 2>&1 &
exit 0
