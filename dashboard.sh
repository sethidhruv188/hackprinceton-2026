#!/usr/bin/env bash
# Start the local ordering dashboard: API on 127.0.0.1:8765, web app on http://127.0.0.1:5173
#   ./dashboard.sh          real data (data/, outputs/ from `python run.py`)
#   ./dashboard.sh demo     synthetic demo (data_demo/, outputs_demo/ from
#                           `python run.py --generate --demo-harvest --today 2026-11-14`)
# Everything stays on this computer: both servers listen on 127.0.0.1 only.
set -euo pipefail
cd "$(dirname "$0")"
if [[ "${1:-}" == "demo" ]]; then
  export DASH_DATA=data_demo DASH_OUT=outputs_demo
else
  export DASH_DATA="${DASH_DATA:-data}" DASH_OUT="${DASH_OUT:-outputs}"
fi
export DASH_API_PORT="${DASH_API_PORT:-8765}"
PY=".venv/bin/python"; [[ -x "$PY" ]] || PY="python3"
if [[ ! -f "$DASH_OUT/brand_orders.csv" ]]; then
  echo "No $DASH_OUT/brand_orders.csv yet. Run python run.py first (see README)." >&2; exit 1
fi
[[ -d web/node_modules ]] || (cd web && npm install --no-audit --no-fund)
"$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$DASH_API_PORT" --log-level warning &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT INT TERM
echo "Data: $DASH_DATA / $DASH_OUT"
echo "Open http://127.0.0.1:5173  (Ctrl+C to stop)"
cd web && npm run dev
