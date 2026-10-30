#!/bin/bash
# Remove the per-user LaunchAgent watchdog; the server itself is left as-is.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
"$PYTHON" - <<'PY'
import json
from server.agent import AgentManager
print(json.dumps(AgentManager().uninstall(), ensure_ascii=False, indent=2))
PY
