from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def strategy_root() -> dict:
    return {"module": "strategy", "status": "todo"}
