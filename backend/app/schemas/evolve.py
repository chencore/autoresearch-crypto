from typing import Any

from pydantic import BaseModel


class EvolveStartRequest(BaseModel):
    engine: str
    symbol: str
    interval: str
    days: int
    generations: int
    evolution_interval: int = 5


class EvolveStartResponse(BaseModel):
    run_id: str
    status: str


class EvolveAgentState(BaseModel):
    name: str
    style: str
    score: float
    weight: float
    generation: int
    params: dict[str, Any]


class EvolveRunSummary(BaseModel):
    id: str
    engine: str
    symbol: str
    status: str
    current_gen: int
    total_generations: int
    started_at: str
    completed_at: str | None
    error: str | None


class EvolveRunListResponse(BaseModel):
    runs: list[EvolveRunSummary]
    total: int


class EvolveRunDetail(BaseModel):
    id: str
    engine: str
    symbol: str
    interval: str
    days: int
    generations: int
    status: str
    current_gen: int
    started_at: str
    completed_at: str | None
    error: str | None
    agents: list[EvolveAgentState]
    event_count: int


class EvolveStopRequest(BaseModel):
    run_id: str


class EvolveStopResponse(BaseModel):
    run_id: str
    status: str
