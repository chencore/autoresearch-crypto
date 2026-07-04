from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Interval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


class DownloadStartRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    interval: Interval
    days: int = Field(..., ge=1, le=365)
    proxy_url: str | None = None
    force: bool = False

    @field_validator("proxy_url")
    @classmethod
    def _validate_proxy_url(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not (v.startswith("http://") or v.startswith("https://") or v.startswith("socks5://")):
            raise ValueError("proxy_url must start with http://, https://, or socks5://")
        return v


class DownloadStartResponse(BaseModel):
    task_id: str
    status: str


class DownloadTaskStatus(BaseModel):
    task_id: str
    symbol: str
    interval: str
    days: int
    status: str
    started_at: str
    completed_at: str | None
    error: str | None
    line_count: int
    last_line: str | None


class DownloadFile(BaseModel):
    filename: str
    symbol: str
    interval: str
    days: int
    size_bytes: int
    mtime: str


class DownloadFileListResponse(BaseModel):
    files: list[DownloadFile]
    total: int


class DownloadStopRequest(BaseModel):
    task_id: str


class DownloadStopResponse(BaseModel):
    task_id: str
    status: str
