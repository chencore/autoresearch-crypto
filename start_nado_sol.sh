#!/bin/bash

LOG_FILE="logs/live_nado_quant.log"
PID_FILE="logs/live_nado_quant.pid"

mkdir -p logs

case "${1:-start}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Already running (PID: $(cat "$PID_FILE"))"
      exit 1
    fi
    nohup python3 live_nado_quant.py --ticker SOL --capital 80 --leverage 3 > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Started live_nado_quant.py (PID: $!)"
    echo "Log: $LOG_FILE"
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      PID=$(cat "$PID_FILE")
      kill "$PID" 2>/dev/null && echo "Stopped (PID: $PID)" || echo "Process not running"
      rm -f "$PID_FILE"
    else
      echo "Not running"
    fi
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Running (PID: $(cat "$PID_FILE"))"
    else
      echo "Not running"
    fi
    ;;
  log)
    tail -f "$LOG_FILE"
    ;;
  *)
    echo "Usage: $0 {start|stop|status|log}"
    exit 1
    ;;
esac
