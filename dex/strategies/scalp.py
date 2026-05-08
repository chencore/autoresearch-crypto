"""
High-frequency scalp strategy. Pure mean-reversion, no trend alignment required.

Entry when price deviates from local mean; exit on reversion, fixed take-profit,
fixed stop-loss, or timeout. Targets 50-200 trades per 30 days with small
per-trade profit (0.3-0.8%).
"""

import numpy as np
import pandas as pd

from dex.indicators import compute_rsi
from dex.strategies.base import BaseStrategy


class ScalpStrategy(BaseStrategy):
    """High-frequency mean-reversion scalping strategy.

    Uses tight Bollinger Bands for mean-reversion entries with optional
    RSI, volume, trend-alignment, and session filters. Exits via fixed
    take-profit, stop-loss, or timeout.

    Attributes:
        window: Bollinger Band rolling window.
        std_dev: Number of standard deviations for band width.
        take_profit_pct: Take-profit percentage (decimal).
        stop_loss_pct: Stop-loss percentage (decimal).
        max_hold_bars: Maximum bars to hold before forced exit.
        use_volume_filter: Enable volume ratio filter.
        volume_threshold: Minimum volume ratio for entry.
        rsi_entry_low: RSI oversold threshold for long entry.
        rsi_entry_high: RSI overbought threshold for short entry.
        rsi_extreme_low: RSI extreme low safety threshold.
        rsi_extreme_high: RSI extreme high safety threshold.
        use_rsi_entry: If True, use strict RSI entry thresholds.
        use_trend_align: If True, align trades with long-term trend.
        trend_ma_period: Period for trend-alignment MA.
        use_session_filter: If True, only trade during specified UTC hours.
        session_start: Session start hour (UTC, inclusive).
        session_end: Session end hour (UTC, exclusive).
        rsi_period: RSI lookback period.
    """

    def __init__(
        self,
        window=10,
        std_dev=1.2,
        take_profit_pct=0.005,
        stop_loss_pct=0.003,
        max_hold_bars=6,
        use_volume_filter=False,
        volume_threshold=0.8,
        rsi_entry_low=30,
        rsi_entry_high=70,
        rsi_extreme_low=20,
        rsi_extreme_high=80,
        use_rsi_entry=False,
        use_trend_align=False,
        trend_ma_period=50,
        use_session_filter=False,
        session_start=13,
        session_end=21,
        rsi_period=14,
    ):
        self.window = window
        self.std_dev = std_dev
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_bars = max_hold_bars
        self.use_volume_filter = use_volume_filter
        self.volume_threshold = volume_threshold
        self.rsi_extreme_low = rsi_extreme_low
        self.rsi_extreme_high = rsi_extreme_high
        self.rsi_entry_low = rsi_entry_low
        self.rsi_entry_high = rsi_entry_high
        self.use_rsi_entry = use_rsi_entry
        self.use_trend_align = use_trend_align
        self.trend_ma_period = trend_ma_period
        self.use_session_filter = use_session_filter
        self.session_start = session_start
        self.session_end = session_end
        self.rsi_period = rsi_period

    def generate_signals(self, df, enable_short=False):
        """
        Generate trading signals. 0=close, 1=hold, 2=long, 3=short.

        Entry: price hits tight Bollinger Bands (pure mean-reversion).
        Exit: mean-reversion / fixed take-profit / fixed stop-loss / timeout.
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # Bollinger Bands
        rolling_mean = (
            pd.Series(close).rolling(window=self.window, min_periods=self.window).mean().values
        )
        rolling_std = (
            pd.Series(close).rolling(window=self.window, min_periods=self.window).std().values
        )
        upper = rolling_mean + self.std_dev * rolling_std
        lower = rolling_mean - self.std_dev * rolling_std

        # RSI (waterfall protection)
        rsi = compute_rsi(close, self.rsi_period)

        # Volume ratio
        vol_ratio = None
        if self.use_volume_filter:
            vol = df["volume"].values.astype(float)
            vol_ma = pd.Series(vol).rolling(window=20, min_periods=20).mean().values
            vol_ratio = np.where(vol_ma > 0, vol / vol_ma, 1.0)

        # Trend MA (trend alignment)
        trend_ma = None
        if self.use_trend_align:
            trend_ma = (
                pd.Series(close)
                .rolling(window=self.trend_ma_period, min_periods=self.trend_ma_period)
                .mean()
                .values
            )

        # Session filter (UTC hour)
        hours = None
        if self.use_session_filter and "timestamp" in df.columns:
            timestamps = pd.to_datetime(df["timestamp"], unit="ms")
            hours = timestamps.dt.hour.values

        # Signal generation
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0

        for i in range(self.window, n):
            price = close[i]

            # === Position management ===
            if position == 1:
                bars_held = i - entry_bar
                pnl_pct = (price - entry_price) / entry_price

                # Fixed take-profit
                if pnl_pct >= self.take_profit_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # Fixed stop-loss
                if pnl_pct <= -self.stop_loss_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # Timeout exit
                if bars_held >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue

                signals[i] = 2
                continue

            elif position == -1:
                bars_held = i - entry_bar
                pnl_pct = (entry_price - price) / entry_price

                # Fixed take-profit
                if pnl_pct >= self.take_profit_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # Fixed stop-loss
                if pnl_pct <= -self.stop_loss_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # Timeout exit
                if bars_held >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue

                signals[i] = 3
                continue

            # === No position: look for entry ===
            if np.isnan(lower[i]) or np.isnan(upper[i]):
                continue

            # Session filter
            if self.use_session_filter and hours is not None:
                if hours[i] < self.session_start or hours[i] >= self.session_end:
                    continue

            # Trend alignment: only mean-revert in trend direction
            trend_long_ok = True
            trend_short_ok = True
            if self.use_trend_align and trend_ma is not None and not np.isnan(trend_ma[i]):
                if price > trend_ma[i]:
                    trend_short_ok = False  # Uptrend, no short
                elif price < trend_ma[i]:
                    trend_long_ok = False  # Downtrend, no long
                else:
                    trend_long_ok = False
                    trend_short_ok = False

            # Volume filter
            vol_pass = True
            if self.use_volume_filter and vol_ratio is not None:
                vol_pass = vol_ratio[i] >= self.volume_threshold

            # RSI entry conditions: require oversold/overbought
            rsi_long_ok = (
                rsi[i] < self.rsi_entry_low if self.use_rsi_entry else rsi[i] > self.rsi_extreme_low
            )
            rsi_short_ok = (
                rsi[i] > self.rsi_entry_high
                if self.use_rsi_entry
                else rsi[i] < self.rsi_extreme_high
            )

            # Long: price touches lower band + RSI oversold + uptrend + volume
            if price <= lower[i] and rsi_long_ok and trend_long_ok and vol_pass:
                signals[i] = 2
                position = 1
                entry_price = price
                entry_bar = i
                continue

            # Short: price touches upper band + RSI overbought + downtrend + volume
            if enable_short and price >= upper[i] and rsi_short_ok and trend_short_ok and vol_pass:
                signals[i] = 3
                position = -1
                entry_price = price
                entry_bar = i
                continue

        return signals
