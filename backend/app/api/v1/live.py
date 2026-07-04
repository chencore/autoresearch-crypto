from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.schemas.live import (
    ExchangeInfo,
    ExchangeListResponse,
    LiveStatus,
    LogResponse,
    StartRequest,
    StartResponse,
    StopRequest,
    StopResponse,
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
        pid = live_manager.start(
            req.exchange, req.symbol, req.mode, req.capital, req.leverage
        )
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
    return LiveStatus(
        exchange=exchange, running=running, state=state, updated_at=updated_at
    )


@router.get("/logs/{exchange}", response_model=LogResponse)
async def logs(
    exchange: str,
    tail: int = Query(default=200, ge=1, le=1000),
) -> LogResponse:
    lines, total = read_logs(exchange, tail)
    return LogResponse(exchange=exchange, lines=lines, total_lines=total)
