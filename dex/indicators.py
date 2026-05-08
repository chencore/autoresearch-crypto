"""
Shared technical indicator functions.

All functions accept numpy arrays (float64) and return numpy arrays.
This single module replaces ~7 duplicated implementations across strategy classes.
"""

import numpy as np
from typing import Tuple


def compute_atr(df: dict, period: int) -> np.ndarray:
    """Compute Average True Range.

    Args:
        df: DataFrame-like with 'high', 'low', 'close' columns.
        period: ATR lookback period.

    Returns:
        Array of ATR values, same length as input. Leading values are 0
        until the first full period.
    """
    high = np.asarray(df["high"], dtype=float)
    low = np.asarray(df["low"], dtype=float)
    close = np.asarray(df["close"], dtype=float)

    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]

    atr = np.zeros(len(tr))
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_adx(
    df: dict, period: int = 14
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute ADX, +DI, -DI for trend strength and direction.

    Args:
        df: DataFrame-like with 'high', 'low', 'close' columns.
        period: ADX lookback period (default 14).

    Returns:
        Tuple of (adx, plus_di, minus_di) — all same-length arrays.
    """
    high = np.asarray(df["high"], dtype=float)
    low = np.asarray(df["low"], dtype=float)
    close = np.asarray(df["close"], dtype=float)
    n = len(high)

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0
        minus_dm[i] = down if down > up and down > 0 else 0

    atr = compute_atr(df, period)

    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    for i in range(period, n):
        if atr[i] > 0:
            plus_di[i] = 100 * np.mean(plus_dm[i - period + 1 : i + 1]) / atr[i]
            minus_di[i] = 100 * np.mean(minus_dm[i - period + 1 : i + 1]) / atr[i]

    dx = np.zeros(n)
    for i in range(period, n):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

    adx = np.zeros(n)
    adx[period * 2 - 1] = np.mean(dx[period : period * 2])
    for i in range(period * 2, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx, plus_di, minus_di


def compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute Relative Strength Index.

    Args:
        close: Array of closing prices (float).
        period: RSI lookback period (default 14).

    Returns:
        RSI values array, same length as input. Leading values filled with 50.
    """
    close = np.asarray(close, dtype=float)
    deltas = np.diff(close, prepend=close[0])
    gain = np.where(deltas > 0, deltas, 0.0)
    loss = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.zeros(len(close))
    avg_loss = np.zeros(len(close))
    avg_gain[period] = np.mean(gain[1 : period + 1])
    avg_loss[period] = np.mean(loss[1 : period + 1])

    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

    rsi = np.full(len(close), 50.0)
    for i in range(period, len(close)):
        if avg_loss[i] > 0:
            rsi[i] = 100.0 - 100.0 / (1.0 + avg_gain[i] / avg_loss[i])
        else:
            rsi[i] = 100.0
    return rsi


def compute_ema(series: np.ndarray, period: int) -> np.ndarray:
    """Compute Exponential Moving Average.

    Args:
        series: Input data array (float).
        period: EMA lookback period.

    Returns:
        EMA values array, same length as input.
    """
    series = np.asarray(series, dtype=float)
    alpha = 2.0 / (period + 1)
    ema = np.empty_like(series, dtype=float)
    ema[0] = series[0]
    for i in range(1, len(series)):
        ema[i] = alpha * series[i] + (1 - alpha) * ema[i - 1]
    return ema


def compute_macd(
    close: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute MACD line, signal line, and histogram.

    Args:
        close: Array of closing prices.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal: Signal line EMA period.

    Returns:
        Tuple of (macd_line, signal_line, macd_histogram).
    """
    ema_fast = compute_ema(close, fast)
    ema_slow = compute_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    macd_hist = macd_line - signal_line
    return macd_line, signal_line, macd_hist


def compute_mfi(df: dict, period: int = 14) -> np.ndarray:
    """Compute Money Flow Index.

    Args:
        df: DataFrame-like with 'high', 'low', 'close', 'volume' columns.
        period: MFI lookback period.

    Returns:
        MFI values array, same length as input (leading values filled with 50).
    """
    high = np.asarray(df["high"], dtype=float)
    low = np.asarray(df["low"], dtype=float)
    close = np.asarray(df["close"], dtype=float)
    volume = np.asarray(df["volume"], dtype=float)

    typical_price = (high + low + close) / 3.0
    money_flow = typical_price * volume

    pos_flow = np.where(typical_price > np.roll(typical_price, 1), money_flow, 0.0)
    neg_flow = np.where(typical_price < np.roll(typical_price, 1), money_flow, 0.0)
    pos_flow[0] = 0.0
    neg_flow[0] = 0.0

    mfi = np.full(len(close), 50.0)
    for i in range(period, len(close)):
        pos_sum = np.sum(pos_flow[i - period + 1 : i + 1])
        neg_sum = np.sum(neg_flow[i - period + 1 : i + 1])
        if neg_sum > 0:
            mfi[i] = 100.0 - 100.0 / (1.0 + pos_sum / neg_sum)
        else:
            mfi[i] = 100.0
    return mfi


def compute_stochastic(df: dict, period: int = 14) -> np.ndarray:
    """Compute Stochastic Oscillator %K.

    Args:
        df: DataFrame-like with 'high', 'low', 'close' columns.
        period: Lookback period.

    Returns:
        Stochastic %K values array (0-100).
    """
    high = np.asarray(df["high"], dtype=float)
    low = np.asarray(df["low"], dtype=float)
    close = np.asarray(df["close"], dtype=float)

    stoch_k = np.full(len(close), 50.0)
    for i in range(period - 1, len(close)):
        lowest = np.min(low[i - period + 1 : i + 1])
        highest = np.max(high[i - period + 1 : i + 1])
        denom = highest - lowest
        if denom > 0:
            stoch_k[i] = 100.0 * (close[i] - lowest) / denom
        else:
            stoch_k[i] = 50.0
    return stoch_k


def compute_obv(df: dict) -> np.ndarray:
    """Compute On-Balance Volume.

    Args:
        df: DataFrame-like with 'close' and 'volume' columns.

    Returns:
        OBV values array.
    """
    close = np.asarray(df["close"], dtype=float)
    volume = np.asarray(df["volume"], dtype=float)

    obv = np.zeros(len(close))
    obv[0] = volume[0]
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            obv[i] = obv[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv[i] = obv[i - 1] - volume[i]
        else:
            obv[i] = obv[i - 1]
    return obv


def compute_vwap(df: dict, period: int = 20) -> np.ndarray:
    """Compute rolling Volume-Weighted Average Price.

    Args:
        df: DataFrame-like with 'high', 'low', 'close', 'volume' columns.
        period: Rolling window length.

    Returns:
        VWAP values array (NaN for first ``period-1`` bars).
    """
    high = np.asarray(df["high"], dtype=float)
    low = np.asarray(df["low"], dtype=float)
    close = np.asarray(df["close"], dtype=float)
    volume = np.asarray(df["volume"], dtype=float)

    typical_price = (high + low + close) / 3.0
    vwap = np.full(len(close), np.nan)
    for i in range(period - 1, len(close)):
        window_slice = slice(i - period + 1, i + 1)
        vwap[i] = np.sum(typical_price[window_slice] * volume[window_slice]) / max(
            np.sum(volume[window_slice]), 1e-10
        )
    return vwap


def compute_htf_macd(
    close: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute Higher Timeframe MACD — alias for compute_macd.

    In live trading this should be fed a downsampled series.
    """
    return compute_macd(close, fast, slow, signal)
