from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dex.config import PROJECT_DIR
from fastapi import WebSocket

_FILENAME_RE = re.compile(r"^(?P<symbol>[A-Za-z0-9]+)_(?P<interval>1m|5m|15m|1h|4h|1d)_(?P<days>\d+)d\.parquet$")
_EVENT_BUFFER_CAP = 500
_EVENT_BUFFER_TRIM_TO = 300


@dataclass
class DownloadTask:
    task_id: str
    symbol: str
    interval: str
    days: int
    proxy_url: str | None
    force: bool
    status: str = "running"
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str | None = None
    error: str | None = None
    line_count: int = 0
    last_line: str | None = None
    event_buffer: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[WebSocket] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    proc: subprocess.Popen | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def status_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "task_id": self.task_id,
                "symbol": self.symbol,
                "interval": self.interval,
                "days": self.days,
                "status": self.status,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "error": self.error,
                "line_count": self.line_count,
                "last_line": self.last_line,
            }


class DataDownloadManager:
    def __init__(self) -> None:
        self._tasks: dict[str, DownloadTask] = {}
        self._active_task_id: str | None = None
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def get_active_task_id(self) -> str | None:
        with self._lock:
            return self._active_task_id

    def get_task(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def start(
        self,
        symbol: str,
        interval: str,
        days: int,
        proxy_url: str | None,
        force: bool,
    ) -> tuple[str | None, str | None]:
        """Returns (task_id, error_code). On success error_code is None.
        On conflict error_code is 'already_running' and task_id is None."""
        with self._lock:
            if self._active_task_id is not None:
                active = self._tasks.get(self._active_task_id)
                if active is not None and active.status == "running":
                    return None, "already_running"
                self._active_task_id = None
            task_id = str(uuid.uuid4())
            task = DownloadTask(
                task_id=task_id,
                symbol=symbol,
                interval=interval,
                days=days,
                proxy_url=proxy_url,
                force=force,
            )
            self._tasks[task_id] = task
            self._active_task_id = task_id

        cmd = [
            "uv", "run", "python", "prepare_crypto.py",
            "--symbol", symbol,
            "--interval", interval,
            "--limit", str(days),
        ]
        if force:
            cmd.append("--force")

        env = os.environ.copy()
        if proxy_url:
            env["HTTPS_PROXY"] = proxy_url
            env["HTTP_PROXY"] = proxy_url

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except Exception as e:
            self._mark_failed(task_id, f"failed to start subprocess: {e}")
            with self._lock:
                if self._active_task_id == task_id:
                    self._active_task_id = None
            return task_id, None

        task.proc = proc
        self._broadcast(task_id, {
            "type": "started",
            "task_id": task_id,
            "symbol": symbol,
            "interval": interval,
            "days": days,
            "total_lines": None,
        })

        thread = threading.Thread(
            target=self._read_stdout_loop,
            args=(task_id, proc),
            daemon=True,
        )
        thread.start()
        return task_id, None

    def _read_stdout_loop(self, task_id: str, proc: subprocess.Popen) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                line_stripped = line.rstrip("\n")
                if not line_stripped:
                    continue
                with task._lock:
                    task.line_count += 1
                    task.last_line = line_stripped
                self._broadcast(task_id, {
                    "type": "progress",
                    "task_id": task_id,
                    "line": line_stripped,
                    "line_number": task.line_count,
                    "timestamp": datetime.now().isoformat(),
                })
            return_code = proc.wait()
        except Exception as e:
            self._mark_failed(task_id, f"stdout reader error: {e}")
            self._release_active(task_id)
            return

        with task._lock:
            cancelled = task.cancel_event.is_set()
            current_status = task.status

        if cancelled or current_status == "stopping":
            self._mark_stopped(task_id)
            self._broadcast(task_id, {"type": "stopped", "task_id": task_id})
        elif return_code == 0:
            file_path = str(PROJECT_DIR / "data" / "crypto" / f"{task.symbol}_{task.interval}_{task.days}d.parquet")
            self._mark_completed(task_id)
            self._broadcast(task_id, {
                "type": "completed",
                "task_id": task_id,
                "line_count": task.line_count,
                "file_path": file_path,
            })
        else:
            err_msg = task.last_line or f"subprocess exited with code {return_code}"
            self._mark_failed(task_id, err_msg)
            self._broadcast(task_id, {
                "type": "error",
                "task_id": task_id,
                "code": "subprocess_failed",
                "message": err_msg,
            })
        self._release_active(task_id)

    def _release_active(self, task_id: str) -> None:
        with self._lock:
            if self._active_task_id == task_id:
                self._active_task_id = None

    def _mark_completed(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        with task._lock:
            task.status = "completed"
            task.completed_at = datetime.now().isoformat()

    def _mark_failed(self, task_id: str, error: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        with task._lock:
            task.status = "failed"
            task.error = error
            task.completed_at = datetime.now().isoformat()

    def _mark_stopped(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        with task._lock:
            task.status = "stopped"
            task.completed_at = datetime.now().isoformat()

    def request_stop(self, task_id: str) -> str | None:
        """Returns None on success, or error code: 'not_found' / 'already_finished'."""
        task = self.get_task(task_id)
        if task is None:
            return "not_found"
        with task._lock:
            if task.status in ("completed", "failed", "stopped"):
                return "already_finished"
            task.cancel_event.set()
            task.status = "stopping"
        proc = task.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            except Exception:
                pass
        return None

    def _broadcast(self, task_id: str, event: dict[str, Any]) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        with task._lock:
            task.event_buffer.append(event)
            if len(task.event_buffer) > _EVENT_BUFFER_CAP:
                task.event_buffer = task.event_buffer[-_EVENT_BUFFER_TRIM_TO:]
            subs = list(task.subscribers)
        if self._loop is None:
            return
        for ws in subs:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(event), self._loop)
            except Exception:
                pass

    def subscribe(self, task_id: str, ws: WebSocket) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        with task._lock:
            task.subscribers.append(ws)
            return True

    def unsubscribe(self, task_id: str, ws: WebSocket) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        with task._lock:
            if ws in task.subscribers:
                task.subscribers.remove(ws)

    def list_files(self) -> list[dict[str, Any]]:
        data_dir = PROJECT_DIR / "data" / "crypto"
        if not data_dir.exists():
            return []
        files: list[dict[str, Any]] = []
        for path in data_dir.iterdir():
            if not path.is_file() or path.suffix != ".parquet":
                continue
            m = _FILENAME_RE.match(path.name)
            if not m:
                continue
            stat = path.stat()
            files.append({
                "filename": path.name,
                "symbol": m.group("symbol"),
                "interval": m.group("interval"),
                "days": int(m.group("days")),
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return files


data_download_manager = DataDownloadManager()
