#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

check_commands() {
  if ! command -v uv >/dev/null 2>&1; then
    echo '错误: 未找到 uv,请先安装: https://docs.astral.sh/uv/' >&2
    exit 1
  fi
  if ! command -v pnpm >/dev/null 2>&1; then
    echo '错误: 未找到 pnpm,请先安装: https://pnpm.io/installation' >&2
    exit 1
  fi
  if ! command -v lsof >/dev/null 2>&1; then
    echo '错误: 未找到 lsof,请先安装 lsof 包' >&2
    exit 1
  fi
}

check_ports() {
  local port
  for port in 8000 5173; do
    if PIDS=$(lsof -ti :"$port" 2>/dev/null) && [ -n "$PIDS" ]; then
      echo "错误: 端口 $port 被占用" >&2
      lsof -i :"$port" >&2 || true
      echo "提示: 请先执行 ./dev-stop.sh 或手动 kill 上述进程" >&2
      exit 1
    fi
  done
}

check_env() {
  if [ ! -f .env ]; then
    echo '警告: 未找到 .env 文件,实盘交易功能将不可用,回测/进化可正常使用' >&2
  fi
}

BACKEND_PID=""
FRONTEND_PID=""
CLEANING_UP=0

cleanup() {
  if [ "$CLEANING_UP" -eq 1 ]; then
    return 0
  fi
  CLEANING_UP=1
  echo
  echo '停止子进程...'
  for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
    if [ -n "$pid" ]; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
    if [ -n "$pid" ]; then
      pkill -TERM -P "$pid" 2>/dev/null || true
    fi
  done
  sleep 2
  for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  pkill -KILL -f 'uvicorn app.main' 2>/dev/null || true
  pkill -KILL -f 'vite/bin/vite' 2>/dev/null || true
  echo '已停止'
  exit 0
}

trap cleanup SIGINT SIGTERM

check_commands
check_ports
check_env

echo '启动后端 (uvicorn 8000)...'
PYTHONPATH=backend uv run --project backend uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload &
BACKEND_PID=$!

echo '启动前端 (vite 5173)...'
(cd frontend && exec pnpm dev) &
FRONTEND_PID=$!

sleep 2
cat <<'BANNER'
═══════════════════════════════════════════════════
Web 工作台已启动
  前端: http://localhost:5173
  后端: http://127.0.0.1:8000/health
  停止: Ctrl+C
═══════════════════════════════════════════════════
BANNER

# wait for either child to exit, then cleanup the other
wait -n 2>/dev/null || wait
cleanup
