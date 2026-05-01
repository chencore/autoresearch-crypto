from __future__ import annotations

"""
Trend strategy: moving-average cross trend-following with Bollinger Band
mean-reversion entries and multi-factor confirmation.

Core logic:
    1. Fast MA > Slow MA = uptrend (long bias); Fast MA < Slow MA = downtrend (short bias)
    2. ADX / MACD / RSI multi-indicator filtering confirms trend strength
    3. Entry at Bollinger Band extremes with optional resonance scoring (3+ factors)
    4. ATR trailing stop + MA reversal stop + time-based exit

The strategy supports 7 tiers of optional confirmation factors:
    - P0: ADX trend filter + volume confirmation
    - P1: MACD momentum confirmation + MA cross event
    - P2: MFI money-flow index + stochastic oscillator
    - P3: RSI / MACD divergence signals
    - P4: Dynamic long/short trend filter
    - P5: OBV, volume spike, VWAP volume-based factors
    - P6: Higher-timeframe MACD trend alignment
    - P7: Multi-factor resonance scoring (requires 3+ aligned factors)

Example:
    >>> from dex.strategies.trend import TrendStrategy
    >>> strategy = TrendStrategy(window=20, use_adx=True, use_macd=True)
    >>> signals = strategy.generate_signals(df, enable_short=True)
"""

import numpy as np
import pandas as pd

from dex.indicators import (
    compute_adx,
    compute_atr,
    compute_ema,
    compute_htf_macd,
    compute_macd,
    compute_mfi,
    compute_obv,
    compute_rsi,
    compute_stochastic,
    compute_vwap,
)
from dex.strategies.base import BaseStrategy


class TrendStrategy(BaseStrategy):
    """Bollinger Band mean-reversion strategy with multi-factor trend confirmation.

    Enters long when price touches the lower Bollinger Band and exits on
    ATR trailing stop, MA reversal, or time expiry.  Supports short entries
    via the ``enable_short`` parameter and offers 7 optional confirmation
    tiers (P0--P7) that can be toggled independently.

    Attributes:
        window: Fast MA / Bollinger Band lookback period.
        std_dev: Number of standard deviations for Bollinger Bands.
        atr_period: ATR lookback period for trailing stop.
        atr_multiplier: ATR multiplier for stop distance.
        max_hold_bars: Maximum bars to hold a position before time exit.
        adx_threshold: Minimum ADX value for trend confirmation.
        entry_zone: Band offset multiplier (tightens trigger relative to band edge).
        rsi_threshold: RSI oversold / overbought threshold (e.g. 30 means RSI < 30 = oversold).
        take_profit_pct: Take-profit percentage (reserved, not used in default logic).
        stop_loss_pct: Stop-loss percentage (reserved, not used in default logic).
        use_adx: Enable ADX trend-strength filter.
        use_volume: Enable volume confirmation (volume > MA * threshold).
        volume_threshold: Minimum volume ratio for volume confirmation.
        use_macd: Enable MACD momentum confirmation.
        macd_confirm_mode: MACD confirmation mode ("direction", "histogram", or "both").
        use_ma_cross: Enable MA cross event filter.
        use_mfi: Enable Money Flow Index filter.
        mfi_period: MFI lookback period.
        mfi_threshold: MFI oversold threshold (mirrors RSI threshold).
        use_stochastic: Enable stochastic oscillator filter.
        stoch_period: Stochastic lookback period.
        stoch_threshold: Stochastic oversold threshold.
        use_rsi_divergence: Enable RSI divergence detection.
        rsi_divergence_lookback: Lookback window for RSI divergence.
        use_macd_divergence: Enable MACD histogram divergence detection.
        macd_divergence_lookback: Lookback window for MACD divergence.
        use_trend_filter: Enable dynamic trend-direction filter.
        trend_window: Fast MA window for trend-direction filter.
        use_obv_trend: Enable OBV trend confirmation.
        obv_ma_period: OBV moving-average period.
        use_volume_spike: Enable volume-spike filter.
        volume_spike_threshold: Minimum volume ratio for spike detection.
        use_vwap: Enable VWAP price-level filter.
        vwap_period: VWAP rolling window.
        use_htf_macd: Enable higher-timeframe MACD trend filter.
        htf_macd_fast: HTF MACD fast EMA period.
        htf_macd_slow: HTF MACD slow EMA period.
        htf_macd_signal: HTF MACD signal line period.
        use_resonance: Enable multi-factor resonance scoring (P7).
        resonance_min_score: Minimum number of aligned factors required.
    """

    def __init__(
        self,
        window: int = 20,
        std_dev: float = 2.0,
        atr_period: int = 14,
        atr_multiplier: float = 2.5,
        max_hold_bars: int = 48,
        adx_threshold: float = 25,
        entry_zone: float = 1.0,
        rsi_threshold: float = 30,
        take_profit_pct: float = 0.05,
        stop_loss_pct: float = 0.03,
        # P0: ADX trend filter + volume confirmation
        use_adx: bool = False,
        use_volume: bool = False,
        volume_threshold: float = 1.2,
        # P1: MACD momentum confirmation + MA cross event
        use_macd: bool = False,
        macd_confirm_mode: str = "direction",
        use_ma_cross: bool = False,
        # P2: MFI money-flow index + stochastic
        use_mfi: bool = False,
        mfi_period: int = 14,
        mfi_threshold: float = 20,
        use_stochastic: bool = False,
        stoch_period: int = 14,
        stoch_threshold: float = 20,
        # P3: Divergence signals
        use_rsi_divergence: bool = False,
        rsi_divergence_lookback: int = 5,
        use_macd_divergence: bool = False,
        macd_divergence_lookback: int = 5,
        # P4: Dynamic long/short trend filter
        use_trend_filter: bool = False,
        trend_window: int = 50,
        # P5: Volume-based factors
        use_obv_trend: bool = False,
        obv_ma_period: int = 20,
        use_volume_spike: bool = False,
        volume_spike_threshold: float = 2.0,
        use_vwap: bool = False,
        vwap_period: int = 20,
        # P6: Higher-timeframe MACD trend confirmation
        use_htf_macd: bool = False,
        htf_macd_fast: int = 12,
        htf_macd_slow: int = 26,
        htf_macd_signal: int = 9,
        # P7: Multi-factor resonance scoring (3+ factors aligned)
        use_resonance: bool = False,
        resonance_min_score: int = 3,
    ) -> None:
        """Initialise the trend strategy with all confirmation-tier parameters.

        Args:
            window: Fast MA / Bollinger Band lookback period.
            std_dev: Number of standard deviations for Bollinger Bands.
            atr_period: ATR lookback period for trailing stop.
            atr_multiplier: ATR multiplier for stop distance.
            max_hold_bars: Maximum bars to hold a position before time exit.
            adx_threshold: Minimum ADX value for trend confirmation.
            entry_zone: Band offset multiplier (tightens trigger).
            rsi_threshold: RSI oversold / overbought threshold.
            take_profit_pct: Take-profit percentage (reserved).
            stop_loss_pct: Stop-loss percentage (reserved).
            use_adx: Enable ADX trend-strength filter.
            use_volume: Enable volume confirmation.
            volume_threshold: Minimum volume ratio for confirmation.
            use_macd: Enable MACD momentum confirmation.
            macd_confirm_mode: MACD mode ("direction", "histogram", "both").
            use_ma_cross: Enable MA cross event filter.
            use_mfi: Enable Money Flow Index filter.
            mfi_period: MFI lookback period.
            mfi_threshold: MFI oversold threshold.
            use_stochastic: Enable stochastic oscillator filter.
            stoch_period: Stochastic lookback period.
            stoch_threshold: Stochastic oversold threshold.
            use_rsi_divergence: Enable RSI divergence detection.
            rsi_divergence_lookback: Lookback window for RSI divergence.
            use_macd_divergence: Enable MACD histogram divergence detection.
            macd_divergence_lookback: Lookback window for MACD divergence.
            use_trend_filter: Enable dynamic trend-direction filter.
            trend_window: Fast MA window for trend-direction filter.
            use_obv_trend: Enable OBV trend confirmation.
            obv_ma_period: OBV moving-average period.
            use_volume_spike: Enable volume-spike filter.
            volume_spike_threshold: Minimum volume ratio for spike.
            use_vwap: Enable VWAP price-level filter.
            vwap_period: VWAP rolling window.
            use_htf_macd: Enable higher-timeframe MACD trend filter.
            htf_macd_fast: HTF MACD fast EMA period.
            htf_macd_slow: HTF MACD slow EMA period.
            htf_macd_signal: HTF MACD signal line period.
            use_resonance: Enable multi-factor resonance scoring.
            resonance_min_score: Minimum aligned factors required.
        """
        self.window = window
        self.std_dev = std_dev
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.adx_threshold = adx_threshold
        self.entry_zone = entry_zone
        self.rsi_threshold = rsi_threshold
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        # P0
        self.use_adx = use_adx
        self.use_volume = use_volume
        self.volume_threshold = volume_threshold
        # P1
        self.use_macd = use_macd
        self.macd_confirm_mode = macd_confirm_mode
        self.use_ma_cross = use_ma_cross
        # P2
        self.use_mfi = use_mfi
        self.mfi_period = mfi_period
        self.mfi_threshold = mfi_threshold
        self.use_stochastic = use_stochastic
        self.stoch_period = stoch_period
        self.stoch_threshold = stoch_threshold
        # P3
        self.use_rsi_divergence = use_rsi_divergence
        self.rsi_divergence_lookback = rsi_divergence_lookback
        self.use_macd_divergence = use_macd_divergence
        self.macd_divergence_lookback = macd_divergence_lookback
        # P4
        self.use_trend_filter = use_trend_filter
        self.trend_window = trend_window
        # P5
        self.use_obv_trend = use_obv_trend
        self.obv_ma_period = obv_ma_period
        self.use_volume_spike = use_volume_spike
        self.volume_spike_threshold = volume_spike_threshold
        self.use_vwap = use_vwap
        self.vwap_period = vwap_period
        # P6: Higher-timeframe MACD trend confirmation
        self.use_htf_macd = use_htf_macd
        self.htf_macd_fast = htf_macd_fast
        self.htf_macd_slow = htf_macd_slow
        self.htf_macd_signal = htf_macd_signal
        # P7: Multi-factor resonance scoring
        self.use_resonance = use_resonance
        self.resonance_min_score = resonance_min_score

    # ------------------------------------------------------------------
    # HTF MACD trend (daily aggregation — strategy-specific)
    # ------------------------------------------------------------------

    def _htf_macd_trend(self, close: np.ndarray, bars_per_day: int = 288) -> np.ndarray:
        """Compute daily-aggregated MACD trend mapped back to 5m bars.

        Aggregates 5-minute closes into daily bars, computes MACD on the
        daily timeframe, then maps the direction (-1/0/+1) back to every
        5-minute bar belonging to that day.

        Args:
            close: Array of 5-minute close prices.
            bars_per_day: Number of 5-minute bars in a trading day.

        Returns:
            Integer array (same length as ``close``) where 1 = bullish,
            -1 = bearish, 0 = neutral / not enough data.
        """
        n = len(close)
        n_days = n // bars_per_day
        if n_days < self.htf_macd_slow + self.htf_macd_signal:
            return np.zeros(n, dtype=int)

        daily_close = np.array([
            close[d * bars_per_day + bars_per_day - 1]
            for d in range(n_days)
            if d * bars_per_day + bars_per_day - 1 < n
        ])

        macd_line, macd_signal, _ = compute_htf_macd(
            daily_close, self.htf_macd_fast, self.htf_macd_slow, self.htf_macd_signal,
        )

        htf_trend = np.zeros(n, dtype=int)
        for d in range(len(macd_line)):
            bar_start = d * bars_per_day
            bar_end = min(bar_start + bars_per_day, n)
            if macd_line[d] > macd_signal[d]:
                htf_trend[bar_start:bar_end] = 1
            elif macd_line[d] < macd_signal[d]:
                htf_trend[bar_start:bar_end] = -1

        return htf_trend

    # ------------------------------------------------------------------
    # RSI / MACD divergence detection (strategy-specific)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_rsi_divergence(
        close: np.ndarray,
        rsi: np.ndarray,
        i: int,
        lookback: int = 5,
        direction: str = "bullish",
    ) -> bool:
        """Detect RSI divergence.

        Bullish (bottom divergence): price makes a new low but RSI
        does not make a new low.
        Bearish (top divergence): price makes a new high but RSI
        does not make a new high.

        Args:
            close: Array of closing prices.
            rsi: Array of RSI values (same length).
            i: Current bar index.
            lookback: Number of bars to look back.
            direction: ``"bullish"`` or ``"bearish"``.

        Returns:
            True if a divergence is detected at bar ``i``.
        """
        if i < lookback * 2:
            return False
        price_window = close[i - lookback : i + 1]
        rsi_window = rsi[i - lookback : i + 1]
        if direction == "bullish":
            price_min_idx = np.argmin(price_window)
            rsi_min_idx = np.argmin(rsi_window)
            # Current price is the lowest but RSI low was earlier -> bottom divergence
            return price_min_idx == lookback and rsi_min_idx < lookback
        else:
            price_max_idx = np.argmax(price_window)
            rsi_max_idx = np.argmax(rsi_window)
            # Current price is the highest but RSI high was earlier -> top divergence
            return price_max_idx == lookback and rsi_max_idx < lookback

    @staticmethod
    def _detect_macd_divergence(
        close: np.ndarray,
        macd_hist: np.ndarray,
        i: int,
        lookback: int = 5,
        direction: str = "bullish",
    ) -> bool:
        """Detect MACD histogram divergence.

        Bullish (bottom divergence): price makes a new low but MACD
        histogram does not make a new low.
        Bearish (top divergence): price makes a new high but MACD
        histogram does not make a new high.

        Args:
            close: Array of closing prices.
            macd_hist: Array of MACD histogram values (same length).
            i: Current bar index.
            lookback: Number of bars to look back.
            direction: ``"bullish"`` or ``"bearish"``.

        Returns:
            True if a divergence is detected at bar ``i``.
        """
        if i < lookback * 2:
            return False
        price_window = close[i - lookback : i + 1]
        hist_window = macd_hist[i - lookback : i + 1]
        if direction == "bullish":
            price_min_idx = np.argmin(price_window)
            hist_min_idx = np.argmin(hist_window)
            return price_min_idx == lookback and hist_min_idx < lookback
        else:
            price_max_idx = np.argmax(price_window)
            hist_max_idx = np.argmax(hist_window)
            return price_max_idx == lookback and hist_max_idx < lookback

    # ------------------------------------------------------------------
    # Signal generation (core logic — DO NOT modify)
    # ------------------------------------------------------------------

    def generate_signals(self, df, enable_short=False):
        """
        生成交易信号（布林带均值回归 + 强趋势过滤）。
        信号: 0=平仓, 1=观望/持有, 2=做多(买入), 3=做空(卖出)

        核心逻辑：
        1. 价格触及布林带上下轨时入场（均值回归）
        2. 强趋势市过滤：ADX 高 + 连续 K 线同向时禁止逆势交易
        3. RSI/MACD 背离提供额外入场机会
        4. ATR 追踪止损 + 均线反转止损 + 时间退出
        """
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        n = len(close)

        # --- 基础指标 ---
        rolling_mean = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        rolling_std = pd.Series(close).rolling(window=self.window, min_periods=self.window).std()
        upper = rolling_mean + self.std_dev * rolling_std
        lower = rolling_mean - self.std_dev * rolling_std

        fast_ma = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        slow_ma = pd.Series(close).rolling(window=self.window * 2, min_periods=self.window * 2).mean()

        atr = compute_atr(df, self.atr_period)
        rsi = compute_rsi(close, 14)

        # --- 可选指标 ---
        adx = plus_di = minus_di = None
        if self.use_adx:
            adx, plus_di, minus_di = compute_adx(df, 14)

        vol_ratio = None
        if self.use_volume:
            vol = df["volume"].values.astype(float)
            vol_ma = pd.Series(vol).rolling(window=20, min_periods=20).mean().values
            vol_ratio = np.where(vol_ma > 0, vol / vol_ma, 1.0)

        macd_line = macd_signal_line = macd_hist = None
        if self.use_macd or self.use_macd_divergence:
            macd_line, macd_signal_line, macd_hist = compute_macd(close)

        mfi = None
        if self.use_mfi:
            mfi = compute_mfi(df, self.mfi_period)

        stoch_k = None
        if self.use_stochastic:
            stoch_k = compute_stochastic(df, self.stoch_period)

        # --- P5: 成交量因子 ---
        obv = obv_ma = None
        if self.use_obv_trend:
            obv = compute_obv(df)
            obv_ma = compute_ema(obv, self.obv_ma_period)

        vol_spike_ratio = None
        if self.use_volume_spike:
            vol = df["volume"].values.astype(float)
            vol_ma = pd.Series(vol).rolling(window=20, min_periods=20).mean().values
            vol_spike_ratio = np.where(vol_ma > 0, vol / vol_ma, 1.0)

        vwap = None
        if self.use_vwap:
            vwap = compute_vwap(df, self.vwap_period)

        # --- P6: 高级别 MACD 趋势（日线聚合后计算 MACD，再映射回 5m） ---
        htf_trend = None
        if self.use_htf_macd:
            htf_trend = self._htf_macd_trend(close, bars_per_day=288)

        # --- 动态趋势过滤预计算 ---
        trend_direction = np.zeros(n, dtype=int)  # 0=震荡, 1=上升, -1=下降
        if self.use_trend_filter:
            trend_fast = pd.Series(close).rolling(window=self.trend_window, min_periods=self.trend_window).mean()
            trend_slow = pd.Series(close).rolling(window=self.trend_window * 2, min_periods=self.trend_window * 2).mean()
            for i in range(self.trend_window * 2, n):
                if trend_fast.iloc[i] > trend_slow.iloc[i]:
                    trend_direction[i] = 1   # 上升趋势
                elif trend_fast.iloc[i] < trend_slow.iloc[i]:
                    trend_direction[i] = -1  # 下降趋势

        # --- 强趋势预计算（连续同向 K 线数） ---
        consec_up = np.zeros(n, dtype=int)
        consec_down = np.zeros(n, dtype=int)
        for i in range(1, n):
            if close[i] > close[i-1]:
                consec_up[i] = consec_up[i-1] + 1
                consec_down[i] = 0
            elif close[i] < close[i-1]:
                consec_down[i] = consec_down[i-1] + 1
                consec_up[i] = 0

        # --- 信号生成主循环 ---
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float('inf')

        for i in range(self.window * 2, n):
            price = close[i]

            is_uptrend = fast_ma.iloc[i] > slow_ma.iloc[i]
            is_downtrend = fast_ma.iloc[i] < slow_ma.iloc[i]

            # --- 持仓管理 ---
            if position == 1:
                if high[i] > highest_after_entry:
                    highest_after_entry = high[i]

                # ATR 追踪止损
                if highest_after_entry > 0:
                    atr_stop = highest_after_entry - self.atr_multiplier * atr[i]
                    if price < atr_stop:
                        signals[i] = 0
                        position = 0
                        continue

                # 均线反转止损
                if is_downtrend:
                    signals[i] = 0
                    position = 0
                    continue

                # 时间退出
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue

                signals[i] = 2
                continue

            elif position == -1:
                if low[i] < lowest_after_entry:
                    lowest_after_entry = low[i]

                # ATR 追踪止损
                if lowest_after_entry < float('inf'):
                    atr_stop = lowest_after_entry + self.atr_multiplier * atr[i]
                    if price > atr_stop:
                        signals[i] = 0
                        position = 0
                        continue

                # 均线反转止损
                if is_uptrend:
                    signals[i] = 0
                    position = 0
                    continue

                # 时间退出
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue

                signals[i] = 3
                continue

            # === 空仓：寻找入场机会 ===
            if position == 0:
                # --- 过滤器 ---
                adx_pass = True
                if self.use_adx and adx is not None:
                    adx_pass = adx[i] >= self.adx_threshold

                vol_pass = True
                if self.use_volume and vol_ratio is not None:
                    vol_pass = vol_ratio[i] >= self.volume_threshold

                # MACD 动量确认
                macd_long_ok = True
                macd_short_ok = True
                if self.use_macd and macd_line is not None:
                    if self.macd_confirm_mode in ("direction", "both"):
                        macd_long_ok = macd_line[i] > macd_signal_line[i]
                        macd_short_ok = macd_line[i] < macd_signal_line[i]
                    if self.macd_confirm_mode in ("histogram", "both") and i > 0:
                        if macd_hist[i] < macd_hist[i-1]:
                            macd_long_ok = False
                        if macd_hist[i] > macd_hist[i-1]:
                            macd_short_ok = False

                # RSI/MFI 超买超卖判断
                is_oversold = rsi[i] < self.rsi_threshold
                is_overbought = rsi[i] > (100 - self.rsi_threshold)
                if self.use_mfi and mfi is not None:
                    is_oversold = mfi[i] < self.mfi_threshold
                    is_overbought = mfi[i] > (100 - self.mfi_threshold)

                # Stochastic 回调确认
                stoch_oversold = True
                stoch_overbought = True
                if self.use_stochastic and stoch_k is not None:
                    stoch_oversold = stoch_k[i] < self.stoch_threshold
                    stoch_overbought = stoch_k[i] > (100 - self.stoch_threshold)

                # --- P5: 成交量因子过滤 ---
                # OBV 趋势：做多要求资金流入，做空要求资金流出
                obv_long_ok = True
                obv_short_ok = True
                if self.use_obv_trend and obv is not None:
                    obv_long_ok = obv[i] > obv_ma[i]
                    obv_short_ok = obv[i] < obv_ma[i]

                # 成交量激增：要求成交量放大
                spike_pass = True
                if self.use_volume_spike and vol_spike_ratio is not None:
                    spike_pass = vol_spike_ratio[i] >= self.volume_spike_threshold

                # VWAP：做多要求价格低于 VWAP，做空要求价格高于 VWAP
                vwap_long_ok = True
                vwap_short_ok = True
                if self.use_vwap and vwap is not None and not np.isnan(vwap[i]):
                    vwap_long_ok = price < vwap[i]
                    vwap_short_ok = price > vwap[i]

                # --- 强趋势过滤 ---
                # 当 ADX 高且连续 K 线同向时，禁止逆势交易
                strong_uptrend = False
                strong_downtrend = False
                if self.use_adx and adx is not None:
                    if adx[i] >= self.adx_threshold:
                        if consec_up[i] >= 6:
                            strong_uptrend = True
                        if consec_down[i] >= 6:
                            strong_downtrend = True

                upper_trigger = upper.iloc[i] - self.entry_zone * rolling_std.iloc[i]
                lower_trigger = lower.iloc[i] + self.entry_zone * rolling_std.iloc[i]

                # --- 动态趋势过滤 ---
                # 上升趋势主要做多，下跌趋势主要做空，震荡双向
                allow_long = True
                allow_short = enable_short
                if self.use_trend_filter:
                    td = trend_direction[i]
                    if td == 1:       # 上升趋势：做多优先，禁止做空
                        allow_short = False
                    elif td == -1:    # 下降趋势：做空优先，禁止做多
                        allow_long = False

                # --- P6: 高级别 MACD 趋势过滤 ---
                # 策略.md 方法9：日线 MACD 定方向
                if self.use_htf_macd and htf_trend is not None:
                    if htf_trend[i] == 1:
                        allow_short = False   # 日线看多，不做空
                    elif htf_trend[i] == -1:
                        allow_long = False    # 日线看空，不做多

                # --- P7: 多因子共振评分 ---
                # 策略.md 核心规则：3+ 因子同向信号置信度显著提升
                # 只计算已启用且实际提供判断的因子（排除默认 True 的因子）
                resonance_long_ok = True
                resonance_short_ok = True
                if self.use_resonance:
                    long_factors = []
                    short_factors = []

                    # 因子1: RSI 超买超卖（始终计算）
                    long_factors.append(is_oversold)
                    short_factors.append(is_overbought)

                    # 因子2: MACD 方向（仅已启用时计算）
                    if self.use_macd:
                        long_factors.append(macd_long_ok)
                        short_factors.append(macd_short_ok)

                    # 因子3: ADX 趋势强度（仅已启用时计算）
                    if self.use_adx:
                        long_factors.append(adx_pass)
                        short_factors.append(adx_pass)

                    # 因子4: 成交量确认（仅已启用时计算）
                    if self.use_volume:
                        long_factors.append(vol_pass)
                        short_factors.append(vol_pass)

                    # 因子5: OBV 资金流（仅已启用时计算）
                    if self.use_obv_trend:
                        long_factors.append(obv_long_ok)
                        short_factors.append(obv_short_ok)

                    # 因子6: VWAP 偏离（仅已启用时计算）
                    if self.use_vwap:
                        long_factors.append(vwap_long_ok)
                        short_factors.append(vwap_short_ok)

                    # 因子7: 成交量激增（仅已启用时计算）
                    if self.use_volume_spike:
                        long_factors.append(spike_pass)
                        short_factors.append(spike_pass)

                    # 因子8: Stochastic（仅已启用时计算）
                    if self.use_stochastic:
                        long_factors.append(stoch_oversold)
                        short_factors.append(stoch_overbought)

                    # 因子9: MFI（仅已启用时计算）
                    if self.use_mfi:
                        long_factors.append(is_oversold)  # MFI 时 is_oversold 用 MFI 值
                        short_factors.append(is_overbought)

                    long_score = sum(long_factors)
                    short_score = sum(short_factors)
                    n_factors = len(long_factors)

                    # 共振规则：已启用因子中，>= 阈值比例才允许入场
                    # 如果启用的因子太少（< min_score），则要求全部通过
                    if n_factors >= self.resonance_min_score:
                        resonance_long_ok = long_score >= self.resonance_min_score
                        resonance_short_ok = short_score >= self.resonance_min_score
                    else:
                        resonance_long_ok = long_score == n_factors
                        resonance_short_ok = short_score == n_factors

                # 优先级 1: 布林带均值回归
                if (allow_long and price <= lower_trigger and resonance_long_ok
                        and not strong_downtrend):
                    if not self.use_resonance:
                        # 非 P7 模式：保留原始 AND 门
                        if not (adx_pass and vol_pass and macd_long_ok
                                and obv_long_ok and spike_pass and vwap_long_ok):
                            pass  # 跳过
                        elif not (is_oversold and stoch_oversold):
                            pass  # 跳过
                        else:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue
                    else:
                        signals[i] = 2
                        position = 1
                        entry_price = price
                        entry_bar = i
                        highest_after_entry = high[i]
                        continue

                if (allow_short and price >= upper_trigger and resonance_short_ok
                        and not strong_uptrend):
                    if not self.use_resonance:
                        if not (adx_pass and vol_pass and macd_short_ok
                                and obv_short_ok and spike_pass and vwap_short_ok):
                            pass
                        elif not (is_overbought and stoch_overbought):
                            pass
                        else:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue
                    else:
                        signals[i] = 3
                        position = -1
                        entry_price = price
                        entry_bar = i
                        lowest_after_entry = low[i]
                        continue

                # 优先级 2: RSI / MACD 背离入场
                if self.use_rsi_divergence:
                    if allow_long and self._detect_rsi_divergence(close, rsi, i, self.rsi_divergence_lookback, "bullish"):
                        if not is_downtrend and price <= lower_trigger and not strong_downtrend:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue
                    if allow_short and self._detect_rsi_divergence(close, rsi, i, self.rsi_divergence_lookback, "bearish"):
                        if not is_uptrend and price >= upper_trigger and not strong_uptrend:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue

                if self.use_macd_divergence and macd_hist is not None:
                    if allow_long and self._detect_macd_divergence(close, macd_hist, i, self.macd_divergence_lookback, "bullish"):
                        if not is_downtrend and price <= lower_trigger and not strong_downtrend:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue
                    if allow_short and self._detect_macd_divergence(close, macd_hist, i, self.macd_divergence_lookback, "bearish"):
                        if not is_uptrend and price >= upper_trigger and not strong_uptrend:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue

                signals[i] = 1

        return signals
