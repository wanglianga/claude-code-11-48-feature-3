#!/usr/bin/env bash
# Start the full platform locally: 6 center agents + FastAPI backend (+ vite if --frontend).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
mkdir -p logs .pids

if [ ! -d data/centers/C1 ]; then
  PYTHONPATH=center_agent $PY -m center_agent.datagen --root data/centers
fi

for i in 1 2 3 4 5 6; do
  if [ -f .pids/agent-C$i.pid ] && kill -0 "$(cat .pids/agent-C$i.pid)" 2>/dev/null; then
    echo "agent C$i already running"
  else
    PYTHONPATH=center_agent nohup $PY -m center_agent.server \
      --center C$i --port 910$i --data data/centers/C$i \
      > logs/agent-C$i.log 2>&1 &
    echo $! > .pids/agent-C$i.pid
    echo "agent C$i -> :910$i"
  fi
done

if [ -f .pids/backend.pid ] && kill -0 "$(cat .pids/backend.pid)" 2>/dev/null; then
  echo "backend already running"
else
  nohup $PY -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend \
    > logs/backend.log 2>&1 &
  echo $! > .pids/backend.pid
  echo "backend -> :8000"
fi

if [ "${1:-}" = "--frontend" ]; then
  (cd frontend && nohup /opt/node-22/bin/npm run dev > ../logs/frontend.log 2>&1 &
   echo $! > ../.pids/frontend.pid)
  echo "frontend -> :5173"
fi

for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8000/api/health > /dev/null && { echo "backend healthy"; exit 0; }
  sleep 1
done
echo "backend did not become healthy; see logs/backend.log" >&2
exit 1
