from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def live_root() -> dict:
    return {"module": "live", "status": "todo"}
