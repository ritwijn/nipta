#!/usr/bin/env bash
# Demo runner (implementation.md §13): one uvicorn process, no workers, no reload.
#   ./run.sh           start the server on :8000
#   ./run.sh --reset   reset the database to the fixture batch first, then start
#   ./run.sh --check   print the fixture decisions and provider status, then exit
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
[ -x "$PY" ] || { echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[ -f .env ] || { echo "Missing .env. Run: cp .env.example .env"; exit 1; }
PORT="${PORT:-8000}"

case "${1:-}" in
  --reset) "$PY" -m backend.seed --reset ;;
  --check)
    "$PY" -m backend.seed
    "$PY" -c 'import json; from backend.config import capability_report; print(json.dumps(capability_report(), indent=2))'
    exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1"; exit 2 ;;
esac

echo "Nipta on http://localhost:$PORT  (webhooks: cloudflared tunnel --url http://localhost:$PORT)"
exec .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port "$PORT" --workers 1
