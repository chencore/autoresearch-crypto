from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def evolve_root() -> dict:
    return {"module": "evolve", "status": "todo"}
