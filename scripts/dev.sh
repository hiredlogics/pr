#!/usr/bin/env bash
# Run the backend and the frontend together.
#
#   ./scripts/dev.sh
#
# Ctrl-C stops both. The browser only ever talks to the Next server on :3000,
# which proxies /api/* through to FastAPI on :8077 - so open http://localhost:3000
# and never the backend port directly.
set -uo pipefail
cd "$(dirname "$0")/.."

API_PORT=${API_PORT:-8077}
WEB_PORT=${WEB_PORT:-3000}
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

# Don't fight an existing server: if the port is taken, say whose it is.
for port in "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $port is already in use by:"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN | tail -n +2 | awk '{print "  pid "$2" "$1}'
    echo "stop it first, or set API_PORT / WEB_PORT."
    exit 1
  fi
done

pids=()
cleanup() {
  echo
  echo "stopping…"
  for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "backend  → http://127.0.0.1:$API_PORT"
$PY -m uvicorn pcn_appeal.api:app --host 127.0.0.1 --port "$API_PORT" 2>&1 \
  | sed 's/^/[api] /' &
pids+=($!)

# Wait for the backend before starting the web server, so the first page load
# does not report the service as unreachable.
for _ in $(seq 1 60); do
  curl -sf -m 1 "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

provider=$(curl -s -m 5 "http://127.0.0.1:$API_PORT/health" 2>/dev/null \
  | $PY -c 'import sys,json; d=json.load(sys.stdin); print(f"{d[\"provider\"]} (vision: {d[\"vision\"]})")' 2>/dev/null \
  || echo "not responding")
echo "reader   → $provider"
if [ "${provider#demo}" != "$provider" ]; then
  echo "           set OPENAI_API_KEY in .env for real extraction"
fi

echo "frontend → http://localhost:$WEB_PORT   ← open this one"
( cd frontend && PCN_API_URL="http://127.0.0.1:$API_PORT" npm run dev -- --port "$WEB_PORT" 2>&1 \
  | sed 's/^/[web] /' ) &
pids+=($!)

wait
