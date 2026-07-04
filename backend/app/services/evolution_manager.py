from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import WebSocket


@dataclass
class EvolutionRun:
    id: str
    engine: str
    symbol: str
    interval: str
    days: int
    generations: int
    status: str = "running"
    current_gen: int = 0
    agents: list[dict[str, Any]] = field(default_factory=list)
    event_buffer: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[WebSocket] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "engine": self.engine,
            "symbol": self.symbol,
            "status": self.status,
            "current_gen": self.current_gen,
            "total_generations": self.generations,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    def detail(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "engine": self.engine,
            "symbol": self.symbol,
            "interval": self.interval,
            "days": self.days,
            "generations": self.generations,
            "status": self.status,
            "current_gen": self.current_gen,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "agents": self.agents,
            "event_count": len(self.event_buffer),
        }


class EvolutionManager:
    def __init__(self) -> None:
        self._runs: dict[str, EvolutionRun] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def create_run(self, config: dict[str, Any]) -> str:
        from app.services.evolution_runner import run_evolution_thread

        run_id = str(uuid.uuid4())
        run = EvolutionRun(
            id=run_id,
            engine=config["engine"],
            symbol=config["symbol"],
            interval=config["interval"],
            days=config["days"],
            generations=config["generations"],
        )
        with self._lock:
            self._runs[run_id] = run
        thread = threading.Thread(
            target=run_evolution_thread, args=(run_id, config), daemon=True
        )
        run.thread = thread
        thread.start()
        return run_id

    def get_run(self, run_id: str) -> EvolutionRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = sorted(
                self._runs.values(),
                key=lambda r: r.started_at,
                reverse=True,
            )
            return [r.summary() for r in runs]

    def request_stop(self, run_id: str) -> str | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return "not_found"
            if run.status in ("completed", "failed", "stopped"):
                return "already_finished"
            run.cancel_event.set()
            run.status = "stopping"
            return None

    def update_gen(self, run_id: str, gen: int) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.current_gen = gen

    def update_agents(self, run_id: str, agents: list[dict[str, Any]]) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.agents = agents

    def mark_completed(self, run_id: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.status == "running":
                run.status = "completed"
                run.completed_at = datetime.now().isoformat()

    def mark_failed(self, run_id: str, error: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.status = "failed"
                run.error = error
                run.completed_at = datetime.now().isoformat()

    def mark_stopped(self, run_id: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.status = "stopped"
                run.completed_at = datetime.now().isoformat()

    def broadcast(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run.event_buffer.append(event)
            if len(run.event_buffer) > 500:
                run.event_buffer = run.event_buffer[-300:]
            subs = list(run.subscribers)
        if self._loop is None:
            return
        for ws in subs:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(event), self._loop)
            except Exception:
                pass

    def subscribe(self, run_id: str, ws: WebSocket) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return False
            run.subscribers.append(ws)
            return True

    def unsubscribe(self, run_id: str, ws: WebSocket) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and ws in run.subscribers:
                run.subscribers.remove(ws)


evolution_manager = EvolutionManager()
