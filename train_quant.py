"""
加密货币量化策略训练脚本。
基于 autoresearch 架构，使用布林带均值回归策略，并在时间预算内自动搜索最优参数。

Usage:
    uv run python train_quant.py
"""

import os
import math
import time
import json

import numpy as np
import pandas as pd
import torch

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "crypto")
TOKENIZER_DIR = os.path.join(PROJECT_DIR, "tokenizer")

TIME_BUDGET = 600       # 训练/搜索时间预算（秒）
INITIAL_CAPITAL = 10000.0
COMMISSION = 0.0002       # 0.02% 手续费（DEX Maker费率）
SLIPPAGE = 0.0002        # 0.02% 滑点

# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def list_crypto_files():
    """列出所有加密货币数据文件"""
    if not os.path.exists(DATA_DIR):
        return []
    return [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.endswith(".parquet")]


def load_crypto_data(filepath):
    """加载单个 Parquet 文件"""
    import pyarrow.parquet as pq
    table = pq.read_table(filepath)
    return table.to_pandas()


# ---------------------------------------------------------------------------
# 策略：布林带均值回归
# ---------------------------------------------------------------------------

class TrendStrategy:
    """
    均线交叉趋势跟随策略（多空双向）
    核心逻辑：
    1. 快线 > 慢线 → 上升趋势 → 做多
    2. 快线 < 慢线 → 下降趋势 → 做空
    3. ADX/MACD/RSI 多指标过滤确认趋势强度
    4. ATR 追踪止损 + 均线反转止损 + 时间退出
    """

    def __init__(self, window=20, std_dev=2.0,
                 atr_period=14, atr_multiplier=2.5,
                 max_hold_bars=48, adx_threshold=25,
                 entry_zone=1.0, rsi_threshold=30,
                 # P0: ADX 趋势过滤 + 量价确认
                 use_adx=False, use_volume=False, volume_threshold=1.2,
                 # P1: MACD 动量确认 + MA 交叉事件
                 use_macd=False, macd_confirm_mode="direction",
                 use_ma_cross=False,
                 # P2: MFI 量价动量 + 随机指标
                 use_mfi=False, mfi_period=14, mfi_threshold=20,
                 use_stochastic=False, stoch_period=14, stoch_threshold=20,
                 # P3: 背离信号
                 use_rsi_divergence=False, rsi_divergence_lookback=5,
                 use_macd_divergence=False, macd_divergence_lookback=5,
                 # P4: 动态多空趋势过滤
                 use_trend_filter=False, trend_window=50,
                 # P5: 成交量因子
                 use_obv_trend=False, obv_ma_period=20,
                 use_volume_spike=False, volume_spike_threshold=2.0,
                 use_vwap=False, vwap_period=20):
        self.window = window
        self.std_dev = std_dev
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.adx_threshold = adx_threshold
        self.entry_zone = entry_zone
        self.rsi_threshold = rsi_threshold
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

    def _compute_atr(self, df, period):
        """计算 ATR"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]

        atr = np.zeros(len(tr))
        atr[period-1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        return atr

    def _compute_adx(self, df, period=14):
        """计算 ADX, +DI, -DI（用于趋势强度过滤和方向确认）"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        plus_dm = np.zeros(len(high))
        minus_dm = np.zeros(len(high))

        for i in range(1, len(high)):
            up = high[i] - high[i-1]
            down = low[i-1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0

        atr = self._compute_atr(df, period)

        plus_di = np.zeros(len(high))
        minus_di = np.zeros(len(high))
        for i in range(period, len(high)):
            if atr[i] > 0:
                plus_di[i] = 100 * np.mean(plus_dm[i-period+1:i+1]) / atr[i]
                minus_di[i] = 100 * np.mean(minus_dm[i-period+1:i+1]) / atr[i]

        dx = np.zeros(len(high))
        for i in range(period, len(high)):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * np.abs(plus_di[i] - minus_di[i]) / di_sum

        adx = np.zeros(len(high))
        adx[period*2-1] = np.mean(dx[period:period*2])
        for i in range(period * 2, len(high)):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

        return adx, plus_di, minus_di

    def _compute_rsi(self, close, period=14):
        """计算 RSI"""
        deltas = np.diff(close, prepend=close[0])
        gain = np.where(deltas > 0, deltas, 0.0)
        loss = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.zeros(len(close))
        avg_loss = np.zeros(len(close))
        avg_gain[period] = np.mean(gain[1:period+1])
        avg_loss[period] = np.mean(loss[1:period+1])

        for i in range(period + 1, len(close)):
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period

        rsi = np.full(len(close), 50.0)
        for i in range(period, len(close)):
            if avg_loss[i] > 0:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100.0 - 100.0 / (1.0 + rs)
            else:
                rsi[i] = 100.0
        return rsi

    def _compute_ema(self, series, period):
        """计算 EMA（指数移动平均）"""
        alpha = 2.0 / (period + 1)
        ema = np.empty_like(series, dtype=float)
        ema[0] = series[0]
        for i in range(1, len(series)):
            ema[i] = alpha * series[i] + (1 - alpha) * ema[i-1]
        return ema

    def _compute_macd(self, close, fast=12, slow=26, signal=9):
        """计算 MACD（线、信号线、柱状图）"""
        ema_fast = self._compute_ema(close, fast)
        ema_slow = self._compute_ema(close, slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._compute_ema(macd_line, signal)
        macd_hist = macd_line - signal_line
        return macd_line, signal_line, macd_hist

    def _compute_mfi(self, df, period=14):
        """计算 MFI（资金流量指数，量价版 RSI）"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        volume = df["volume"].values

        typical_price = (high + low + close) / 3.0
        money_flow = typical_price * volume

        pos_flow = np.where(typical_price > np.roll(typical_price, 1), money_flow, 0.0)
        neg_flow = np.where(typical_price < np.roll(typical_price, 1), money_flow, 0.0)
        pos_flow[0] = 0.0
        neg_flow[0] = 0.0

        mfi = np.full(len(close), 50.0)
        for i in range(period, len(close)):
            pos_sum = np.sum(pos_flow[i-period+1:i+1])
            neg_sum = np.sum(neg_flow[i-period+1:i+1])
            if neg_sum > 0:
                mfi[i] = 100.0 - 100.0 / (1.0 + pos_sum / neg_sum)
            else:
                mfi[i] = 100.0
        return mfi

    def _compute_stochastic(self, df, period=14):
        """计算随机指标 %K"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        stoch_k = np.full(len(close), 50.0)
        for i in range(period - 1, len(close)):
            lowest = np.min(low[i-period+1:i+1])
            highest = np.max(high[i-period+1:i+1])
            if highest > lowest:
                stoch_k[i] = 100.0 * (close[i] - lowest) / (highest - lowest)
        return stoch_k

    def _compute_obv(self, close, volume, ma_period):
        """计算 OBV（能量潮）及其移动平均"""
        obv = np.zeros(len(close))
        for i in range(1, len(close)):
            if close[i] > close[i-1]:
                obv[i] = obv[i-1] + volume[i]
            elif close[i] < close[i-1]:
                obv[i] = obv[i-1] - volume[i]
            else:
                obv[i] = obv[i-1]
        obv_ma = pd.Series(obv).rolling(window=ma_period, min_periods=ma_period).mean().values
        return obv, obv_ma

    def _compute_vwap(self, df, period):
        """计算滚动 VWAP（成交量加权平均价）"""
        typical_price = (df["high"].values + df["low"].values + df["close"].values) / 3.0
        vol = df["volume"].values.astype(float)
        tp_vol = typical_price * vol

        vwap = np.full(len(df), np.nan)
        for i in range(period - 1, len(df)):
            vol_sum = np.sum(vol[i-period+1:i+1])
            if vol_sum > 0:
                vwap[i] = np.sum(tp_vol[i-period+1:i+1]) / vol_sum
            else:
                vwap[i] = typical_price[i]
        return vwap

    def _detect_rsi_divergence(self, close, rsi, i, lookback=5, direction="bullish"):
        """
        检测 RSI 背离。
        direction="bullish": 底背离（价格创新低，RSI 未创新低）
        direction="bearish": 顶背离（价格创新高，RSI 未创新高）
        """
        if i < lookback * 2:
            return False
        price_window = close[i-lookback:i+1]
        rsi_window = rsi[i-lookback:i+1]
        if direction == "bullish":
            price_min_idx = np.argmin(price_window)
            rsi_min_idx = np.argmin(rsi_window)
            # 当前价格是最低点，但 RSI 最低点在更早前 → 底背离
            return price_min_idx == lookback and rsi_min_idx < lookback
        else:
            price_max_idx = np.argmax(price_window)
            rsi_max_idx = np.argmax(rsi_window)
            # 当前价格是最高点，但 RSI 最高点在更早前 → 顶背离
            return price_max_idx == lookback and rsi_max_idx < lookback

    def _detect_macd_divergence(self, close, macd_hist, i, lookback=5, direction="bullish"):
        """
        检测 MACD 柱状图背离。
        direction="bullish": 底背离（价格创新低，MACD柱未创新低）
        direction="bearish": 顶背离（价格创新高，MACD柱未创新高）
        """
        if i < lookback * 2:
            return False
        price_window = close[i-lookback:i+1]
        hist_window = macd_hist[i-lookback:i+1]
        if direction == "bullish":
            price_min_idx = np.argmin(price_window)
            hist_min_idx = np.argmin(hist_window)
            return price_min_idx == lookback and hist_min_idx < lookback
        else:
            price_max_idx = np.argmax(price_window)
            hist_max_idx = np.argmax(hist_window)
            return price_max_idx == lookback and hist_max_idx < lookback

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

        atr = self._compute_atr(df, self.atr_period)
        rsi = self._compute_rsi(close, 14)

        # --- 可选指标 ---
        adx = plus_di = minus_di = None
        if self.use_adx:
            adx, plus_di, minus_di = self._compute_adx(df, 14)

        vol_ratio = None
        if self.use_volume:
            vol = df["volume"].values.astype(float)
            vol_ma = pd.Series(vol).rolling(window=20, min_periods=20).mean().values
            vol_ratio = np.where(vol_ma > 0, vol / vol_ma, 1.0)

        macd_line = macd_signal_line = macd_hist = None
        if self.use_macd or self.use_macd_divergence:
            macd_line, macd_signal_line, macd_hist = self._compute_macd(close)

        mfi = None
        if self.use_mfi:
            mfi = self._compute_mfi(df, self.mfi_period)

        stoch_k = None
        if self.use_stochastic:
            stoch_k = self._compute_stochastic(df, self.stoch_period)

        # --- P5: 成交量因子 ---
        obv = obv_ma = None
        if self.use_obv_trend:
            vol = df["volume"].values.astype(float)
            obv, obv_ma = self._compute_obv(close, vol, self.obv_ma_period)

        vol_spike_ratio = None
        if self.use_volume_spike:
            vol = df["volume"].values.astype(float)
            vol_ma = pd.Series(vol).rolling(window=20, min_periods=20).mean().values
            vol_spike_ratio = np.where(vol_ma > 0, vol / vol_ma, 1.0)

        vwap = None
        if self.use_vwap:
            vwap = self._compute_vwap(df, self.vwap_period)

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

                # 优先级 1: 布林带均值回归
                if (allow_long and price <= lower_trigger and adx_pass and vol_pass
                        and macd_long_ok and obv_long_ok and spike_pass and vwap_long_ok):
                    if is_oversold and stoch_oversold:
                        if not strong_downtrend:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue

                if (allow_short and price >= upper_trigger and adx_pass and vol_pass
                        and macd_short_ok and obv_short_ok and spike_pass and vwap_short_ok):
                    if is_overbought and stoch_overbought:
                        if not strong_uptrend:
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


# ---------------------------------------------------------------------------
# 评估器
# ---------------------------------------------------------------------------

class StrategyEvaluator:
    """策略绩效评估器"""

    def __init__(self, initial_capital=INITIAL_CAPITAL, commission=COMMISSION, slippage=SLIPPAGE):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def simulate(self, signals, prices, df=None):
        """
        模拟交易（支持做多/做空/空仓）
        信号: 0=平仓, 1=持有, 2=做多, 3=做空
        """
        capital = self.initial_capital
        shares = 0.0  # 正数=多头, 负数=空头
        position = 0  # 1=多头, -1=空头, 0=空仓
        equity = []
        trades = []
        entry_cost_basis = 0.0
        entry_price = 0.0
        entry_step = 0

        for i in range(len(signals)):
            signal = signals[i]
            price = prices[i]

            # 解析信号 → 目标仓位
            if signal == 2:
                target_pos = 1
            elif signal == 3:
                target_pos = -1
            elif signal == 0:
                target_pos = 0
            else:
                target_pos = position

            if target_pos != position:
                # 先平掉当前仓位
                if position == 1 and target_pos <= 0:
                    exec_price = price * (1 - self.slippage)
                    gross = shares * exec_price
                    cost = gross * self.commission
                    capital = gross - cost
                    pnl = capital - entry_cost_basis
                    trades.append({"type": "sell", "step": i, "pnl": float(pnl)})
                    shares = 0.0
                    position = 0

                elif position == -1 and target_pos >= 0:
                    exec_price = price * (1 + self.slippage)
                    # 空头平仓：买入还给市场
                    buy_cost = abs(shares) * exec_price
                    buy_cost_total = buy_cost * (1 + self.commission)
                    pnl = entry_cost_basis - buy_cost_total
                    capital = capital + pnl
                    # 防护：不允许负资金
                    if capital < 0:
                        capital = 0
                    trades.append({"type": "buy_cover", "step": i, "pnl": float(pnl)})
                    shares = 0.0
                    position = 0

                # 开新仓（资金不足则跳过）
                if target_pos == 1 and position == 0 and capital > 0:
                    exec_price = price * (1 + self.slippage)
                    shares = capital * (1 - self.commission) / exec_price
                    entry_cost_basis = capital
                    entry_price = exec_price
                    entry_step = i
                    capital = 0.0
                    trades.append({"type": "buy", "step": i})
                    position = 1

                elif target_pos == -1 and position == 0 and capital > 0:
                    # 做空：卖出借来的币，记录卖出所得
                    exec_price = price * (1 - self.slippage)
                    shares = -(capital * (1 - self.commission) / exec_price)
                    entry_cost_basis = capital
                    entry_price = exec_price
                    entry_step = i
                    # capital 暂存卖出所得（扣除手续费后）
                    capital = capital * (1 - self.commission)  # 卖出所得 = 本金 * (1-手续费)
                    trades.append({"type": "sell_short", "step": i})
                    position = -1

            # 计算当前权益
            if position == 1:
                current_equity = capital + shares * price
            elif position == -1:
                # 空头权益 = 卖出所得 + (入场价 - 当前价) * |仓位|
                current_equity = capital + abs(shares) * (entry_price - price)
            else:
                current_equity = capital

            # 防护：权益异常则截断
            if not np.isfinite(current_equity) or current_equity > 1e15 or current_equity < 0:
                equity.append(max(0, current_equity) if np.isfinite(current_equity) else 0)
                break
            equity.append(current_equity)

        # 结算未平仓位
        if position == 1:
            exec_price = prices[-1] * (1 - self.slippage)
            gross = shares * exec_price
            cost = gross * self.commission
            capital = gross - cost
            pnl = capital - entry_cost_basis
            trades.append({"type": "sell_final", "step": len(signals) - 1, "pnl": float(pnl)})
            equity[-1] = capital
        elif position == -1:
            exec_price = prices[-1] * (1 + self.slippage)
            buy_cost = abs(shares) * exec_price * (1 + self.commission)
            capital = capital + (entry_cost_basis - buy_cost)
            equity[-1] = capital

        return np.array(equity), trades

    def compute_metrics(self, equity_curve, trades):
        """计算绩效指标"""
        equity = equity_curve
        returns = np.diff(equity) / equity[:-1]

        total_return = (equity[-1] / equity[0]) - 1

        n_steps = len(equity)
        years = n_steps * 5 / (288 * 365)
        if years < 0.01:
            years = 0.01
        # 防止 overflow: 限制 total_return 范围
        total_return = max(-1.0, min(100.0, total_return))
        try:
            annualized_return = (1 + total_return) ** (1 / years) - 1
            annualized_return = max(-10.0, min(10.0, annualized_return))
        except (OverflowError, ValueError):
            annualized_return = 0.0

        annualized_vol = np.std(returns) * math.sqrt(288 * 365) if len(returns) > 0 else 0
        sharpe = annualized_return / annualized_vol if annualized_vol > 0 else 0

        peak = equity[0]
        max_drawdown = 0
        for e in equity:
            if e > peak:
                peak = e
            dd = (e - peak) / peak
            if dd < max_drawdown:
                max_drawdown = dd

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        total_trades = len(trade_pnls)
        winning_trades = len([t for t in trade_pnls if t["pnl"] > 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.5

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_vol": annualized_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
        }

    def evaluate(self, signals, prices, df=None):
        """评估一组信号"""
        equity, trades = self.simulate(signals, prices, df)

        # 防护：权益曲线出现 NaN/Inf 则直接返回零分
        if len(equity) == 0 or not np.all(np.isfinite(equity)):
            return 0.0, {"total_return": 0, "annualized_return": 0, "annualized_vol": 0,
                          "sharpe_ratio": 0, "max_drawdown": -0.99, "win_rate": 0}, []

        metrics = self.compute_metrics(equity, trades)

        # 防护：指标异常则零分
        if not np.isfinite(metrics["sharpe_ratio"]) or not np.isfinite(metrics["total_return"]):
            return 0.0, metrics, trades

        # 防护：回撤超过30%视为策略失效，直接零分
        if metrics["max_drawdown"] < -0.30:
            return 0.0, metrics, trades

        # 防护：最终权益低于初始70%视为失效
        if equity[-1] < self.initial_capital * 0.7:
            return 0.0, metrics, trades

        # 防护：必须盈利（或接近盈亏平衡）才有资格评分
        if metrics["total_return"] <= -0.005:  # 允许 -0.5% 以内的亏损
            return 0.0, metrics, trades

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)

        # 惩罚大回撤
        dd_penalty = max(0, 1 - abs(metrics["max_drawdown"]) / 0.20) if metrics["max_drawdown"] < 0 else 1.0

        # 最低交易量门槛：少于10笔交易大幅惩罚
        min_trade_penalty = min(1.0, n_trades / 10.0) if n_trades < 10 else 1.0

        # 限制各项指标范围，防止异常值
        sharpe_clamped = max(0, min(5.0, metrics["sharpe_ratio"]))
        return_clamped = max(0, min(2.0, metrics["total_return"]))  # 最高200%
        win_rate_clamped = max(0, min(1.0, metrics["win_rate"]))

        score = (
            sharpe_clamped * 0.25 +
            return_clamped * 0.15 +
            win_rate_clamped * 0.10 +
            dd_penalty * (1 + metrics["max_drawdown"]) * 0.15 +
            min(1.0, n_trades / 40.0) * 0.25 +   # 交易次数权重大幅提高
            min_trade_penalty * 0.10
        )

        return score, metrics, trades


# ---------------------------------------------------------------------------
# 参数搜索
# ---------------------------------------------------------------------------

def grid_search(df, time_budget=TIME_BUDGET):
    """
    两阶段网格搜索最优策略参数。
    Stage 1: 搜索核心布林带参数（50%时间预算）
    Stage 2: 固定核心参数，搜索指标组合（50%时间预算）
    数据集划分：最后 10% 作为验证集（按时间顺序）。
    """
    n = len(df)
    train_size = int(n * 0.9)
    train_df = df.iloc[:train_size].reset_index(drop=True)
    val_df = df.iloc[train_size:].reset_index(drop=True)

    evaluator = StrategyEvaluator()

    # ======================================================================
    # Stage 1: 核心参数搜索（布林带 + ATR + RSI）
    # ======================================================================
    stage1_grid = {
        "window": [15, 20, 25, 30],
        "std_dev": [2.0, 2.5, 3.0],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 18, 24, 36],
        "rsi_threshold": [30, 35, 40],
        "entry_zone": [0.0],
    }

    best_score = -float("inf")
    best_params = None
    best_metrics = None

    stage1_budget = time_budget * 0.5
    total_combos = 1
    for v in stage1_grid.values():
        total_combos *= len(v)

    print(f"Stage 1: 核心参数搜索 (训练集 {len(train_df)} 条, 验证集 {len(val_df)} 条)")
    print(f"  参数组合: {total_combos}, 时间预算: {stage1_budget:.0f}s")
    print()

    t_start = time.time()
    tried = 0

    for window in stage1_grid["window"]:
        for std_dev in stage1_grid["std_dev"]:
            for atr_mult in stage1_grid["atr_multiplier"]:
                for max_hold in stage1_grid["max_hold_bars"]:
                    for rsi_th in stage1_grid["rsi_threshold"]:
                        for ez in stage1_grid["entry_zone"]:
                            if time.time() - t_start > stage1_budget * 0.9:
                                print("Stage 1 时间预算即将耗尽，提前结束")
                                break

                            strategy = TrendStrategy(
                                window=window, std_dev=std_dev,
                                atr_multiplier=atr_mult, max_hold_bars=max_hold,
                                rsi_threshold=rsi_th, entry_zone=ez
                            )
                            signals = strategy.generate_signals(val_df, enable_short=True)
                            prices = val_df["close"].values

                            valid_signals = signals[window*2:]
                            valid_prices = prices[window*2:]
                            valid_df = val_df.iloc[window*2:].reset_index(drop=True)

                            if len(valid_signals) < 50:
                                continue

                            score, metrics, trades = evaluator.evaluate(valid_signals, valid_prices, valid_df)
                            n_trades = len([t for t in trades if t.get("pnl") is not None])

                            tried += 1
                            if tried % 50 == 0 or score > best_score:
                                print(f"  [{tried}/{total_combos}] w={window} std={std_dev} atr={atr_mult} hold={max_hold} rsi={rsi_th} ez={ez:.1f} | "
                                      f"评分={score:.4f} | 收益={metrics['total_return']*100:.2f}% | 夏普={metrics['sharpe_ratio']:.2f} | DD={metrics['max_drawdown']*100:.1f}% | 交易={n_trades}")

                            if score > best_score:
                                best_score = score
                                best_params = {
                                    "window": window,
                                    "std_dev": std_dev,
                                    "atr_multiplier": atr_mult,
                                    "max_hold_bars": max_hold,
                                    "rsi_threshold": rsi_th,
                                    "entry_zone": ez,
                                }
                                best_metrics = metrics

    stage1_time = time.time() - t_start
    print(f"\nStage 1 完成: {tried}/{total_combos} 组合, 耗时 {stage1_time:.1f}s")
    if best_params:
        ez = best_params.get('entry_zone', 0.0)
        print(f"  最优核心参数: w={best_params['window']} std={best_params['std_dev']} "
              f"atr={best_params['atr_multiplier']} hold={best_params['max_hold_bars']} rsi={best_params['rsi_threshold']} ez={ez:.1f}")
        print(f"  评分={best_score:.4f} 收益={best_metrics['total_return']*100:.2f}%")

    # ======================================================================
    # Stage 2: 指标组合搜索（固定核心参数）
    # ======================================================================
    if not best_params:
        return best_params, best_score, best_metrics

    # 预定义指标组合（精选，避免全排列爆炸）
    indicator_combos = [
        {},  # 基线（无额外指标）
        # P4: 趋势过滤
        {"use_trend_filter": True, "trend_window": 25},
        {"use_trend_filter": True, "trend_window": 50},
        {"use_trend_filter": True, "trend_window": 100},
        # P4 + P0: 趋势过滤 + Volume
        {"use_trend_filter": True, "trend_window": 25, "use_volume": True, "volume_threshold": 0.8},
        {"use_trend_filter": True, "trend_window": 25, "use_volume": True, "volume_threshold": 1.0},
        {"use_trend_filter": True, "trend_window": 25, "use_volume": True, "volume_threshold": 1.2},
        {"use_trend_filter": True, "trend_window": 50, "use_volume": True, "volume_threshold": 0.8},
        {"use_trend_filter": True, "trend_window": 50, "use_volume": True, "volume_threshold": 1.0},
        {"use_trend_filter": True, "trend_window": 50, "use_volume": True, "volume_threshold": 1.2},
        # P0: ADX
        {"use_adx": True, "adx_threshold": 20},
        {"use_adx": True, "adx_threshold": 25},
        {"use_adx": True, "adx_threshold": 30},
        # P0: Volume
        {"use_volume": True, "volume_threshold": 1.0},
        {"use_volume": True, "volume_threshold": 1.2},
        {"use_volume": True, "volume_threshold": 1.5},
        # P0: ADX + Volume
        {"use_adx": True, "adx_threshold": 20, "use_volume": True, "volume_threshold": 1.2},
        {"use_adx": True, "adx_threshold": 25, "use_volume": True, "volume_threshold": 1.2},
        {"use_adx": True, "adx_threshold": 25, "use_volume": True, "volume_threshold": 1.5},
        # P1: MACD
        {"use_macd": True, "macd_confirm_mode": "direction"},
        {"use_macd": True, "macd_confirm_mode": "histogram"},
        {"use_macd": True, "macd_confirm_mode": "both"},
        # P1: MA Cross
        {"use_ma_cross": True},
        # P1: MACD + MA Cross
        {"use_macd": True, "macd_confirm_mode": "direction", "use_ma_cross": True},
        # P0+P1: ADX + MACD
        {"use_adx": True, "adx_threshold": 25, "use_macd": True, "macd_confirm_mode": "direction"},
        {"use_adx": True, "adx_threshold": 25, "use_macd": True, "macd_confirm_mode": "both"},
        # P0+P1: ADX + Volume + MACD
        {"use_adx": True, "adx_threshold": 25, "use_volume": True, "volume_threshold": 1.2, "use_macd": True, "macd_confirm_mode": "direction"},
        # P2: MFI
        {"use_mfi": True, "mfi_threshold": 20},
        {"use_mfi": True, "mfi_threshold": 25},
        {"use_mfi": True, "mfi_threshold": 30},
        # P2: Stochastic
        {"use_stochastic": True, "stoch_threshold": 20},
        {"use_stochastic": True, "stoch_threshold": 30},
        # P2: MFI + Stochastic
        {"use_mfi": True, "mfi_threshold": 20, "use_stochastic": True, "stoch_threshold": 20},
        # P0+P2: ADX + MFI
        {"use_adx": True, "adx_threshold": 25, "use_mfi": True, "mfi_threshold": 25},
        # P0+P2: ADX + Stochastic
        {"use_adx": True, "adx_threshold": 25, "use_stochastic": True, "stoch_threshold": 20},
        # P3: RSI 背离
        {"use_rsi_divergence": True, "rsi_divergence_lookback": 5},
        {"use_rsi_divergence": True, "rsi_divergence_lookback": 8},
        # P3: MACD 背离
        {"use_macd_divergence": True, "macd_divergence_lookback": 5},
        {"use_macd_divergence": True, "macd_divergence_lookback": 8},
        # P3: RSI + MACD 背离
        {"use_rsi_divergence": True, "rsi_divergence_lookback": 5, "use_macd_divergence": True, "macd_divergence_lookback": 5},
        # P1+P3: MACD 方向 + RSI 背离
        {"use_macd": True, "macd_confirm_mode": "direction", "use_rsi_divergence": True, "rsi_divergence_lookback": 5},
        # 全量组合
        {"use_adx": True, "adx_threshold": 25, "use_volume": True, "volume_threshold": 1.2,
         "use_macd": True, "macd_confirm_mode": "direction"},
        {"use_adx": True, "adx_threshold": 25, "use_volume": True, "volume_threshold": 1.2,
         "use_macd": True, "macd_confirm_mode": "direction", "use_ma_cross": True},
        {"use_adx": True, "adx_threshold": 25, "use_mfi": True, "mfi_threshold": 25,
         "use_macd": True, "macd_confirm_mode": "direction"},
        # P5: OBV 趋势
        {"use_obv_trend": True, "obv_ma_period": 15},
        {"use_obv_trend": True, "obv_ma_period": 20},
        {"use_obv_trend": True, "obv_ma_period": 30},
        # P5: Volume Spike
        {"use_volume_spike": True, "volume_spike_threshold": 1.5},
        {"use_volume_spike": True, "volume_spike_threshold": 2.0},
        {"use_volume_spike": True, "volume_spike_threshold": 2.5},
        # P5: VWAP
        {"use_vwap": True, "vwap_period": 15},
        {"use_vwap": True, "vwap_period": 20},
        {"use_vwap": True, "vwap_period": 30},
        # P5: OBV + 趋势过滤
        {"use_obv_trend": True, "obv_ma_period": 20, "use_trend_filter": True, "trend_window": 50},
        {"use_obv_trend": True, "obv_ma_period": 20, "use_trend_filter": True, "trend_window": 25},
        # P5: Volume Spike + 趋势过滤
        {"use_volume_spike": True, "volume_spike_threshold": 1.5, "use_trend_filter": True, "trend_window": 50},
        # P5: VWAP + 趋势过滤
        {"use_vwap": True, "vwap_period": 20, "use_trend_filter": True, "trend_window": 50},
        # P5: OBV + Volume Spike
        {"use_obv_trend": True, "obv_ma_period": 20, "use_volume_spike": True, "volume_spike_threshold": 1.5},
        # P5: OBV + VWAP
        {"use_obv_trend": True, "obv_ma_period": 20, "use_vwap": True, "vwap_period": 20},
        # P5: 全部成交量因子
        {"use_obv_trend": True, "obv_ma_period": 20, "use_volume_spike": True, "volume_spike_threshold": 1.5,
         "use_vwap": True, "vwap_period": 20},
        # P5 + P4: 全部成交量 + 趋势过滤
        {"use_obv_trend": True, "obv_ma_period": 20, "use_volume_spike": True, "volume_spike_threshold": 1.5,
         "use_vwap": True, "vwap_period": 20, "use_trend_filter": True, "trend_window": 50},
        # P5 + P0: OBV + Volume
        {"use_obv_trend": True, "obv_ma_period": 20, "use_volume": True, "volume_threshold": 1.0},
        # P5 + P0: VWAP + Volume
        {"use_vwap": True, "vwap_period": 20, "use_volume": True, "volume_threshold": 1.0},
    ]

    stage2_budget = time_budget - stage1_time
    print(f"\nStage 2: 指标组合搜索 ({len(indicator_combos)} 种组合)")
    print(f"  时间预算: {stage2_budget:.0f}s")
    print()

    t2_start = time.time()
    stage2_tried = 0

    for combo in indicator_combos:
        if time.time() - t2_start > stage2_budget * 0.9:
            print("Stage 2 时间预算即将耗尽，提前结束")
            break

        # 合并核心参数和指标参数
        merged = {**best_params, **combo}

        strategy = TrendStrategy(**merged)
        signals = strategy.generate_signals(val_df, enable_short=True)
        prices = val_df["close"].values

        window = best_params["window"]
        valid_signals = signals[window*2:]
        valid_prices = prices[window*2:]
        valid_df = val_df.iloc[window*2:].reset_index(drop=True)

        if len(valid_signals) < 50:
            continue

        score, metrics, trades = evaluator.evaluate(valid_signals, valid_prices, valid_df)
        n_trades = len([t for t in trades if t.get("pnl") is not None])

        # 生成组合描述
        active_indicators = [k for k, v in combo.items() if v is True and k.startswith("use_")]
        desc = "+".join(active_indicators) if active_indicators else "baseline"
        if "macd_confirm_mode" in combo:
            desc += f"[{combo['macd_confirm_mode']}]"

        stage2_tried += 1
        if score > best_score:
            best_score = score
            best_params = merged.copy()
            best_metrics = metrics
            print(f"  [{stage2_tried}/{len(indicator_combos)}] {desc:40s} | "
                  f"评分={score:.4f} | 收益={metrics['total_return']*100:.2f}% | 夏普={metrics['sharpe_ratio']:.2f} | DD={metrics['max_drawdown']*100:.1f}% | 交易={n_trades} <<< NEW BEST")
        elif stage2_tried % 10 == 0:
            print(f"  [{stage2_tried}/{len(indicator_combos)}] {desc:40s} | "
                  f"评分={score:.4f} | 收益={metrics['total_return']*100:.2f}% | 交易={n_trades}")

    stage2_time = time.time() - t2_start
    print(f"\nStage 2 完成: {stage2_tried}/{len(indicator_combos)} 组合, 耗时 {stage2_time:.1f}s")

    # 输出活跃指标信息
    active = {k: v for k, v in best_params.items() if k.startswith("use_") and v is True}
    if active:
        indicator_str = ", ".join(f"{k}={v}" for k, v in best_params.items() if k.startswith("use_") or k in ("adx_threshold", "volume_threshold", "macd_confirm_mode", "mfi_threshold", "mfi_period", "stoch_threshold", "stoch_period"))
        print(f"  活跃指标: {indicator_str}")
    else:
        print(f"  无额外指标（纯均线交叉策略）")

    print()
    return best_params, best_score, best_metrics


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    t_start = time.time()

    print("=" * 60)
    print("加密货币量化策略训练 (布林带均值回归 + 强趋势过滤 - 多空双向)")
    print("=" * 60)

    data_files = list_crypto_files()
    if not data_files:
        print("错误: 未找到数据文件。请先运行 python prepare_crypto.py")
        return

    print(f"找到 {len(data_files)} 个数据文件")
    for f in data_files:
        print(f"  {os.path.basename(f)}")

    # 按币种独立训练，避免不同币种价格尺度差异导致回测失真
    per_symbol_budget = TIME_BUDGET / len(data_files)
    all_results = []

    for fp in data_files:
        symbol = os.path.basename(fp).replace("_5m.parquet", "").replace("_1m.parquet", "")
        print(f"\n{'='*60}")
        print(f"训练币种: {symbol}")
        print(f"{'='*60}")

        df = load_crypto_data(fp)
        df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
        print(f"数据量: {len(df)} 条K线, 价格范围: {df['close'].min():.2f} - {df['close'].max():.2f}")

        best_params, best_score, best_metrics = grid_search(df, per_symbol_budget)
        all_results.append({
            "symbol": symbol,
            "params": best_params,
            "score": best_score,
            "metrics": best_metrics,
        })

    # 选择综合表现最好的参数（优先选评分高、回撤小的）
    valid_results = [r for r in all_results if r["params"] is not None]
    if not valid_results:
        print("\n未找到有效参数组合")
        return None, None

    # 按评分排序
    valid_results.sort(key=lambda r: r["score"], reverse=True)
    best_result = valid_results[0]
    best_params = best_result["params"]
    best_score = best_result["score"]
    best_metrics = best_result["metrics"]

    # 保存最优参数
    checkpoint_dir = os.path.join(PROJECT_DIR, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, "quant_model.pt")

    checkpoint = {
        "strategy": "bollinger_trend_filter",
        "params": best_params,
        "score": best_score,
        "metrics": best_metrics,
        "all_results": [
            {"symbol": r["symbol"], "score": r["score"],
             "return": r["metrics"]["total_return"] if r["metrics"] else 0,
             "sharpe": r["metrics"]["sharpe_ratio"] if r["metrics"] else 0}
            for r in valid_results
        ],
    }
    torch.save(checkpoint, checkpoint_path)
    print(f"\n最优参数已保存: {checkpoint_path}")

    print("\n" + "=" * 60)
    print("最优参数与回测结果")
    print("=" * 60)
    print(f"来源币种:       {best_result['symbol']}")
    if best_params:
        print(f"布林带周期:     {best_params['window']}")
        print(f"标准差倍数:     {best_params['std_dev']}")
        print(f"ATR止损倍数:    {best_params['atr_multiplier']}")
        print(f"最大持仓K线:   {best_params['max_hold_bars']}")
        print(f"RSI阈值:        {best_params.get('rsi_threshold', 30)}")
        print(f"入场提前量:     {best_params.get('entry_zone', 0.0)}")
        # 指标开关
        indicator_keys = ["use_adx", "adx_threshold", "use_volume", "volume_threshold",
                          "use_macd", "macd_confirm_mode", "use_ma_cross",
                          "use_mfi", "mfi_period", "mfi_threshold",
                          "use_stochastic", "stoch_period", "stoch_threshold",
                          "use_rsi_divergence", "rsi_divergence_lookback",
                          "use_macd_divergence", "macd_divergence_lookback"]
        active_indicators = []
        for k in indicator_keys:
            v = best_params.get(k)
            if v is not None and (k.startswith("use_") and v is True or not k.startswith("use_")):
                active_indicators.append(f"{k}={v}")
        if any(best_params.get(k) for k in indicator_keys if k.startswith("use_")):
            print(f"活跃指标:       {', '.join(active_indicators)}")
        else:
            print(f"活跃指标:       无（纯布林带策略）")
        print(f"综合评分:       {best_score:.6f}")
        print(f"夏普比率:       {best_metrics['sharpe_ratio']:.4f}")
        print(f"总收益率:       {best_metrics['total_return']*100:.2f}%")
        print(f"年化收益率:     {best_metrics['annualized_return']*100:.2f}%")
        print(f"年化波动率:     {best_metrics['annualized_vol']*100:.2f}%")
        print(f"最大回撤:       {best_metrics['max_drawdown']*100:.2f}%")
        print(f"胜率:           {best_metrics['win_rate']*100:.1f}%")
    else:
        print("未找到有效参数组合")
    print(f"总耗时:         {time.time() - t_start:.1f}s")

    return best_score, best_metrics


if __name__ == "__main__":
    main()
