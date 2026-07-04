from __future__ import annotations

import subprocess
import threading

from dex.config import PROJECT_DIR

EXCHANGES: dict[str, str] = {
    "binance": "live_binance_quant.py",
    "okx": "live_okx_quant.py",
    "nado": "live_nado_quant.py",
}

STATE_FILES = {
    "binance": "live_binance_state.json",
    "okx": "live_okx_state.json",
    "nado": "live_nado_state.json",
}

LOG_FILES = {
    "binance": "live_binance_log.txt",
    "okx": "live_okx_log.txt",
}


class LiveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class LiveManager:
    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def _cleanup_if_dead(self, exchange: str) -> None:
        proc = self._processes.get(exchange)
        if proc is not None and proc.poll() is not None:
            self._processes.pop(exchange, None)

    def is_running(self, exchange: str) -> bool:
        with self._lock:
            self._cleanup_if_dead(exchange)
            return exchange in self._processes

    def get_pid(self, exchange: str) -> int | None:
        with self._lock:
            self._cleanup_if_dead(exchange)
            proc = self._processes.get(exchange)
            return proc.pid if proc else None

    def list_exchanges(self) -> list[dict]:
        results = []
        for name, script in EXCHANGES.items():
            with self._lock:
                self._cleanup_if_dead(name)
                proc = self._processes.get(name)
                status = "running" if proc else "stopped"
                pid = proc.pid if proc else None
            log_file = LOG_FILES.get(name, f"live_{name}_log_*.txt")
            results.append({
                "name": name,
                "status": status,
                "pid": pid,
                "script": script,
                "state_file": STATE_FILES[name],
                "log_file": log_file,
            })
        return results

    def start(self, exchange: str, symbol: str, mode: str, capital: float, leverage: float) -> int:
        if exchange not in EXCHANGES:
            raise LiveError("invalid_exchange", f"unsupported exchange: {exchange}")
        if mode not in ("demo", "live"):
            raise LiveError("invalid_mode", f"mode must be 'demo' or 'live', got: {mode}")
        with self._lock:
            self._cleanup_if_dead(exchange)
            if exchange in self._processes:
                raise LiveError("already_running", f"{exchange} is already running, stop it first")
            script = EXCHANGES[exchange]
            cmd = [
                "uv", "run", "python", script,
                "--symbol", symbol,
                f"--{mode}",
                "--capital", str(capital),
                "--leverage", str(leverage),
            ]
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._processes[exchange] = proc
            return proc.pid

    def stop(self, exchange: str) -> None:
        with self._lock:
            self._cleanup_if_dead(exchange)
            proc = self._processes.get(exchange)
            if proc is None:
                raise LiveError("not_running", f"{exchange} is not running")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            self._processes.pop(exchange, None)


live_manager = LiveManager()
