"""Mixed mean-reversion + momentum strategy.

Core logic (Q-RSI concept):
1. Mean reversion: RSI enters extreme zones (<25 oversold, >75 overbought)
2. Momentum filter: price remains on the correct side of the short-term EMA
   - Long:  RSI oversold BUT price > EMA (trend intact, not free-fall)
   - Short: RSI overbought BUT price < EMA (trend reversed, not parabolic)
3. Exit: ATR trailing stop + MA reversal + time-out
"""

from __future__ import annotations

import numpy as np

from dex.indicators import compute_rsi, compute_atr, compute_ema
from dex.strategies.base import BaseStrategy


class HybridMeanRevMomentumStrategy(BaseStrategy):
    """Mixed mean-reversion and momentum strategy using RSI crossovers with EMA filter.

    Long entries occur when RSI crosses back above the oversold threshold
    while price is above the fast EMA (trend confirmation).  Short entries
    occur when RSI crosses below the overbought threshold while price is
    below the fast EMA.

    Attributes:
        rsi_period: RSI lookback period.
        rsi_low: Oversold threshold (RSI below this triggers long watch).
        rsi_high: Overbought threshold (RSI above this triggers short watch).
        ma_period: Short-term EMA period for momentum filter.
        atr_period: ATR lookback period.
        atr_multiplier: ATR trailing stop multiplier.
        max_hold_bars: Maximum bars to hold before forced exit.
        enable_short: Whether short selling is allowed.
        take_profit_pct: Take-profit percentage for live trading.
        stop_loss_pct: Stop-loss percentage for live trading.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_low: int = 25,
        rsi_high: int = 75,
        ma_period: int = 20,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
        max_hold_bars: int = 24,
        enable_short: bool = True,
        take_profit_pct: float = 0.03,
        stop_loss_pct: float = 0.02,
    ) -> None:
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.ma_period = ma_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.enable_short = enable_short
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        # 兼容实盘脚本所需的属性
        self.window = max(rsi_period, ma_period, atr_period)
        self.std_dev = 2.0

    def generate_signals(self, df, enable_short=False):
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # --- RSI ---
        rsi = compute_rsi(close, self.rsi_period)

        # --- EMA（动量过滤用）---
        ema_fast = compute_ema(close, self.ma_period)
        ema_slow = compute_ema(close, self.ma_period * 2)

        # --- ATR ---
        atr = compute_atr(df, self.atr_period)

        # --- 信号生成 ---
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float("inf")

        min_idx = max(self.rsi_period, self.ma_period, self.atr_period)

        for i in range(min_idx, n):
            price = close[i]

            is_uptrend = ema_fast[i] > ema_slow[i]
            is_downtrend = ema_fast[i] < ema_slow[i]

            # === 持仓管理 ===
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

            # === 空仓：混合信号入场（RSI 交叉 + EMA 动量过滤）===
            if position == 0:
                prev_rsi = rsi[i - 1]

                # 做多：RSI 从超卖区回升 + 价格在短期均线上方（趋势确认）
                long_cross = (
                    prev_rsi < self.rsi_low and rsi[i] >= self.rsi_low and price > ema_fast[i]
                )
                if long_cross:
                    signals[i] = 2
                    position = 1
                    entry_price = price
                    entry_bar = i
                    highest_after_entry = high[i]
                    continue

                # 做空：RSI 从超买区回落 + 价格跌破短期均线（趋势确认）
                short_cross = (
                    self.enable_short
                    and prev_rsi > self.rsi_high
                    and rsi[i] <= self.rsi_high
                    and price < ema_fast[i]
                )
                if short_cross:
                    signals[i] = 3
                    position = -1
                    entry_price = price
                    entry_bar = i
                    lowest_after_entry = low[i]
                    continue

        return signals
