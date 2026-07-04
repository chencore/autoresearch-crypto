from fastapi import APIRouter

from app.api.v1 import backtest, data_download, evolve, live, strategy, ws

api_router = APIRouter()

api_router.include_router(strategy.router, prefix="/strategy", tags=["strategy"])
api_router.include_router(backtest.router, prefix="/backtest", tags=["backtest"])
api_router.include_router(live.router, prefix="/live", tags=["live"])
api_router.include_router(evolve.router, prefix="/evolve", tags=["evolve"])
api_router.include_router(data_download.router, prefix="/data-download", tags=["data-download"])
api_router.include_router(ws.router, prefix="/ws", tags=["ws"])
