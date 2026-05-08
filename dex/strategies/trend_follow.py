"""Pure trend-following strategy.

Core logic: long-period EMA determines trend direction, short-period EMA
provides pullback entry signals.  Only trades with the trend: long in uptrends,
short in downtrends.  Exits via ATR trailing stop, MA reversal, or time-out.
"""

from __future__ import annotations

import numpy as np

from dex.indicators import compute_atr, compute_ema
from dex.strategies.base import BaseStrategy


class TrendFollowStrategy(BaseStrategy):
    """EMA trend-following with pullback entries.

    Long:  price > long_ma AND price pulls back to ``pull_ma``.
    Short: price < long_ma AND price bounces to ``pull_ma``.
    Exit via ATR trailing stop, MA reversal, or time limit.

    Attributes:
        long_ma_period: Long EMA for trend direction.
        pull_ma_period: Short EMA for pullback entry.
        atr_period: ATR lookback period.
        atr_multiplier: ATR trailing stop multiplier.
        max_hold_bars: Maximum bars to hold before forced exit.
        entry_zone: Tolerance band around pull_ma for entry trigger.
        enable_short: Whether short selling is allowed.
        volume_threshold: Optional minimum volume ratio filter.
    """

    def __init__(
        self,
        long_ma_period: int = 100,
        pull_ma_period: int = 20,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
        max_hold_bars: int = 24,
        entry_zone: float = 0.002,
        enable_short: bool = True,
        volume_threshold: float | None = None,
    ) -> None:
        self.long_ma_period = long_ma_period
        self.pull_ma_period = pull_ma_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.entry_zone = entry_zone
        self.enable_short = enable_short
        self.volume_threshold = volume_threshold

    def generate_signals(self, df):
        """Generate trading signals (0=close, 1=hold, 2=long, 3=short)."""
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume_ratio = df["volume_ratio"].values if "volume_ratio" in df.columns else None
        n = len(close)

        long_ma = compute_ema(close, self.long_ma_period)
        pull_ma = compute_ema(close, self.pull_ma_period)
        mid_ma = compute_ema(close, self.pull_ma_period * 2)
        atr = compute_atr(df, self.atr_period)

        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float("inf")

        min_idx = max(self.long_ma_period, self.pull_ma_period, self.atr_period)

        for i in range(min_idx, n):
            price = close[i]

            is_uptrend = pull_ma[i] > mid_ma[i]
            is_downtrend = pull_ma[i] < mid_ma[i]

            # --- Position management: Long ---
            if position == 1:
                if high[i] > highest_after_entry:
                    highest_after_entry = high[i]
                if highest_after_entry > 0:
                    atr_stop = highest_after_entry - self.atr_multiplier * atr[i]
                    if price < atr_stop:
                        signals[i] = 0
                        position = 0
                        continue
                if is_downtrend:
                    signals[i] = 0
                    position = 0
                    continue
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue
                signals[i] = 2
                continue

            # --- Position management: Short ---
            elif position == -1:
                if low[i] < lowest_after_entry:
                    lowest_after_entry = low[i]
                if lowest_after_entry < float("inf"):
                    atr_stop = lowest_after_entry + self.atr_multiplier * atr[i]
                    if price > atr_stop:
                        signals[i] = 0
                        position = 0
                        continue
                if is_uptrend:
                    signals[i] = 0
                    position = 0
                    continue
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue
                signals[i] = 3
                continue

            # --- Entry signals ---
            if position == 0:
                if self.volume_threshold is not None and volume_ratio is not None:
                    if volume_ratio[i] < self.volume_threshold:
                        continue

                price_above_long = price > long_ma[i]
                price_below_long = price < long_ma[i]

                # Long: uptrend pullback to short EMA
                long_pullback = (
                    price_above_long
                    and price <= pull_ma[i] * (1 + self.entry_zone)
                    and not np.isnan(pull_ma[i])
                )
                if long_pullback:
                    signals[i] = 2
                    position = 1
                    entry_price = price
                    entry_bar = i
                    highest_after_entry = high[i]
                    continue

                # Short: downtrend bounce to short EMA
                if self.enable_short:
                    short_bounce = (
                        price_below_long
                        and price >= pull_ma[i] * (1 - self.entry_zone)
                        and not np.isnan(pull_ma[i])
                    )
                    if short_bounce:
                        signals[i] = 3
                        position = -1
                        entry_price = price
                        entry_bar = i
                        lowest_after_entry = low[i]
                        continue

        return signals
