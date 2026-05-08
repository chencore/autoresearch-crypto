"""
Pure price-action strategy. No factor constraints, only price statistical features.

Entry when price touches Bollinger Band extremes (mean-reversion).
Exit via ATR trailing stop, MA reversal, or timeout.
No indicator filters (no ADX, volume, MACD, RSI, MFI, etc.).

trend_ma_period: Trend alignment parameter. Set to None for no trend filter
(pure mean-reversion); set to a value (e.g. 50/100/200) to only trade in the
trend direction.
"""

import numpy as np
import pandas as pd

from dex.indicators import compute_atr, compute_adx
from dex.strategies.base import BaseStrategy


class PureActionStrategy(BaseStrategy):
    """Pure price-action mean-reversion with ATR trailing stop.

    Uses Bollinger Bands for mean-reversion entries with consecutive-bar
    momentum detection. Entry at band extremes, exit via ATR trailing stop,
    MA reversal, or time limit. Supports optional trend-alignment filter
    and ADX-based risk avoidance.

    Attributes:
        window: Bollinger Band and fast MA period.
        std_dev: Bollinger Band standard deviation multiplier.
        atr_period: ATR lookback period.
        atr_multiplier: ATR trailing stop multiplier.
        max_hold_bars: Maximum bars to hold before forced exit.
        entry_zone: Tolerance band around Bollinger Band for entry.
        enable_short: Whether short selling is allowed.
        trend_ma_period: Optional long-term trend MA period or None.
        adx_threshold: Optional ADX threshold for risk avoidance or None.
        adx_period: ADX lookback period.
    """

    def __init__(
        self,
        window=20,
        std_dev=2.0,
        atr_period=14,
        atr_multiplier=2.0,
        max_hold_bars=36,
        entry_zone=0.0,
        enable_short=True,
        trend_ma_period=None,
        adx_threshold=None,
        adx_period=14,
    ):
        self.window = window
        self.std_dev = std_dev
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.entry_zone = entry_zone
        self.enable_short = enable_short
        self.trend_ma_period = trend_ma_period
        self.adx_threshold = adx_threshold
        self.adx_period = adx_period

    def generate_signals(self, df):
        """
        Generate trading signals. 0=close, 1=hold, 2=long, 3=short.
        Pure price action, no factor constraints.
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # --- Bollinger Bands ---
        rolling_mean = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        rolling_std = pd.Series(close).rolling(window=self.window, min_periods=self.window).std()
        upper = rolling_mean + self.std_dev * rolling_std
        lower = rolling_mean - self.std_dev * rolling_std

        # --- MA trend ---
        fast_ma = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        slow_ma = (
            pd.Series(close).rolling(window=self.window * 2, min_periods=self.window * 2).mean()
        )

        # --- ATR ---
        atr = compute_atr(df, self.atr_period)

        # --- Long-term trend MA (trend-alignment filter) ---
        trend_ma = None
        if self.trend_ma_period is not None:
            trend_ma = (
                pd.Series(close)
                .rolling(window=self.trend_ma_period, min_periods=self.trend_ma_period)
                .mean()
                .values
            )

        # --- ADX trend strength (skip entry when trend too strong) ---
        adx = None
        if self.adx_threshold is not None:
            adx, _, _ = compute_adx(df, self.adx_period)

        # --- Consecutive same-direction bars (pure price momentum) ---
        consec_up = np.zeros(n, dtype=int)
        consec_down = np.zeros(n, dtype=int)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                consec_up[i] = consec_up[i - 1] + 1
                consec_down[i] = 0
            elif close[i] < close[i - 1]:
                consec_down[i] = consec_down[i - 1] + 1
                consec_up[i] = 0

        # --- Signal generation ---
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float("inf")

        for i in range(self.window * 2, n):
            price = close[i]

            is_uptrend = fast_ma.iloc[i] > slow_ma.iloc[i]
            is_downtrend = fast_ma.iloc[i] < slow_ma.iloc[i]

            # === Position management ===
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

            # === No position: look for entry ===
            if position == 0:
                if np.isnan(lower.iloc[i]) or np.isnan(upper.iloc[i]):
                    continue

                upper_trigger = upper.iloc[i] - self.entry_zone * rolling_std.iloc[i]
                lower_trigger = lower.iloc[i] + self.entry_zone * rolling_std.iloc[i]

                strong_uptrend = consec_up[i] >= 6
                strong_downtrend = consec_down[i] >= 6

                # Trend-alignment filter: only trade in trend direction
                allow_long = True
                allow_short = self.enable_short
                if self.trend_ma_period is not None and trend_ma is not None:
                    if i >= self.trend_ma_period and not np.isnan(trend_ma[i]):
                        if price > trend_ma[i]:
                            allow_short = False  # Uptrend, no short
                        elif price < trend_ma[i]:
                            allow_long = False  # Downtrend, no long

                # ADX trend-strength filter: avoid entry in strong trend
                if self.adx_threshold is not None and adx is not None:
                    if i >= self.adx_period * 2 and adx[i] > self.adx_threshold:
                        continue  # Trend too strong, skip all entries

                # Long: price hits lower band, not in strong downtrend, trend allows
                if allow_long and price <= lower_trigger and not strong_downtrend:
                    signals[i] = 2
                    position = 1
                    entry_price = price
                    entry_bar = i
                    highest_after_entry = high[i]
                    continue

                # Short: price hits upper band, not in strong uptrend, trend allows
                if allow_short and price >= upper_trigger and not strong_uptrend:
                    signals[i] = 3
                    position = -1
                    entry_price = price
                    entry_bar = i
                    lowest_after_entry = low[i]
                    continue

        return signals
