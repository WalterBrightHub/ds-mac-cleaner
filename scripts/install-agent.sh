#!/bin/bash
# Install/refresh the per-user LaunchAgent watchdog.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
"$PYTHON" - <<'PY'
import json
from server.agent import AgentManager
print(json.dumps(AgentManager().install(), ensure_ascii=False, indent=2))
PY
