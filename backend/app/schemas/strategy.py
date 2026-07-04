from typing import Any

from pydantic import BaseModel


class ParamDef(BaseModel):
    name: str
    type: str
    default: Any = None
    annotation: str | None = None
    required: bool


class StrategySummary(BaseModel):
    name: str
    module: str
    file: str
    description: str
    params_count: int
    live_status: str


class StrategyDetail(BaseModel):
    name: str
    module: str
    file: str
    description: str
    class_docstring: str
    params: list[ParamDef]
    signal_kind: str
    runtime_params: list[str]
    live_status: str


class StrategyListResponse(BaseModel):
    strategies: list[StrategySummary]
    total: int
