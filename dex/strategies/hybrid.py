"""Market-regime adaptive strategy.

ADX <= adx_threshold: ranging market -> Bollinger Band mean reversion (PureAction logic).
ADX >  adx_threshold: trending market -> trend following (MA pullback entry).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.indicators import compute_atr, compute_adx
from dex.strategies.base import BaseStrategy


class HybridStrategy(BaseStrategy):
    """Market-regime adaptive strategy that switches between mean reversion and trend following.

    Uses ADX to classify market state: ranging (ADX <= threshold) triggers
    Bollinger Band mean-reversion entries; trending (ADX > threshold) triggers
    MA pullback entries.  Position management uses ATR trailing stops, MA
    reversal signals, and time-based exits.

    Attributes:
        window: Bollinger Band / fast MA rolling window.
        std_dev: Number of standard deviations for band width.
        atr_period: ATR lookback period.
        atr_multiplier: ATR trailing stop multiplier.
        max_hold_bars: Maximum bars to hold before forced exit.
        entry_zone: Buffer zone inside bands (in std units) for entry trigger.
        enable_short: Whether short selling is allowed.
        trend_ma_period: Long-term MA period for trend alignment.
        adx_threshold: ADX threshold separating ranging from trending markets.
        adx_period: ADX lookback period.
    """

    def __init__(
        self,
        window: int = 20,
        std_dev: float = 2.0,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
        max_hold_bars: int = 24,
        entry_zone: float = 0.0,
        enable_short: bool = True,
        trend_ma_period: int = 100,
        adx_threshold: float = 25,
        adx_period: int = 14,
    ) -> None:
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
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # --- 布林带（震荡模式用）---
        rolling_mean = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        rolling_std = pd.Series(close).rolling(window=self.window, min_periods=self.window).std()
        upper = rolling_mean + self.std_dev * rolling_std
        lower = rolling_mean - self.std_dev * rolling_std

        # --- 均线 ---
        fast_ma = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        slow_ma = pd.Series(close).rolling(window=self.window * 2, min_periods=self.window * 2).mean()

        # --- ATR ---
        atr = compute_atr(df, self.atr_period)

        # --- 趋势MA ---
        trend_ma = pd.Series(close).rolling(window=self.trend_ma_period, min_periods=self.trend_ma_period).mean().values

        # --- ADX（市场状态判定）---
        adx, _, _ = compute_adx(df, self.adx_period)

        # --- 连续同向K线 ---
        consec_up = np.zeros(n, dtype=int)
        consec_down = np.zeros(n, dtype=int)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                consec_up[i] = consec_up[i - 1] + 1
                consec_down[i] = 0
            elif close[i] < close[i - 1]:
                consec_down[i] = consec_down[i - 1] + 1
                consec_up[i] = 0

        # --- 信号生成 ---
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float('inf')

        min_idx = max(self.window * 2, self.trend_ma_period, self.adx_period * 2)

        for i in range(min_idx, n):
            price = close[i]

            is_uptrend = fast_ma.iloc[i] > slow_ma.iloc[i]
            is_downtrend = fast_ma.iloc[i] < slow_ma.iloc[i]

            # === 持仓管理（震荡/趋势通用） ===
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
                if lowest_after_entry < float('inf'):
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

            # === 空仓：根据市场状态选择入场模式 ===
            if position == 0:
                regime_trending = adx[i] > self.adx_threshold

                if regime_trending:
                    # ========== 趋势市：趋势跟随 ==========
                    price_above_trend = price > trend_ma[i]
                    price_below_trend = price < trend_ma[i]
                    prev_close = close[i - 1]
                    prev_fast_ma = fast_ma.iloc[i - 1]

                    # 做多：上升趋势中，价格从上往下穿越快线（回调入场）
                    long_cross = (price_above_trend and
                                  prev_close > prev_fast_ma and
                                  price <= fast_ma.iloc[i] and
                                  not np.isnan(fast_ma.iloc[i]) and
                                  not np.isnan(prev_fast_ma))
                    if long_cross:
                        signals[i] = 2
                        position = 1
                        entry_price = price
                        entry_bar = i
                        highest_after_entry = high[i]
                        continue

                    # 做空：下降趋势中，价格从下往上穿越快线（反弹入场）
                    if self.enable_short:
                        short_cross = (price_below_trend and
                                       prev_close < prev_fast_ma and
                                       price >= fast_ma.iloc[i] and
                                       not np.isnan(fast_ma.iloc[i]) and
                                       not np.isnan(prev_fast_ma))
                        if short_cross:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue

                else:
                    # ========== 震荡市：均值回归 ==========
                    if np.isnan(lower.iloc[i]) or np.isnan(upper.iloc[i]):
                        continue

                    upper_trigger = upper.iloc[i] - self.entry_zone * rolling_std.iloc[i]
                    lower_trigger = lower.iloc[i] + self.entry_zone * rolling_std.iloc[i]

                    strong_uptrend = consec_up[i] >= 6
                    strong_downtrend = consec_down[i] >= 6

                    # 趋势对齐
                    allow_long = True
                    allow_short = self.enable_short
                    if i >= self.trend_ma_period and not np.isnan(trend_ma[i]):
                        if price > trend_ma[i]:
                            allow_short = False
                        elif price < trend_ma[i]:
                            allow_long = False

                    # 做多
                    if allow_long and price <= lower_trigger and not strong_downtrend:
                        signals[i] = 2
                        position = 1
                        entry_price = price
                        entry_bar = i
                        highest_after_entry = high[i]
                        continue

                    # 做空
                    if allow_short and price >= upper_trigger and not strong_uptrend:
                        signals[i] = 3
                        position = -1
                        entry_price = price
                        entry_bar = i
                        lowest_after_entry = low[i]
                        continue

        return signals
