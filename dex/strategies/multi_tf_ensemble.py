"""
Multi-timeframe nested voting ensemble.

Each timeframe has a distinct role:
  - 1d  → direction (long-only / short-only)
  - 4h  → position sizing (full / half)
  - 1h  → entry timing
  - 15m → exit / reversal signals

The ensemble computes independent signals per TF, then aggregates
via weighted voting.  Agents optimise the weights, vote threshold,
and per-TF parameters.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_ema, compute_rsi, compute_atr
from dex.strategies.base import BaseStrategy


# ---------------------------------------------------------------------------
# Per-timeframe signal generator
# ---------------------------------------------------------------------------

def _tf_signal(
    df: pd.DataFrame,
    ma_fast: int = 10,
    ma_slow: int = 30,
    rsi_period: int = 14,
    rsi_oversold: int = 30,
    rsi_overbought: int = 70,
) -> np.ndarray:
    """Generate discrete signals for a single timeframe.

    Returns:
        Array: 1=bullish, -1=bearish, 0=neutral.
    """
    close = df["close"].values.astype(float)
    n = len(close)

    ema_f = compute_ema(close, ma_fast)
    ema_s = compute_ema(close, ma_slow)
    rsi = compute_rsi(close, rsi_period)

    signals = np.zeros(n, dtype=int)
    for i in range(max(ma_slow, rsi_period), n):
        # Trend direction
        trend_up = ema_f[i] > ema_s[i]
        trend_down = ema_f[i] < ema_s[i]

        # RSI extremes
        oversold = rsi[i] < rsi_oversold
        overbought = rsi[i] > rsi_overbought

        if trend_up and oversold:
            signals[i] = 1  # bullish reversal
        elif trend_down and overbought:
            signals[i] = -1  # bearish reversal
        elif trend_up:
            signals[i] = 1
        elif trend_down:
            signals[i] = -1
        # else 0 = ranging / no clear signal

    return signals


# ---------------------------------------------------------------------------
# Multi-TF Ensemble Strategy
# ---------------------------------------------------------------------------

class MultiTFEnsembleStrategy(BaseStrategy):
    """Nested multi-timeframe voting ensemble.

    Each timeframe (1d, 4h, 1h, 15m) generates independent signals.
    The ensemble aggregates via weighted voting to produce a final
    entry decision.

    Design:
    - 1d  → direction gate (long-only / short-only)
    - 4h  → position sizing (full if aligned, half if divergent)
    - 1h  → entry timing
    - 15m → exit trigger

    Attributes:
        tf_weights: Per-timeframe voting weight.
        vote_threshold: Minimum weighted score to enter.
        tf_params: Per-timeframe (ma_fast, ma_slow, rsi_period,
                   rsi_oversold, rsi_overbought).
        atr_period: ATR period for stops.
        atr_multiplier: ATR trailing stop multiplier.
        max_hold_bars: Maximum position duration.
        enable_short: Whether short selling is allowed.
    """

    # Maps timeframe labels to 5m bar counts
    TF_BARS = {"15m": 3, "1h": 12, "4h": 48, "1d": 288}

    def __init__(
        self,
        tf_weights: dict | None = None,
        vote_threshold: float = 0.50,
        tf_params: dict | None = None,
        atr_period: int = 14,
        atr_multiplier: float = 2.5,
        max_hold_bars: int = 36,
        enable_short: bool = True,
    ) -> None:
        self.tf_weights = tf_weights or {
            "1d": 0.4, "4h": 0.3, "1h": 0.2, "15m": 0.1,
        }
        self.vote_threshold = vote_threshold
        self.tf_params = tf_params or {
            "1d":  {"ma_fast": 10, "ma_slow": 30, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70},
            "4h":  {"ma_fast": 10, "ma_slow": 30, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70},
            "1h":  {"ma_fast": 10, "ma_slow": 30, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70},
            "15m": {"ma_fast": 5,  "ma_slow": 20, "rsi_period": 7,  "rsi_oversold": 25, "rsi_overbought": 75},
        }
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.enable_short = enable_short
        # Compatibility
        self.window = max(50, atr_period)
        self.std_dev = 2.0
        self._cache: dict = {}

    def _resample(self, df: pd.DataFrame, tf_name: str) -> pd.DataFrame:
        """Resample 5m data to a higher timeframe."""
        if tf_name in self._cache:
            return self._cache[tf_name]

        tf_bars = self.TF_BARS.get(tf_name, 1)
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
            w = df.iloc[start:end]
            records.append({
                "open": w["open"].iloc[0],
                "high": w["high"].max(),
                "low": w["low"].min(),
                "close": w["close"].iloc[-1],
                "volume": w["volume"].sum(),
            })
        result = pd.DataFrame(records)
        result.index = [min((i + 1) * tf_bars - 1, n - 1) for i in range(n_htf)]
        self._cache[tf_name] = result
        return result

    def _compute_tf_votes(self, df: pd.DataFrame) -> dict:
        """Compute signals for all timeframes, mapped to 5m resolution."""
        n = len(df)
        votes = {}

        for tf_name in self.tf_weights:
            tf_bars = self.TF_BARS.get(tf_name, 1)
            htf = self._resample(df, tf_name)
            htf_sig = _tf_signal(htf, **self.tf_params.get(tf_name, {}))

            # Map back to 5m resolution
            sig_5m = np.zeros(n, dtype=int)
            for i in range(len(htf)):
                bar_idx = htf.index[i]
                sig_5m[bar_idx:] = htf_sig[i]
            votes[tf_name] = sig_5m

        return votes

    def generate_signals(self, df) -> np.ndarray:
        """Generate ensemble trading signals.

        Returns:
            0=close, 1=hold, 2=long, 3=short.
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        self._cache = {}
        tf_votes = self._compute_tf_votes(df)
        atr = compute_atr(df, self.atr_period)

        signals = np.ones(n, dtype=int)
        position = 0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float("inf")

        min_idx = max(288, self.atr_period)  # start after 1d of data

        for i in range(min_idx, n):
            price = close[i]

            # --- Position management ---
            if position == 1:
                if high[i] > highest_after_entry:
                    highest_after_entry = high[i]
                if highest_after_entry > 0:
                    atr_stop = highest_after_entry - self.atr_multiplier * atr[i]
                    if price < atr_stop:
                        signals[i] = 0; position = 0; continue
                # 15m TF exit: if 15m goes bearish, exit long
                if tf_votes.get("15m", np.zeros(n))[i] < 0:
                    signals[i] = 0; position = 0; continue
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0; position = 0; continue
                signals[i] = 2; continue

            elif position == -1:
                if low[i] < lowest_after_entry:
                    lowest_after_entry = low[i]
                if lowest_after_entry < float("inf"):
                    atr_stop = lowest_after_entry + self.atr_multiplier * atr[i]
                    if price > atr_stop:
                        signals[i] = 0; position = 0; continue
                if tf_votes.get("15m", np.zeros(n))[i] > 0:
                    signals[i] = 0; position = 0; continue
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0; position = 0; continue
                signals[i] = 3; continue

            # --- Entry: weighted voting ---
            if position == 0:
                weighted_score = 0.0
                for tf_name, weight in self.tf_weights.items():
                    vote = tf_votes.get(tf_name, np.zeros(n))[i]
                    weighted_score += vote * weight

                # 1d direction gate
                day_vote = tf_votes.get("1d", np.zeros(n))[i]

                # Entry decision
                if weighted_score >= self.vote_threshold and day_vote >= 0:
                    signals[i] = 2; position = 1; entry_bar = i
                    highest_after_entry = high[i]; continue
                elif (self.enable_short and weighted_score <= -self.vote_threshold
                      and day_vote <= 0):
                    signals[i] = 3; position = -1; entry_bar = i
                    lowest_after_entry = low[i]; continue

        return signals

    def describe(self) -> str:
        """Human-readable config summary."""
        w = self.tf_weights
        return (
            f"MultiTF(w:1d={w['1d']:.1f}/4h={w['4h']:.1f}/"
            f"1h={w['1h']:.1f}/15m={w['15m']:.1f} thr={self.vote_threshold:.2f})"
        )
