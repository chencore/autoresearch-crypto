from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas.backtest import (
    BacktestRequest,
    BacktestResponse,
    SymbolListResponse,
)
from app.services.backtest_runner import BacktestError, run_backtest
from app.services.symbol_scanner import list_symbols

router = APIRouter()

_STATUS_MAP = {
    "not_found": 404,
    "data_not_found": 404,
    "no_data_in_range": 400,
}


@router.get("/symbols", response_model=SymbolListResponse)
async def list_symbols_endpoint() -> SymbolListResponse:
    symbols = list_symbols()
    return SymbolListResponse(symbols=symbols, total=len(symbols))


@router.post("/run", response_model=BacktestResponse)
async def run_backtest_endpoint(req: BacktestRequest):
    try:
        return run_backtest(req)
    except BacktestError as e:
        status = _STATUS_MAP.get(e.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": {"code": e.code, "message": e.message}},
        )
