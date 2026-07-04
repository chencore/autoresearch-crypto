from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas.data_download import (
    DownloadFile,
    DownloadFileListResponse,
    DownloadStartRequest,
    DownloadStartResponse,
    DownloadStopRequest,
    DownloadStopResponse,
    DownloadTaskStatus,
)
from app.services.data_download_manager import data_download_manager

router = APIRouter()

_STATUS_MAP = {
    "already_running": 409,
    "already_finished": 409,
    "not_found": 404,
}


def _error(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS_MAP.get(code, 500),
        content={"error": {"code": code, "message": message}},
    )


@router.post("/start", response_model=DownloadStartResponse)
async def start(req: DownloadStartRequest):
    active_id = data_download_manager.get_active_task_id()
    if active_id is not None:
        return _error(
            "already_running",
            f"已有下载任务在跑,task_id: {active_id}",
        )
    task_id, err = data_download_manager.start(
        symbol=req.symbol,
        interval=req.interval,
        days=req.days,
        proxy_url=req.proxy_url,
        force=req.force,
    )
    if err is not None:
        return _error(err, f"start failed: {err}")
    return DownloadStartResponse(task_id=task_id, status="running")


@router.get("/status/{task_id}", response_model=DownloadTaskStatus)
async def get_status(task_id: str):
    task = data_download_manager.get_task(task_id)
    if task is None:
        return _error("not_found", f"task not found: {task_id}")
    return DownloadTaskStatus(**task.status_dict())


@router.post("/stop", response_model=DownloadStopResponse)
async def stop(req: DownloadStopRequest):
    err = data_download_manager.request_stop(req.task_id)
    if err is None:
        return DownloadStopResponse(task_id=req.task_id, status="stopping")
    if err == "already_finished":
        task = data_download_manager.get_task(req.task_id)
        status = task.status if task else "unknown"
        return _error(
            "already_finished",
            f"task {req.task_id} is already {status}",
        )
    return _error("not_found", f"task not found: {req.task_id}")


@router.get("/files", response_model=DownloadFileListResponse)
async def list_files() -> DownloadFileListResponse:
    files = data_download_manager.list_files()
    return DownloadFileListResponse(
        files=[DownloadFile(**f) for f in files], total=len(files)
    )
