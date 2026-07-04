from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas.evolve import (
    EvolveRunListResponse,
    EvolveRunSummary,
    EvolveStartRequest,
    EvolveStartResponse,
    EvolveStopRequest,
    EvolveStopResponse,
)
from app.services.evolution_manager import evolution_manager
from app.services.evolution_runner import EvolveRunnerError, _check_data_exists

router = APIRouter()

_STATUS_MAP = {
    "invalid_engine": 400,
    "data_not_found": 404,
    "not_found": 404,
    "already_finished": 409,
}


def _error(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS_MAP.get(code, 500),
        content={"error": {"code": code, "message": message}},
    )


@router.post("/start")
async def start(req: EvolveStartRequest):
    if req.engine not in ("atlas", "gepa"):
        return _error(
            "invalid_engine",
            f"engine must be 'atlas' or 'gepa', got: {req.engine}",
        )
    try:
        _check_data_exists(req.symbol, req.interval, req.days)
    except EvolveRunnerError as e:
        return _error(e.code, e.message)
    config = req.model_dump()
    run_id = evolution_manager.create_run(config)
    return EvolveStartResponse(run_id=run_id, status="running")


@router.get("/runs", response_model=EvolveRunListResponse)
async def list_runs() -> EvolveRunListResponse:
    runs = evolution_manager.list_runs()
    return EvolveRunListResponse(
        runs=[EvolveRunSummary(**r) for r in runs], total=len(runs)
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = evolution_manager.get_run(run_id)
    if run is None:
        return _error("not_found", f"run not found: {run_id}")
    return run.detail()


@router.post("/stop")
async def stop(req: EvolveStopRequest):
    err = evolution_manager.request_stop(req.run_id)
    if err is None:
        return EvolveStopResponse(run_id=req.run_id, status="stopping")
    if err == "already_finished":
        run = evolution_manager.get_run(req.run_id)
        status = run.status if run else "unknown"
        return _error(
            "already_finished",
            f"run {req.run_id} is already {status}",
        )
    return _error("not_found", f"run not found: {req.run_id}")
