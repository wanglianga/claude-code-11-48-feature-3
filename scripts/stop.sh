#!/usr/bin/env bash
# Stop all locally started platform processes.
cd "$(dirname "$0")/.."
for f in .pids/*.pid; do
  [ -f "$f" ] || continue
  pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" && echo "stopped $f ($pid)"
  fi
  rm -f "$f"
done
