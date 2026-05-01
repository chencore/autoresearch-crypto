"""
Grid / market-making strategy for perpetual futures.

Places virtual buy orders below current price and sell orders above,
simulating a grid-trading bot without actual order management.
Each filled level creates a counter-order at the opposite side.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.strategies.base import BaseStrategy


class GridStrategy(BaseStrategy):
    """Mean-reversion grid-trading strategy.

    Maintains a virtual grid of buy and sell levels around a moving
    mid-price.  When price crosses a level the position is adjusted
    and a take-profit order is placed on the opposite side.

    Designed for ranging / low-volatility markets where price
    oscillates within a bounded channel.

    Attributes:
        grid_spacing_pct: Distance between grid levels as fraction of price.
        grid_levels: Number of levels on each side of the mid-price.
        base_size: Position size per grid level (fraction of capital).
        atr_period: ATR period for dynamic grid spacing.
        atr_spacing_mult: If > 0, grid spacing = atr * mult instead of fixed pct.
        max_position: Maximum total exposure (fraction of capital).
        trend_ma_period: Long MA for trend filter — disable grid in strong trends.
    """

    def __init__(
        self,
        grid_spacing_pct: float = 0.005,
        grid_levels: int = 5,
        base_size: float = 0.1,
        atr_period: int = 14,
        atr_spacing_mult: float = 0.5,
        max_position: float = 1.0,
        trend_ma_period: int = 100,
    ) -> None:
        self.grid_spacing_pct = grid_spacing_pct
        self.grid_levels = grid_levels
        self.base_size = base_size
        self.atr_period = atr_period
        self.atr_spacing_mult = atr_spacing_mult
        self.max_position = max_position
        self.trend_ma_period = trend_ma_period
        # Compatibility
        self.window = max(atr_period, trend_ma_period)
        self.std_dev = 2.0

    def generate_signals(self, df, enable_short: bool = False) -> np.ndarray:
        """Generate grid-trading signals.

        Returns:
            Array of position targets: positive = net long size,
            negative = net short size, 0 = flat.  The caller should
            interpret these as desired position adjustments.
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # Compute ATR for dynamic spacing
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1)),
            ),
        )
        tr[0] = high[0] - low[0]
        atr = np.zeros(n)
        atr[self.atr_period - 1] = np.mean(tr[: self.atr_period])
        for i in range(self.atr_period, n):
            atr[i] = (atr[i - 1] * (self.atr_period - 1) + tr[i]) / self.atr_period

        # Trend filter
        trend_ma = pd.Series(close).rolling(
            window=self.trend_ma_period, min_periods=self.trend_ma_period
        ).mean().values

        # Grid state
        base_price = close[0]
        position = 0.0  # current net position (fraction of capital)
        signals = np.zeros(n, dtype=float)

        for i in range(1, n):
            price = close[i]

            # Determine grid spacing
            spacing = self.grid_spacing_pct
            if self.atr_spacing_mult > 0 and atr[i] > 0:
                spacing = max(spacing, (self.atr_spacing_mult * atr[i]) / price)

            # Trend guard — hold position in strong trends, don't add
            in_strong_trend = False
            if not np.isnan(trend_ma[i]):
                deviation = abs(price - trend_ma[i]) / trend_ma[i]
                in_strong_trend = deviation > 0.05  # 5% away from trend MA

            # Rebalance grid around mid-price on significant moves
            if abs(price - base_price) / base_price > spacing * self.grid_levels:
                base_price = price

            # Compute net desired position
            desired_position = 0.0
            for level in range(1, self.grid_levels + 1):
                buy_price = base_price * (1 - spacing * level)
                sell_price = base_price * (1 + spacing * level)

                if price <= buy_price and not in_strong_trend:
                    desired_position += self.base_size
                elif price >= sell_price:
                    desired_position -= self.base_size

            desired_position = np.clip(desired_position, -self.max_position, self.max_position)
            signals[i] = desired_position

        return signals


def grid_signals_to_discrete(
    position_targets: np.ndarray,
    close: np.ndarray,
    threshold: float = 0.05,
) -> np.ndarray:
    """Convert continuous grid position targets to discrete 0/1/2/3 signals.

    Args:
        position_targets: Array from GridStrategy.generate_signals.
        close: Close price array.
        threshold: Minimum position change to trigger a signal.

    Returns:
        Integer signal array: 0=close, 1=hold, 2=long, 3=short.
    """
    n = len(position_targets)
    signals = np.ones(n, dtype=int)
    current_pos = 0.0

    for i in range(1, n):
        target = position_targets[i]
        delta = target - current_pos

        if abs(delta) < threshold:
            # Hold current
            if current_pos > threshold:
                signals[i] = 2
            elif current_pos < -threshold:
                signals[i] = 3
            else:
                signals[i] = 1
        elif delta > 0:
            signals[i] = 2
            current_pos = target
        elif delta < 0:
            if target <= -threshold:
                signals[i] = 3
                current_pos = target
            else:
                signals[i] = 0
                current_pos = 0

    return signals
