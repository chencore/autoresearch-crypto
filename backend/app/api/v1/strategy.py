from fastapi import APIRouter, HTTPException

from app.schemas.strategy import StrategyDetail, StrategyListResponse
from app.services.strategy_registry import get_detail, list_summaries

router = APIRouter()


@router.get("", response_model=StrategyListResponse)
async def list_strategies() -> StrategyListResponse:
    summaries = list_summaries()
    return StrategyListResponse(strategies=summaries, total=len(summaries))


@router.get("/{name}", response_model=StrategyDetail)
async def get_strategy(name: str) -> StrategyDetail:
    detail = get_detail(name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"strategy not found: {name}")
    return detail
