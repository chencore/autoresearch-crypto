"""
Shared utilities for live trading scripts.

Extracted from live_nado_quant.py and live_okx_quant.py to eliminate
~300 lines of duplicated code.
"""

import os
import sys
import json
import math
import time
import functools
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Process lock (prevents duplicate instances)
# ---------------------------------------------------------------------------


def acquire_lock(lock_path: str) -> int:
    """Acquire an exclusive file lock to prevent duplicate process instances.

    Args:
        lock_path: Path to the lock file.

    Returns:
        File descriptor for the lock.

    Raises:
        SystemExit: If another instance is already running.
    """
    try:
        import msvcrt

        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            print("错误: 已有另一个实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd
    except ImportError:
        import fcntl

        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            print("错误: 已有另一个实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state(path: str) -> Optional[dict]:
    """Load trading state from a JSON file.

    Args:
        path: Path to the state JSON file.

    Returns:
        State dict, or None if the file does not exist.
    """
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state: dict, path: str) -> None:
    """Save trading state to a JSON file.

    Args:
        state: State dictionary to persist.
        path: Path to the state JSON file.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TradeLogger:
    """Simple file + console logger for live trading."""

    def __init__(self, log_path: str):
        """Initialize logger with a file path.

        Args:
            log_path: Path to the log file (appended mode).
        """
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    def log(self, msg: str) -> None:
        """Write a timestamped message to console and log file.

        Args:
            msg: Message string.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


def retry_on_exception(
    max_retries: int = 3,
    delay: float = 1.0,
    exceptions: Tuple[type, ...] = (Exception,),
    logger: Optional[TradeLogger] = None,
) -> Callable:
    """Decorator: retry a function on specified exceptions.

    Args:
        max_retries: Maximum number of attempts.
        delay: Seconds to wait between retries.
        exceptions: Tuple of exception classes to catch.
        logger: Optional TradeLogger for retry messages.

    Returns:
        Decorated function wrapper.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise
                    msg = f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}，{delay}s 后重试..."
                    if logger:
                        logger.log(msg)
                    else:
                        print(msg)
                    time.sleep(delay)
            return None

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Time alignment
# ---------------------------------------------------------------------------


def align_next_wake_time(interval_seconds: int, offset_seconds: int = 15) -> datetime:
    """Calculate the next wake time aligned to interval boundaries.

    Args:
        interval_seconds: Interval in seconds (e.g. 300 for 5m).
        offset_seconds: Extra seconds to add after the boundary.

    Returns:
        Datetime of the next wake time.
    """
    now = datetime.now()
    epoch = datetime(1970, 1, 1)
    now_ts = (now - epoch).total_seconds()
    next_boundary = math.ceil(now_ts / interval_seconds) * interval_seconds
    next_wake_ts = next_boundary + offset_seconds
    return epoch + timedelta(seconds=next_wake_ts)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


def predict_signal(strategy: Any, df: pd.DataFrame, enable_short: bool = False) -> Tuple[int, dict]:
    """Generate trading signal and Bollinger Band display info.

    Args:
        strategy: Strategy object with ``generate_signals`` and ``window``,
                  ``std_dev`` attributes.
        df: OHLCV DataFrame with at least a 'close' column.
        enable_short: Whether short signals are allowed.

    Returns:
        Tuple of (signal_id, bb_info). signal_id: 0=close, 1=hold,
        2=long, 3=short.
    """
    signals = strategy.generate_signals(df, enable_short=enable_short)
    signal_id = int(signals[-1])

    close = df["close"].values
    window = strategy.window
    rolling_mean = pd.Series(close).rolling(window=window, min_periods=window).mean()
    rolling_std = pd.Series(close).rolling(window=window, min_periods=window).std()
    upper = rolling_mean + strategy.std_dev * rolling_std
    lower = rolling_mean - strategy.std_dev * rolling_std

    bb_info = {
        "price": float(close[-1]),
        "upper": float(upper.iloc[-1]),
        "mid": float(rolling_mean.iloc[-1]),
        "lower": float(lower.iloc[-1]),
    }
    return signal_id, bb_info


# ---------------------------------------------------------------------------
# Price utilities
# ---------------------------------------------------------------------------


def round_to_tick(price: float, tick_size: float) -> float:
    """Round a price to the nearest valid tick increment.

    Args:
        price: Raw price value.
        tick_size: Minimum price increment.

    Returns:
        Price rounded to the nearest tick.
    """
    ticks = round(float(price) / float(tick_size))
    return float(Decimal(ticks) * Decimal(str(tick_size)))


def compute_order_price(side: str, best_bid: float, best_ask: float, tick_size: float) -> float:
    """Compute a POST_ONLY limit order price at the best bid/ask.

    Args:
        side: 'buy' or 'sell'.
        best_bid: Current best bid price.
        best_ask: Current best ask price.
        tick_size: Minimum price increment.

    Returns:
        Limit order price aligned to tick_size.
    """
    if side == "buy":
        price = best_bid
        if price >= best_ask:
            price = best_ask - float(tick_size)
    else:
        price = best_ask
        if price <= best_bid:
            price = best_bid + float(tick_size)
    return round_to_tick(price, tick_size)


def compute_ioc_price(
    side: str, best_bid: float, best_ask: float, tick_size: float
) -> Optional[float]:
    """Compute an IOC (Taker) order price that crosses the spread.

    Args:
        side: 'buy' or 'sell'.
        best_bid: Current best bid price.
        best_ask: Current best ask price.
        tick_size: Minimum price increment.

    Returns:
        IOC order price, or None if prices are unavailable.
    """
    if side == "buy":
        price = best_ask + float(tick_size) * 2 if best_ask else None
    else:
        price = best_bid - float(tick_size) * 2 if best_bid else None
    if price is not None:
        price = round_to_tick(price, tick_size)
    return price


# ---------------------------------------------------------------------------
# Stop-loss / time-exit check
# ---------------------------------------------------------------------------


def check_stop_loss(
    state: dict,
    current_price: float,
    kline_low: Optional[float] = None,
    kline_high: Optional[float] = None,
    stop_loss_pct: float = 0.03,
    max_hold_bars: int = 48,
    logger: Optional[TradeLogger] = None,
) -> Tuple[bool, str]:
    """Check if stop-loss or time-based exit conditions are triggered.

    Supports both long and short positions. Uses K-line high/low for
    intra-bar penetration detection before falling back to close price.

    Args:
        state: Trading state dict (position, entry_price, entry_bar,
               confirmed_entry_bar, bar_count, pending_close).
        current_price: Current bar close price.
        kline_low: Current bar low (for long stop detection).
        kline_high: Current bar high (for short stop detection).
        stop_loss_pct: Stop-loss percentage as decimal (e.g. 0.03 = 3%).
        max_hold_bars: Maximum number of bars to hold before time exit.
        logger: Optional TradeLogger for anomaly warnings.

    Returns:
        Tuple of (should_exit: bool, reason: str).
    """
    pos = state.get("position", 0)
    if pos == 0:
        return False, ""

    if state.get("pending_close"):
        return False, ""

    entry_price = state.get("entry_price", 0)
    entry_bar = state.get("confirmed_entry_bar", 0) or state.get("entry_bar", 0)
    current_bar = state.get("bar_count", 0)

    if pos == 1:
        if entry_price > 0:
            stop_price = entry_price * (1 - stop_loss_pct)
            if kline_low is not None and kline_low <= stop_price:
                return True, f"多头止损: K线low={kline_low:.2f} <= 止损价={stop_price:.2f}"
            if (entry_price - current_price) / entry_price >= stop_loss_pct:
                return (
                    True,
                    f"多头止损: 跌幅 {(entry_price - current_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
                )
    elif pos == -1:
        if entry_price > 0:
            stop_price = entry_price * (1 + stop_loss_pct)
            if kline_high is not None and kline_high >= stop_price:
                return True, f"空头止损: K线high={kline_high:.2f} >= 止损价={stop_price:.2f}"
            if (current_price - entry_price) / entry_price >= stop_loss_pct:
                return (
                    True,
                    f"空头止损: 涨幅 {(current_price - entry_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
                )

    bars_held = current_bar - entry_bar
    if entry_bar > 0 and bars_held > max_hold_bars * 2:
        if logger:
            logger.log(
                f"[异常报警] 持仓 K 线数 {bars_held} 超过 2*max_hold={max_hold_bars * 2}，跳过时间退出判断"
            )
        return False, ""

    if bars_held >= max_hold_bars:
        pos_name = "多头" if pos == 1 else "空头"
        return True, f"{pos_name}时间退出: 持仓 {bars_held} 根K线 >= {max_hold_bars}"

    return False, ""
