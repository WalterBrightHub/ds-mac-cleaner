#!/bin/bash
# LaunchAgent watchdog: start the server only if it isn't already healthy.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/.runtime/agent.log"
mkdir -p "$ROOT/.runtime"
exec >> "$LOG" 2>&1

PORT="${DSMC_PORT:-8787}"
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  PORT="${DSMC_PORT:-8787}"
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') watchdog check (port $PORT)"
if curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "server already healthy"
  exit 0
fi
echo "server is down; starting it"
nohup "$ROOT/scripts/start-server.sh" >> "$LOG" 2>&1 &
echo "started pid $!"
exit 0
