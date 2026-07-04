## 1. Pydantic 模型

- [x] 1.1 创建 `backend/app/schemas/live.py`：
  - `class ExchangeInfo(BaseModel)`: `name: str`、`status: str`、`pid: int | None`、`script: str`、`state_file: str`、`log_file: str`
  - `class ExchangeListResponse(BaseModel)`: `exchanges: list[ExchangeInfo]`、`total: int`
  - `class StartRequest(BaseModel)`: `exchange: str`、`symbol: str`、`mode: str`、`capital: float = 100.0`、`leverage: float = 1.0`
  - `class StartResponse(BaseModel)`: `exchange: str`、`pid: int`、`status: str`
  - `class StopRequest(BaseModel)`: `exchange: str`
  - `class StopResponse(BaseModel)`: `exchange: str`、`status: str`
  - `class LiveStatus(BaseModel)`: `exchange: str`、`running: bool`、`state: dict[str, Any] | None`、`updated_at: str | None`
  - `class LogResponse(BaseModel)`: `exchange: str`、`lines: list[str]`、`total_lines: int`

## 2. 进程管理器

- [x] 2.1 创建 `backend/app/services/live_manager.py`：
  - `import subprocess`、`import threading`、`import os`、`from pathlib import Path`、`from dex.config import PROJECT_DIR`
  - `EXCHANGES = {"binance": "live_binance_quant.py", "okx": "live_okx_quant.py", "nado": "live_nado_quant.py"}`
  - `class LiveManager:`
    - `__init__`: `self._processes: dict[str, subprocess.Popen] = {}`、`self._lock = threading.Lock()`
    - `def _cleanup_if_dead(exchange)`: 检查 `proc.poll() is not None`，是则从映射表移除
    - `def is_running(exchange) -> bool`: 加锁，`_cleanup_if_dead`，返回 `exchange in self._processes`
    - `def get_pid(exchange) -> int | None`: 加锁，`_cleanup_if_dead`，返回 `proc.pid` 或 None
    - `def list_exchanges() -> list[dict]`: 遍历 EXCHANGES，对每个调 `is_running` + `get_pid`，构造 ExchangeInfo dict
    - `def start(exchange, symbol, mode, capital, leverage) -> int`: 加锁，检查 `exchange in EXCHANGES`（否则抛 `LiveError("invalid_exchange", ...)`），检查 `is_running`（是则抛 `LiveError("already_running", ...)`），构造命令 `["uv", "run", "python", script, "--symbol", symbol, f"--{mode}", "--capital", str(capital), "--leverage", str(leverage)]`，`Popen(cmd, cwd=PROJECT_DIR, stdout=DEVNULL, stderr=DEVNULL)`，存入映射表，返回 `proc.pid`
    - `def stop(exchange) -> None`: 加锁，`_cleanup_if_dead`，检查 `exchange not in self._processes`（否则抛 `LiveError("not_running", ...)`），`proc.terminate() → proc.wait(timeout=5)`，超时 `proc.kill() → proc.wait()`，从映射表移除
  - `class LiveError(Exception)`: `__init__(code, message)`
  - 模块级单例 `live_manager = LiveManager()`

## 3. 状态与日志读取器

- [x] 3.1 创建 `backend/app/services/live_state_reader.py`：
  - `import json`、`from pathlib import Path`、`from datetime import datetime`、`from dex.config import LOG_DIR`
  - `STATE_FILES = {"binance": "live_binance_state.json", "okx": "live_okx_state.json", "nado": "live_nado_state.json"}`
  - `LOG_FILES = {"binance": "live_binance_log.txt", "okx": "live_okx_log.txt"}`（nado 特殊处理）
  - `def read_state(exchange) -> tuple[dict | None, str | None]`:
    - `path = LOG_DIR / STATE_FILES[exchange]`
    - 不存在返回 `(None, None)`
    - 读 JSON → `state`，`datetime.fromtimestamp(path.stat().st_mtime).isoformat()` → `updated_at`
    - 返回 `(state, updated_at)`
  - `def read_logs(exchange, tail=200) -> tuple[list[str], int]`:
    - `path = _resolve_log_path(exchange)`：binance/okx 直接用 LOG_FILES；nado 用 `glob("live_nado_log_*.txt")` 取最新（按文件名降序）
    - 不存在返回 `([], 0)`
    - 读所有行，取末尾 `min(tail, 1000, len)` 行
    - 返回 `(lines, total)`
  - `def _resolve_log_path(exchange) -> Path | None`:
    - binance/okx：`LOG_DIR / LOG_FILES[exchange]`
    - nado：`sorted(LOG_DIR.glob("live_nado_log_*.txt"), reverse=True)[0]` 或 None

## 4. 路由改造

- [x] 4.1 改造 `backend/app/api/v1/live.py`：
  - `from fastapi import APIRouter, HTTPException, Query`
  - `from fastapi.responses import JSONResponse`
  - `from app.schemas.live import *`
  - `from app.services.live_manager import live_manager, LiveError`
  - `from app.services.live_state_reader import read_state, read_logs`
  - `router = APIRouter()`
  - `_STATUS_MAP = {"invalid_exchange": 400, "already_running": 409, "not_running": 404, "not_found": 404}`
  - `def _error(code, message, status=None) -> JSONResponse`: 用 `_STATUS_MAP.get(code, 500)` 返回 `JSONResponse(status_code=..., content={"error": {"code": code, "message": message}})`
  - `@router.get("/exchanges", response_model=ExchangeListResponse)`:
    - `exchanges = live_manager.list_exchanges()`
    - 返回 `{"exchanges": [...], "total": 3}`
  - `@router.post("/start")`:
    - 接 `req: StartRequest`
    - try `pid = live_manager.start(req.exchange, req.symbol, req.mode, req.capital, req.leverage)` 返回 `StartResponse(exchange=req.exchange, pid=pid, status="running")`
    - except `LiveError as e`: `return _error(e.code, e.message)`
  - `@router.post("/stop")`:
    - 接 `req: StopRequest`
    - try `live_manager.stop(req.exchange)` 返回 `StopResponse(exchange=req.exchange, status="stopped")`
    - except `LiveError as e`: `return _error(e.code, e.message)`
  - `@router.get("/status/{exchange}", response_model=LiveStatus)`:
    - 若 `exchange not in EXCHANGES`：return `_error("not_found", f"unsupported exchange: {exchange}")`
    - `running = live_manager.is_running(exchange)`
    - `state, updated_at = read_state(exchange)`
    - 返回 `LiveStatus(exchange=exchange, running=running, state=state, updated_at=updated_at)`
  - `@router.get("/logs/{exchange}", response_model=LogResponse)`:
    - `tail = min(tail, 1000)` (Query 默认 200，最大 1000)
    - `lines, total = read_logs(exchange, tail)`
    - 返回 `LogResponse(exchange=exchange, lines=lines, total_lines=total)`

## 5. 启动验证

- [x] 5.1 启动后端 `cd backend && uv run uvicorn app.main:app --port 8000`
- [x] 5.2 `curl -s http://127.0.0.1:8000/api/v1/live/exchanges | python3 -m json.tool` 验证返回 3 个交易所全 stopped
- [x] 5.3 验证 invalid exchange：`curl -s -X POST http://127.0.0.1:8000/api/v1/live/start -H "Content-Type: application/json" -d '{"exchange":"huobi","symbol":"BTCUSDT","mode":"demo"}'` 返回 `{"error":{"code":"invalid_exchange","message":"unsupported exchange: huobi"}}`
- [x] 5.4 验证 start binance（子进程会立刻退出因 torch 装不上）：`curl -s -X POST http://127.0.0.1:8000/api/v1/live/start -H "Content-Type: application/json" -d '{"exchange":"binance","symbol":"BTCUSDT","mode":"demo","capital":100}'` 返回 `{"exchange":"binance","pid":<PID>,"status":"running"}`
- [x] 5.5 等 2 秒后 `curl -s http://127.0.0.1:8000/api/v1/live/exchanges` 验证 binance 已 stopped（子进程退出）
- [x] 5.6 验证 not_running：`curl -s -X POST http://127.0.0.1:8000/api/v1/live/stop -H "Content-Type: application/json" -d '{"exchange":"binance"}'` 返回 `{"error":{"code":"not_running","message":"binance is not running"}}`
- [x] 5.7 准备 fake state 文件：`echo '{"position": 1, "strategy_size": 0.5, "entry_price": 2000}' > logs/live_binance_state.json`，`curl -s http://127.0.0.1:8000/api/v1/live/status/binance` 验证 state 字段含 position/strategy_size/entry_price，updated_at 非 null
- [x] 5.8 准备 fake log 文件：`python3 -c "open('logs/live_binance_log.txt','w').write('\n'.join(f'line {i}' for i in range(500)))"`，`curl -s "http://127.0.0.1:8000/api/v1/live/logs/binance?tail=10"` 验证返回末尾 10 行，total_lines=500
- [x] 5.9 验证 logs 文件不存在：`rm logs/live_okx_log.txt`，`curl -s http://127.0.0.1:8000/api/v1/live/logs/okx` 返回 `{"exchange":"okx","lines":[],"total_lines":0}`
- [x] 5.10 验证 tail 上限：`curl -s "http://127.0.0.1:8000/api/v1/live/logs/binance?tail=5000"` 返回 1000 行（裁剪到上限）
- [x] 5.11 验证 status 不存在交易所：`curl -s http://127.0.0.1:8000/api/v1/live/status/huobi` 返回 `{"error":{"code":"not_found","message":"unsupported exchange: huobi"}}`
