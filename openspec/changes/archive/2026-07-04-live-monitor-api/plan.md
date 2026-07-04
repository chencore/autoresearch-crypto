# Plan: live-monitor-api

> **详细实现计划**。位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`。

---

## 计划总览

本次实现分 **5 个阶段**。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | Pydantic 模型 | `schemas/live.py` 8 个模型 | 0.25d |
| S2 | 进程管理器 | `services/live_manager.py` 单例 + Lock | 0.5d |
| S3 | 状态/日志读取器 | `services/live_state_reader.py` | 0.5d |
| S4 | 路由改造 | `api/v1/live.py` 5 个端点 | 0.5d |
| S5 | 启动验证 | 11 项 curl 全绿 | 0.25d |

**总估时**：约 2 人日。

---

## S1. Pydantic 模型

### 目标

定义请求 / 响应 schema。

### 实施步骤

1. 创建 `backend/app/schemas/live.py`：
   ```python
   from typing import Any
   from pydantic import BaseModel

   class ExchangeInfo(BaseModel):
       name: str
       status: str
       pid: int | None
       script: str
       state_file: str
       log_file: str

   class ExchangeListResponse(BaseModel):
       exchanges: list[ExchangeInfo]
       total: int

   class StartRequest(BaseModel):
       exchange: str
       symbol: str
       mode: str
       capital: float = 100.0
       leverage: float = 1.0

   class StartResponse(BaseModel):
       exchange: str
       pid: int
       status: str

   class StopRequest(BaseModel):
       exchange: str

   class StopResponse(BaseModel):
       exchange: str
       status: str

   class LiveStatus(BaseModel):
       exchange: str
       running: bool
       state: dict[str, Any] | None
       updated_at: str | None

   class LogResponse(BaseModel):
       exchange: str
       lines: list[str]
       total_lines: int
   ```

### ✅ 完成验证
- [ ] `uv run python -c "from app.schemas.live import ExchangeInfo, StartRequest, LiveStatus, LogResponse; print('ok')"` 输出 `ok`

---

## S2. 进程管理器

### 目标

线程安全的 `exchange → Popen` 内存映射 + start/stop/list/is_running/get_pid。

### 实施步骤

1. 创建 `backend/app/services/live_manager.py`：
   ```python
   from __future__ import annotations

   import subprocess
   import threading
   from pathlib import Path

   from dex.config import PROJECT_DIR

   EXCHANGES: dict[str, str] = {
       "binance": "live_binance_quant.py",
       "okx": "live_okx_quant.py",
       "nado": "live_nado_quant.py",
   }

   STATE_FILES = {
       "binance": "live_binance_state.json",
       "okx": "live_okx_state.json",
       "nado": "live_nado_state.json",
   }

   LOG_FILES = {
       "binance": "live_binance_log.txt",
       "okx": "live_okx_log.txt",
       # nado 特殊处理：live_nado_log_{timestamp}.txt
   }


   class LiveError(Exception):
       def __init__(self, code: str, message: str) -> None:
           self.code = code
           self.message = message
           super().__init__(f"{code}: {message}")


   class LiveManager:
       def __init__(self) -> None:
           self._processes: dict[str, subprocess.Popen] = {}
           self._lock = threading.Lock()

       def _cleanup_if_dead(self, exchange: str) -> None:
           """调用方必须持有 _lock。"""
           proc = self._processes.get(exchange)
           if proc is not None and proc.poll() is not None:
               self._processes.pop(exchange, None)

       def is_running(self, exchange: str) -> bool:
           with self._lock:
               self._cleanup_if_dead(exchange)
               return exchange in self._processes

       def get_pid(self, exchange: str) -> int | None:
           with self._lock:
               self._cleanup_if_dead(exchange)
               proc = self._processes.get(exchange)
               return proc.pid if proc else None

       def list_exchanges(self) -> list[dict]:
           results = []
           for name, script in EXCHANGES.items():
               with self._lock:
                   self._cleanup_if_dead(name)
                   proc = self._processes.get(name)
                   status = "running" if proc else "stopped"
                   pid = proc.pid if proc else None
               results.append({
                   "name": name,
                   "status": status,
                   "pid": pid,
                   "script": script,
                   "state_file": STATE_FILES[name],
                   "log_file": LOG_FILES.get(name, f"live_{name}_log_*.txt"),
               })
           return results

       def start(self, exchange: str, symbol: str, mode: str, capital: float, leverage: float) -> int:
           if exchange not in EXCHANGES:
               raise LiveError("invalid_exchange", f"unsupported exchange: {exchange}")
           if mode not in ("demo", "live"):
               raise LiveError("invalid_mode", f"mode must be 'demo' or 'live', got: {mode}")
           with self._lock:
               self._cleanup_if_dead(exchange)
               if exchange in self._processes:
                   raise LiveError("already_running", f"{exchange} is already running, stop it first")
               script = EXCHANGES[exchange]
               cmd = [
                   "uv", "run", "python", script,
                   "--symbol", symbol,
                   f"--{mode}",
                   "--capital", str(capital),
                   "--leverage", str(leverage),
               ]
               proc = subprocess.Popen(
                   cmd,
                   cwd=str(PROJECT_DIR),
                   stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,
               )
               self._processes[exchange] = proc
               return proc.pid

       def stop(self, exchange: str) -> None:
           with self._lock:
               self._cleanup_if_dead(exchange)
               proc = self._processes.get(exchange)
               if proc is None:
                   raise LiveError("not_running", f"{exchange} is not running")
               proc.terminate()
               try:
                   proc.wait(timeout=5)
               except subprocess.TimeoutExpired:
                   proc.kill()
                   proc.wait()
               self._processes.pop(exchange, None)


   live_manager = LiveManager()
   ```
   - 注：nado 的 CLI 用 `--ticker` 不是 `--symbol`，但 v0.1 统一用 `--symbol` 让 nado 脚本报错即可（v0.1 简化）；或可加 `if exchange == "nado": cmd = ["uv", "run", "python", script, "--ticker", symbol, f"--{mode}", ...]`。看 nado 脚本支持哪些参数后定。本 task 先按统一 `--symbol` 跑，nado 报错是已知问题。

### ✅ 完成验证
- [ ] `uv run python -c "from app.services.live_manager import live_manager, EXCHANGES; print(list(EXCHANGES))"` 输出 `['binance', 'okx', 'nado']`
- [ ] `uv run python -c "from app.services.live_manager import live_manager; print(live_manager.list_exchanges())"` 返回 3 项全 stopped

---

## S3. 状态/日志读取器

### 目标

读 state JSON + log txt，nado log 取最新。

### 实施步骤

1. 创建 `backend/app/services/live_state_reader.py`：
   ```python
   from __future__ import annotations

   import json
   from datetime import datetime
   from pathlib import Path

   from dex.config import LOG_DIR

   from app.services.live_manager import STATE_FILES, LOG_FILES

   MAX_TAIL = 1000


   def _resolve_log_path(exchange: str) -> Path | None:
       if exchange in LOG_FILES:
           return LOG_DIR / LOG_FILES[exchange]
       if exchange == "nado":
           candidates = sorted(LOG_DIR.glob("live_nado_log_*.txt"), reverse=True)
           return candidates[0] if candidates else None
       return None


   def read_state(exchange: str) -> tuple[dict | None, str | None]:
       if exchange not in STATE_FILES:
           return None, None
       path = LOG_DIR / STATE_FILES[exchange]
       if not path.exists():
           return None, None
       with open(path, "r", encoding="utf-8") as f:
           state = json.load(f)
       updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
       return state, updated_at


   def read_logs(exchange: str, tail: int = 200) -> tuple[list[str], int]:
       path = _resolve_log_path(exchange)
       if path is None or not path.exists():
           return [], 0
       with open(path, "r", encoding="utf-8", errors="replace") as f:
           all_lines = f.readlines()
       all_lines = [line.rstrip("\n") for line in all_lines]
       total = len(all_lines)
       limited_tail = min(tail, MAX_TAIL)
       return all_lines[-limited_tail:] if limited_tail > 0 else [], total
   ```

### ✅ 完成验证
- [ ] `uv run python -c "from app.services.live_state_reader import read_state, read_logs; print(read_state('binance')); print(read_logs('binance', 10))"` 不报错（文件不存在返回 None/[]）

---

## S4. 路由改造

### 目标

5 个端点替换占位。

### 实施步骤

1. 改造 `backend/app/api/v1/live.py`：
   ```python
   from fastapi import APIRouter, Query
   from fastapi.responses import JSONResponse

   from app.schemas.live import (
       ExchangeInfo, ExchangeListResponse, StartRequest, StartResponse,
       StopRequest, StopResponse, LiveStatus, LogResponse,
   )
   from app.services.live_manager import EXCHANGES, LiveError, live_manager
   from app.services.live_state_reader import read_logs, read_state

   router = APIRouter()

   _STATUS_MAP = {
       "invalid_exchange": 400,
       "invalid_mode": 400,
       "already_running": 409,
       "not_running": 404,
       "not_found": 404,
   }


   def _error(code: str, message: str) -> JSONResponse:
       return JSONResponse(
           status_code=_STATUS_MAP.get(code, 500),
           content={"error": {"code": code, "message": message}},
       )


   @router.get("/exchanges", response_model=ExchangeListResponse)
   async def list_exchanges() -> ExchangeListResponse:
       items = live_manager.list_exchanges()
       exchanges = [ExchangeInfo(**item) for item in items]
       return ExchangeListResponse(exchanges=exchanges, total=len(exchanges))


   @router.post("/start")
   async def start(req: StartRequest):
       try:
           pid = live_manager.start(req.exchange, req.symbol, req.mode, req.capital, req.leverage)
           return StartResponse(exchange=req.exchange, pid=pid, status="running")
       except LiveError as e:
           return _error(e.code, e.message)


   @router.post("/stop")
   async def stop(req: StopRequest):
       try:
           live_manager.stop(req.exchange)
           return StopResponse(exchange=req.exchange, status="stopped")
       except LiveError as e:
           return _error(e.code, e.message)


   @router.get("/status/{exchange}")
   async def status(exchange: str):
       if exchange not in EXCHANGES:
           return _error("not_found", f"unsupported exchange: {exchange}")
       running = live_manager.is_running(exchange)
       state, updated_at = read_state(exchange)
       return LiveStatus(exchange=exchange, running=running, state=state, updated_at=updated_at)


   @router.get("/logs/{exchange}", response_model=LogResponse)
   async def logs(
       exchange: str,
       tail: int = Query(default=200, ge=1, le=1000),
   ) -> LogResponse:
       lines, total = read_logs(exchange, tail)
       return LogResponse(exchange=exchange, lines=lines, total_lines=total)
   ```
   - `tail` 用 `Query(ge=1, le=1000)` 限制范围，超限 FastAPI 自动返 422

### ✅ 完成验证
- [ ] `uv run python -c "from app.api.v1.live import router; print([(r.path, r.methods) for r in router.routes])"` 显示 5 个路由

---

## S5. 启动验证

### 目标

11 项 curl 全绿。

### 实施步骤

1. 启动后端 `cd backend && uv run uvicorn app.main:app --port 8000`
2. 逐项 curl

### ✅ 完成验证
- [ ] 5.2 exchanges 返回 3 项全 stopped
- [ ] 5.3 invalid exchange → `{"error":{"code":"invalid_exchange",...}}`
- [ ] 5.4 start binance 返 200 + PID（子进程会立刻退出因 torch 装不上）
- [ ] 5.5 等 2s 后 exchanges 显示 binance stopped
- [ ] 5.6 stop 未运行 → `{"error":{"code":"not_running",...}}`
- [ ] 5.7 fake state 文件 → status 返 state + updated_at
- [ ] 5.8 fake log 文件 500 行 → logs?tail=10 返末尾 10 行 + total_lines=500
- [ ] 5.9 okx log 不存在 → `{"lines":[],"total_lines":0}`
- [ ] 5.10 tail=5000 被 Query le=1000 限制 → 返 422（Pydantic 校验失败）
- [ ] 5.11 status huobi → `{"error":{"code":"not_found",...}}`

---

## 风险与回滚

- **[子进程立刻退出]** → start 返 200 + PID 后子进程因 torch 缺失退出，前端通过 exchanges/status 看到 stopped。这是预期行为。
- **[并发 start 竞态]** → `threading.Lock` 保护映射表，第二个 start 看到 already_running 返 409。
- **[stop 时进程已退出]** → `proc.terminate()` 对已退出进程是 no-op 不抛错；`_cleanup_if_dead` 在 stop 开头先清理，若已退出则抛 not_running。
- **[nado CLI 用 --ticker 不是 --symbol]** → 本 task 统一用 --symbol，nado 启动会报错。这是已知 v0.1 限制，留给 v0.2 修复（按 exchange 适配 CLI 参数）。
- **[回滚]**：`git checkout backend/app/api/v1/live.py` 恢复占位 + 删新增 schemas/services
