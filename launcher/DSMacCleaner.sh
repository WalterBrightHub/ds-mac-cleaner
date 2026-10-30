#!/bin/bash
# macOS launcher: start the local server if needed, then open the web UI.
set -u

# A script-only app can be started translated under Rosetta on Apple Silicon.
# Re-exec natively so the arm64 Python virtualenv (and psycopg2) loads.
if [ "$(uname -m)" = "x86_64" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
  exec /usr/bin/arch -arm64 /bin/bash "$0" "$@"
fi

PROJECT="__PROJECT_DIR__"

# LaunchServices starts apps with a minimal PATH. Finder-launched apps need
# Docker/Homebrew paths explicitly, otherwise the DB looks "offline".
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

if [ -f "$PROJECT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT/.env"
  set +a
fi

PORT="${DSMC_PORT:-8787}"
LOG_DIR="$PROJECT/.runtime"
LOG="$LOG_DIR/launcher.log"
HEALTH="http://127.0.0.1:${PORT}/api/health"

mkdir -p "$LOG_DIR"
{
  echo
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') launch ==="
  echo "project=$PROJECT port=$PORT arch=$(uname -m) translated=$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)"
} >> "$LOG" 2>&1

if curl -fsS --max-time 1 "$HEALTH" >/dev/null 2>&1; then
  open "http://127.0.0.1:${PORT}/?launch=1"
  exit 0
fi

# Docker history is optional; don't let a sleeping Docker block the UI.
if [ "${DSMC_AUTOSTART_DB:-1}" = "1" ]; then
  nohup "$PROJECT/scripts/ensure-db.sh" >> "$LOG_DIR/docker.log" 2>&1 &
fi

nohup "$PROJECT/scripts/start-server.sh" >> "$LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 80); do
  if curl -fsS --max-time 1 "$HEALTH" >/dev/null 2>&1; then
    open "http://127.0.0.1:${PORT}/?launch=1"
    exit 0
  fi
  sleep 0.25
done

osascript -e 'display alert "DS 麦清理启动失败" message "请查看项目内 .runtime/launcher.log 获取详细信息。" as critical' >/dev/null 2>&1
exit 1
