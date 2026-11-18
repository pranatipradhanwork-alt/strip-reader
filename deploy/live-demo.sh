#!/usr/bin/env bash
# Live demo from this Mac: the app in production mode + a public https link (ngrok).
#   ./deploy/live-demo.sh          then open the link it prints; Ctrl+C stops both.
# Same settings as the cloud: 10 photo readings per visitor per day, readings deleted after 24 h.
# Demo data is kept apart in out/demo, so your own readings and cached replies are never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-5050}"
command -v ngrok >/dev/null || { echo "ngrok is not installed: brew install ngrok"; exit 1; }
[ -f .venv/bin/activate ] || { echo "No .venv here: create it and pip install -r requirements.txt"; exit 1; }
source .venv/bin/activate
set -a; [ -f .env ] && . ./.env; set +a

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is busy (is the app already running in another tab?). Stop it first, or: PORT=5051 $0"; exit 1
fi

export STRIP_READER_RUNS="$PWD/out/demo/runs" STRIP_READER_CACHE="$PWD/out/demo/cache" \
       STRIP_READER_QUEUE="$PWD/out/demo/queue" RETENTION_HOURS=24 DAILY_LIMIT=10 BEHIND_PROXY=1
gunicorn -w 1 --threads 8 --timeout 120 -b "127.0.0.1:$PORT" 'strip_reader.app:wsgi()' &
APP=$!
trap 'kill $APP 2>/dev/null; echo; echo "Stopped the app and the link."' EXIT

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break
  sleep 1
done
curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null || { echo "The app did not start."; exit 1; }
echo "App is up on this Mac. Opening the public link (the https address on the 'Forwarding' line)..."
echo "Keep this window open during the demo. Ctrl+C stops everything."
sleep 2
ngrok http "$PORT"
