"""
PureAction V2 — trend-aligned pure price-action with asymmetric entries.

Key improvements over V1:
1. trend_aligned_only: blocks counter-trend trades entirely
2. RSI filter: avoids catching falling knives / shorting melt-ups
3. Asymmetric entry: different aggression for long vs short
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_atr, compute_adx, compute_rsi
from dex.strategies.base import BaseStrategy


class PureActionV2Strategy(BaseStrategy):
    """Trend-aligned Bollinger Band mean-reversion with RSI guard.

    Improvements over PureActionStrategy:
    - ``trend_aligned_only`` blocks counter-trend trades entirely
    - RSI filter avoids entries without momentum confirmation
    - Asymmetric entry zones allow more aggressive shorts in downtrends

    Attributes:
        window: Bollinger Band period.
        std_dev: Band width in standard deviations.
        atr_period: ATR lookback.
        atr_multiplier: ATR trailing stop multiplier.
        max_hold_bars: Maximum position duration.
        entry_zone: Base entry zone (std units inside band).
        short_entry_bonus: Extra zone for shorts (makes short entry easier).
        trend_ma_period: Long MA for trend direction.
        trend_aligned_only: If True, block all counter-trend trades.
        use_rsi_filter: Enable RSI guard on entries.
        rsi_period: RSI lookback.
        rsi_oversold: Max RSI for long entry (only enter if RSI < this).
        rsi_overbought: Min RSI for short entry (only enter if RSI > this).
        adx_threshold: Optional ADX threshold — skip entries above this.
        adx_period: ADX lookback.
    """

    def __init__(
        self,
        window: int = 10,
        std_dev: float = 2.5,
        atr_period: int = 7,
        atr_multiplier: float = 3.0,
        max_hold_bars: int = 12,
        entry_zone: float = 0.0,
        short_entry_bonus: float = 0.0,
        enable_short: bool = True,
        trend_ma_period: int | None = 100,
        trend_aligned_only: bool = False,
        use_rsi_filter: bool = False,
        rsi_period: int = 14,
        rsi_oversold: int = 30,
        rsi_overbought: int = 70,
        adx_threshold: float | None = None,
        adx_period: int = 14,
    ) -> None:
        self.window = window
        self.std_dev = std_dev
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.entry_zone = entry_zone
        self.short_entry_bonus = short_entry_bonus
        self.enable_short = enable_short
        self.trend_ma_period = trend_ma_period
        self.trend_aligned_only = trend_aligned_only
        self.use_rsi_filter = use_rsi_filter
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.adx_threshold = adx_threshold
        self.adx_period = adx_period

    def generate_signals(self, df):
        """Generate trading signals (0=close, 1=hold, 2=long, 3=short)."""
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # Bollinger Bands
        rm = pd.Series(close).rolling(self.window, min_periods=self.window).mean()
        rs = pd.Series(close).rolling(self.window, min_periods=self.window).std()
        upper = rm + self.std_dev * rs
        lower = rm - self.std_dev * rs

        # Trend indicators
        fast_ma = rm  # same window
        slow_ma = pd.Series(close).rolling(self.window * 2, min_periods=self.window * 2).mean()
        atr = compute_atr(df, self.atr_period)

        # Trend MA
        trend_ma = None
        if self.trend_ma_period is not None:
            trend_ma = pd.Series(close).rolling(
                self.trend_ma_period, min_periods=self.trend_ma_period
            ).mean().values

        # ADX
        adx = None
        if self.adx_threshold is not None:
            adx, _, _ = compute_adx(df, self.adx_period)

        # RSI
        rsi = None
        if self.use_rsi_filter:
            rsi = compute_rsi(close, self.rsi_period)

        # Consecutive bars
        consec_up = np.zeros(n, dtype=int)
        consec_down = np.zeros(n, dtype=int)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                consec_up[i] = consec_up[i - 1] + 1
                consec_down[i] = 0
            elif close[i] < close[i - 1]:
                consec_down[i] = consec_down[i - 1] + 1
                consec_up[i] = 0

        signals = np.ones(n, dtype=int)
        position = 0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float("inf")
        min_idx = self.window * 2

        for i in range(min_idx, n):
            price = close[i]

            is_uptrend = fast_ma.iloc[i] > slow_ma.iloc[i]
            is_downtrend = fast_ma.iloc[i] < slow_ma.iloc[i]

            # --- Position management ---
            if position == 1:
                if high[i] > highest_after_entry:
                    highest_after_entry = high[i]
                if highest_after_entry > 0:
                    atr_stop = highest_after_entry - self.atr_multiplier * atr[i]
                    if price < atr_stop:
                        signals[i] = 0; position = 0; continue
                if is_downtrend:
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
                if is_uptrend:
                    signals[i] = 0; position = 0; continue
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0; position = 0; continue
                signals[i] = 3; continue

            # --- Entry signals ---
            if position == 0:
                if np.isnan(lower.iloc[i]) or np.isnan(upper.iloc[i]):
                    continue

                # Asymmetric triggers: shorts get bonus zone
                upper_trigger = upper.iloc[i] - (self.entry_zone + self.short_entry_bonus) * rs.iloc[i]
                lower_trigger = lower.iloc[i] + self.entry_zone * rs.iloc[i]

                strong_up = consec_up[i] >= 6
                strong_down = consec_down[i] >= 6

                # Trend direction
                trend_is_up = True
                trend_is_down = True
                if self.trend_ma_period is not None and trend_ma is not None:
                    if i >= self.trend_ma_period and not np.isnan(trend_ma[i]):
                        trend_is_up = price > trend_ma[i]
                        trend_is_down = price < trend_ma[i]

                # Trend-aligned-only: hard block counter-trend
                allow_long = not (self.trend_aligned_only and trend_is_down)
                allow_short = self.enable_short and not (self.trend_aligned_only and trend_is_up)

                # Trend-aligned (soft): standard filter
                if not self.trend_aligned_only:
                    if self.trend_ma_period is not None and trend_ma is not None:
                        if i >= self.trend_ma_period and not np.isnan(trend_ma[i]):
                            if not trend_is_up:
                                allow_short = False
                            if not trend_is_down:
                                allow_long = False

                # ADX guard
                if self.adx_threshold is not None and adx is not None:
                    if i >= self.adx_period * 2 and adx[i] > self.adx_threshold:
                        continue

                # Long entry
                if allow_long and price <= lower_trigger and not strong_down:
                    # RSI filter: avoid catching falling knives
                    rsi_ok = True
                    if self.use_rsi_filter and rsi is not None:
                        rsi_ok = rsi[i] < self.rsi_oversold
                    if rsi_ok:
                        signals[i] = 2; position = 1; entry_bar = i
                        highest_after_entry = high[i]; continue

                # Short entry
                if allow_short and price >= upper_trigger and not strong_up:
                    rsi_ok = True
                    if self.use_rsi_filter and rsi is not None:
                        rsi_ok = rsi[i] > self.rsi_overbought
                    if rsi_ok:
                        signals[i] = 3; position = -1; entry_bar = i
                        lowest_after_entry = low[i]; continue

        return signals
