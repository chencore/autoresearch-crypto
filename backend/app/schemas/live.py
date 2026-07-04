from typing import Any

from pydantic import BaseModel


class ExchangeInfo(BaseModel):
    name: str
    status: str
    pid: int | None
    script: str
    state_file: str
    log_file: str


class ExchangeListResponse(BaseModel):
    exchanges: list[ExchangeInfo]
    total: int


class StartRequest(BaseModel):
    exchange: str
    symbol: str
    mode: str
    capital: float = 100.0
    leverage: float = 1.0


class StartResponse(BaseModel):
    exchange: str
    pid: int
    status: str


class StopRequest(BaseModel):
    exchange: str


class StopResponse(BaseModel):
    exchange: str
    status: str


class LiveStatus(BaseModel):
    exchange: str
    running: bool
    state: dict[str, Any] | None
    updated_at: str | None


class LogResponse(BaseModel):
    exchange: str
    lines: list[str]
    total_lines: int
