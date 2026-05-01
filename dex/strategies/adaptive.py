"""
Market-state adaptive hybrid strategy.

ADX determines market regime:
- Ranging (ADX <= adx_threshold): RSI cross mean-reversion.
- Trending (ADX >  adx_threshold): EMA pullback trend-following.
"""

import numpy as np

from dex.indicators import compute_rsi, compute_adx, compute_atr, compute_ema
from dex.strategies.base import BaseStrategy


class AdaptiveHybridStrategy(BaseStrategy):
    """Adaptive regime-switching strategy using ADX + RSI + EMA.

    Market regime classification via ADX:
      - Ranging (ADX <= threshold): RSI cross, no EMA filter.
      - Trending (ADX > threshold): RSI cross with EMA trend-alignment
        filter.

    Exit via ATR trailing stop, EMA reversal, or time limit.

    Attributes:
        rsi_period: RSI lookback period.
        rsi_low: RSI oversold threshold.
        rsi_high: RSI overbought threshold.
        ma_period: Short EMA period for momentum filter.
        trend_long_ma: Long EMA period for trending regime.
        trend_pull_ma: Pullback EMA period for trending regime.
        adx_period: ADX lookback period.
        adx_threshold: ADX value separating ranging from trending.
        atr_period: ATR lookback period.
        atr_multiplier: ATR trailing stop multiplier.
        max_hold_bars: Maximum bars to hold before forced exit.
        enable_short: Whether short selling is allowed.
    """

    def __init__(self, rsi_period=14, rsi_low=30, rsi_high=70, ma_period=20,
                 trend_long_ma=100, trend_pull_ma=20,
                 adx_period=14, adx_threshold=25,
                 atr_period=14, atr_multiplier=2.0,
                 max_hold_bars=24, enable_short=True):
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.ma_period = ma_period
        self.trend_long_ma = trend_long_ma
        self.trend_pull_ma = trend_pull_ma
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.enable_short = enable_short
        # Compatibility with live trading scripts
        self.window = max(rsi_period, ma_period, atr_period)
        self.std_dev = 2.0

    def generate_signals(self, df):
        """Generate trading signals. 0=close, 1=hold, 2=long, 3=short.

        Market-state adaptive: RSI cross mean-reversion in ranging,
        EMA pullback trend-following in trending.
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # --- RSI (ranging regime) ---
        rsi = compute_rsi(close, self.rsi_period)

        # --- EMA (ranging momentum filter + trending pullback entries) ---
        ema_fast = compute_ema(close, self.ma_period)
        ema_slow = compute_ema(close, self.ma_period * 2)

        # --- Trending regime EMAs ---
        trend_long = compute_ema(close, self.trend_long_ma)
        trend_pull = compute_ema(close, self.trend_pull_ma)

        # --- ADX (market-state classification) ---
        adx, _, _ = compute_adx(df, self.adx_period)

        # --- ATR ---
        atr = compute_atr(df, self.atr_period)

        # --- Signal generation ---
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float('inf')

        min_idx = max(self.rsi_period, self.ma_period, self.adx_period * 2,
                      self.trend_long_ma, self.trend_pull_ma, self.atr_period)

        for i in range(min_idx, n):
            price = close[i]

            is_uptrend = ema_fast[i] > ema_slow[i]
            is_downtrend = ema_fast[i] < ema_slow[i]

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

            # === No position: choose entry mode by market state ===
            if position == 0:
                regime_trending = adx[i] > self.adx_threshold
                prev_rsi = rsi[i - 1]

                # ADX regime classification + RSI cross bidirectional entry
                if regime_trending:
                    # Strong trend: only trade in trend direction, with long EMA filter
                    if is_uptrend:
                        long_cross = (prev_rsi < self.rsi_low and
                                      rsi[i] >= self.rsi_low and
                                      price > trend_long[i])
                        if long_cross:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue
                    elif is_downtrend and self.enable_short:
                        short_cross = (prev_rsi > self.rsi_high and
                                       rsi[i] <= self.rsi_high and
                                       price < trend_long[i])
                        if short_cross:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue
                else:
                    # Ranging: RSI bidirectional mean-reversion (no EMA filter)
                    long_cross = (prev_rsi < self.rsi_low and rsi[i] >= self.rsi_low)
                    if long_cross:
                        signals[i] = 2
                        position = 1
                        entry_price = price
                        entry_bar = i
                        highest_after_entry = high[i]
                        continue

                    if self.enable_short:
                        short_cross = (prev_rsi > self.rsi_high and rsi[i] <= self.rsi_high)
                        if short_cross:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue

        return signals
