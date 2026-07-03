from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def backtest_root() -> dict:
    return {"module": "backtest", "status": "todo"}
