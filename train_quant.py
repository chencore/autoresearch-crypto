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
import inspect
import shutil
from datetime import datetime

import numpy as np
import pandas as pd
import torch

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "crypto")
TOKENIZER_DIR = os.path.join(PROJECT_DIR, "tokenizer")

TIME_BUDGET = 600  # 训练/搜索时间预算（秒）
INITIAL_CAPITAL = 10000.0
COMMISSION = 0.0002  # 0.02% 手续费（DEX Maker费率）
SLIPPAGE = 0.0002  # 0.02% 滑点

# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------


def list_crypto_files():
    """列出所有加密货币数据文件。
    优先选择带天数标识的新格式文件（如 ETHUSDT_5m_60d.parquet），
    避免新旧格式同时存在时重复加载。
    """
    if not os.path.exists(DATA_DIR):
        return []

    all_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".parquet")]

    # 按 (symbol, interval) 分组，优先选择带天数标识的文件
    grouped = {}
    for fname in all_files:
        # 新格式: ETHUSDT_5m_60d.parquet
        # 旧格式: ETHUSDT_5m.parquet
        parts = fname.replace(".parquet", "").split("_")
        if len(parts) >= 2:
            key = (parts[0], parts[1])  # (symbol, interval)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(fname)

    selected = []
    for key, fnames in grouped.items():
        # 优先选择带天数标识的文件（文件名包含3个或以上部分）
        tagged = [f for f in fnames if len(f.replace(".parquet", "").split("_")) >= 3]
        if tagged:
            # 如果有多个带标识的文件，选择天数最大的（文件名排序即可）
            selected.append(sorted(tagged)[-1])
        else:
            selected.append(fnames[0])

    return [os.path.join(DATA_DIR, f) for f in selected]


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

    def __init__(
        self,
        window=20,
        std_dev=2.0,
        atr_period=14,
        atr_multiplier=2.5,
        max_hold_bars=48,
        adx_threshold=25,
        entry_zone=1.0,
        rsi_threshold=30,
        take_profit_pct=0.05,
        stop_loss_pct=0.03,
        # P0: ADX 趋势过滤 + 量价确认
        use_adx=False,
        use_volume=False,
        volume_threshold=1.2,
        # P1: MACD 动量确认 + MA 交叉事件
        use_macd=False,
        macd_confirm_mode="direction",
        use_ma_cross=False,
        # P2: MFI 量价动量 + 随机指标
        use_mfi=False,
        mfi_period=14,
        mfi_threshold=20,
        use_stochastic=False,
        stoch_period=14,
        stoch_threshold=20,
        # P3: 背离信号
        use_rsi_divergence=False,
        rsi_divergence_lookback=5,
        use_macd_divergence=False,
        macd_divergence_lookback=5,
        # P4: 动态多空趋势过滤
        use_trend_filter=False,
        trend_window=50,
        # P5: 成交量因子
        use_obv_trend=False,
        obv_ma_period=20,
        use_volume_spike=False,
        volume_spike_threshold=2.0,
        use_vwap=False,
        vwap_period=20,
        # P6: 高级别 MACD 趋势确认（策略.md 方法9）
        use_htf_macd=False,
        htf_macd_fast=12,
        htf_macd_slow=26,
        htf_macd_signal=9,
        # P7: 多因子共振评分（策略.md 核心规则：3+因子同向）
        use_resonance=False,
        resonance_min_score=3,
    ):
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
        # P6: 高级别 MACD 趋势确认
        self.use_htf_macd = use_htf_macd
        self.htf_macd_fast = htf_macd_fast
        self.htf_macd_slow = htf_macd_slow
        self.htf_macd_signal = htf_macd_signal
        # P7: 多因子共振评分
        self.use_resonance = use_resonance
        self.resonance_min_score = resonance_min_score

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
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def _compute_adx(self, df, period=14):
        """计算 ADX, +DI, -DI（用于趋势强度过滤和方向确认）"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        plus_dm = np.zeros(len(high))
        minus_dm = np.zeros(len(high))

        for i in range(1, len(high)):
            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0

        atr = self._compute_atr(df, period)

        plus_di = np.zeros(len(high))
        minus_di = np.zeros(len(high))
        for i in range(period, len(high)):
            if atr[i] > 0:
                plus_di[i] = 100 * np.mean(plus_dm[i - period + 1 : i + 1]) / atr[i]
                minus_di[i] = 100 * np.mean(minus_dm[i - period + 1 : i + 1]) / atr[i]

        dx = np.zeros(len(high))
        for i in range(period, len(high)):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * np.abs(plus_di[i] - minus_di[i]) / di_sum

        adx = np.zeros(len(high))
        adx[period * 2 - 1] = np.mean(dx[period : period * 2])
        for i in range(period * 2, len(high)):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return adx, plus_di, minus_di

    def _compute_rsi(self, close, period=14):
        """计算 RSI"""
        deltas = np.diff(close, prepend=close[0])
        gain = np.where(deltas > 0, deltas, 0.0)
        loss = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.zeros(len(close))
        avg_loss = np.zeros(len(close))
        avg_gain[period] = np.mean(gain[1 : period + 1])
        avg_loss[period] = np.mean(loss[1 : period + 1])

        for i in range(period + 1, len(close)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

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
            ema[i] = alpha * series[i] + (1 - alpha) * ema[i - 1]
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
            pos_sum = np.sum(pos_flow[i - period + 1 : i + 1])
            neg_sum = np.sum(neg_flow[i - period + 1 : i + 1])
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
            lowest = np.min(low[i - period + 1 : i + 1])
            highest = np.max(high[i - period + 1 : i + 1])
            if highest > lowest:
                stoch_k[i] = 100.0 * (close[i] - lowest) / (highest - lowest)
        return stoch_k

    def _compute_htf_macd(self, close, bars_per_day=288):
        """
        计算高级别（日线）MACD 趋势方向。
        将 5m 数据聚合为日线，计算日线 MACD，再映射回 5m 级别。
        策略.md 方法9：周线/月线 MACD 定中长期趋势方向。
        """
        n = len(close)
        # 聚合为日线收盘价
        n_days = n // bars_per_day
        if n_days < self.htf_macd_slow + self.htf_macd_signal:
            return np.zeros(n, dtype=int)

        daily_close = np.array(
            [
                close[d * bars_per_day + bars_per_day - 1]
                for d in range(n_days)
                if d * bars_per_day + bars_per_day - 1 < n
            ]
        )

        # EMA 计算
        def ema(data, period):
            result = np.zeros(len(data))
            result[period - 1] = np.mean(data[:period])
            k = 2.0 / (period + 1)
            for i in range(period, len(data)):
                result[i] = data[i] * k + result[i - 1] * (1 - k)
            return result

        fast_ema = ema(daily_close, self.htf_macd_fast)
        slow_ema = ema(daily_close, self.htf_macd_slow)
        macd_line = fast_ema - slow_ema
        signal_line = ema(macd_line, self.htf_macd_signal)

        # 映射回 5m 级别：每日的趋势方向应用到当天的所有 bar
        htf_trend = np.zeros(n, dtype=int)
        for d in range(len(macd_line)):
            bar_start = d * bars_per_day
            bar_end = min(bar_start + bars_per_day, n)
            if macd_line[d] > signal_line[d]:
                htf_trend[bar_start:bar_end] = 1  # 日线看多
            elif macd_line[d] < signal_line[d]:
                htf_trend[bar_start:bar_end] = -1  # 日线看空

        return htf_trend

    def _compute_obv(self, close, volume, ma_period):
        """计算 OBV（能量潮）及其移动平均"""
        obv = np.zeros(len(close))
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv[i] = obv[i - 1] + volume[i]
            elif close[i] < close[i - 1]:
                obv[i] = obv[i - 1] - volume[i]
            else:
                obv[i] = obv[i - 1]
        obv_ma = pd.Series(obv).rolling(window=ma_period, min_periods=ma_period).mean().values
        return obv, obv_ma

    def _compute_vwap(self, df, period):
        """计算滚动 VWAP（成交量加权平均价）"""
        typical_price = (df["high"].values + df["low"].values + df["close"].values) / 3.0
        vol = df["volume"].values.astype(float)
        tp_vol = typical_price * vol

        vwap = np.full(len(df), np.nan)
        for i in range(period - 1, len(df)):
            vol_sum = np.sum(vol[i - period + 1 : i + 1])
            if vol_sum > 0:
                vwap[i] = np.sum(tp_vol[i - period + 1 : i + 1]) / vol_sum
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
        price_window = close[i - lookback : i + 1]
        rsi_window = rsi[i - lookback : i + 1]
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
        slow_ma = (
            pd.Series(close).rolling(window=self.window * 2, min_periods=self.window * 2).mean()
        )

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

        # --- P6: 高级别 MACD 趋势 ---
        htf_trend = None
        if self.use_htf_macd:
            htf_trend = self._compute_htf_macd(close, bars_per_day=288)

        # --- 动态趋势过滤预计算 ---
        trend_direction = np.zeros(n, dtype=int)  # 0=震荡, 1=上升, -1=下降
        if self.use_trend_filter:
            trend_fast = (
                pd.Series(close)
                .rolling(window=self.trend_window, min_periods=self.trend_window)
                .mean()
            )
            trend_slow = (
                pd.Series(close)
                .rolling(window=self.trend_window * 2, min_periods=self.trend_window * 2)
                .mean()
            )
            for i in range(self.trend_window * 2, n):
                if trend_fast.iloc[i] > trend_slow.iloc[i]:
                    trend_direction[i] = 1  # 上升趋势
                elif trend_fast.iloc[i] < trend_slow.iloc[i]:
                    trend_direction[i] = -1  # 下降趋势

        # --- 强趋势预计算（连续同向 K 线数） ---
        consec_up = np.zeros(n, dtype=int)
        consec_down = np.zeros(n, dtype=int)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                consec_up[i] = consec_up[i - 1] + 1
                consec_down[i] = 0
            elif close[i] < close[i - 1]:
                consec_down[i] = consec_down[i - 1] + 1
                consec_up[i] = 0

        # --- 信号生成主循环 ---
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
                if lowest_after_entry < float("inf"):
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
                        if macd_hist[i] < macd_hist[i - 1]:
                            macd_long_ok = False
                        if macd_hist[i] > macd_hist[i - 1]:
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
                    if td == 1:  # 上升趋势：做多优先，禁止做空
                        allow_short = False
                    elif td == -1:  # 下降趋势：做空优先，禁止做多
                        allow_long = False

                # --- P6: 高级别 MACD 趋势过滤 ---
                # 策略.md 方法9：日线 MACD 定方向
                if self.use_htf_macd and htf_trend is not None:
                    if htf_trend[i] == 1:
                        allow_short = False  # 日线看多，不做空
                    elif htf_trend[i] == -1:
                        allow_long = False  # 日线看空，不做多

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
                if (
                    allow_long
                    and price <= lower_trigger
                    and resonance_long_ok
                    and not strong_downtrend
                ):
                    if not self.use_resonance:
                        # 非 P7 模式：保留原始 AND 门
                        if not (
                            adx_pass
                            and vol_pass
                            and macd_long_ok
                            and obv_long_ok
                            and spike_pass
                            and vwap_long_ok
                        ):
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

                if (
                    allow_short
                    and price >= upper_trigger
                    and resonance_short_ok
                    and not strong_uptrend
                ):
                    if not self.use_resonance:
                        if not (
                            adx_pass
                            and vol_pass
                            and macd_short_ok
                            and obv_short_ok
                            and spike_pass
                            and vwap_short_ok
                        ):
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
                    if allow_long and self._detect_rsi_divergence(
                        close, rsi, i, self.rsi_divergence_lookback, "bullish"
                    ):
                        if not is_downtrend and price <= lower_trigger and not strong_downtrend:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue
                    if allow_short and self._detect_rsi_divergence(
                        close, rsi, i, self.rsi_divergence_lookback, "bearish"
                    ):
                        if not is_uptrend and price >= upper_trigger and not strong_uptrend:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue

                if self.use_macd_divergence and macd_hist is not None:
                    if allow_long and self._detect_macd_divergence(
                        close, macd_hist, i, self.macd_divergence_lookback, "bullish"
                    ):
                        if not is_downtrend and price <= lower_trigger and not strong_downtrend:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue
                    if allow_short and self._detect_macd_divergence(
                        close, macd_hist, i, self.macd_divergence_lookback, "bearish"
                    ):
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
# 剥头皮策略：纯均值回归，高频短线
# ---------------------------------------------------------------------------


class ScalpStrategy:
    """
    高频剥头皮策略。纯均值回归，不要求趋势方向对齐。
    价格偏离局部均值时入场，回归时快速离场。
    目标：30天 50-200 笔交易，单笔小利（0.3-0.8%）。
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

    def _compute_rsi(self, close, period=14):
        delta = np.diff(close)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = np.zeros(len(close))
        avg_loss = np.zeros(len(close))
        avg_gain[period] = np.mean(gain[:period])
        avg_loss[period] = np.mean(loss[:period])
        for i in range(period + 1, len(close)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
        rsi = 100.0 - 100.0 / (1.0 + rs)
        rsi[:period] = 50.0
        return rsi

    def _compute_vwap(self, df, period):
        typical_price = (df["high"].values + df["low"].values + df["close"].values) / 3.0
        vol = df["volume"].values.astype(float)
        tp_vol = typical_price * vol
        vwap = np.full(len(df), np.nan)
        for i in range(period - 1, len(df)):
            vol_sum = np.sum(vol[i - period + 1 : i + 1])
            if vol_sum > 0:
                vwap[i] = np.sum(tp_vol[i - period + 1 : i + 1]) / vol_sum
            else:
                vwap[i] = typical_price[i]
        return vwap

    def generate_signals(self, df, enable_short=False):
        """
        生成交易信号。信号: 0=平仓, 1=观望, 2=做多, 3=做空。

        入场：价格触及紧布林带上下轨（纯均值回归）
        出场：回归均值 / 固定止盈 / 固定止损 / 超时
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # 布林带
        rolling_mean = (
            pd.Series(close).rolling(window=self.window, min_periods=self.window).mean().values
        )
        rolling_std = (
            pd.Series(close).rolling(window=self.window, min_periods=self.window).std().values
        )
        upper = rolling_mean + self.std_dev * rolling_std
        lower = rolling_mean - self.std_dev * rolling_std

        # RSI（瀑布防护）
        rsi = self._compute_rsi(close, self.rsi_period)

        # 成交量比
        vol_ratio = None
        if self.use_volume_filter:
            vol = df["volume"].values.astype(float)
            vol_ma = pd.Series(vol).rolling(window=20, min_periods=20).mean().values
            vol_ratio = np.where(vol_ma > 0, vol / vol_ma, 1.0)

        # 趋势MA（趋势对齐）
        trend_ma = None
        if self.use_trend_align:
            trend_ma = (
                pd.Series(close)
                .rolling(window=self.trend_ma_period, min_periods=self.trend_ma_period)
                .mean()
                .values
            )

        # 时段过滤（UTC小时）
        hours = None
        if self.use_session_filter and "timestamp" in df.columns:
            timestamps = pd.to_datetime(df["timestamp"], unit="ms")
            hours = timestamps.dt.hour.values

        # 信号生成
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0

        for i in range(self.window, n):
            price = close[i]

            # === 持仓管理 ===
            if position == 1:
                bars_held = i - entry_bar
                pnl_pct = (price - entry_price) / entry_price

                # 固定止盈
                if pnl_pct >= self.take_profit_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # 固定止损
                if pnl_pct <= -self.stop_loss_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # 超时退出
                if bars_held >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue

                signals[i] = 2
                continue

            elif position == -1:
                bars_held = i - entry_bar
                pnl_pct = (entry_price - price) / entry_price

                # 固定止盈
                if pnl_pct >= self.take_profit_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # 固定止损
                if pnl_pct <= -self.stop_loss_pct:
                    signals[i] = 0
                    position = 0
                    continue

                # 超时退出
                if bars_held >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue

                signals[i] = 3
                continue

            # === 空仓：寻找入场 ===
            if np.isnan(lower[i]) or np.isnan(upper[i]):
                continue

            # 时段过滤
            if self.use_session_filter and hours is not None:
                if hours[i] < self.session_start or hours[i] >= self.session_end:
                    continue

            # 趋势对齐：只在趋势方向做均值回归
            trend_long_ok = True
            trend_short_ok = True
            if self.use_trend_align and trend_ma is not None and not np.isnan(trend_ma[i]):
                if price > trend_ma[i]:
                    trend_short_ok = False  # 上升趋势，不做空
                elif price < trend_ma[i]:
                    trend_long_ok = False  # 下降趋势，不做多
                else:
                    trend_long_ok = False
                    trend_short_ok = False

            # 成交量过滤
            vol_pass = True
            if self.use_volume_filter and vol_ratio is not None:
                vol_pass = vol_ratio[i] >= self.volume_threshold

            # RSI 入场条件：要求超卖/超买（策略.md 策略9）
            rsi_long_ok = (
                rsi[i] < self.rsi_entry_low if self.use_rsi_entry else rsi[i] > self.rsi_extreme_low
            )
            rsi_short_ok = (
                rsi[i] > self.rsi_entry_high
                if self.use_rsi_entry
                else rsi[i] < self.rsi_extreme_high
            )

            # 做多：价格触及下轨 + RSI超卖 + 趋势向上 + 成交量
            if price <= lower[i] and rsi_long_ok and trend_long_ok and vol_pass:
                signals[i] = 2
                position = 1
                entry_price = price
                entry_bar = i
                continue

            # 做空：价格触及上轨 + RSI超买 + 趋势向下 + 成交量
            if enable_short and price >= upper[i] and rsi_short_ok and trend_short_ok and vol_pass:
                signals[i] = 3
                position = -1
                entry_price = price
                entry_bar = i
                continue

        return signals


# ---------------------------------------------------------------------------
# 纯价格行为策略：无因子约束，布林带均值回归 + ATR/MA/Time退出
# ---------------------------------------------------------------------------


class PureActionStrategy:
    """
    纯价格行为策略。不做任何因子约束，仅依赖价格本身的统计特征。
    入场：价格触及布林带上下轨（均值回归）
    退出：ATR 追踪止损 / 均线反转 / 时间超时
    不做任何指标过滤（无ADX、无Volume、无MACD、无RSI、无MFI等）。

    trend_ma_period: 趋势对齐参数。设为 None 则不做趋势过滤（纯均值回归）；
                    设为数值（如50/100/200）则仅在趋势方向交易（方案C）。
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

    def _compute_atr(self, df, period):
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]
        atr = np.zeros(len(tr))
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def _compute_adx(self, df, period=14):
        """计算 ADX 趋势强度。返回值越大趋势越强。"""
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        n = len(high)

        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0

        atr_adx = self._compute_atr(df, period)

        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        for i in range(period, n):
            if atr_adx[i] > 0:
                plus_di[i] = 100 * np.mean(plus_dm[i - period + 1 : i + 1]) / atr_adx[i]
                minus_di[i] = 100 * np.mean(minus_dm[i - period + 1 : i + 1]) / atr_adx[i]

        dx = np.zeros(n)
        for i in range(period, n):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

        adx = np.zeros(n)
        adx[period * 2 - 1] = np.mean(dx[period : period * 2])
        for i in range(period * 2, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return adx

    def generate_signals(self, df):
        """
        生成交易信号。信号: 0=平仓, 1=观望, 2=做多, 3=做空。
        纯价格行为，不做任何因子约束。
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # --- 布林带 ---
        rolling_mean = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        rolling_std = pd.Series(close).rolling(window=self.window, min_periods=self.window).std()
        upper = rolling_mean + self.std_dev * rolling_std
        lower = rolling_mean - self.std_dev * rolling_std

        # --- 均线趋势 ---
        fast_ma = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        slow_ma = (
            pd.Series(close).rolling(window=self.window * 2, min_periods=self.window * 2).mean()
        )

        # --- ATR ---
        atr = self._compute_atr(df, self.atr_period)

        # --- 长期趋势MA（趋势对齐过滤）---
        trend_ma = None
        if self.trend_ma_period is not None:
            trend_ma = (
                pd.Series(close)
                .rolling(window=self.trend_ma_period, min_periods=self.trend_ma_period)
                .mean()
                .values
            )

        # --- ADX 趋势强度（高位时空仓避险）---
        adx = None
        if self.adx_threshold is not None:
            adx = self._compute_adx(df, self.adx_period)

        # --- 连续同向K线（纯价格动量检测）---
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
        lowest_after_entry = float("inf")

        for i in range(self.window * 2, n):
            price = close[i]

            is_uptrend = fast_ma.iloc[i] > slow_ma.iloc[i]
            is_downtrend = fast_ma.iloc[i] < slow_ma.iloc[i]

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

            # === 空仓：寻找入场机会 ===
            if position == 0:
                if np.isnan(lower.iloc[i]) or np.isnan(upper.iloc[i]):
                    continue

                upper_trigger = upper.iloc[i] - self.entry_zone * rolling_std.iloc[i]
                lower_trigger = lower.iloc[i] + self.entry_zone * rolling_std.iloc[i]

                strong_uptrend = consec_up[i] >= 6
                strong_downtrend = consec_down[i] >= 6

                # 趋势对齐过滤（方案C）：只在趋势方向交易
                allow_long = True
                allow_short = self.enable_short
                if self.trend_ma_period is not None and trend_ma is not None:
                    if i >= self.trend_ma_period and not np.isnan(trend_ma[i]):
                        if price > trend_ma[i]:
                            allow_short = False  # 上升趋势，不做空
                        elif price < trend_ma[i]:
                            allow_long = False  # 下降趋势，不做多

                # ADX 趋势强度过滤：强趋势时空仓避险
                if self.adx_threshold is not None and adx is not None:
                    if i >= self.adx_period * 2 and adx[i] > self.adx_threshold:
                        continue  # 趋势太强，不做任何入场

                # 做多：价格触及下轨，不在强烈下跌中，趋势方向允许
                if allow_long and price <= lower_trigger and not strong_downtrend:
                    signals[i] = 2
                    position = 1
                    entry_price = price
                    entry_bar = i
                    highest_after_entry = high[i]
                    continue

                # 做空：价格触及上轨，不在强烈上涨中，趋势方向允许
                if allow_short and price >= upper_trigger and not strong_uptrend:
                    signals[i] = 3
                    position = -1
                    entry_price = price
                    entry_bar = i
                    lowest_after_entry = low[i]
                    continue

        return signals


# ---------------------------------------------------------------------------
# 策略：市场状态自适应（ADX 判市，震荡=均值回归，趋势=趋势跟随）
# ---------------------------------------------------------------------------


class HybridStrategy:
    """
    市场状态自适应策略。
    ADX <= adx_threshold: 震荡市 → 布林带均值回归（PureActionStrategy 逻辑）
    ADX >  adx_threshold: 趋势市 → 趋势跟随（MA 回调入场）
    """

    def __init__(
        self,
        window=20,
        std_dev=2.0,
        atr_period=14,
        atr_multiplier=2.0,
        max_hold_bars=24,
        entry_zone=0.0,
        enable_short=True,
        trend_ma_period=100,
        adx_threshold=25,
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

    def _compute_atr(self, df, period):
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]
        atr = np.zeros(len(tr))
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def _compute_adx(self, df, period=14):
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        n = len(high)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0
        atr_adx = self._compute_atr(df, period)
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        for i in range(period, n):
            if atr_adx[i] > 0:
                plus_di[i] = 100 * np.mean(plus_dm[i - period + 1 : i + 1]) / atr_adx[i]
                minus_di[i] = 100 * np.mean(minus_dm[i - period + 1 : i + 1]) / atr_adx[i]
        dx = np.zeros(n)
        for i in range(period, n):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum
        adx = np.zeros(n)
        adx[period * 2 - 1] = np.mean(dx[period : period * 2])
        for i in range(period * 2, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
        return adx

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
        slow_ma = (
            pd.Series(close).rolling(window=self.window * 2, min_periods=self.window * 2).mean()
        )

        # --- ATR ---
        atr = self._compute_atr(df, self.atr_period)

        # --- 趋势MA ---
        trend_ma = (
            pd.Series(close)
            .rolling(window=self.trend_ma_period, min_periods=self.trend_ma_period)
            .mean()
            .values
        )

        # --- ADX（市场状态判定）---
        adx = self._compute_adx(df, self.adx_period)

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
        lowest_after_entry = float("inf")

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
                    long_cross = (
                        price_above_trend
                        and prev_close > prev_fast_ma
                        and price <= fast_ma.iloc[i]
                        and not np.isnan(fast_ma.iloc[i])
                        and not np.isnan(prev_fast_ma)
                    )
                    if long_cross:
                        signals[i] = 2
                        position = 1
                        entry_price = price
                        entry_bar = i
                        highest_after_entry = high[i]
                        continue

                    # 做空：下降趋势中，价格从下往上穿越快线（反弹入场）
                    if self.enable_short:
                        short_cross = (
                            price_below_trend
                            and prev_close < prev_fast_ma
                            and price >= fast_ma.iloc[i]
                            and not np.isnan(fast_ma.iloc[i])
                            and not np.isnan(prev_fast_ma)
                        )
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


# ---------------------------------------------------------------------------
# 策略：纯趋势跟随（EMA 定方向，EMA 回调入场）
# ---------------------------------------------------------------------------


def _ema(series, window):
    alpha = 2 / (window + 1)
    ema = np.zeros(len(series), dtype=np.float64)
    ema[0] = series[0]
    for i in range(1, len(series)):
        ema[i] = alpha * series[i] + (1 - alpha) * ema[i - 1]
    return ema.astype(np.float32)


class TrendFollowStrategy:
    """
    纯趋势跟随策略。
    核心逻辑：EMA 长周期定趋势方向，EMA 短周期回调入场。
    只做顺势交易：上升趋势只做多，下降趋势只做空。
    入场：价格回调到短周期 EMA 附近时顺势入场。
    出场：ATR 追踪止损 + 均线反转 + 时间退出。
    """

    def __init__(
        self,
        long_ma_period=100,
        pull_ma_period=20,
        atr_period=14,
        atr_multiplier=2.0,
        max_hold_bars=24,
        entry_zone=0.002,
        enable_short=True,
        volume_threshold=None,
    ):
        self.long_ma_period = long_ma_period
        self.pull_ma_period = pull_ma_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.entry_zone = entry_zone
        self.enable_short = enable_short
        self.volume_threshold = volume_threshold
        self.window = max(long_ma_period, pull_ma_period, atr_period)

    def _compute_atr(self, df, period):
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]
        atr = np.zeros(len(tr))
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def generate_signals(self, df):
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume_ratio = df["volume_ratio"].values if "volume_ratio" in df.columns else None
        n = len(close)

        # --- EMA 均线 ---
        long_ma = _ema(close, self.long_ma_period)
        pull_ma = _ema(close, self.pull_ma_period)
        mid_ma = _ema(close, self.pull_ma_period * 2)

        # --- ATR ---
        atr = self._compute_atr(df, self.atr_period)

        # --- 信号生成 ---
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

            # === 空仓：顺势回调入场 ===
            if position == 0:
                # 成交量过滤
                if self.volume_threshold is not None and volume_ratio is not None:
                    if volume_ratio[i] < self.volume_threshold:
                        continue

                price_above_long = price > long_ma[i]
                price_below_long = price < long_ma[i]

                # 做多：上升趋势中，价格回调到短 EMA 附近
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

                # 做空：下降趋势中，价格反弹到短 EMA 附近
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


# ---------------------------------------------------------------------------
# 策略：混合均值回归 + 动量（Q-RSI 思想适配 5m）
# ---------------------------------------------------------------------------


class HybridMeanRevMomentumStrategy:
    """
    混合均值回归 + 动量策略。
    核心逻辑（Q-RSI 思想）：
    1. 均值回归：RSI 进入极端区域（<25 超卖, >75 超买）
    2. 动量过滤：价格仍在短期 EMA 趋势方向一侧
       - 做多：RSI 超卖 BUT 价格 > EMA（趋势未破，不是自由落体）
       - 做空：RSI 超买 BUT 价格 < EMA（趋势已转，不是疯狂上涨）
    3. 出场：ATR 追踪止损 + 均线反转 + 时间退出
    """

    def __init__(
        self,
        rsi_period=14,
        rsi_low=25,
        rsi_high=75,
        ma_period=20,
        atr_period=14,
        atr_multiplier=2.0,
        max_hold_bars=24,
        enable_short=True,
        take_profit_pct=0.03,
        stop_loss_pct=0.02,
        ema_tolerance=0.005,
    ):
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
        self.ema_tolerance = ema_tolerance
        # 兼容实盘脚本所需的属性
        self.window = max(rsi_period, ma_period, atr_period)
        self.std_dev = 2.0

    def _compute_rsi(self, close, period):
        n = len(close)
        returns = np.zeros(n, dtype=np.float32)
        returns[1:] = (close[1:] - close[:-1]) / close[:-1]
        gains = np.where(returns > 0, returns, 0.0)
        losses = np.where(returns < 0, -returns, 0.0)
        avg_gain = np.zeros(n, dtype=np.float32)
        avg_loss = np.zeros(n, dtype=np.float32)
        if n > period:
            avg_gain[period] = np.mean(gains[1 : period + 1])
            avg_loss[period] = np.mean(losses[1 : period + 1])
            for i in range(period + 1, n):
                avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
                avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period
        rsi = np.full(n, 50.0, dtype=np.float32)
        for i in range(period, n):
            if avg_loss[i] == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100 - (100 / (1 + rs))
        return rsi

    def _compute_atr(self, df, period):
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]
        atr = np.zeros(len(tr))
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def generate_signals(self, df, enable_short=False):
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # --- RSI ---
        rsi = self._compute_rsi(close, self.rsi_period)

        # --- EMA（动量过滤用）---
        ema_fast = _ema(close, self.ma_period)
        ema_slow = _ema(close, self.ma_period * 2)

        # --- ATR ---
        atr = self._compute_atr(df, self.atr_period)

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

                # 做多：RSI 从超卖区回升 + 价格在短期均线附近（宽松 EMA 容差）
                long_cross = (
                    prev_rsi < self.rsi_low
                    and rsi[i] >= self.rsi_low
                    and price > ema_fast[i] * (1 - self.ema_tolerance)
                )
                if long_cross:
                    signals[i] = 2
                    position = 1
                    entry_price = price
                    entry_bar = i
                    highest_after_entry = high[i]
                    continue

                # 做空：RSI 从超买区回落 + 价格在短期均线附近（宽松 EMA 容差）
                short_cross = (
                    self.enable_short
                    and prev_rsi > self.rsi_high
                    and rsi[i] <= self.rsi_high
                    and price < ema_fast[i] * (1 + self.ema_tolerance)
                )
                if short_cross:
                    signals[i] = 3
                    position = -1
                    entry_price = price
                    entry_bar = i
                    lowest_after_entry = low[i]
                    continue

        return signals


# ---------------------------------------------------------------------------
# 策略：市场状态自适应混合（ADX 判市，震荡=RSI均值回归，趋势=EMA趋势跟随）
# ---------------------------------------------------------------------------


class AdaptiveHybridStrategy:
    """
    市场状态自适应混合策略。
    ADX 判市：
    - 震荡市（ADX <= adx_threshold）：RSI 交叉均值回归
    - 趋势市（ADX >  adx_threshold）：EMA 回调趋势跟随
    """

    def __init__(
        self,
        rsi_period=14,
        rsi_low=30,
        rsi_high=70,
        ma_period=20,
        trend_long_ma=100,
        trend_pull_ma=20,
        adx_period=14,
        adx_threshold=25,
        atr_period=14,
        atr_multiplier=2.0,
        max_hold_bars=24,
        enable_short=True,
        take_profit_pct=0.03,
        stop_loss_pct=0.02,
        ema_tolerance=0.0,
        use_volume_filter=True,
        volume_threshold=0.5,
    ):
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
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.ema_tolerance = ema_tolerance
        self.use_volume_filter = use_volume_filter
        self.volume_threshold = volume_threshold
        # 兼容实盘脚本所需的属性
        self.window = max(
            rsi_period, ma_period, adx_period * 2, trend_long_ma, trend_pull_ma, atr_period
        )
        self.std_dev = 2.0

    def _compute_rsi(self, close, period):
        n = len(close)
        returns = np.zeros(n, dtype=np.float32)
        returns[1:] = (close[1:] - close[:-1]) / close[:-1]
        gains = np.where(returns > 0, returns, 0.0)
        losses = np.where(returns < 0, -returns, 0.0)
        avg_gain = np.zeros(n, dtype=np.float32)
        avg_loss = np.zeros(n, dtype=np.float32)
        if n > period:
            avg_gain[period] = np.mean(gains[1 : period + 1])
            avg_loss[period] = np.mean(losses[1 : period + 1])
            for i in range(period + 1, n):
                avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
                avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period
        rsi = np.full(n, 50.0, dtype=np.float32)
        for i in range(period, n):
            if avg_loss[i] == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100 - (100 / (1 + rs))
        return rsi

    def _compute_adx(self, df, period=14):
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        n = len(high)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0
        atr_adx = self._compute_atr(df, period)
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        for i in range(period, n):
            if atr_adx[i] > 0:
                plus_di[i] = 100 * np.mean(plus_dm[i - period + 1 : i + 1]) / atr_adx[i]
                minus_di[i] = 100 * np.mean(minus_dm[i - period + 1 : i + 1]) / atr_adx[i]
        dx = np.zeros(n)
        for i in range(period, n):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum
        adx = np.zeros(n)
        adx[period * 2 - 1] = np.mean(dx[period : period * 2])
        for i in range(period * 2, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
        return adx

    def _compute_atr(self, df, period):
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]
        atr = np.zeros(len(tr))
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def generate_signals(self, df):
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        n = len(close)

        # --- RSI（震荡市用）---
        rsi = self._compute_rsi(close, self.rsi_period)

        # --- EMA（震荡市动量过滤 + 趋势市回调入场）---
        ema_fast = _ema(close, self.ma_period)
        ema_slow = _ema(close, self.ma_period * 2)

        # --- 趋势市 EMA ---
        trend_long = _ema(close, self.trend_long_ma)
        trend_pull = _ema(close, self.trend_pull_ma)

        # --- ADX（市场状态判定）---
        adx = self._compute_adx(df, self.adx_period)

        # --- ATR ---
        atr = self._compute_atr(df, self.atr_period)

        # --- Volume filter ---
        vol_ratio = None
        if self.use_volume_filter and "volume" in df.columns:
            vol = df["volume"].values.astype(float)
            vol_ma = pd.Series(vol).rolling(window=20, min_periods=20).mean().values
            vol_ratio = np.where(vol_ma > 0, vol / vol_ma, 1.0)

        # --- 信号生成 ---
        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0
        lowest_after_entry = float("inf")

        min_idx = max(
            self.rsi_period,
            self.ma_period,
            self.adx_period * 2,
            self.trend_long_ma,
            self.trend_pull_ma,
            self.atr_period,
        )

        for i in range(min_idx, n):
            price = close[i]

            is_uptrend = ema_fast[i] > ema_slow[i]
            is_downtrend = ema_fast[i] < ema_slow[i]
            vol_ok = vol_ratio is None or vol_ratio[i] >= self.volume_threshold

            # === 持仓管理 ===
            if position == 1:
                if high[i] > highest_after_entry:
                    highest_after_entry = high[i]

                # 固定止盈
                if self.take_profit_pct > 0 and price >= entry_price * (1 + self.take_profit_pct):
                    signals[i] = 0
                    position = 0
                    continue
                # 固定止损
                if self.stop_loss_pct > 0 and price <= entry_price * (1 - self.stop_loss_pct):
                    signals[i] = 0
                    position = 0
                    continue

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

                # 固定止盈
                if self.take_profit_pct > 0 and price <= entry_price * (1 - self.take_profit_pct):
                    signals[i] = 0
                    position = 0
                    continue
                # 固定止损
                if self.stop_loss_pct > 0 and price >= entry_price * (1 + self.stop_loss_pct):
                    signals[i] = 0
                    position = 0
                    continue

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

            # === 空仓：根据市场状态选择入场模式 ===
            if position == 0:
                regime_trending = adx[i] > self.adx_threshold
                prev_rsi = rsi[i - 1]

                if regime_trending:
                    # 强趋势市：只顺势交易，用长期EMA过滤（带容差）
                    if is_uptrend:
                        long_cross = (
                            prev_rsi < self.rsi_low
                            and rsi[i] >= self.rsi_low
                            and price > trend_long[i] * (1 - self.ema_tolerance)
                            and vol_ok
                        )
                        if long_cross:
                            signals[i] = 2
                            position = 1
                            entry_price = price
                            entry_bar = i
                            highest_after_entry = high[i]
                            continue
                    elif is_downtrend and self.enable_short:
                        short_cross = (
                            prev_rsi > self.rsi_high
                            and rsi[i] <= self.rsi_high
                            and price < trend_long[i] * (1 + self.ema_tolerance)
                            and vol_ok
                        )
                        if short_cross:
                            signals[i] = 3
                            position = -1
                            entry_price = price
                            entry_bar = i
                            lowest_after_entry = low[i]
                            continue
                else:
                    # 震荡市：RSI 双向均值回归（带成交量确认）
                    long_cross = prev_rsi < self.rsi_low and rsi[i] >= self.rsi_low
                    if long_cross and vol_ok:
                        signals[i] = 2
                        position = 1
                        entry_price = price
                        entry_bar = i
                        highest_after_entry = high[i]
                        continue

                    if (
                        self.enable_short
                        and prev_rsi > self.rsi_high
                        and rsi[i] <= self.rsi_high
                        and vol_ok
                    ):
                        signals[i] = 3
                        position = -1
                        entry_price = price
                        entry_bar = i
                        lowest_after_entry = low[i]
                        continue

        return signals


# ---------------------------------------------------------------------------
# 策略：市场状态动态选择器（震荡市=RSI均值回归，趋势市=EMA趋势跟随）
# ---------------------------------------------------------------------------


class RegimeStrategy:
    """
    基于实时ADX动态切换子策略：
    - ADX <= adx_threshold (震荡市): HybridMeanRevMomentumStrategy (RSI均值回归)
    - ADX >  adx_threshold (趋势市): TrendFollowStrategy (EMA趋势跟随回调)
    """

    def __init__(
        self, ranging_params=None, trending_params=None, enable_short=True, adx_threshold=25
    ):
        self.adx_threshold = adx_threshold
        self.enable_short = enable_short

        # 震荡市子策略：RSI均值回归+动量
        rp = ranging_params or {}
        self.ranging = HybridMeanRevMomentumStrategy(
            rsi_period=rp.get("rsi_period", 14),
            rsi_low=rp.get("rsi_low", 30),
            rsi_high=rp.get("rsi_high", 70),
            ma_period=rp.get("ma_period", 20),
            atr_period=rp.get("atr_period", 14),
            atr_multiplier=rp.get("atr_multiplier", 2.0),
            max_hold_bars=rp.get("max_hold_bars", 24),
            enable_short=enable_short,
        )

        # 趋势市子策略：EMA趋势跟随
        tp = trending_params or {}
        self.trending = TrendFollowStrategy(
            long_ma_period=tp.get("long_ma_period", 100),
            pull_ma_period=tp.get("pull_ma_period", 20),
            atr_period=tp.get("atr_period", 14),
            atr_multiplier=tp.get("atr_multiplier", 2.0),
            max_hold_bars=tp.get("max_hold_bars", 24),
            entry_zone=tp.get("entry_zone", 0.001),
            enable_short=enable_short,
        )

        self.window = max(self.ranging.window, self.trending.window)
        self._last_regime = "unknown"
        self._last_adx = 0.0

    @property
    def current_regime(self):
        return self._last_regime

    @property
    def current_adx(self):
        return self._last_adx

    def _compute_adx_simple(self, df, period=14):
        """简版 ADX 计算（只用 close 价格估算，避免高精度需求）"""
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        n = len(high)

        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0

        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]

        atr_val = np.zeros(n)
        atr_val[period - 1] = np.mean(tr[:period])
        for i in range(period, n):
            atr_val[i] = (atr_val[i - 1] * (period - 1) + tr[i]) / period

        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        for i in range(period, n):
            if atr_val[i] > 0:
                plus_di[i] = 100 * np.mean(plus_dm[i - period + 1 : i + 1]) / atr_val[i]
                minus_di[i] = 100 * np.mean(minus_dm[i - period + 1 : i + 1]) / atr_val[i]

        dx = np.zeros(n)
        for i in range(period, n):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

        adx = np.zeros(n)
        adx[period * 2 - 1] = np.mean(dx[period : period * 2])
        for i in range(period * 2, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return adx

    def generate_signals(self, df, enable_short=False):
        n = len(df)
        # 计算当前 ADX 值
        adx = self._compute_adx_simple(df)
        adx_now = float(adx[-1]) if n > 0 else 0.0
        self._last_adx = adx_now

        if adx_now > self.adx_threshold:
            self._last_regime = "trending"
            return self.trending.generate_signals(df)
        else:
            self._last_regime = "ranging"
            return self.ranging.generate_signals(df, enable_short=enable_short)


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
        years = n_steps / (288 * 365)
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
            return (
                0.0,
                {
                    "total_return": 0,
                    "annualized_return": 0,
                    "annualized_vol": 0,
                    "sharpe_ratio": 0,
                    "max_drawdown": -0.99,
                    "win_rate": 0,
                },
                [],
            )

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

        # 回撤评分：线性评分，-20%回撤得0分，0回撤得1分（单一惩罚）
        dd_score = (
            max(0, 1 + metrics["max_drawdown"] / 0.20) if metrics["max_drawdown"] < 0 else 1.0
        )

        # 最低交易量门槛：少于10笔交易小幅惩罚
        min_trade_penalty = min(1.0, n_trades / 10.0) if n_trades < 10 else 1.0

        # 限制各项指标范围，防止异常值
        sharpe_clamped = max(0, min(5.0, metrics["sharpe_ratio"]))
        return_clamped = max(0, min(2.0, metrics["total_return"]))  # 最高200%
        win_rate_clamped = max(0, min(1.0, metrics["win_rate"]))

        score = (
            sharpe_clamped * 0.35  # 夏普权重：质量优先
            + return_clamped * 0.20  # 收益权重
            + win_rate_clamped * 0.15  # 胜率权重
            + dd_score * 0.20  # 回撤权重（单一路径）
            + min(1.0, n_trades / 20.0) * 0.15  # 交易次数权重提高，20笔满分
            + min_trade_penalty * 0.05  # 最低交易惩罚
        )

        return score, metrics, trades


def scalp_evaluate(signals, prices, evaluator, min_trades=50):
    """高频策略专用评分函数。奖励交易量、一致性和适度收益。"""
    equity, trades = evaluator.simulate(signals, prices)

    if len(equity) == 0 or not np.all(np.isfinite(equity)):
        return (
            0.0,
            {
                "total_return": 0,
                "annualized_return": 0,
                "annualized_vol": 0,
                "sharpe_ratio": 0,
                "max_drawdown": -0.99,
                "win_rate": 0,
            },
            [],
        )

    metrics = evaluator.compute_metrics(equity, trades)

    if not np.isfinite(metrics["sharpe_ratio"]) or not np.isfinite(metrics["total_return"]):
        return 0.0, metrics, trades
    if metrics["max_drawdown"] < -0.30:
        return 0.0, metrics, trades
    if equity[-1] < evaluator.initial_capital * 0.7:
        return 0.0, metrics, trades

    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n_trades = len(trade_pnls)

    if n_trades < min_trades:
        return 0.0, metrics, trades

    # 交易数得分（30%）：100笔满分
    trade_count_score = min(1.0, n_trades / 100.0)

    # 一致性得分（25%）：每笔交易PnL的均值/标准差
    pnls = [t["pnl"] for t in trade_pnls]
    pnl_mean = np.mean(pnls)
    pnl_std = np.std(pnls)
    consistency = pnl_mean / pnl_std if pnl_std > 0 else 0
    consistency_score = max(0, min(1.0, consistency / 2.0))

    # 胜率得分（20%）：40%起算，80%满分
    win_rate_score = max(0, min(1.0, (metrics["win_rate"] - 0.40) / 0.40))

    # 收益得分（15%）：5%收益满分
    return_score = max(0, min(1.0, metrics["total_return"] / 0.05))

    # 回撤得分（10%）
    dd_score = max(0, 1 + metrics["max_drawdown"]) if metrics["max_drawdown"] < 0 else 1.0

    score = (
        trade_count_score * 0.30
        + consistency_score * 0.25
        + win_rate_score * 0.20
        + return_score * 0.15
        + dd_score * 0.10
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
                                window=window,
                                std_dev=std_dev,
                                atr_multiplier=atr_mult,
                                max_hold_bars=max_hold,
                                rsi_threshold=rsi_th,
                                entry_zone=ez,
                            )
                            signals = strategy.generate_signals(val_df, enable_short=True)
                            prices = val_df["close"].values

                            valid_signals = signals[window * 2 :]
                            valid_prices = prices[window * 2 :]
                            valid_df = val_df.iloc[window * 2 :].reset_index(drop=True)

                            if len(valid_signals) < 50:
                                continue

                            score, metrics, trades = evaluator.evaluate(
                                valid_signals, valid_prices, valid_df
                            )
                            n_trades = len([t for t in trades if t.get("pnl") is not None])

                            tried += 1
                            if tried % 50 == 0 or score > best_score:
                                print(
                                    f"  [{tried}/{total_combos}] w={window} std={std_dev} atr={atr_mult} hold={max_hold} rsi={rsi_th} ez={ez:.1f} | "
                                    f"评分={score:.4f} | 收益={metrics['total_return'] * 100:.2f}% | 夏普={metrics['sharpe_ratio']:.2f} | DD={metrics['max_drawdown'] * 100:.1f}% | 交易={n_trades}"
                                )

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
        ez = best_params.get("entry_zone", 0.0)
        print(
            f"  最优核心参数: w={best_params['window']} std={best_params['std_dev']} "
            f"atr={best_params['atr_multiplier']} hold={best_params['max_hold_bars']} rsi={best_params['rsi_threshold']} ez={ez:.1f}"
        )
        print(f"  评分={best_score:.4f} 收益={best_metrics['total_return'] * 100:.2f}%")

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
        {
            "use_adx": True,
            "adx_threshold": 25,
            "use_volume": True,
            "volume_threshold": 1.2,
            "use_macd": True,
            "macd_confirm_mode": "direction",
        },
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
        {
            "use_rsi_divergence": True,
            "rsi_divergence_lookback": 5,
            "use_macd_divergence": True,
            "macd_divergence_lookback": 5,
        },
        # P1+P3: MACD 方向 + RSI 背离
        {
            "use_macd": True,
            "macd_confirm_mode": "direction",
            "use_rsi_divergence": True,
            "rsi_divergence_lookback": 5,
        },
        # 全量组合
        {
            "use_adx": True,
            "adx_threshold": 25,
            "use_volume": True,
            "volume_threshold": 1.2,
            "use_macd": True,
            "macd_confirm_mode": "direction",
        },
        {
            "use_adx": True,
            "adx_threshold": 25,
            "use_volume": True,
            "volume_threshold": 1.2,
            "use_macd": True,
            "macd_confirm_mode": "direction",
            "use_ma_cross": True,
        },
        {
            "use_adx": True,
            "adx_threshold": 25,
            "use_mfi": True,
            "mfi_threshold": 25,
            "use_macd": True,
            "macd_confirm_mode": "direction",
        },
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
        {
            "use_volume_spike": True,
            "volume_spike_threshold": 1.5,
            "use_trend_filter": True,
            "trend_window": 50,
        },
        # P5: VWAP + 趋势过滤
        {"use_vwap": True, "vwap_period": 20, "use_trend_filter": True, "trend_window": 50},
        # P5: OBV + Volume Spike
        {
            "use_obv_trend": True,
            "obv_ma_period": 20,
            "use_volume_spike": True,
            "volume_spike_threshold": 1.5,
        },
        # P5: OBV + VWAP
        {"use_obv_trend": True, "obv_ma_period": 20, "use_vwap": True, "vwap_period": 20},
        # P5: 全部成交量因子
        {
            "use_obv_trend": True,
            "obv_ma_period": 20,
            "use_volume_spike": True,
            "volume_spike_threshold": 1.5,
            "use_vwap": True,
            "vwap_period": 20,
        },
        # P5 + P4: 全部成交量 + 趋势过滤
        {
            "use_obv_trend": True,
            "obv_ma_period": 20,
            "use_volume_spike": True,
            "volume_spike_threshold": 1.5,
            "use_vwap": True,
            "vwap_period": 20,
            "use_trend_filter": True,
            "trend_window": 50,
        },
        # P5 + P0: OBV + Volume
        {"use_obv_trend": True, "obv_ma_period": 20, "use_volume": True, "volume_threshold": 1.0},
        # P5 + P0: VWAP + Volume
        {"use_vwap": True, "vwap_period": 20, "use_volume": True, "volume_threshold": 1.0},
        # P6: 高级别 MACD 趋势确认（策略.md 方法9）
        {"use_htf_macd": True},
        {"use_htf_macd": True, "use_trend_filter": True, "trend_window": 50},
        {"use_htf_macd": True, "use_volume": True, "volume_threshold": 1.0},
        {"use_htf_macd": True, "use_obv_trend": True, "obv_ma_period": 20},
        {
            "use_htf_macd": True,
            "use_trend_filter": True,
            "trend_window": 50,
            "use_volume": True,
            "volume_threshold": 1.0,
        },
        {"use_htf_macd": True, "use_trend_filter": True, "trend_window": 25},
        # P7: 多因子共振评分（策略.md 核心规则，需搭配实际因子）
        {
            "use_resonance": True,
            "resonance_min_score": 2,
            "use_macd": True,
            "macd_confirm_mode": "direction",
        },
        {
            "use_resonance": True,
            "resonance_min_score": 2,
            "use_volume": True,
            "volume_threshold": 1.0,
        },
        {
            "use_resonance": True,
            "resonance_min_score": 2,
            "use_obv_trend": True,
            "obv_ma_period": 20,
        },
        {
            "use_resonance": True,
            "resonance_min_score": 3,
            "use_macd": True,
            "macd_confirm_mode": "direction",
            "use_volume": True,
            "volume_threshold": 1.0,
        },
        {
            "use_resonance": True,
            "resonance_min_score": 3,
            "use_macd": True,
            "macd_confirm_mode": "direction",
            "use_obv_trend": True,
            "obv_ma_period": 20,
        },
        {
            "use_resonance": True,
            "resonance_min_score": 3,
            "use_volume": True,
            "volume_threshold": 1.0,
            "use_obv_trend": True,
            "obv_ma_period": 20,
        },
        {"use_resonance": True, "resonance_min_score": 2, "use_htf_macd": True},
        {
            "use_resonance": True,
            "resonance_min_score": 2,
            "use_htf_macd": True,
            "use_trend_filter": True,
            "trend_window": 50,
        },
        {
            "use_resonance": True,
            "resonance_min_score": 3,
            "use_macd": True,
            "macd_confirm_mode": "direction",
            "use_volume": True,
            "volume_threshold": 1.0,
            "use_obv_trend": True,
            "obv_ma_period": 20,
        },
        {
            "use_resonance": True,
            "resonance_min_score": 4,
            "use_macd": True,
            "macd_confirm_mode": "direction",
            "use_volume": True,
            "volume_threshold": 1.0,
            "use_obv_trend": True,
            "obv_ma_period": 20,
            "use_adx": True,
            "adx_threshold": 25,
        },
        # P6 + P5 组合
        {"use_htf_macd": True, "use_resonance": True, "resonance_min_score": 3},
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
        valid_signals = signals[window * 2 :]
        valid_prices = prices[window * 2 :]
        valid_df = val_df.iloc[window * 2 :].reset_index(drop=True)

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
            print(
                f"  [{stage2_tried}/{len(indicator_combos)}] {desc:40s} | "
                f"评分={score:.4f} | 收益={metrics['total_return'] * 100:.2f}% | 夏普={metrics['sharpe_ratio']:.2f} | DD={metrics['max_drawdown'] * 100:.1f}% | 交易={n_trades} <<< NEW BEST"
            )
        elif stage2_tried % 10 == 0:
            print(
                f"  [{stage2_tried}/{len(indicator_combos)}] {desc:40s} | "
                f"评分={score:.4f} | 收益={metrics['total_return'] * 100:.2f}% | 交易={n_trades}"
            )

    stage2_time = time.time() - t2_start
    print(f"\nStage 2 完成: {stage2_tried}/{len(indicator_combos)} 组合, 耗时 {stage2_time:.1f}s")

    # 输出活跃指标信息
    active = {k: v for k, v in best_params.items() if k.startswith("use_") and v is True}
    if active:
        indicator_str = ", ".join(
            f"{k}={v}"
            for k, v in best_params.items()
            if k.startswith("use_")
            or k
            in (
                "adx_threshold",
                "volume_threshold",
                "macd_confirm_mode",
                "mfi_threshold",
                "mfi_period",
                "stoch_threshold",
                "stoch_period",
            )
        )
        print(f"  活跃指标: {indicator_str}")
    else:
        print("  无额外指标（纯均线交叉策略）")

    print()
    return best_params, best_score, best_metrics


# ---------------------------------------------------------------------------
# 高频剥头皮参数搜索
# ---------------------------------------------------------------------------


def scalp_grid_search(df, time_budget=TIME_BUDGET):
    """
    高频剥头皮策略参数搜索。
    Stage 1: 搜索核心参数（window, std_dev, TP, SL, hold）
    Stage 2: 搜索过滤器组合
    """
    n = len(df)
    train_size = int(n * 0.9)
    train_df = df.iloc[:train_size].reset_index(drop=True)
    val_df = df.iloc[train_size:].reset_index(drop=True)
    val_prices = val_df["close"].values.astype(float)

    evaluator = StrategyEvaluator()

    # Stage 1: 核心参数
    stage1_grid = {
        "window": [8, 10, 12, 15, 20],
        "std_dev": [1.0, 1.2, 1.5, 2.0],
        "take_profit_pct": [0.005, 0.008, 0.012, 0.015, 0.020, 0.030],
        "stop_loss_pct": [0.003, 0.004, 0.005, 0.008],
        "max_hold_bars": [6, 12, 18, 24, 36, 48],
        "rsi_period": [7, 14],
    }

    total_combos = 1
    for v in stage1_grid.values():
        total_combos *= len(v)
    print(f"  参数空间: {total_combos}, 时间预算: {time_budget}s")
    print()

    best_s1_score = -1
    best_s1_params = {}
    best_s1_desc = ""

    t1_start = time.time()
    stage1_time = time_budget * 0.5
    tried = 0

    for w in stage1_grid["window"]:
        for sd in stage1_grid["std_dev"]:
            for tp in stage1_grid["take_profit_pct"]:
                for sl in stage1_grid["stop_loss_pct"]:
                    for hold in stage1_grid["max_hold_bars"]:
                        for rsi_p in stage1_grid["rsi_period"]:
                            if time.time() - t1_start > stage1_time:
                                break
                            tried += 1

                            strategy = ScalpStrategy(
                                window=w,
                                std_dev=sd,
                                take_profit_pct=tp,
                                stop_loss_pct=sl,
                                max_hold_bars=hold,
                                rsi_period=rsi_p,
                            )
                            try:
                                signals = strategy.generate_signals(val_df, enable_short=True)
                                score, metrics, trades = scalp_evaluate(
                                    signals, val_prices, evaluator
                                )
                            except Exception:
                                score = 0
                                metrics = {}
                                trades = []

                            trade_pnls = [t for t in trades if t.get("pnl") is not None]
                            n_trades = len(trade_pnls)
                            ret = metrics.get("total_return", 0) * 100
                            sharpe = metrics.get("sharpe_ratio", 0)
                            dd = metrics.get("max_drawdown", 0) * 100
                            wr = metrics.get("win_rate", 0) * 100

                            is_best = score > best_s1_score
                            if is_best:
                                best_s1_score = score
                                best_s1_params = {
                                    "window": w,
                                    "std_dev": sd,
                                    "take_profit_pct": tp,
                                    "stop_loss_pct": sl,
                                    "max_hold_bars": hold,
                                    "rsi_period": rsi_p,
                                }
                                best_s1_desc = (
                                    f"w={w} std={sd} tp={tp} sl={sl} hold={hold} rsi={rsi_p}"
                                )

                            if tried % 100 == 0 or is_best:
                                desc = f"w={w} std={sd} tp={tp:.3f} sl={sl:.3f} hold={hold} rsi={rsi_p}"
                                best_tag = " <<< NEW BEST" if is_best else ""
                                print(
                                    f"  [{tried}/{total_combos}] {desc:55s} | "
                                    f"score={score:.4f} | ret={ret:+.2f}% | "
                                    f"sharpe={sharpe:.2f} | DD={dd:+.1f}% | "
                                    f"WR={wr:.0f}% | trades={n_trades}{best_tag}"
                                )
                        else:
                            continue
                        break
                    else:
                        continue
                    break
                else:
                    continue
                break
            else:
                continue
            break
        else:
            continue
        break

    s1_time = time.time() - t1_start
    print(f"\nStage 1 完成: {tried}/{total_combos} 组合, 耗时 {s1_time:.1f}s")
    print(f"  最佳参数: {best_s1_desc}")
    print(f"  得分={best_s1_score:.4f}")

    # Stage 2: 过滤器组合
    stage2_combos = [
        {},
        {"use_volume_filter": True, "volume_threshold": 0.8},
        {"use_volume_filter": True, "volume_threshold": 1.0},
        {"use_volume_filter": True, "volume_threshold": 1.2},
        {"rsi_extreme_low": 25, "rsi_extreme_high": 75},
        {"rsi_extreme_low": 30, "rsi_extreme_high": 70},
        {
            "use_volume_filter": True,
            "volume_threshold": 0.8,
            "rsi_extreme_low": 25,
            "rsi_extreme_high": 75,
        },
        {
            "use_volume_filter": True,
            "volume_threshold": 1.0,
            "rsi_extreme_low": 30,
            "rsi_extreme_high": 70,
        },
        # RSI 入场要求（策略2.md 策略9：RSI<30做多 / RSI>70做空）
        {"use_rsi_entry": True, "rsi_entry_low": 30, "rsi_entry_high": 70},
        {"use_rsi_entry": True, "rsi_entry_low": 35, "rsi_entry_high": 65},
        {"use_rsi_entry": True, "rsi_entry_low": 40, "rsi_entry_high": 60},
        {
            "use_rsi_entry": True,
            "rsi_entry_low": 30,
            "rsi_entry_high": 70,
            "use_volume_filter": True,
            "volume_threshold": 0.8,
        },
        {
            "use_rsi_entry": True,
            "rsi_entry_low": 35,
            "rsi_entry_high": 65,
            "use_volume_filter": True,
            "volume_threshold": 1.0,
        },
        # 趋势对齐（策略2.md 策略7：趋势方向上的均值回归）
        {"use_trend_align": True, "trend_ma_period": 50},
        {"use_trend_align": True, "trend_ma_period": 100},
        {
            "use_trend_align": True,
            "trend_ma_period": 50,
            "use_volume_filter": True,
            "volume_threshold": 0.8,
        },
        {
            "use_trend_align": True,
            "trend_ma_period": 100,
            "use_volume_filter": True,
            "volume_threshold": 0.8,
        },
        # 趋势对齐 + RSI 入场
        {
            "use_trend_align": True,
            "trend_ma_period": 50,
            "use_rsi_entry": True,
            "rsi_entry_low": 35,
            "rsi_entry_high": 65,
        },
        {
            "use_trend_align": True,
            "trend_ma_period": 100,
            "use_rsi_entry": True,
            "rsi_entry_low": 35,
            "rsi_entry_high": 65,
        },
        # 时段过滤（策略2.md：欧美开盘时段胜率提升15%）
        {"use_session_filter": True, "session_start": 13, "session_end": 21},
        {"use_session_filter": True, "session_start": 13, "session_end": 23},
        {"use_session_filter": True, "session_start": 8, "session_end": 22},
        {
            "use_session_filter": True,
            "session_start": 13,
            "session_end": 21,
            "use_trend_align": True,
            "trend_ma_period": 50,
        },
        {
            "use_session_filter": True,
            "session_start": 13,
            "session_end": 21,
            "use_trend_align": True,
            "trend_ma_period": 100,
        },
        # 趋势对齐 + RSI + 时段（三重过滤）
        {
            "use_trend_align": True,
            "trend_ma_period": 50,
            "use_rsi_entry": True,
            "rsi_entry_low": 35,
            "rsi_entry_high": 65,
            "use_session_filter": True,
            "session_start": 13,
            "session_end": 21,
        },
        {
            "use_trend_align": True,
            "trend_ma_period": 100,
            "use_session_filter": True,
            "session_start": 13,
            "session_end": 21,
        },
    ]

    print(f"\nStage 2: 过滤器搜索 ({len(stage2_combos)} 种)")
    best_s2_score = best_s1_score
    best_s2_params = best_s1_params.copy()
    best_s2_desc = "无过滤器"

    for idx, combo in enumerate(stage2_combos):
        params = {**best_s1_params, **combo}
        strategy = ScalpStrategy(**params)
        try:
            signals = strategy.generate_signals(val_df, enable_short=True)
            score, metrics, trades = scalp_evaluate(signals, val_prices, evaluator)
        except Exception:
            score = 0
            metrics = {}
            trades = []

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)
        ret = metrics.get("total_return", 0) * 100
        sharpe = metrics.get("sharpe_ratio", 0)
        wr = metrics.get("win_rate", 0) * 100

        is_best = score > best_s2_score
        if is_best:
            best_s2_score = score
            best_s2_params = params.copy()
            desc_parts = [k for k in combo if k.startswith("use_") or k in ("rsi_extreme_low",)]
            best_s2_desc = str(combo) if combo else "无过滤器"

        if (idx + 1) % 2 == 0 or is_best:
            combo_str = str(combo)[:40] if combo else "无过滤器"
            best_tag = " <<< NEW BEST" if is_best else ""
            print(
                f"  [{idx + 1}/{len(stage2_combos)}] {combo_str:40s} | "
                f"score={score:.4f} | ret={ret:+.2f}% | "
                f"WR={wr:.0f}% | trades={n_trades}{best_tag}"
            )

    print("\nStage 2 完成")
    print(f"  活跃指标: {best_s2_desc}")

    return best_s2_score, best_s2_params, metrics, trades


# ---------------------------------------------------------------------------
# 纯价格行为参数搜索（无因子约束）
# ---------------------------------------------------------------------------


def pure_grid_search(df, time_budget=TIME_BUDGET):
    """
    纯价格行为策略全网格搜索。无因子约束，搜索所有核心参数组合。
    单阶段全量搜索（无 Stage 1/2 区分，因为没有因子需要搜索）。
    """
    n = len(df)
    train_size = int(n * 0.9)
    val_df = df.iloc[train_size:].reset_index(drop=True)
    val_prices = val_df["close"].values.astype(float)

    evaluator = StrategyEvaluator()

    grid = {
        "window": [12, 15, 20, 25, 30],
        "std_dev": [1.5, 2.0, 2.5, 3.0],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 18, 24, 36, 48],
        "entry_zone": [0.0, 0.5, 1.0],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("纯价格行为策略参数搜索 (无因子约束)")
    print(f"  验证集: {len(val_df)} 条K线, 参数组合: {total_combos}, 时间预算: {time_budget:.0f}s")
    print()

    best_score = -float("inf")
    best_params = None
    best_metrics = None

    t_start = time.time()
    tried = 0

    for window in grid["window"]:
        for std_dev in grid["std_dev"]:
            for atr_p in grid["atr_period"]:
                for atr_m in grid["atr_multiplier"]:
                    for max_hold in grid["max_hold_bars"]:
                        for ez in grid["entry_zone"]:
                            if time.time() - t_start > time_budget * 0.9:
                                print("时间预算即将耗尽，提前结束")
                                break

                            tried += 1

                            strategy = PureActionStrategy(
                                window=window,
                                std_dev=std_dev,
                                atr_period=atr_p,
                                atr_multiplier=atr_m,
                                max_hold_bars=max_hold,
                                entry_zone=ez,
                                enable_short=True,
                            )

                            try:
                                signals = strategy.generate_signals(val_df)
                                score, metrics, trades = evaluator.evaluate(
                                    signals[window * 2 :],
                                    val_prices[window * 2 :],
                                    val_df.iloc[window * 2 :].reset_index(drop=True),
                                )
                            except Exception:
                                score = 0.0
                                metrics = {}
                                trades = []

                            n_trades = len([t for t in trades if t.get("pnl") is not None])

                            is_best = score > best_score
                            if is_best:
                                best_score = score
                                best_params = {
                                    "window": window,
                                    "std_dev": std_dev,
                                    "atr_period": atr_p,
                                    "atr_multiplier": atr_m,
                                    "max_hold_bars": max_hold,
                                    "entry_zone": ez,
                                    "enable_short": True,
                                }
                                best_metrics = metrics

                            if tried % 50 == 0 or is_best:
                                desc = f"w={window} std={std_dev} atr_p={atr_p} atr_m={atr_m} hold={max_hold} ez={ez}"
                                best_tag = " <<< NEW BEST" if is_best else ""
                                ret = metrics.get("total_return", 0) * 100
                                sharpe = metrics.get("sharpe_ratio", 0)
                                dd = metrics.get("max_drawdown", 0) * 100
                                wr = metrics.get("win_rate", 0) * 100
                                print(
                                    f"  [{tried}/{total_combos}] {desc:60s} | "
                                    f"score={score:.4f} | ret={ret:+.2f}% | "
                                    f"sharpe={sharpe:.2f} | DD={dd:+.1f}% | "
                                    f"WR={wr:.0f}% | trades={n_trades}{best_tag}"
                                )

                    if time.time() - t_start > time_budget * 0.9:
                        break

    elapsed = time.time() - t_start
    print(f"\n完成: {tried}/{total_combos} 组合, 耗时 {elapsed:.1f}s")

    if best_params:
        print(
            f"最优参数: w={best_params['window']} std={best_params['std_dev']} "
            f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
            f"hold={best_params['max_hold_bars']} ez={best_params['entry_zone']}"
        )
        print(f"最优评分: {best_score:.4f}")
        print(f"收益率:   {best_metrics['total_return'] * 100:.2f}%")
        print(f"夏普比率: {best_metrics['sharpe_ratio']:.4f}")
        print(f"最大回撤: {best_metrics['max_drawdown'] * 100:.2f}%")
        print(f"胜率:     {best_metrics['win_rate'] * 100:.1f}%")

    return best_params, best_score, best_metrics


# ---------------------------------------------------------------------------
# Walk-Forward 验证搜索（纯价格行为，跨时间窗口稳健性验证）
# ---------------------------------------------------------------------------


def walk_forward_pure_search(df, time_budget=TIME_BUDGET, n_windows=5):
    """
    Walk-Forward 验证的纯价格行为策略搜索。
    将数据按时间顺序切分为多个窗口，在每个窗口的训练段搜索参数，
    在验证段评估，汇总跨窗口表现以选取稳健参数。
    """
    n = len(df)
    seg_size = n // (n_windows + 1)  # +1: 预留最终验证段

    evaluator = StrategyEvaluator()

    # 聚焦搜索空间（基于敏感性分析，排除明确无效的参数）
    grid = {
        "window": [15, 20, 25],
        "std_dev": [2.0, 2.5, 3.0],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5],
        "max_hold_bars": [12, 18, 24],
        "entry_zone": [0.0, 0.5],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("Walk-Forward 验证搜索 (纯价格行为, 无因子约束)")
    print(f"  总数据: {n} 条K线, {n_windows} 个窗口, 每窗口 {seg_size} 条")
    print(f"  参数组合: {total_combos}, 总时间预算: {time_budget:.0f}s")
    print()

    per_window_budget = time_budget * 0.85 / n_windows

    # 存储: 每个窗口的最优参数及其验证表现
    window_champions = []
    all_cross_scores = {}  # param_key -> [scores across windows]

    t_total_start = time.time()

    for w_idx in range(n_windows):
        train_end = (w_idx + 1) * seg_size
        val_start = train_end
        val_end = min(val_start + seg_size, n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        val_df = df.iloc[val_start:val_end].reset_index(drop=True)
        val_prices = val_df["close"].values.astype(float)

        print(f"{'─' * 60}")
        print(f"窗口 {w_idx + 1}/{n_windows}: 训练 [{0}:{train_end}] 验证 [{val_start}:{val_end}]")
        print(f"  训练集: {len(train_df)} 条, 验证集: {len(val_df)} 条")

        # 在训练集上搜索最优参数
        best_score = -float("inf")
        best_params = None
        best_metrics = None
        tried = 0

        t_w_start = time.time()

        for window in grid["window"]:
            for std_dev in grid["std_dev"]:
                for atr_p in grid["atr_period"]:
                    for atr_m in grid["atr_multiplier"]:
                        for max_hold in grid["max_hold_bars"]:
                            for ez in grid["entry_zone"]:
                                if time.time() - t_w_start > per_window_budget:
                                    break
                                tried += 1

                                strategy = PureActionStrategy(
                                    window=window,
                                    std_dev=std_dev,
                                    atr_period=atr_p,
                                    atr_multiplier=atr_m,
                                    max_hold_bars=max_hold,
                                    entry_zone=ez,
                                    enable_short=True,
                                )

                                try:
                                    signals = strategy.generate_signals(val_df)
                                    w2 = window * 2
                                    score, metrics, trades = evaluator.evaluate(
                                        signals[w2:],
                                        val_prices[w2:],
                                        val_df.iloc[w2:].reset_index(drop=True),
                                    )
                                except Exception:
                                    score = 0.0
                                    metrics = {}
                                    trades = []

                                if score > best_score:
                                    best_score = score
                                    best_params = {
                                        "window": window,
                                        "std_dev": std_dev,
                                        "atr_period": atr_p,
                                        "atr_multiplier": atr_m,
                                        "max_hold_bars": max_hold,
                                        "entry_zone": ez,
                                        "enable_short": True,
                                    }
                                    best_metrics = metrics

                            if time.time() - t_w_start > per_window_budget:
                                break

        w_time = time.time() - t_w_start

        if best_params:
            n_trades = len([t for t in (best_metrics and trades or []) if t.get("pnl") is not None])
            print(
                f"  窗口最优: w={best_params['window']} std={best_params['std_dev']} "
                f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
                f"hold={best_params['max_hold_bars']} ez={best_params['entry_zone']}"
            )
            print(
                f"  验证评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
                f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:+.1f}% | "
                f"交易={n_trades} | 耗时={w_time:.1f}s"
            )
            window_champions.append(
                {
                    "window": w_idx,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
            )

            # 用此窗口最优参数评估所有其他窗口的验证集
            p_key = f"w{best_params['window']}_s{best_params['std_dev']}_ap{best_params['atr_period']}_am{best_params['atr_multiplier']}_h{best_params['max_hold_bars']}_ez{best_params['entry_zone']}"
            if p_key not in all_cross_scores:
                all_cross_scores[p_key] = {"params": best_params, "scores": [], "returns": []}

            all_cross_scores[p_key]["scores"].append(best_score)
            all_cross_scores[p_key]["returns"].append(best_metrics.get("total_return", 0))
        else:
            print(f"  未找到有效参数 (耗时 {w_time:.1f}s)")

        if time.time() - t_total_start > time_budget * 0.95:
            print(f"\n总时间预算即将耗尽，提前结束（完成 {w_idx + 1}/{n_windows} 窗口）")
            break

    # =========================================================================
    # 跨窗口分析：找出最稳健的参数
    # =========================================================================
    print(f"\n{'=' * 60}")
    print("跨窗口稳健性分析")
    print(f"{'=' * 60}")

    if not all_cross_scores:
        print("未找到任何有效参数")
        return None, 0, {}

    # 排序：按平均得分（兼顾最低得分作为稳健性惩罚）
    ranked = []
    for p_key, data in all_cross_scores.items():
        scores = data["scores"]
        returns = data["returns"]
        avg_score = np.mean(scores)
        min_score = min(scores)
        avg_return = np.mean(returns)
        n_wins = len(scores)
        # 稳健性评分 = 平均分 * (1 - 变异系数) 鼓励稳定表现
        cv = np.std(scores) / (avg_score + 0.001)
        robustness = avg_score * (1.0 - min(cv, 0.5))
        ranked.append(
            {
                "key": p_key,
                "params": data["params"],
                "avg_score": avg_score,
                "min_score": min_score,
                "robustness": robustness,
                "avg_return": avg_return,
                "n_windows": n_wins,
                "scores": scores,
            }
        )

    ranked.sort(key=lambda x: x["robustness"], reverse=True)

    print(
        f"{'参数':55s} {'窗口数':>5s} {'平均分':>8s} {'最低分':>8s} {'稳健分':>8s} {'均收益':>8s}"
    )
    print("-" * 95)
    for r in ranked[:10]:
        print(
            f"{r['key']:55s} {r['n_windows']:>5d} {r['avg_score']:>8.4f} {r['min_score']:>8.4f} {r['robustness']:>8.4f} {r['avg_return'] * 100:>+7.2f}%"
        )

    # 选取最优
    champion = ranked[0]
    champion_params = champion["params"]

    print(
        f"\n稳健冠军参数: w={champion_params['window']} std={champion_params['std_dev']} "
        f"atr_p={champion_params['atr_period']} atr_m={champion_params['atr_multiplier']} "
        f"hold={champion_params['max_hold_bars']} ez={champion_params['entry_zone']}"
    )
    print(f"跨窗口平均评分: {champion['avg_score']:.4f} (最低: {champion['min_score']:.4f})")
    print(f"跨窗口平均收益: {champion['avg_return'] * 100:+.2f}%")

    # 最终在全部数据上评估冠军参数
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    final_strategy = PureActionStrategy(**champion_params)
    final_signals = final_strategy.generate_signals(df)
    w2 = champion_params["window"] * 2
    final_score, final_metrics, final_trades = evaluator.evaluate(
        final_signals[w2:],
        df["close"].values[w2:],
        df.iloc[w2:].reset_index(drop=True),
    )

    n_trades = len([t for t in final_trades if t.get("pnl") is not None])
    print(
        f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
        f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:+.1f}% | "
        f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
    )

    elapsed = time.time() - t_total_start
    print(f"\nWalk-Forward 搜索完成, 总耗时 {elapsed:.1f}s")

    return champion_params, champion["robustness"], final_metrics


# ---------------------------------------------------------------------------
# Walk-Forward 验证搜索（趋势对齐方案C）
# ---------------------------------------------------------------------------


def walk_forward_trend_search(df, time_budget=TIME_BUDGET, n_windows=5):
    """
    Walk-Forward 验证的趋势对齐策略搜索（方案C）。
    上升趋势只做多，下降趋势只做空，震荡区间双向。
    """
    n = len(df)
    seg_size = n // (n_windows + 1)

    evaluator = StrategyEvaluator()

    grid = {
        "window": [15, 20, 25],
        "std_dev": [2.0, 2.5, 3.0],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5],
        "max_hold_bars": [12, 18, 24],
        "entry_zone": [0.0, 0.5],
        "trend_ma_period": [50, 100, 200],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("Walk-Forward 验证搜索 (趋势对齐方案C)")
    print(f"  总数据: {n} 条K线, {n_windows} 个窗口, 每窗口 {seg_size} 条")
    print(f"  参数组合: {total_combos}, 总时间预算: {time_budget:.0f}s")
    print()

    per_window_budget = time_budget * 0.85 / n_windows
    window_champions = []
    all_cross_scores = {}
    t_total_start = time.time()

    for w_idx in range(n_windows):
        train_end = (w_idx + 1) * seg_size
        val_start = train_end
        val_end = min(val_start + seg_size, n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        val_df = df.iloc[val_start:val_end].reset_index(drop=True)
        val_prices = val_df["close"].values.astype(float)

        print(f"{'─' * 60}")
        print(f"窗口 {w_idx + 1}/{n_windows}: 训练 [{0}:{train_end}] 验证 [{val_start}:{val_end}]")

        best_score = -float("inf")
        best_params = None
        best_metrics = None
        tried = 0
        t_w_start = time.time()

        for window in grid["window"]:
            for std_dev in grid["std_dev"]:
                for atr_p in grid["atr_period"]:
                    for atr_m in grid["atr_multiplier"]:
                        for max_hold in grid["max_hold_bars"]:
                            for ez in grid["entry_zone"]:
                                for trend_ma in grid["trend_ma_period"]:
                                    if time.time() - t_w_start > per_window_budget:
                                        break
                                    tried += 1

                                    strategy = PureActionStrategy(
                                        window=window,
                                        std_dev=std_dev,
                                        atr_period=atr_p,
                                        atr_multiplier=atr_m,
                                        max_hold_bars=max_hold,
                                        entry_zone=ez,
                                        enable_short=True,
                                        trend_ma_period=trend_ma,
                                    )

                                    try:
                                        signals = strategy.generate_signals(val_df)
                                        w2 = window * 2
                                        score, metrics, trades = evaluator.evaluate(
                                            signals[w2:],
                                            val_prices[w2:],
                                            val_df.iloc[w2:].reset_index(drop=True),
                                        )
                                    except Exception:
                                        score = 0.0
                                        metrics = {}
                                        trades = []

                                    if score > best_score:
                                        best_score = score
                                        best_params = {
                                            "window": window,
                                            "std_dev": std_dev,
                                            "atr_period": atr_p,
                                            "atr_multiplier": atr_m,
                                            "max_hold_bars": max_hold,
                                            "entry_zone": ez,
                                            "enable_short": True,
                                            "trend_ma_period": trend_ma,
                                        }
                                        best_metrics = metrics

                                if time.time() - t_w_start > per_window_budget:
                                    break

        w_time = time.time() - t_w_start

        if best_params and best_metrics:
            n_trades = len([t for t in trades if t.get("pnl") is not None])
            print(
                f"  窗口最优: w={best_params['window']} std={best_params['std_dev']} "
                f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
                f"hold={best_params['max_hold_bars']} ez={best_params['entry_zone']} "
                f"trend_ma={best_params['trend_ma_period']}"
            )
            print(
                f"  验证评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
                f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:+.1f}% | "
                f"交易={n_trades} | 耗时={w_time:.1f}s"
            )
            window_champions.append(
                {
                    "window": w_idx,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
            )

            p_key = (
                f"w{best_params['window']}_s{best_params['std_dev']}"
                f"_ap{best_params['atr_period']}_am{best_params['atr_multiplier']}"
                f"_h{best_params['max_hold_bars']}_ez{best_params['entry_zone']}"
                f"_t{best_params['trend_ma_period']}"
            )
            if p_key not in all_cross_scores:
                all_cross_scores[p_key] = {"params": best_params, "scores": [], "returns": []}
            all_cross_scores[p_key]["scores"].append(best_score)
            all_cross_scores[p_key]["returns"].append(best_metrics.get("total_return", 0))
        else:
            print(f"  未找到有效参数 (耗时 {w_time:.1f}s)")

        if time.time() - t_total_start > time_budget * 0.95:
            print(f"\n总时间预算即将耗尽，提前结束（完成 {w_idx + 1}/{n_windows} 窗口）")
            break

    # 跨窗口分析
    print(f"\n{'=' * 60}")
    print("跨窗口稳健性分析")
    print(f"{'=' * 60}")

    if not all_cross_scores:
        print("未找到任何有效参数")
        return None, 0, {}

    ranked = []
    for p_key, data in all_cross_scores.items():
        scores = data["scores"]
        returns = data["returns"]
        avg_score = np.mean(scores)
        min_score = min(scores)
        avg_return = np.mean(returns)
        cv = np.std(scores) / (avg_score + 0.001)
        robustness = avg_score * (1.0 - min(cv, 0.5))
        ranked.append(
            {
                "key": p_key,
                "params": data["params"],
                "avg_score": avg_score,
                "min_score": min_score,
                "robustness": robustness,
                "avg_return": avg_return,
                "n_windows": len(scores),
                "scores": scores,
            }
        )

    ranked.sort(key=lambda x: x["robustness"], reverse=True)

    print(f"{'参数':70s} {'窗口':>5s} {'平均分':>8s} {'最低分':>8s} {'稳健分':>8s} {'均收益':>8s}")
    print("-" * 110)
    for r in ranked[:10]:
        print(
            f"{r['key']:70s} {r['n_windows']:>5d} {r['avg_score']:>8.4f} {r['min_score']:>8.4f} {r['robustness']:>8.4f} {r['avg_return'] * 100:>+7.2f}%"
        )

    champion = ranked[0]
    cp = champion["params"]
    print(
        f"\n稳健冠军: w={cp['window']} std={cp['std_dev']} atr_p={cp['atr_period']} "
        f"atr_m={cp['atr_multiplier']} hold={cp['max_hold_bars']} ez={cp['entry_zone']} "
        f"trend_ma={cp['trend_ma_period']}"
    )
    print(f"跨窗口平均评分: {champion['avg_score']:.4f} (最低: {champion['min_score']:.4f})")
    print(f"跨窗口平均收益: {champion['avg_return'] * 100:+.2f}%")

    # 全量数据评估
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    final_strategy = PureActionStrategy(**cp)
    final_signals = final_strategy.generate_signals(df)
    w2 = cp["window"] * 2
    final_score, final_metrics, final_trades = evaluator.evaluate(
        final_signals[w2:],
        df["close"].values[w2:],
        df.iloc[w2:].reset_index(drop=True),
    )
    n_trades = len([t for t in final_trades if t.get("pnl") is not None])
    print(
        f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
        f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:+.1f}% | "
        f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
    )

    elapsed = time.time() - t_total_start
    print(f"\nWalk-Forward 搜索完成, 总耗时 {elapsed:.1f}s")

    return cp, champion["robustness"], final_metrics


# ---------------------------------------------------------------------------
# Walk-Forward 验证搜索（ADX 趋势强度过滤 + 趋势对齐）
# ---------------------------------------------------------------------------


def walk_forward_adx_search(df, time_budget=TIME_BUDGET, n_windows=5):
    """
    Walk-Forward 验证的 ADX+趋势对齐策略搜索。
    ADX 高位时空仓避险，低位时趋势对齐交易。
    """
    n = len(df)
    seg_size = n // (n_windows + 1)

    evaluator = StrategyEvaluator()

    grid = {
        "window": [15, 20, 25],
        "std_dev": [2.0, 2.5, 3.0],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5],
        "max_hold_bars": [12, 18, 24],
        "entry_zone": [0.0, 0.5],
        "trend_ma_period": [50, 100, 200],
        "adx_threshold": [20, 25, 30, None],  # None = 不做ADX过滤
        "adx_period": [14],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("Walk-Forward 验证搜索 (ADX趋势强度 + 趋势对齐)")
    print(f"  总数据: {n} 条K线, {n_windows} 个窗口, 每窗口 {seg_size} 条")
    print(f"  参数组合: {total_combos}, 总时间预算: {time_budget:.0f}s")
    print()

    per_window_budget = time_budget * 0.85 / n_windows
    window_champions = []
    all_cross_scores = {}
    t_total_start = time.time()

    for w_idx in range(n_windows):
        train_end = (w_idx + 1) * seg_size
        val_start = train_end
        val_end = min(val_start + seg_size, n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        val_df = df.iloc[val_start:val_end].reset_index(drop=True)
        val_prices = val_df["close"].values.astype(float)

        print(f"{'─' * 60}")
        print(f"窗口 {w_idx + 1}/{n_windows}: 训练 [{0}:{train_end}] 验证 [{val_start}:{val_end}]")

        best_score = -float("inf")
        best_params = None
        best_metrics = None
        tried = 0
        t_w_start = time.time()

        for window in grid["window"]:
            for std_dev in grid["std_dev"]:
                for atr_p in grid["atr_period"]:
                    for atr_m in grid["atr_multiplier"]:
                        for max_hold in grid["max_hold_bars"]:
                            for ez in grid["entry_zone"]:
                                for trend_ma in grid["trend_ma_period"]:
                                    for adx_th in grid["adx_threshold"]:
                                        for adx_p in grid["adx_period"]:
                                            if time.time() - t_w_start > per_window_budget:
                                                break
                                            tried += 1

                                            strategy = PureActionStrategy(
                                                window=window,
                                                std_dev=std_dev,
                                                atr_period=atr_p,
                                                atr_multiplier=atr_m,
                                                max_hold_bars=max_hold,
                                                entry_zone=ez,
                                                enable_short=True,
                                                trend_ma_period=trend_ma,
                                                adx_threshold=adx_th,
                                                adx_period=adx_p,
                                            )

                                            try:
                                                signals = strategy.generate_signals(val_df)
                                                w2 = window * 2
                                                score, metrics, trades = evaluator.evaluate(
                                                    signals[w2:],
                                                    val_prices[w2:],
                                                    val_df.iloc[w2:].reset_index(drop=True),
                                                )
                                            except Exception:
                                                score = 0.0
                                                metrics = {}
                                                trades = []

                                            if score > best_score:
                                                best_score = score
                                                best_params = {
                                                    "window": window,
                                                    "std_dev": std_dev,
                                                    "atr_period": atr_p,
                                                    "atr_multiplier": atr_m,
                                                    "max_hold_bars": max_hold,
                                                    "entry_zone": ez,
                                                    "enable_short": True,
                                                    "trend_ma_period": trend_ma,
                                                    "adx_threshold": adx_th,
                                                    "adx_period": adx_p,
                                                }
                                                best_metrics = metrics

        w_time = time.time() - t_w_start

        if best_params and best_metrics:
            trades_list = best_metrics and trades or []
            n_trades = len([t for t in trades_list if t.get("pnl") is not None])
            print(
                f"  窗口最优: w={best_params['window']} std={best_params['std_dev']} "
                f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
                f"hold={best_params['max_hold_bars']} ez={best_params['entry_zone']} "
                f"trend_ma={best_params['trend_ma_period']} "
                f"adx_th={best_params['adx_threshold']} adx_p={best_params['adx_period']}"
            )
            print(
                f"  验证评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
                f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:+.1f}% | "
                f"交易={n_trades} | 耗时={w_time:.1f}s"
            )
            window_champions.append(
                {
                    "window": w_idx,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
            )

            p_key = (
                f"w{best_params['window']}_s{best_params['std_dev']}"
                f"_ap{best_params['atr_period']}_am{best_params['atr_multiplier']}"
                f"_h{best_params['max_hold_bars']}_ez{best_params['entry_zone']}"
                f"_t{best_params['trend_ma_period']}"
                f"_adx{best_params['adx_threshold']}"
            )
            if p_key not in all_cross_scores:
                all_cross_scores[p_key] = {"params": best_params, "scores": [], "returns": []}
            all_cross_scores[p_key]["scores"].append(best_score)
            all_cross_scores[p_key]["returns"].append(best_metrics.get("total_return", 0))
        else:
            print(f"  未找到有效参数 (耗时 {w_time:.1f}s)")

        if time.time() - t_total_start > time_budget * 0.95:
            print(f"\n总时间预算即将耗尽，提前结束（完成 {w_idx + 1}/{n_windows} 窗口）")
            break

    # 跨窗口分析
    print(f"\n{'=' * 60}")
    print("跨窗口稳健性分析")
    print(f"{'=' * 60}")

    if not all_cross_scores:
        print("未找到任何有效参数")
        return None, 0, {}

    ranked = []
    for p_key, data in all_cross_scores.items():
        scores = data["scores"]
        returns = data["returns"]
        avg_score = np.mean(scores)
        min_score = min(scores)
        avg_return = np.mean(returns)
        cv = np.std(scores) / (avg_score + 0.001)
        robustness = avg_score * (1.0 - min(cv, 0.5))
        ranked.append(
            {
                "key": p_key,
                "params": data["params"],
                "avg_score": avg_score,
                "min_score": min_score,
                "robustness": robustness,
                "avg_return": avg_return,
                "n_windows": len(scores),
                "scores": scores,
            }
        )

    ranked.sort(key=lambda x: x["robustness"], reverse=True)

    print(f"{'参数':80s} {'窗口':>5s} {'平均分':>8s} {'最低分':>8s} {'稳健分':>8s} {'均收益':>8s}")
    print("-" * 120)
    for r in ranked[:15]:
        print(
            f"{r['key']:80s} {r['n_windows']:>5d} {r['avg_score']:>8.4f} {r['min_score']:>8.4f} {r['robustness']:>8.4f} {r['avg_return'] * 100:>+7.2f}%"
        )

    champion = ranked[0]
    cp = champion["params"]
    print(
        f"\n稳健冠军: w={cp['window']} std={cp['std_dev']} atr_p={cp['atr_period']} "
        f"atr_m={cp['atr_multiplier']} hold={cp['max_hold_bars']} ez={cp['entry_zone']} "
        f"trend_ma={cp['trend_ma_period']} adx_th={cp['adx_threshold']} adx_p={cp['adx_period']}"
    )
    print(f"跨窗口平均评分: {champion['avg_score']:.4f} (最低: {champion['min_score']:.4f})")
    print(f"跨窗口平均收益: {champion['avg_return'] * 100:+.2f}%")

    # 全量数据评估
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    final_strategy = PureActionStrategy(**cp)
    final_signals = final_strategy.generate_signals(df)
    w2 = cp["window"] * 2
    final_score, final_metrics, final_trades = evaluator.evaluate(
        final_signals[w2:],
        df["close"].values[w2:],
        df.iloc[w2:].reset_index(drop=True),
    )
    n_trades = len([t for t in final_trades if t.get("pnl") is not None])
    print(
        f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
        f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:+.1f}% | "
        f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
    )

    elapsed = time.time() - t_total_start
    print(f"\nWalk-Forward 搜索完成, 总耗时 {elapsed:.1f}s")

    return cp, champion["robustness"], final_metrics


# ---------------------------------------------------------------------------
# Walk-Forward 验证搜索（市场状态自适应：震荡=均值回归，趋势=趋势跟随）
# ---------------------------------------------------------------------------


def walk_forward_hybrid_search(df, time_budget=TIME_BUDGET, n_windows=5):
    """
    Walk-Forward 验证的市场状态自适应策略搜索。
    ADX 判市：低位震荡→均值回归，高位趋势→趋势跟随。
    """
    n = len(df)
    seg_size = n // (n_windows + 1)

    evaluator = StrategyEvaluator()

    grid = {
        "window": [15, 20, 25],
        "std_dev": [2.0, 2.5, 3.0],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5],
        "max_hold_bars": [12, 18, 24],
        "entry_zone": [0.0, 0.5],
        "trend_ma_period": [50, 100, 200],
        "adx_threshold": [20, 25, 30],
        "adx_period": [14],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("Walk-Forward 验证搜索 (市场状态自适应: 震荡=均值回归, 趋势=趋势跟随)")
    print(f"  总数据: {n} 条K线, {n_windows} 个窗口, 每窗口 {seg_size} 条")
    print(f"  参数组合: {total_combos}, 总时间预算: {time_budget:.0f}s")
    print()

    per_window_budget = time_budget * 0.85 / n_windows
    window_champions = []
    all_cross_scores = {}
    t_total_start = time.time()

    for w_idx in range(n_windows):
        train_end = (w_idx + 1) * seg_size
        val_start = train_end
        val_end = min(val_start + seg_size, n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        val_df = df.iloc[val_start:val_end].reset_index(drop=True)
        val_prices = val_df["close"].values.astype(float)

        print(f"{'─' * 60}")
        print(f"窗口 {w_idx + 1}/{n_windows}: 训练 [{0}:{train_end}] 验证 [{val_start}:{val_end}]")

        best_score = -float("inf")
        best_params = None
        best_metrics = None
        tried = 0
        t_w_start = time.time()

        for window in grid["window"]:
            for std_dev in grid["std_dev"]:
                for atr_p in grid["atr_period"]:
                    for atr_m in grid["atr_multiplier"]:
                        for max_hold in grid["max_hold_bars"]:
                            for ez in grid["entry_zone"]:
                                for trend_ma in grid["trend_ma_period"]:
                                    for adx_th in grid["adx_threshold"]:
                                        for adx_p in grid["adx_period"]:
                                            if time.time() - t_w_start > per_window_budget:
                                                break
                                            tried += 1

                                            strategy = HybridStrategy(
                                                window=window,
                                                std_dev=std_dev,
                                                atr_period=atr_p,
                                                atr_multiplier=atr_m,
                                                max_hold_bars=max_hold,
                                                entry_zone=ez,
                                                enable_short=True,
                                                trend_ma_period=trend_ma,
                                                adx_threshold=adx_th,
                                                adx_period=adx_p,
                                            )

                                            try:
                                                signals = strategy.generate_signals(val_df)
                                                w2 = window * 2
                                                score, metrics, trades = evaluator.evaluate(
                                                    signals[w2:],
                                                    val_prices[w2:],
                                                    val_df.iloc[w2:].reset_index(drop=True),
                                                )
                                            except Exception:
                                                score = 0.0
                                                metrics = {}
                                                trades = []

                                            if score > best_score:
                                                best_score = score
                                                best_params = {
                                                    "window": window,
                                                    "std_dev": std_dev,
                                                    "atr_period": atr_p,
                                                    "atr_multiplier": atr_m,
                                                    "max_hold_bars": max_hold,
                                                    "entry_zone": ez,
                                                    "enable_short": True,
                                                    "trend_ma_period": trend_ma,
                                                    "adx_threshold": adx_th,
                                                    "adx_period": adx_p,
                                                }
                                                best_metrics = metrics

        w_time = time.time() - t_w_start

        if best_params and best_metrics:
            trades_list = best_metrics and trades or []
            n_trades = len([t for t in trades_list if t.get("pnl") is not None])
            print(
                f"  窗口最优: w={best_params['window']} std={best_params['std_dev']} "
                f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
                f"hold={best_params['max_hold_bars']} ez={best_params['entry_zone']} "
                f"trend_ma={best_params['trend_ma_period']} "
                f"adx_th={best_params['adx_threshold']}"
            )
            print(
                f"  验证评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
                f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:+.1f}% | "
                f"交易={n_trades} | 耗时={w_time:.1f}s"
            )
            window_champions.append(
                {
                    "window": w_idx,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
            )

            p_key = (
                f"w{best_params['window']}_s{best_params['std_dev']}"
                f"_ap{best_params['atr_period']}_am{best_params['atr_multiplier']}"
                f"_h{best_params['max_hold_bars']}_ez{best_params['entry_zone']}"
                f"_t{best_params['trend_ma_period']}"
                f"_adx{best_params['adx_threshold']}"
            )
            if p_key not in all_cross_scores:
                all_cross_scores[p_key] = {"params": best_params, "scores": [], "returns": []}
            all_cross_scores[p_key]["scores"].append(best_score)
            all_cross_scores[p_key]["returns"].append(best_metrics.get("total_return", 0))
        else:
            print(f"  未找到有效参数 (耗时 {w_time:.1f}s)")

        if time.time() - t_total_start > time_budget * 0.95:
            print(f"\n总时间预算即将耗尽，提前结束（完成 {w_idx + 1}/{n_windows} 窗口）")
            break

    # 跨窗口分析
    print(f"\n{'=' * 60}")
    print("跨窗口稳健性分析")
    print(f"{'=' * 60}")

    if not all_cross_scores:
        print("未找到任何有效参数")
        return None, 0, {}

    ranked = []
    for p_key, data in all_cross_scores.items():
        scores = data["scores"]
        returns = data["returns"]
        avg_score = np.mean(scores)
        min_score = min(scores)
        avg_return = np.mean(returns)
        cv = np.std(scores) / (avg_score + 0.001)
        robustness = avg_score * (1.0 - min(cv, 0.5))
        ranked.append(
            {
                "key": p_key,
                "params": data["params"],
                "avg_score": avg_score,
                "min_score": min_score,
                "robustness": robustness,
                "avg_return": avg_return,
                "n_windows": len(scores),
                "scores": scores,
            }
        )

    ranked.sort(key=lambda x: x["robustness"], reverse=True)

    print(f"{'参数':80s} {'窗口':>5s} {'平均分':>8s} {'最低分':>8s} {'稳健分':>8s} {'均收益':>8s}")
    print("-" * 120)
    for r in ranked[:15]:
        print(
            f"{r['key']:80s} {r['n_windows']:>5d} {r['avg_score']:>8.4f} {r['min_score']:>8.4f} {r['robustness']:>8.4f} {r['avg_return'] * 100:>+7.2f}%"
        )

    champion = ranked[0]
    cp = champion["params"]
    print(
        f"\n稳健冠军: w={cp['window']} std={cp['std_dev']} atr_p={cp['atr_period']} "
        f"atr_m={cp['atr_multiplier']} hold={cp['max_hold_bars']} ez={cp['entry_zone']} "
        f"trend_ma={cp['trend_ma_period']} adx_th={cp['adx_threshold']}"
    )
    print(f"跨窗口平均评分: {champion['avg_score']:.4f} (最低: {champion['min_score']:.4f})")
    print(f"跨窗口平均收益: {champion['avg_return'] * 100:+.2f}%")

    # 全量数据评估
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    final_strategy = HybridStrategy(**cp)
    final_signals = final_strategy.generate_signals(df)
    w2 = cp["window"] * 2
    final_score, final_metrics, final_trades = evaluator.evaluate(
        final_signals[w2:],
        df["close"].values[w2:],
        df.iloc[w2:].reset_index(drop=True),
    )
    n_trades = len([t for t in final_trades if t.get("pnl") is not None])
    print(
        f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
        f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:+.1f}% | "
        f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
    )

    elapsed = time.time() - t_total_start
    print(f"\nWalk-Forward 搜索完成, 总耗时 {elapsed:.1f}s")

    return cp, champion["robustness"], final_metrics


# ---------------------------------------------------------------------------
# Walk-Forward 验证搜索（纯趋势跟随）
# ---------------------------------------------------------------------------


def walk_forward_trendfollow_search(df, time_budget=TIME_BUDGET, n_windows=5):
    """
    Walk-Forward 验证的纯趋势跟随策略搜索。
    EMA 长周期定方向，EMA 短周期回调入场。
    """
    n = len(df)
    seg_size = n // (n_windows + 1)

    evaluator = StrategyEvaluator()

    grid = {
        "long_ma_period": [50, 100, 200],
        "pull_ma_period": [10, 15, 20],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 18, 24, 36],
        "entry_zone": [0.001, 0.002, 0.005],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("Walk-Forward 验证搜索 (纯趋势跟随: EMA定方向, EMA回调入场)")
    print(f"  总数据: {n} 条K线, {n_windows} 个窗口, 每窗口 {seg_size} 条")
    print(f"  参数组合: {total_combos}, 总时间预算: {time_budget:.0f}s")
    print()

    per_window_budget = time_budget * 0.85 / n_windows
    window_champions = []
    all_cross_scores = {}
    t_total_start = time.time()

    for w_idx in range(n_windows):
        train_end = (w_idx + 1) * seg_size
        val_start = train_end
        val_end = min(val_start + seg_size, n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        val_df = df.iloc[val_start:val_end].reset_index(drop=True)
        val_prices = val_df["close"].values.astype(float)

        print(f"{'─' * 60}")
        print(f"窗口 {w_idx + 1}/{n_windows}: 训练 [{0}:{train_end}] 验证 [{val_start}:{val_end}]")

        best_score = -float("inf")
        best_params = None
        best_metrics = None
        tried = 0
        t_w_start = time.time()

        for long_ma in grid["long_ma_period"]:
            for pull_ma in grid["pull_ma_period"]:
                for atr_p in grid["atr_period"]:
                    for atr_m in grid["atr_multiplier"]:
                        for max_hold in grid["max_hold_bars"]:
                            for ez in grid["entry_zone"]:
                                if time.time() - t_w_start > per_window_budget:
                                    break
                                tried += 1

                                strategy = TrendFollowStrategy(
                                    long_ma_period=long_ma,
                                    pull_ma_period=pull_ma,
                                    atr_period=atr_p,
                                    atr_multiplier=atr_m,
                                    max_hold_bars=max_hold,
                                    entry_zone=ez,
                                    enable_short=True,
                                )

                                try:
                                    signals = strategy.generate_signals(val_df)
                                    w2 = pull_ma * 2
                                    score, metrics, trades = evaluator.evaluate(
                                        signals[w2:],
                                        val_prices[w2:],
                                        val_df.iloc[w2:].reset_index(drop=True),
                                    )
                                except Exception:
                                    score = 0.0
                                    metrics = {}
                                    trades = []

                                if score > best_score:
                                    best_score = score
                                    best_params = {
                                        "long_ma_period": long_ma,
                                        "pull_ma_period": pull_ma,
                                        "atr_period": atr_p,
                                        "atr_multiplier": atr_m,
                                        "max_hold_bars": max_hold,
                                        "entry_zone": ez,
                                        "enable_short": True,
                                    }
                                    best_metrics = metrics

        w_time = time.time() - t_w_start

        if best_params and best_metrics:
            trades_list = best_metrics and trades or []
            n_trades = len([t for t in trades_list if t.get("pnl") is not None])
            print(
                f"  窗口最优: long={best_params['long_ma_period']} pull={best_params['pull_ma_period']} "
                f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
                f"hold={best_params['max_hold_bars']} ez={best_params['entry_zone']}"
            )
            print(
                f"  验证评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
                f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:+.1f}% | "
                f"交易={n_trades} | 耗时={w_time:.1f}s"
            )
            window_champions.append(
                {
                    "window": w_idx,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
            )

            p_key = (
                f"L{best_params['long_ma_period']}_P{best_params['pull_ma_period']}"
                f"_ap{best_params['atr_period']}_am{best_params['atr_multiplier']}"
                f"_h{best_params['max_hold_bars']}_ez{best_params['entry_zone']}"
            )
            if p_key not in all_cross_scores:
                all_cross_scores[p_key] = {"params": best_params, "scores": [], "returns": []}
            all_cross_scores[p_key]["scores"].append(best_score)
            all_cross_scores[p_key]["returns"].append(best_metrics.get("total_return", 0))
        else:
            print(f"  未找到有效参数 (耗时 {w_time:.1f}s)")

        if time.time() - t_total_start > time_budget * 0.95:
            print(f"\n总时间预算即将耗尽，提前结束（完成 {w_idx + 1}/{n_windows} 窗口）")
            break

    # 跨窗口分析
    print(f"\n{'=' * 60}")
    print("跨窗口稳健性分析")
    print(f"{'=' * 60}")

    if not all_cross_scores:
        print("未找到任何有效参数")
        return None, 0, {}

    ranked = []
    for p_key, data in all_cross_scores.items():
        scores = data["scores"]
        returns = data["returns"]
        avg_score = np.mean(scores)
        min_score = min(scores)
        avg_return = np.mean(returns)
        cv = np.std(scores) / (avg_score + 0.001)
        robustness = avg_score * (1.0 - min(cv, 0.5))
        ranked.append(
            {
                "key": p_key,
                "params": data["params"],
                "avg_score": avg_score,
                "min_score": min_score,
                "robustness": robustness,
                "avg_return": avg_return,
                "n_windows": len(scores),
                "scores": scores,
            }
        )

    ranked.sort(key=lambda x: x["robustness"], reverse=True)

    print(f"{'参数':55s} {'窗口':>5s} {'平均分':>8s} {'最低分':>8s} {'稳健分':>8s} {'均收益':>8s}")
    print("-" * 95)
    for r in ranked[:15]:
        print(
            f"{r['key']:55s} {r['n_windows']:>5d} {r['avg_score']:>8.4f} {r['min_score']:>8.4f} {r['robustness']:>8.4f} {r['avg_return'] * 100:>+7.2f}%"
        )

    champion = ranked[0]
    cp = champion["params"]
    print(
        f"\n稳健冠军: long={cp['long_ma_period']} pull={cp['pull_ma_period']} "
        f"atr_p={cp['atr_period']} atr_m={cp['atr_multiplier']} "
        f"hold={cp['max_hold_bars']} ez={cp['entry_zone']}"
    )
    print(f"跨窗口平均评分: {champion['avg_score']:.4f} (最低: {champion['min_score']:.4f})")
    print(f"跨窗口平均收益: {champion['avg_return'] * 100:+.2f}%")

    # 全量数据评估
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    final_strategy = TrendFollowStrategy(**cp)
    final_signals = final_strategy.generate_signals(df)
    w2 = cp["pull_ma_period"] * 2
    final_score, final_metrics, final_trades = evaluator.evaluate(
        final_signals[w2:],
        df["close"].values[w2:],
        df.iloc[w2:].reset_index(drop=True),
    )
    n_trades = len([t for t in final_trades if t.get("pnl") is not None])
    print(
        f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
        f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:+.1f}% | "
        f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
    )

    elapsed = time.time() - t_total_start
    print(f"\nWalk-Forward 搜索完成, 总耗时 {elapsed:.1f}s")

    return cp, champion["robustness"], final_metrics


# ---------------------------------------------------------------------------
# Walk-Forward 验证搜索（混合均值回归 + 动量）
# ---------------------------------------------------------------------------


def walk_forward_hybrid_mm_search(df, time_budget=TIME_BUDGET, n_windows=5):
    """
    Walk-Forward 验证的混合均值回归 + 动量策略搜索。
    RSI 极端值 + EMA 动量过滤。
    """
    n = len(df)
    seg_size = n // (n_windows + 1)

    evaluator = StrategyEvaluator()

    grid = {
        "rsi_period": [14],
        "rsi_low": [20, 25, 30, 35, 40],
        "rsi_high": [60, 65, 70, 75, 80],
        "ma_period": [10, 15, 20],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5],
        "max_hold_bars": [12, 18, 24, 36],
        "ema_tolerance": [0.0, 0.003, 0.005],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("Walk-Forward 验证搜索 (混合均值回归 + 动量: RSI极端值 + EMA过滤)")
    print(f"  总数据: {n} 条K线, {n_windows} 个窗口, 每窗口 {seg_size} 条")
    print(f"  参数组合: {total_combos}, 总时间预算: {time_budget:.0f}s")
    print()

    per_window_budget = time_budget * 0.85 / n_windows
    window_champions = []
    all_cross_scores = {}
    t_total_start = time.time()

    for w_idx in range(n_windows):
        train_end = (w_idx + 1) * seg_size
        val_start = train_end
        val_end = min(val_start + seg_size, n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        val_df = df.iloc[val_start:val_end].reset_index(drop=True)
        val_prices = val_df["close"].values.astype(float)

        print(f"{'─' * 60}")
        print(f"窗口 {w_idx + 1}/{n_windows}: 训练 [{0}:{train_end}] 验证 [{val_start}:{val_end}]")

        best_score = -float("inf")
        best_params = None
        best_metrics = None
        tried = 0
        t_w_start = time.time()

        for rsi_p in grid["rsi_period"]:
            for rsi_l in grid["rsi_low"]:
                for rsi_h in grid["rsi_high"]:
                    for ma_p in grid["ma_period"]:
                        for atr_p in grid["atr_period"]:
                            for atr_m in grid["atr_multiplier"]:
                                for max_hold in grid["max_hold_bars"]:
                                    for etol in grid["ema_tolerance"]:
                                        if time.time() - t_w_start > per_window_budget:
                                            break
                                        tried += 1

                                        strategy = HybridMeanRevMomentumStrategy(
                                            rsi_period=rsi_p,
                                            rsi_low=rsi_l,
                                            rsi_high=rsi_h,
                                            ma_period=ma_p,
                                            atr_period=atr_p,
                                            atr_multiplier=atr_m,
                                            max_hold_bars=max_hold,
                                            enable_short=True,
                                            ema_tolerance=etol,
                                        )

                                    try:
                                        signals = strategy.generate_signals(val_df)
                                        min_idx = max(rsi_p, ma_p, atr_p)
                                        score, metrics, trades = evaluator.evaluate(
                                            signals[min_idx:],
                                            val_prices[min_idx:],
                                            val_df.iloc[min_idx:].reset_index(drop=True),
                                        )
                                    except Exception:
                                        score = 0.0
                                        metrics = {}
                                        trades = []

                                    if score > best_score:
                                        best_score = score
                                        best_params = {
                                            "rsi_period": rsi_p,
                                            "rsi_low": rsi_l,
                                            "rsi_high": rsi_h,
                                            "ma_period": ma_p,
                                            "atr_period": atr_p,
                                            "atr_multiplier": atr_m,
                                            "max_hold_bars": max_hold,
                                            "enable_short": True,
                                            "ema_tolerance": etol,
                                        }
                                        best_metrics = metrics

        w_time = time.time() - t_w_start

        if best_params and best_metrics:
            trades_list = best_metrics and trades or []
            n_trades = len([t for t in trades_list if t.get("pnl") is not None])
            print(
                f"  窗口最优: rsi_low={best_params['rsi_low']} rsi_high={best_params['rsi_high']} "
                f"ma={best_params['ma_period']} atr_p={best_params['atr_period']} "
                f"atr_m={best_params['atr_multiplier']} hold={best_params['max_hold_bars']}"
            )
            print(
                f"  验证评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
                f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:+.1f}% | "
                f"交易={n_trades} | 耗时={w_time:.1f}s"
            )
            window_champions.append(
                {
                    "window": w_idx,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
            )

            p_key = (
                f"rsiL{best_params['rsi_low']}_rsiH{best_params['rsi_high']}"
                f"_ma{best_params['ma_period']}_ap{best_params['atr_period']}"
                f"_am{best_params['atr_multiplier']}_h{best_params['max_hold_bars']}"
            )
            if p_key not in all_cross_scores:
                all_cross_scores[p_key] = {"params": best_params, "scores": [], "returns": []}
            all_cross_scores[p_key]["scores"].append(best_score)
            all_cross_scores[p_key]["returns"].append(best_metrics.get("total_return", 0))
        else:
            print(f"  未找到有效参数 (耗时 {w_time:.1f}s)")

        if time.time() - t_total_start > time_budget * 0.95:
            print(f"\n总时间预算即将耗尽，提前结束（完成 {w_idx + 1}/{n_windows} 窗口）")
            break

    # 跨窗口分析
    print(f"\n{'=' * 60}")
    print("跨窗口稳健性分析")
    print(f"{'=' * 60}")

    if not all_cross_scores:
        print("未找到任何有效参数")
        return None, 0, {}

    ranked = []
    for p_key, data in all_cross_scores.items():
        scores = data["scores"]
        returns = data["returns"]
        avg_score = np.mean(scores)
        min_score = min(scores)
        avg_return = np.mean(returns)
        cv = np.std(scores) / (avg_score + 0.001)
        robustness = avg_score * (1.0 - min(cv, 0.5))
        ranked.append(
            {
                "key": p_key,
                "params": data["params"],
                "avg_score": avg_score,
                "min_score": min_score,
                "robustness": robustness,
                "avg_return": avg_return,
                "n_windows": len(scores),
                "scores": scores,
            }
        )

    ranked.sort(key=lambda x: x["robustness"], reverse=True)

    print(f"{'参数':55s} {'窗口':>5s} {'平均分':>8s} {'最低分':>8s} {'稳健分':>8s} {'均收益':>8s}")
    print("-" * 95)
    for r in ranked[:15]:
        print(
            f"{r['key']:55s} {r['n_windows']:>5d} {r['avg_score']:>8.4f} {r['min_score']:>8.4f} {r['robustness']:>8.4f} {r['avg_return'] * 100:>+7.2f}%"
        )

    champion = ranked[0]
    cp = champion["params"]
    print(
        f"\n稳健冠军: rsi_low={cp['rsi_low']} rsi_high={cp['rsi_high']} ma={cp['ma_period']} "
        f"atr_p={cp['atr_period']} atr_m={cp['atr_multiplier']} hold={cp['max_hold_bars']}"
    )
    print(f"跨窗口平均评分: {champion['avg_score']:.4f} (最低: {champion['min_score']:.4f})")
    print(f"跨窗口平均收益: {champion['avg_return'] * 100:+.2f}%")

    # 全量数据评估
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    final_strategy = HybridMeanRevMomentumStrategy(**cp)
    final_signals = final_strategy.generate_signals(df)
    min_idx = max(cp["rsi_period"], cp["ma_period"], cp["atr_period"])
    final_score, final_metrics, final_trades = evaluator.evaluate(
        final_signals[min_idx:],
        df["close"].values[min_idx:],
        df.iloc[min_idx:].reset_index(drop=True),
    )
    n_trades = len([t for t in final_trades if t.get("pnl") is not None])
    print(
        f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
        f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:+.1f}% | "
        f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
    )

    elapsed = time.time() - t_total_start
    print(f"\nWalk-Forward 搜索完成, 总耗时 {elapsed:.1f}s")

    return cp, champion["robustness"], final_metrics


# ---------------------------------------------------------------------------
# 趋势分析模块
# ---------------------------------------------------------------------------


def analyze_market_regime(df):
    """
    快速分析当前市场趋势状态。
    返回: (regime_name, regime_info_dict)
    regime_name: strong_uptrend | strong_downtrend | weak_uptrend | weak_downtrend | ranging
    """
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    n = len(close)

    # EMA50 / EMA200
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)

    # ADX 简化计算（14周期）
    period = 14
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0
        minus_dm[i] = down if down > up and down > 0 else 0

    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]
    atr = np.zeros(n)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    # DI+ / DI- 使用 Wilder 平滑（与 ATR / ADX 保持一致）
    smoothed_plus_dm = np.zeros(n)
    smoothed_minus_dm = np.zeros(n)
    smoothed_plus_dm[period - 1] = np.sum(plus_dm[:period])
    smoothed_minus_dm[period - 1] = np.sum(minus_dm[:period])
    for i in range(period, n):
        smoothed_plus_dm[i] = (smoothed_plus_dm[i - 1] * (period - 1) + plus_dm[i]) / period
        smoothed_minus_dm[i] = (smoothed_minus_dm[i - 1] * (period - 1) + minus_dm[i]) / period

    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    for i in range(period - 1, n):
        if atr[i] > 0:
            plus_di[i] = 100 * smoothed_plus_dm[i] / atr[i]
            minus_di[i] = 100 * smoothed_minus_dm[i] / atr[i]

    dx = np.zeros(n)
    for i in range(period, n):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

    adx = np.zeros(n)
    adx[period * 2 - 1] = np.mean(dx[period : period * 2])
    for i in range(period * 2, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    adx_val = adx[-1]
    is_uptrend = ema50[-1] > ema200[-1]
    is_downtrend = ema50[-1] < ema200[-1]
    price_dev = (close[-1] - ema200[-1]) / ema200[-1] * 100

    # 波动率（最近7天年化）
    returns = np.diff(close) / close[:-1]
    vol = (
        np.std(returns[-288 * 7 :]) * np.sqrt(288 * 365)
        if len(returns) >= 288 * 7
        else np.std(returns) * np.sqrt(288 * 365)
    )

    # 趋势判定
    if adx_val > 25:
        if is_uptrend:
            regime = "strong_uptrend"
        elif is_downtrend:
            regime = "strong_downtrend"
        else:
            regime = "strong_uptrend" if price_dev > 0 else "strong_downtrend"
    elif adx_val > 15:
        if is_uptrend:
            regime = "weak_uptrend"
        elif is_downtrend:
            regime = "weak_downtrend"
        else:
            regime = "weak_uptrend" if price_dev > 0 else "weak_downtrend"
    else:
        regime = "ranging"

    info = {
        "regime": regime,
        "adx": float(adx_val),
        "ema50_vs_ema200": "uptrend" if is_uptrend else "downtrend" if is_downtrend else "neutral",
        "price_vs_ema200_pct": float(price_dev),
        "volatility_annualized": float(vol),
    }
    return regime, info


# ---------------------------------------------------------------------------
# 直接全量搜索（市场状态自适应混合，非Walk-Forward）
# ---------------------------------------------------------------------------


def direct_adaptive_search(df, time_budget=TIME_BUDGET):
    """
    直接在全量数据上进行网格搜索，选择最优参数。
    不做Walk-Forward验证，适合短周期数据。
    """
    n = len(df)
    evaluator = StrategyEvaluator()
    prices = df["close"].values.astype(float)

    grid = {
        "rsi_low": [25, 30, 35],
        "rsi_high": [65, 70, 75],
        "ma_period": [10, 20],
        "trend_long_ma": [50, 100, 200],
        "trend_pull_ma": [10, 20],
        "adx_threshold": [20, 25, 30],
        "adx_period": [14],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.5],
        "max_hold_bars": [6, 12, 24],
        "ema_tolerance": [0.0, 0.005, 0.01],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("直接全量搜索 (市场状态自适应: ADX判市, 震荡=RSI均值回归, 趋势=EMA趋势跟随)")
    print(f"  总数据: {n} 条K线")
    print(f"  参数组合: {total_combos}, 时间预算: {time_budget:.0f}s")
    print()

    best_score = -float("inf")
    best_params = None
    best_metrics = None
    best_trades = []
    tried = 0
    t_start = time.time()

    for rsi_l in grid["rsi_low"]:
        for rsi_h in grid["rsi_high"]:
            for ma_p in grid["ma_period"]:
                for trend_long in grid["trend_long_ma"]:
                    for trend_pull in grid["trend_pull_ma"]:
                        for adx_th in grid["adx_threshold"]:
                            for adx_p in grid["adx_period"]:
                                for atr_p in grid["atr_period"]:
                                    for atr_m in grid["atr_multiplier"]:
                                        for max_hold in grid["max_hold_bars"]:
                                            for etol in grid["ema_tolerance"]:
                                                if time.time() - t_start > time_budget:
                                                    break
                                                tried += 1

                                                strategy = AdaptiveHybridStrategy(
                                                    rsi_low=rsi_l,
                                                    rsi_high=rsi_h,
                                                    ma_period=ma_p,
                                                    trend_long_ma=trend_long,
                                                    trend_pull_ma=trend_pull,
                                                    adx_threshold=adx_th,
                                                    adx_period=adx_p,
                                                    atr_period=atr_p,
                                                    atr_multiplier=atr_m,
                                                    max_hold_bars=max_hold,
                                                    enable_short=True,
                                                    ema_tolerance=etol,
                                                )

                                            try:
                                                signals = strategy.generate_signals(df)
                                                min_idx = max(
                                                    14,
                                                    ma_p,
                                                    adx_p * 2,
                                                    trend_long,
                                                    trend_pull,
                                                    atr_p,
                                                )
                                                score, metrics, trades = evaluator.evaluate(
                                                    signals[min_idx:],
                                                    prices[min_idx:],
                                                    df.iloc[min_idx:].reset_index(drop=True),
                                                )
                                            except Exception:
                                                score = 0.0
                                                metrics = {}
                                                trades = []

                                            if score > best_score:
                                                best_score = score
                                                best_params = {
                                                    "rsi_period": 14,
                                                    "rsi_low": rsi_l,
                                                    "rsi_high": rsi_h,
                                                    "ma_period": ma_p,
                                                    "trend_long_ma": trend_long,
                                                    "trend_pull_ma": trend_pull,
                                                    "adx_threshold": adx_th,
                                                    "adx_period": adx_p,
                                                    "atr_period": atr_p,
                                                    "atr_multiplier": atr_m,
                                                    "max_hold_bars": max_hold,
                                                    "enable_short": True,
                                                    "ema_tolerance": etol,
                                                }
                                                best_metrics = metrics
                                                best_trades = trades

    elapsed = time.time() - t_start
    n_trades = len([t for t in best_trades if t.get("pnl") is not None])
    print(f"搜索完成, 尝试 {tried}/{total_combos} 组参数, 耗时 {elapsed:.1f}s")
    if best_params and best_metrics:
        print(
            f"最优参数: rsiL={best_params['rsi_low']} rsiH={best_params['rsi_high']} "
            f"ma={best_params['ma_period']} trendL={best_params['trend_long_ma']} "
            f"adx_th={best_params['adx_threshold']} atr_p={best_params['atr_period']} "
            f"atr_m={best_params['atr_multiplier']} hold={best_params['max_hold_bars']} "
            f"tol={best_params.get('ema_tolerance', 0):.3f}"
        )
        print(
            f"全量评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
            f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:.1f}% | "
            f"交易={n_trades}"
        )
    else:
        print("未找到有效参数")

    return best_params, best_score, best_metrics


# ---------------------------------------------------------------------------
# 直接全量搜索（趋势跟随，非Walk-Forward）
# ---------------------------------------------------------------------------


def direct_trendfollow_search(df, time_budget=TIME_BUDGET):
    """
    直接在全量数据上搜索 TrendFollowStrategy 最优参数。
    """
    n = len(df)
    evaluator = StrategyEvaluator()
    prices = df["close"].values.astype(float)

    grid = {
        "long_ma_period": [50, 100, 200],
        "pull_ma_period": [10, 20],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.5],
        "max_hold_bars": [12, 24],
        "entry_zone": [0.001, 0.002, 0.005],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("  直接全量搜索 (TrendFollowStrategy)")
    print(f"    参数组合: {total_combos}")

    best_score = -float("inf")
    best_params = None
    best_metrics = None
    best_trades = []
    tried = 0
    t_start = time.time()

    for long_p in grid["long_ma_period"]:
        for pull_p in grid["pull_ma_period"]:
            for atr_p in grid["atr_period"]:
                for atr_m in grid["atr_multiplier"]:
                    for max_hold in grid["max_hold_bars"]:
                        for zone in grid["entry_zone"]:
                            if time.time() - t_start > time_budget:
                                break
                            tried += 1

                            strategy = TrendFollowStrategy(
                                long_ma_period=long_p,
                                pull_ma_period=pull_p,
                                atr_period=atr_p,
                                atr_multiplier=atr_m,
                                max_hold_bars=max_hold,
                                entry_zone=zone,
                                enable_short=True,
                            )

                            try:
                                signals = strategy.generate_signals(df)
                                min_idx = max(long_p, pull_p, atr_p)
                                score, metrics, trades = evaluator.evaluate(
                                    signals[min_idx:],
                                    prices[min_idx:],
                                    df.iloc[min_idx:].reset_index(drop=True),
                                )
                            except Exception:
                                score = 0.0
                                metrics = {}
                                trades = []

                            if score > best_score:
                                best_score = score
                                best_params = {
                                    "long_ma_period": long_p,
                                    "pull_ma_period": pull_p,
                                    "atr_period": atr_p,
                                    "atr_multiplier": atr_m,
                                    "max_hold_bars": max_hold,
                                    "entry_zone": zone,
                                    "enable_short": True,
                                }
                                best_metrics = metrics
                                best_trades = trades

    n_trades = len([t for t in best_trades if t.get("pnl") is not None])
    if best_params and best_metrics:
        print(
            f"    最优: long={best_params['long_ma_period']} pull={best_params['pull_ma_period']} "
            f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
            f"hold={best_params['max_hold_bars']} zone={best_params['entry_zone']}"
        )
        print(
            f"    评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
            f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:.1f}% | "
            f"交易={n_trades}"
        )
    else:
        print("    未找到有效参数")

    return best_params, best_score, best_metrics


# ---------------------------------------------------------------------------
# 直接全量搜索（混合均值回归+动量，非Walk-Forward）
# ---------------------------------------------------------------------------


def direct_hybrid_mm_search(df, time_budget=TIME_BUDGET):
    """
    直接在全量数据上搜索 HybridMeanRevMomentumStrategy 最优参数。
    """
    n = len(df)
    evaluator = StrategyEvaluator()
    prices = df["close"].values.astype(float)

    grid = {
        "rsi_period": [14],
        "rsi_low": [18, 20, 22, 25],
        "rsi_high": [65, 68, 70, 72],
        "ma_period": [10, 20],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.5],
        "max_hold_bars": [6, 12, 24],
        "ema_tolerance": [0.0, 0.003, 0.005],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("  直接全量搜索 (HybridMeanRevMomentumStrategy)")
    print(f"    参数组合: {total_combos}")

    best_score = -float("inf")
    best_params = None
    best_metrics = None
    best_trades = []
    tried = 0
    t_start = time.time()

    for rsi_l in grid["rsi_low"]:
        for rsi_h in grid["rsi_high"]:
            for ma_p in grid["ma_period"]:
                for atr_p in grid["atr_period"]:
                    for atr_m in grid["atr_multiplier"]:
                        for max_hold in grid["max_hold_bars"]:
                            for etol in grid["ema_tolerance"]:
                                if time.time() - t_start > time_budget:
                                    break
                                tried += 1

                                strategy = HybridMeanRevMomentumStrategy(
                                    rsi_period=14,
                                    rsi_low=rsi_l,
                                    rsi_high=rsi_h,
                                    ma_period=ma_p,
                                    atr_period=atr_p,
                                    atr_multiplier=atr_m,
                                    max_hold_bars=max_hold,
                                    enable_short=True,
                                    ema_tolerance=etol,
                                )

                                try:
                                    signals = strategy.generate_signals(df)
                                    min_idx = max(14, ma_p, atr_p)
                                    score, metrics, trades = evaluator.evaluate(
                                        signals[min_idx:],
                                        prices[min_idx:],
                                        df.iloc[min_idx:].reset_index(drop=True),
                                    )
                                except Exception:
                                    score = 0.0
                                    metrics = {}
                                    trades = []

                                if score > best_score:
                                    best_score = score
                                    best_params = {
                                        "rsi_period": 14,
                                        "rsi_low": rsi_l,
                                        "rsi_high": rsi_h,
                                        "ma_period": ma_p,
                                        "atr_period": atr_p,
                                        "atr_multiplier": atr_m,
                                        "max_hold_bars": max_hold,
                                        "enable_short": True,
                                        "ema_tolerance": etol,
                                    }
                                    best_metrics = metrics
                                    best_trades = trades

    n_trades = len([t for t in best_trades if t.get("pnl") is not None])
    if best_params and best_metrics:
        print(
            f"    最优: rsiL={best_params['rsi_low']} rsiH={best_params['rsi_high']} "
            f"ma={best_params['ma_period']} atr_p={best_params['atr_period']} "
            f"atr_m={best_params['atr_multiplier']} hold={best_params['max_hold_bars']} "
            f"tolerance={best_params.get('ema_tolerance', 0):.3f}"
        )
        print(
            f"    评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
            f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:.1f}% | "
            f"交易={n_trades}"
        )
    else:
        print("    未找到有效参数")

    return best_params, best_score, best_metrics


# ---------------------------------------------------------------------------
# 智能搜索（趋势感知 + 多策略竞争）
# ---------------------------------------------------------------------------


def smart_search(df, time_budget=TIME_BUDGET):
    """
    智能搜索：先分析市场趋势，然后根据趋势给不同策略分配时间预算，
    并行评估多种策略类型，选择综合评分最高的策略。
    """
    n = len(df)
    prices = df["close"].values.astype(float)

    print(f"{'=' * 60}")
    print("智能策略搜索 (趋势感知 + 多策略竞争)")
    print(f"{'=' * 60}")
    print(f"数据: {n} 条K线")

    # 1. 趋势分析
    regime, regime_info = analyze_market_regime(df)
    print("\n市场状态分析:")
    print(f"  判定结果: {regime}")
    print(
        f"  ADX={regime_info['adx']:.1f} | EMA趋势: {regime_info['ema50_vs_ema200']} | "
        f"价格偏离EMA200={regime_info['price_vs_ema200_pct']:+.2f}% | "
        f"年化波动率={regime_info['volatility_annualized'] * 100:.1f}%"
    )

    # 2. 策略池配置（名称, 搜索函数, 时间预算权重）
    if "uptrend" in regime:
        strategy_pool = [
            ("trendfollow", direct_trendfollow_search, 0.5),
            ("adaptive", direct_adaptive_search, 0.3),
            ("hybrid_mm", direct_hybrid_mm_search, 0.2),
        ]
    elif regime == "strong_downtrend":
        strategy_pool = [
            ("trendfollow", direct_trendfollow_search, 0.5),
            ("adaptive", direct_adaptive_search, 0.3),
            ("hybrid_mm", direct_hybrid_mm_search, 0.2),
        ]
    elif regime == "weak_downtrend":
        strategy_pool = [
            ("adaptive", direct_adaptive_search, 0.4),
            ("hybrid_mm", direct_hybrid_mm_search, 0.4),
            ("trendfollow", direct_trendfollow_search, 0.2),
        ]
    else:
        strategy_pool = [
            ("adaptive", direct_adaptive_search, 0.4),
            ("hybrid_mm", direct_hybrid_mm_search, 0.4),
            ("trendfollow", direct_trendfollow_search, 0.2),
        ]

    # 3. 多策略竞争搜索
    print(f"\n{'─' * 60}")
    print("策略竞争")
    print(f"{'─' * 60}")

    best_overall_name = None
    best_overall_params = None
    best_overall_score = -float("inf")
    best_overall_metrics = None
    results = []

    for name, search_fn, weight in strategy_pool:
        budget = time_budget * weight
        print(f"\n搜索策略: {name} (时间预算 {budget:.0f}s)")
        params, score, metrics = search_fn(df, budget)
        results.append((name, params, score, metrics))
        if score > best_overall_score:
            best_overall_score = score
            best_overall_name = name
            best_overall_params = params
            best_overall_metrics = metrics

    # 4. 汇总输出
    print(f"\n{'=' * 60}")
    print("策略竞争结果汇总")
    print(f"{'=' * 60}")
    print(f"{'策略':<15} {'评分':>8} {'收益':>8} {'夏普':>8} {'回撤':>8} {'交易数':>8}")
    print("-" * 60)
    for name, params, score, metrics in results:
        if metrics:
            ret = metrics.get("total_return", 0) * 100
            sharpe = metrics.get("sharpe_ratio", 0)
            dd = metrics.get("max_drawdown", 0) * 100
            trades = len([t for t in metrics.get("trades", []) if t.get("pnl") is not None])
            print(f"{name:<15} {score:>8.4f} {ret:>+7.2f}% {sharpe:>8.2f} {dd:>+7.1f}% {trades:>8}")
        else:
            print(f"{name:<15} {score:>8.4f} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>8}")

    print(f"\n最佳策略: {best_overall_name}")
    if best_overall_metrics:
        print(
            f"全量回测: 评分={best_overall_score:.4f} | "
            f"收益={best_overall_metrics['total_return'] * 100:+.2f}% | "
            f"夏普={best_overall_metrics['sharpe_ratio']:.2f} | "
            f"DD={best_overall_metrics['max_drawdown'] * 100:.1f}% | "
            f"WR={best_overall_metrics['win_rate'] * 100:.1f}%"
        )

    # 5. 最终全量评估（使用最佳参数）
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    strategy_cls = None
    if best_overall_name == "trendfollow":
        strategy_cls = TrendFollowStrategy
    elif best_overall_name == "adaptive":
        strategy_cls = AdaptiveHybridStrategy
    elif best_overall_name == "hybrid_mm":
        strategy_cls = HybridMeanRevMomentumStrategy

    if strategy_cls and best_overall_params:
        import inspect

        sig = inspect.signature(strategy_cls.__init__)
        valid_keys = set(sig.parameters.keys()) - {"self"}
        filtered_params = {k: v for k, v in best_overall_params.items() if k in valid_keys}
        final_strategy = strategy_cls(**filtered_params)
        final_signals = final_strategy.generate_signals(df)

        if best_overall_name == "trendfollow":
            min_idx = max(
                best_overall_params.get("long_ma_period", 100),
                best_overall_params.get("pull_ma_period", 20),
                best_overall_params.get("atr_period", 14),
            )
        elif best_overall_name == "adaptive":
            min_idx = max(
                best_overall_params.get("rsi_period", 14),
                best_overall_params.get("ma_period", 20),
                best_overall_params.get("adx_period", 14) * 2,
                best_overall_params.get("trend_long_ma", 100),
                best_overall_params.get("trend_pull_ma", 10),
                best_overall_params.get("atr_period", 14),
            )
        else:  # hybrid_mm
            min_idx = max(
                best_overall_params.get("rsi_period", 14),
                best_overall_params.get("ma_period", 20),
                best_overall_params.get("atr_period", 14),
            )

        evaluator = StrategyEvaluator()
        final_score, final_metrics, final_trades = evaluator.evaluate(
            final_signals[min_idx:],
            prices[min_idx:],
            df.iloc[min_idx:].reset_index(drop=True),
        )
        n_trades = len([t for t in final_trades if t.get("pnl") is not None])
        print(
            f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
            f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:.1f}% | "
            f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
        )

        # 合并趋势信息到 metrics 中
        final_metrics["regime"] = regime
        final_metrics["regime_info"] = regime_info

        return best_overall_params, final_score, final_metrics, best_overall_name

    return best_overall_params, best_overall_score, best_overall_metrics, best_overall_name


# ---------------------------------------------------------------------------
# Walk-Forward 验证搜索（市场状态自适应混合）
# ---------------------------------------------------------------------------


def walk_forward_adaptive_search(df, time_budget=TIME_BUDGET, n_windows=5):
    """
    Walk-Forward 验证的市场状态自适应混合策略搜索。
    ADX 判市：震荡市=RSI均值回归，趋势市=EMA趋势跟随。
    """
    n = len(df)
    seg_size = n // (n_windows + 1)

    evaluator = StrategyEvaluator()

    grid = {
        "rsi_low": [25, 30, 35],
        "rsi_high": [65, 70, 75],
        "ma_period": [10, 20],
        "trend_long_ma": [50, 100, 200],
        "trend_pull_ma": [10, 20],
        "adx_threshold": [20, 25, 30],
        "adx_period": [14],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.5],
        "max_hold_bars": [12, 24],
    }

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    print("Walk-Forward 验证搜索 (市场状态自适应: ADX判市, 震荡=RSI均值回归, 趋势=EMA趋势跟随)")
    print(f"  总数据: {n} 条K线, {n_windows} 个窗口, 每窗口 {seg_size} 条")
    print(f"  参数组合: {total_combos}, 总时间预算: {time_budget:.0f}s")
    print()

    per_window_budget = time_budget * 0.85 / n_windows
    window_champions = []
    all_cross_scores = {}
    t_total_start = time.time()

    for w_idx in range(n_windows):
        train_end = (w_idx + 1) * seg_size
        val_start = train_end
        val_end = min(val_start + seg_size, n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        val_df = df.iloc[val_start:val_end].reset_index(drop=True)
        val_prices = val_df["close"].values.astype(float)

        print(f"{'─' * 60}")
        print(f"窗口 {w_idx + 1}/{n_windows}: 训练 [{0}:{train_end}] 验证 [{val_start}:{val_end}]")

        best_score = -float("inf")
        best_params = None
        best_metrics = None
        tried = 0
        t_w_start = time.time()

        for rsi_l in grid["rsi_low"]:
            for rsi_h in grid["rsi_high"]:
                for ma_p in grid["ma_period"]:
                    for trend_long in grid["trend_long_ma"]:
                        for trend_pull in grid["trend_pull_ma"]:
                            for adx_th in grid["adx_threshold"]:
                                for adx_p in grid["adx_period"]:
                                    for atr_p in grid["atr_period"]:
                                        for atr_m in grid["atr_multiplier"]:
                                            for max_hold in grid["max_hold_bars"]:
                                                if time.time() - t_w_start > per_window_budget:
                                                    break
                                                tried += 1

                                                strategy = AdaptiveHybridStrategy(
                                                    rsi_low=rsi_l,
                                                    rsi_high=rsi_h,
                                                    ma_period=ma_p,
                                                    trend_long_ma=trend_long,
                                                    trend_pull_ma=trend_pull,
                                                    adx_threshold=adx_th,
                                                    adx_period=adx_p,
                                                    atr_period=atr_p,
                                                    atr_multiplier=atr_m,
                                                    max_hold_bars=max_hold,
                                                    enable_short=True,
                                                )

                                                try:
                                                    signals = strategy.generate_signals(val_df)
                                                    min_idx = max(
                                                        rsi_l,
                                                        ma_p,
                                                        adx_p * 2,
                                                        trend_long,
                                                        trend_pull,
                                                        atr_p,
                                                    )
                                                    score, metrics, trades = evaluator.evaluate(
                                                        signals[min_idx:],
                                                        val_prices[min_idx:],
                                                        val_df.iloc[min_idx:].reset_index(
                                                            drop=True
                                                        ),
                                                    )
                                                except Exception:
                                                    score = 0.0
                                                    metrics = {}
                                                    trades = []

                                                if score > best_score:
                                                    best_score = score
                                                    best_params = {
                                                        "rsi_period": 14,
                                                        "rsi_low": rsi_l,
                                                        "rsi_high": rsi_h,
                                                        "ma_period": ma_p,
                                                        "trend_long_ma": trend_long,
                                                        "trend_pull_ma": trend_pull,
                                                        "adx_threshold": adx_th,
                                                        "adx_period": adx_p,
                                                        "atr_period": atr_p,
                                                        "atr_multiplier": atr_m,
                                                        "max_hold_bars": max_hold,
                                                        "enable_short": True,
                                                    }
                                                    best_metrics = metrics

        w_time = time.time() - t_w_start

        if best_params and best_metrics:
            trades_list = best_metrics and trades or []
            n_trades = len([t for t in trades_list if t.get("pnl") is not None])
            print(
                f"  窗口最优: rsiL={best_params['rsi_low']} rsiH={best_params['rsi_high']} "
                f"ma={best_params['ma_period']} trendL={best_params['trend_long_ma']} "
                f"trendP={best_params['trend_pull_ma']} adx_th={best_params['adx_threshold']} "
                f"atr_p={best_params['atr_period']} atr_m={best_params['atr_multiplier']} "
                f"hold={best_params['max_hold_bars']}"
            )
            print(
                f"  验证评分={best_score:.4f} | 收益={best_metrics['total_return'] * 100:+.2f}% | "
                f"夏普={best_metrics['sharpe_ratio']:.2f} | DD={best_metrics['max_drawdown'] * 100:+.1f}% | "
                f"交易={n_trades} | 耗时={w_time:.1f}s"
            )
            window_champions.append(
                {
                    "window": w_idx,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
            )

            p_key = (
                f"rsiL{best_params['rsi_low']}_rsiH{best_params['rsi_high']}"
                f"_ma{best_params['ma_period']}_tL{best_params['trend_long_ma']}"
                f"_tP{best_params['trend_pull_ma']}_adx{best_params['adx_threshold']}"
                f"_ap{best_params['atr_period']}_am{best_params['atr_multiplier']}"
                f"_h{best_params['max_hold_bars']}"
            )
            if p_key not in all_cross_scores:
                all_cross_scores[p_key] = {"params": best_params, "scores": [], "returns": []}
            all_cross_scores[p_key]["scores"].append(best_score)
            all_cross_scores[p_key]["returns"].append(best_metrics.get("total_return", 0))
        else:
            print(f"  未找到有效参数 (耗时 {w_time:.1f}s)")

        if time.time() - t_total_start > time_budget * 0.95:
            print(f"\n总时间预算即将耗尽，提前结束（完成 {w_idx + 1}/{n_windows} 窗口）")
            break

    # 跨窗口分析
    print(f"\n{'=' * 60}")
    print("跨窗口稳健性分析")
    print(f"{'=' * 60}")

    if not all_cross_scores:
        print("未找到任何有效参数")
        return None, 0, {}

    ranked = []
    for p_key, data in all_cross_scores.items():
        scores = data["scores"]
        returns = data["returns"]
        avg_score = np.mean(scores)
        min_score = min(scores)
        avg_return = np.mean(returns)
        cv = np.std(scores) / (avg_score + 0.001)
        robustness = avg_score * (1.0 - min(cv, 0.5))
        ranked.append(
            {
                "key": p_key,
                "params": data["params"],
                "avg_score": avg_score,
                "min_score": min_score,
                "robustness": robustness,
                "avg_return": avg_return,
                "n_windows": len(scores),
                "scores": scores,
            }
        )

    ranked.sort(key=lambda x: x["robustness"], reverse=True)

    print(f"{'参数':75s} {'窗口':>5s} {'平均分':>8s} {'最低分':>8s} {'稳健分':>8s} {'均收益':>8s}")
    print("-" * 115)
    for r in ranked[:15]:
        print(
            f"{r['key']:75s} {r['n_windows']:>5d} {r['avg_score']:>8.4f} {r['min_score']:>8.4f} {r['robustness']:>8.4f} {r['avg_return'] * 100:>+7.2f}%"
        )

    champion = ranked[0]
    cp = champion["params"]
    print(
        f"\n稳健冠军: rsiL={cp['rsi_low']} rsiH={cp['rsi_high']} ma={cp['ma_period']} "
        f"trendL={cp['trend_long_ma']} trendP={cp['trend_pull_ma']} "
        f"adx_th={cp['adx_threshold']} atr_p={cp['atr_period']} atr_m={cp['atr_multiplier']} "
        f"hold={cp['max_hold_bars']}"
    )
    print(f"跨窗口平均评分: {champion['avg_score']:.4f} (最低: {champion['min_score']:.4f})")
    print(f"跨窗口平均收益: {champion['avg_return'] * 100:+.2f}%")

    # 全量数据评估
    print(f"\n{'=' * 60}")
    print("冠军参数全量数据评估")
    print(f"{'=' * 60}")

    final_strategy = AdaptiveHybridStrategy(**cp)
    final_signals = final_strategy.generate_signals(df)
    min_idx = max(
        cp["rsi_period"],
        cp["ma_period"],
        cp["adx_period"] * 2,
        cp["trend_long_ma"],
        cp["trend_pull_ma"],
        cp["atr_period"],
    )
    final_score, final_metrics, final_trades = evaluator.evaluate(
        final_signals[min_idx:],
        df["close"].values[min_idx:],
        df.iloc[min_idx:].reset_index(drop=True),
    )
    n_trades = len([t for t in final_trades if t.get("pnl") is not None])
    print(
        f"全量数据: score={final_score:.4f} | ret={final_metrics['total_return'] * 100:+.2f}% | "
        f"sharpe={final_metrics['sharpe_ratio']:.2f} | DD={final_metrics['max_drawdown'] * 100:+.1f}% | "
        f"WR={final_metrics['win_rate'] * 100:.1f}% | trades={n_trades}"
    )

    elapsed = time.time() - t_total_start
    print(f"\nWalk-Forward 搜索完成, 总耗时 {elapsed:.1f}s")

    return cp, champion["robustness"], final_metrics


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------


def archive_results(
    mode, symbol, best_params, best_metrics, df, checkpoint_path, strategy_name=None
):
    """
    归档训练结果。
    保存模型、参数、指标、回测数据（equity/trades）和说明文档到时间戳目录。
    strategy_name: smart 模式下实际使用的策略类型名称
    """
    if not best_params or not best_metrics:
        print("参数或指标为空，跳过归档")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"{timestamp}_{symbol}_{mode}"
    archive_dir = os.path.join(PROJECT_DIR, "archives", archive_name)
    os.makedirs(archive_dir, exist_ok=True)

    # 1. 复制模型文件
    if os.path.exists(checkpoint_path):
        shutil.copy2(checkpoint_path, os.path.join(archive_dir, "quant_model.pt"))

    # 2. 保存参数
    with open(os.path.join(archive_dir, "params.json"), "w", encoding="utf-8") as f:
        json.dump(best_params, f, indent=2, ensure_ascii=False)

    # 3. 保存指标
    with open(os.path.join(archive_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(best_metrics, f, indent=2, ensure_ascii=False)

    # 4. 根据 mode 选择策略类，重新生成信号和回测数据
    strategy_cls = None
    if mode in ("pure", "pure_wf", "pure_trend", "pure_adx"):
        strategy_cls = PureActionStrategy
    elif mode == "hybrid":
        strategy_cls = HybridStrategy
    elif mode == "trendfollow":
        strategy_cls = TrendFollowStrategy
    elif mode == "hybrid_mm":
        strategy_cls = HybridMeanRevMomentumStrategy
    elif mode == "adaptive":
        strategy_cls = AdaptiveHybridStrategy
    elif mode == "smart":
        # smart 模式下根据实际选中的策略类型决定类
        if strategy_name == "trendfollow":
            strategy_cls = TrendFollowStrategy
        elif strategy_name == "hybrid_mm":
            strategy_cls = HybridMeanRevMomentumStrategy
        else:
            strategy_cls = AdaptiveHybridStrategy
    elif mode == "trend":
        strategy_cls = TrendStrategy
    elif mode == "scalp":
        strategy_cls = ScalpStrategy

    if strategy_cls is not None:
        try:
            # 过滤掉策略类不认识的参数
            sig = inspect.signature(strategy_cls.__init__)
            valid_keys = set(sig.parameters.keys()) - {"self"}
            filtered_params = {k: v for k, v in best_params.items() if k in valid_keys}

            strategy = strategy_cls(**filtered_params)

            # generate_signals 参数差异处理
            if mode in ("trend", "scalp"):
                signals = strategy.generate_signals(df, enable_short=True)
            else:
                signals = strategy.generate_signals(df)

            prices = df["close"].values.astype(float)
            evaluator = StrategyEvaluator()
            equity, trades = evaluator.simulate(signals, prices, df)

            # 保存权益曲线
            timestamps = (
                df["timestamp"].values if "timestamp" in df.columns else list(range(len(equity)))
            )
            equity_df = pd.DataFrame(
                {
                    "step": list(range(len(equity))),
                    "timestamp": timestamps[: len(equity)],
                    "equity": equity,
                }
            )
            equity_df.to_csv(os.path.join(archive_dir, "equity.csv"), index=False, encoding="utf-8")

            # 保存交易记录
            if trades:
                trades_df = pd.DataFrame(trades)
                trades_df.to_csv(
                    os.path.join(archive_dir, "trades.csv"), index=False, encoding="utf-8"
                )

            n_trades = len([t for t in trades if t.get("pnl") is not None])
        except Exception as e:
            print(f"回测数据生成失败: {e}")
            n_trades = 0
    else:
        n_trades = 0

    # 5. 生成 README.md
    readme_lines = [
        f"# {symbol} {mode} 策略归档",
        "",
        f"- **归档时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **交易对**: {symbol}",
        f"- **策略模式**: {mode}",
        f"- **数据量**: {len(df)} 条K线",
        f"- **价格范围**: {df['close'].min():.2f} - {df['close'].max():.2f}",
    ]

    # smart 模式添加趋势判定信息
    if mode == "smart" and best_metrics.get("regime"):
        readme_lines.append(f"- **市场状态**: {best_metrics['regime']}")
        if best_metrics.get("regime_info"):
            info = best_metrics["regime_info"]
            readme_lines.append(f"- **ADX**: {info['adx']:.1f}")
            readme_lines.append(f"- **EMA趋势**: {info['ema50_vs_ema200']}")
            readme_lines.append(f"- **价格偏离EMA200**: {info['price_vs_ema200_pct']:+.2f}%")
            readme_lines.append(f"- **年化波动率**: {info['volatility_annualized'] * 100:.1f}%")
        if strategy_name:
            readme_lines.append(f"- **选中策略**: {strategy_name}")

    readme_lines.extend(
        [
            "",
            "## 回测指标",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 综合评分 | {best_metrics.get('score', best_metrics.get('total_return', 0)):.6f} |",
            f"| 总收益率 | {best_metrics.get('total_return', 0) * 100:.2f}% |",
            f"| 年化收益率 | {best_metrics.get('annualized_return', 0) * 100:.2f}% |",
            f"| 夏普比率 | {best_metrics.get('sharpe_ratio', 0):.4f} |",
            f"| 最大回撤 | {best_metrics.get('max_drawdown', 0) * 100:.2f}% |",
            f"| 胜率 | {best_metrics.get('win_rate', 0) * 100:.1f}% |",
            f"| 交易笔数 | {n_trades} |",
            "",
            "## 策略参数",
            "",
            "```json",
            json.dumps(best_params, indent=2, ensure_ascii=False),
            "```",
            "",
            "## 文件说明",
            "",
            "| 文件 | 说明 |",
            "|------|------|",
            "| `quant_model.pt` | PyTorch 模型/参数文件 |",
            "| `params.json` | 策略参数 JSON |",
            "| `metrics.json` | 回测指标 JSON |",
            "| `equity.csv` | 权益曲线（每行一个时间步） |",
            "| `trades.csv` | 交易记录（每笔交易的类型、步数、盈亏） |",
            "| `README.md` | 本说明文档 |",
            "",
        ]
    )

    with open(os.path.join(archive_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(readme_lines))

    print(f"\n归档完成: {archive_dir}")
    return archive_dir


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------


def main():
    t_start = time.time()

    # 解析命令行参数
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=[
            "trend",
            "scalp",
            "pure",
            "pure_wf",
            "pure_trend",
            "pure_adx",
            "hybrid",
            "trendfollow",
            "hybrid_mm",
            "adaptive",
            "smart",
        ],
        default="trend",
        help="策略模式: trend=趋势均值回归, scalp=高频剥头皮, pure=纯价格行为, pure_wf=WF, pure_trend=趋势对齐WF, pure_adx=ADX+趋势对齐WF, hybrid=市场自适应WF, trendfollow=趋势跟随回调, hybrid_mm=混合均值回归+动量, adaptive=市场状态自适应混合, smart=智能搜索(趋势感知+多策略竞争)",
    )
    parser.add_argument("--symbol", default=None, help="只训练指定币种 (如 ETHUSDT)")
    parser.add_argument(
        "--days", type=int, default=None, help="只使用最近 N 天的数据 (如 30 或 60)"
    )
    parser.add_argument(
        "--no-wf", action="store_true", help="禁用Walk-Forward验证，直接在全量数据上搜索最优参数"
    )
    args = parser.parse_args()
    mode = args.mode

    if mode == "scalp":
        print("=" * 60)
        print("高频剥头皮策略训练 (ScalpStrategy)")
        print("=" * 60)
    elif mode == "pure":
        print("=" * 60)
        print("纯价格行为策略训练 (PureActionStrategy) - 无因子约束")
        print("=" * 60)
    elif mode == "pure_wf":
        print("=" * 60)
        print("纯价格行为策略训练 (PureActionStrategy) - Walk-Forward 验证")
        print("=" * 60)
    elif mode == "pure_trend":
        print("=" * 60)
        print("趋势对齐策略训练 (PureActionStrategy+TrendAlign) - Walk-Forward 验证")
        print("=" * 60)
    elif mode == "pure_adx":
        print("=" * 60)
        print("ADX趋势强度策略训练 (PureActionStrategy+TrendAlign+ADX) - Walk-Forward 验证")
        print("=" * 60)
    elif mode == "hybrid":
        print("=" * 60)
        print("市场状态自适应策略训练 (HybridStrategy) - Walk-Forward 验证")
        print("=" * 60)
    elif mode == "trendfollow":
        print("=" * 60)
        print("趋势跟随策略训练 (TrendFollowStrategy) - Walk-Forward 验证")
        print("=" * 60)
    elif mode == "hybrid_mm":
        print("=" * 60)
        print("混合均值回归+动量策略训练 (HybridMeanRevMomentumStrategy) - Walk-Forward 验证")
        print("=" * 60)
    elif mode == "adaptive":
        print("=" * 60)
        print("市场状态自适应混合策略训练 (AdaptiveHybridStrategy) - Walk-Forward 验证")
        print("=" * 60)
    elif mode == "smart":
        print("=" * 60)
        print("智能策略搜索 (趋势感知 + 多策略竞争)")
        print("=" * 60)
    else:
        print("=" * 60)
        print("加密货币量化策略训练 (布林带均值回归 + 强趋势过滤 - 多空双向)")
        print("=" * 60)

    data_files = list_crypto_files()
    if not data_files:
        print("错误: 未找到数据文件。请先运行 python prepare_crypto.py")
        return

    # 按 --symbol 过滤
    if args.symbol:
        sym = args.symbol.upper()
        data_files = [f for f in data_files if sym in os.path.basename(f).upper()]
        if not data_files:
            print(f"错误: 未找到 {sym} 数据文件")
            return

    print(f"找到 {len(data_files)} 个数据文件")
    for f in data_files:
        print(f"  {os.path.basename(f)}")

    per_symbol_budget = TIME_BUDGET / len(data_files)
    all_results = []

    for fp in data_files:
        symbol = os.path.basename(fp).replace("_5m.parquet", "").replace("_1m.parquet", "")
        print(f"\n{'=' * 60}")
        print(f"训练币种: {symbol}")
        print(f"{'=' * 60}")

        df = load_crypto_data(fp)
        df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
        # smart 模式默认使用60天数据
        days_to_use = args.days if args.days else (60 if mode == "smart" else None)
        if days_to_use:
            n_bars = days_to_use * 288  # 5分钟K线，每天288条
            if len(df) > n_bars:
                df = df.iloc[-n_bars:].reset_index(drop=True)
                print(f"已截取最近 {days_to_use} 天数据: {len(df)} 条K线")
        print(
            f"数据量: {len(df)} 条K线, 价格范围: {df['close'].min():.2f} - {df['close'].max():.2f}"
        )

        if mode == "scalp":
            best_score, best_params, best_metrics, _ = scalp_grid_search(df, per_symbol_budget)
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "pure":
            best_params, best_score, best_metrics = pure_grid_search(df, per_symbol_budget)
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "pure_wf":
            best_params, best_score, best_metrics = walk_forward_pure_search(df, per_symbol_budget)
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "pure_trend":
            best_params, best_score, best_metrics = walk_forward_trend_search(df, per_symbol_budget)
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "pure_adx":
            best_params, best_score, best_metrics = walk_forward_adx_search(df, per_symbol_budget)
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "hybrid":
            best_params, best_score, best_metrics = walk_forward_hybrid_search(
                df, per_symbol_budget
            )
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "trendfollow":
            best_params, best_score, best_metrics = walk_forward_trendfollow_search(
                df, per_symbol_budget
            )
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "hybrid_mm":
            best_params, best_score, best_metrics = walk_forward_hybrid_mm_search(
                df, per_symbol_budget
            )
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "adaptive":
            if args.no_wf:
                best_params, best_score, best_metrics = direct_adaptive_search(
                    df, per_symbol_budget
                )
            else:
                best_params, best_score, best_metrics = walk_forward_adaptive_search(
                    df, per_symbol_budget
                )
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )
        elif mode == "smart":
            best_params, best_score, best_metrics, best_strategy_name = smart_search(
                df, per_symbol_budget
            )
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                    "strategy_name": best_strategy_name,
                }
            )
        else:
            best_params, best_score, best_metrics = grid_search(df, per_symbol_budget)
            all_results.append(
                {
                    "symbol": symbol,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                    "df": df,
                    "filepath": fp,
                }
            )

    # 选择综合表现最好的参数
    valid_results = [r for r in all_results if r["params"] is not None and r["score"] > 0]
    if not valid_results:
        print("\n未找到有效参数组合")
        return None, None

    valid_results.sort(key=lambda r: r["score"], reverse=True)
    best_result = valid_results[0]
    best_params = best_result["params"]
    best_score = best_result["score"]
    best_metrics = best_result["metrics"]

    # 保存最优参数
    checkpoint_dir = os.path.join(PROJECT_DIR, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, "quant_model.pt")

    strategy_name = {
        "adaptive": "adaptive",
        "hybrid_mm": "hybrid_mm",
        "trendfollow": "trendfollow",
        "scalp": "scalp",
        "hybrid": "hybrid",
        "pure_adx": "pure_adx",
        "pure_trend": "pure_trend_align",
        "pure_wf": "pure_action_wf",
        "pure": "pure_action",
        "smart": best_result.get("strategy_name", "smart"),
    }.get(mode, "bollinger_trend_filter")
    checkpoint = {
        "strategy": strategy_name,
        "params": best_params,
        "score": best_score,
        "metrics": best_metrics,
        "all_results": [
            {
                "symbol": r["symbol"],
                "score": r["score"],
                "return": r["metrics"]["total_return"] if r["metrics"] else 0,
                "sharpe": r["metrics"]["sharpe_ratio"] if r["metrics"] else 0,
            }
            for r in valid_results
        ],
    }
    torch.save(checkpoint, checkpoint_path)
    print(f"\n最优参数已保存: {checkpoint_path}")

    print("\n" + "=" * 60)
    print("最优参数与回测结果")
    print("=" * 60)
    print(f"来源币种:       {best_result['symbol']}")
    if mode == "scalp":
        print(f"布林带周期:     {best_params.get('window', '?')}")
        print(f"标准差倍数:     {best_params.get('std_dev', '?')}")
        print(f"止盈:           {best_params.get('take_profit_pct', 0) * 100:.2f}%")
        print(f"止损:           {best_params.get('stop_loss_pct', 0) * 100:.2f}%")
        print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        active = []
        if best_params.get("use_volume_filter"):
            active.append(f"volume>={best_params.get('volume_threshold', 0.8)}")
        if best_params.get("rsi_extreme_low", 20) != 20:
            active.append(
                f"RSI guard [{best_params.get('rsi_extreme_low')}, {best_params.get('rsi_extreme_high')}]"
            )
        print(f"活跃指标:       {', '.join(active) if active else '无'}")
    elif mode == "trendfollow":
        print(f"趋势方向EMA:    {best_params.get('long_ma_period', '?')} (EMA定趋势方向)")
        print(f"回调入场EMA:    {best_params.get('pull_ma_period', '?')} (EMA回调入场)")
        print(f"ATR周期:        {best_params.get('atr_period', '?')}")
        print(f"ATR止损倍数:    {best_params.get('atr_multiplier', '?')}")
        print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        print(f"回调容忍度:     {best_params.get('entry_zone', 0.0)}")
        print("活跃指标:       纯趋势跟随 (顺势回调入场)")
    elif mode == "hybrid_mm":
        print(f"RSI周期:        {best_params.get('rsi_period', '?')}")
        print(f"RSI超卖阈值:    {best_params.get('rsi_low', '?')} (RSI<阈值且价格在EMA上方才做多)")
        print(f"RSI超买阈值:    {best_params.get('rsi_high', '?')} (RSI>阈值且价格在EMA下方才做空)")
        print(f"动量EMA周期:    {best_params.get('ma_period', '?')} (趋势过滤)")
        print(f"ATR周期:        {best_params.get('atr_period', '?')}")
        print(f"ATR止损倍数:    {best_params.get('atr_multiplier', '?')}")
        print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        print("活跃指标:       RSI极端值 + EMA动量过滤 (混合策略)")
    elif mode == "adaptive":
        print(f"RSI超卖阈值:    {best_params.get('rsi_low', '?')} (震荡市做多)")
        print(f"RSI超买阈值:    {best_params.get('rsi_high', '?')} (震荡市做空)")
        print(f"震荡市EMA:      {best_params.get('ma_period', '?')} (均值回归动量过滤)")
        print(f"趋势市长EMA:    {best_params.get('trend_long_ma', '?')} (趋势方向)")
        print(f"趋势市短EMA:    {best_params.get('trend_pull_ma', '?')} (回调入场)")
        print(
            f"ADX阈值:        {best_params.get('adx_threshold', '?')} (>阈值=趋势市, <=阈值=震荡市)"
        )
        print(f"ATR周期:        {best_params.get('atr_period', '?')}")
        print(f"ATR止损倍数:    {best_params.get('atr_multiplier', '?')}")
        print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        print("活跃指标:       ADX判市 + 震荡市RSI均值回归 + 趋势市EMA趋势跟随")
    elif mode == "smart":
        print(f"选中策略:       {best_result.get('strategy_name', '?')}")
        print(f"市场状态:       {best_metrics.get('regime', '?')}")
        if best_metrics.get("regime_info"):
            info = best_metrics["regime_info"]
            print(
                f"  ADX={info['adx']:.1f} | EMA趋势={info['ema50_vs_ema200']} | "
                f"价格偏离EMA200={info['price_vs_ema200_pct']:+.2f}% | "
                f"年化波动率={info['volatility_annualized'] * 100:.1f}%"
            )
        if best_result.get("strategy_name") == "trendfollow":
            print(f"趋势方向EMA:    {best_params.get('long_ma_period', '?')}")
            print(f"回调入场EMA:    {best_params.get('pull_ma_period', '?')}")
            print(f"ATR止损倍数:    {best_params.get('atr_multiplier', '?')}")
            print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        elif best_result.get("strategy_name") == "hybrid_mm":
            print(f"RSI超卖阈值:    {best_params.get('rsi_low', '?')}")
            print(f"RSI超买阈值:    {best_params.get('rsi_high', '?')}")
            print(f"动量EMA周期:    {best_params.get('ma_period', '?')}")
            print(f"ATR止损倍数:    {best_params.get('atr_multiplier', '?')}")
            print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        else:  # adaptive
            print(f"RSI超卖阈值:    {best_params.get('rsi_low', '?')}")
            print(f"RSI超买阈值:    {best_params.get('rsi_high', '?')}")
            print(f"趋势市长EMA:    {best_params.get('trend_long_ma', '?')}")
            print(f"ADX阈值:        {best_params.get('adx_threshold', '?')}")
            print(f"ATR止损倍数:    {best_params.get('atr_multiplier', '?')}")
            print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        print("活跃指标:       智能选择 (趋势感知 + 多策略竞争)")
    elif mode in ("pure", "pure_wf", "pure_trend", "pure_adx", "hybrid"):
        print(f"布林带周期:     {best_params.get('window', '?')}")
        print(f"标准差倍数:     {best_params.get('std_dev', '?')}")
        print(f"ATR周期:        {best_params.get('atr_period', '?')}")
        print(f"ATR止损倍数:    {best_params.get('atr_multiplier', '?')}")
        print(f"最大持仓K线:   {best_params.get('max_hold_bars', '?')}")
        print(f"入场提前量:     {best_params.get('entry_zone', 0.0)}")
        if best_params.get("trend_ma_period"):
            print(f"趋势对齐MA:     {best_params['trend_ma_period']} (趋势方向判定)")
        if best_params.get("adx_threshold") is not None:
            if mode == "hybrid":
                print(
                    f"ADX判市阈值:    {best_params['adx_threshold']} (<=阈值震荡均值回归, >阈值趋势跟随)"
                )
            else:
                print(
                    f"ADX阈值:        {best_params['adx_threshold']} (adx_period={best_params.get('adx_period', 14)}, ADX>阈值时空仓避险)"
                )
        if mode == "hybrid":
            print("活跃指标:       市场状态自适应 (震荡=均值回归, 趋势=趋势跟随)")
        elif (
            best_params.get("adx_threshold") is None and best_params.get("trend_ma_period") is None
        ):
            print("活跃指标:       无（纯价格行为，无因子约束）")
        elif best_params.get("adx_threshold") is not None:
            print("活跃指标:       ADX趋势强度过滤 + 趋势对齐")
        else:
            print("活跃指标:       趋势对齐（方案C）")
    elif best_params:
        print(f"布林带周期:     {best_params['window']}")
        print(f"标准差倍数:     {best_params['std_dev']}")
        print(f"ATR止损倍数:    {best_params['atr_multiplier']}")
        print(f"最大持仓K线:   {best_params['max_hold_bars']}")
        print(f"RSI阈值:        {best_params.get('rsi_threshold', 30)}")
        print(f"入场提前量:     {best_params.get('entry_zone', 0.0)}")
        indicator_keys = [
            "use_adx",
            "adx_threshold",
            "use_volume",
            "volume_threshold",
            "use_macd",
            "macd_confirm_mode",
            "use_ma_cross",
            "use_mfi",
            "mfi_period",
            "mfi_threshold",
            "use_stochastic",
            "stoch_period",
            "stoch_threshold",
            "use_rsi_divergence",
            "rsi_divergence_lookback",
            "use_macd_divergence",
            "macd_divergence_lookback",
            "use_trend_filter",
            "trend_window",
            "use_obv_trend",
            "obv_ma_period",
            "use_volume_spike",
            "volume_spike_threshold",
            "use_vwap",
            "vwap_period",
            "use_htf_macd",
            "use_resonance",
            "resonance_min_score",
        ]
        active_indicators = []
        for k in indicator_keys:
            v = best_params.get(k)
            if v is not None and (k.startswith("use_") and v is True or not k.startswith("use_")):
                active_indicators.append(f"{k}={v}")
        if any(best_params.get(k) for k in indicator_keys if k.startswith("use_")):
            print(f"活跃指标:       {', '.join(active_indicators)}")
        else:
            print("活跃指标:       无（纯布林带策略）")
    print(f"综合评分:       {best_score:.6f}")
    print(f"夏普比率:       {best_metrics.get('sharpe_ratio', 0):.4f}")
    print(f"总收益率:       {best_metrics.get('total_return', 0) * 100:.2f}%")
    print(f"年化收益率:     {best_metrics.get('annualized_return', 0) * 100:.2f}%")
    print(f"年化波动率:     {best_metrics.get('annualized_vol', 0) * 100:.2f}%")
    print(f"最大回撤:       {best_metrics.get('max_drawdown', 0) * 100:.2f}%")
    print(f"胜率:           {best_metrics.get('win_rate', 0) * 100:.1f}%")
    print(f"总耗时:         {time.time() - t_start:.1f}s")

    # 归档保存
    if best_result:
        archive_results(
            mode=mode,
            symbol=best_result["symbol"],
            best_params=best_params,
            best_metrics=best_metrics,
            df=best_result["df"],
            checkpoint_path=checkpoint_path,
            strategy_name=best_result.get("strategy_name"),
        )

    return best_score, best_metrics


if __name__ == "__main__":
    main()
