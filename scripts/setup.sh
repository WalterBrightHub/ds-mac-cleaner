#!/bin/bash
# One-time local setup. No global packages are installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -x ".venv/bin/python" ]; then
  echo "→ creating .venv"
  python3 -m venv .venv
fi

echo "→ installing PostgreSQL driver into .venv"
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "→ wrote .env (local development defaults)"
fi

if command -v docker >/dev/null 2>&1; then
  echo "→ starting PostgreSQL (Docker Compose)"
  docker compose up -d db
else
  echo "! docker not found; the app will use local JSONL history until it is installed."
fi

echo
echo "Ready. Start the app with:"
echo "  ./scripts/start-server.sh"
echo "or build the macOS launcher with:"
echo "  ./scripts/install-app.sh"
