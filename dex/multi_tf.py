"""
Multi-timeframe trend analysis.

Resamples 5-minute OHLCV data to higher timeframes (1h, 4h, 1d)
and computes consensus trend signals for entry filtering.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from dex.indicators import compute_ema


# Bars per timeframe (based on 5m base)
TF_BARS: Dict[str, int] = {
    "15m": 3,
    "1h": 12,
    "4h": 48,
    "1d": 288,
}


def resample_ohlcv(df: pd.DataFrame, tf_bars: int) -> pd.DataFrame:
    """Resample 5-minute OHLCV to a higher timeframe.

    Args:
        df: 5-minute DataFrame with OHLCV columns.
        tf_bars: Number of 5m bars per higher-TF bar.

    Returns:
        Resampled OHLCV DataFrame.
    """
    if tf_bars <= 1:
        return df.copy()

    n = len(df)
    n_htf = n // tf_bars
    if n_htf < 10:
        return df.copy()

    records = []
    for i in range(n_htf):
        start = i * tf_bars
        end = min(start + tf_bars, n)
        window = df.iloc[start:end]
        records.append({
            "open": window["open"].iloc[0],
            "high": window["high"].max(),
            "low": window["low"].min(),
            "close": window["close"].iloc[-1],
            "volume": window["volume"].sum(),
        })

    result = pd.DataFrame(records)
    # Map back to original index (last bar of each window)
    result.index = [min((i + 1) * tf_bars - 1, n - 1) for i in range(n_htf)]
    return result


class MultiTimeframeTrend:
    """Multi-timeframe trend consensus detector.

    Computes EMA trend direction on multiple timeframes and returns
    a consensus: 'bullish' (all aligned up), 'bearish' (all aligned down),
    or 'neutral' (mixed / ranging).

    Attributes:
        timeframes: List of timeframe labels (e.g. ['1h', '4h', '1d']).
        ma_period: EMA period applied on each TF.
        min_consensus: Minimum fraction of TFs that must agree.
    """

    def __init__(
        self,
        timeframes: Tuple[str, ...] = ("1h", "4h", "1d"),
        ma_period: int = 20,
        min_consensus: float = 0.67,
    ):
        self.timeframes = timeframes
        self.ma_period = ma_period
        self.min_consensus = min_consensus
        self._cache: Dict[str, np.ndarray] = {}

    def compute(self, df: pd.DataFrame) -> np.ndarray:
        """Compute multi-TF trend signal for every 5m bar.

        Returns:
            Array of same length as df:
            1 = bullish consensus, -1 = bearish consensus, 0 = neutral.
        """
        n = len(df)
        trend = np.zeros(n, dtype=int)

        for tf_name in self.timeframes:
            tf_bars = TF_BARS.get(tf_name, 12)
            htf = resample_ohlcv(df, tf_bars)

            # Compute EMA on HTF close
            close_htf = htf["close"].values.astype(float)
            ema_htf = compute_ema(close_htf, self.ma_period)

            # Map HTF signal back to 5m resolution
            htf_signal_5m = np.zeros(n, dtype=int)
            for i in range(len(htf)):
                bar_idx = htf.index[i]
                if i >= self.ma_period:
                    # Compare close to EMA for trend direction
                    if close_htf[i] > ema_htf[i]:
                        htf_signal_5m[bar_idx:] = 1
                    else:
                        htf_signal_5m[bar_idx:] = -1

            self._cache[tf_name] = htf_signal_5m

        # Consensus: sum of signals across TFs
        for i in range(n):
            votes = [self._cache[tf][i] for tf in self.timeframes]
            score = sum(votes)
            n_tf = len(votes)
            if score >= n_tf * self.min_consensus:
                trend[i] = 1
            elif score <= -n_tf * self.min_consensus:
                trend[i] = -1
            # else 0 (neutral / mixed)

        return trend

    def describe(self, df: pd.DataFrame) -> str:
        """Human-readable summary of current multi-TF state."""
        trend = self.compute(df)
        last = trend[-1]

        details = []
        for tf_name in self.timeframes:
            if tf_name in self._cache:
                details.append(
                    f"{tf_name}={self._cache[tf_name][-1]:+d}"
                )

        label = {1: "BULLISH", -1: "BEARISH", 0: "NEUTRAL"}[last]
        return f"MultiTF: {label} ({', '.join(details)})"


def multi_tf_filter(
    df: pd.DataFrame,
    timeframes: Tuple[str, ...] = ("1h", "4h", "1d"),
    ma_period: int = 20,
) -> Tuple[np.ndarray, MultiTimeframeTrend]:
    """Convenience: compute multi-TF trend and return (array, detector).

    Args:
        df: 5-minute OHLCV DataFrame.
        timeframes: Higher timeframes to analyse.
        ma_period: EMA period on each TF.

    Returns:
        (trend_array, detector) — trend is 1/-1/0 per bar.
    """
    detector = MultiTimeframeTrend(timeframes, ma_period)
    trend = detector.compute(df)
    return trend, detector
