#!/usr/bin/env bash
set -euo pipefail

STOPPED=0

for port in 8000 5173; do
  PIDS=$(lsof -ti :"$port" 2>/dev/null | tr '\n' ' ' | sed 's/ $//')
  if [ -z "$PIDS" ]; then
    continue
  fi
  echo "停止端口 $port 上的进程: $PIDS"
  for pid in $PIDS; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 3
  for pid in $PIDS; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
      echo "强制 kill 端口 $port 上的进程: $pid"
    fi
  done
  STOPPED=1
done

if [ "$STOPPED" -eq 0 ]; then
  echo '端口 8000/5173 均空闲'
fi

exit 0
