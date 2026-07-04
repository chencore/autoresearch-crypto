from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dex.config import LOG_DIR

from app.services.live_manager import LOG_FILES, STATE_FILES

MAX_TAIL = 1000


def _resolve_log_path(exchange: str) -> Path | None:
    if exchange in LOG_FILES:
        return LOG_DIR / LOG_FILES[exchange]
    if exchange == "nado":
        candidates = sorted(LOG_DIR.glob("live_nado_log_*.txt"), reverse=True)
        return candidates[0] if candidates else None
    return None


def read_state(exchange: str) -> tuple[dict | None, str | None]:
    if exchange not in STATE_FILES:
        return None, None
    path = LOG_DIR / STATE_FILES[exchange]
    if not path.exists():
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    return state, updated_at


def read_logs(exchange: str, tail: int = 200) -> tuple[list[str], int]:
    path = _resolve_log_path(exchange)
    if path is None or not path.exists():
        return [], 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    all_lines = [line.rstrip("\n") for line in all_lines]
    total = len(all_lines)
    limited_tail = min(tail, MAX_TAIL)
    return all_lines[-limited_tail:] if limited_tail > 0 else [], total
