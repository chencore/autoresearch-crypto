"""
验证当前策略在最新SOL数据上的信号表现。
使用本地 Parquet 数据（因 Binance API 被墙）。
"""
import os
import sys
import numpy as np
import pandas as pd

from train_quant import BollingerStrategy

DATA_PATH = "data/crypto/SOLUSDT_5m.parquet"

# 当前最佳参数（来自 checkpoints/quant_model.pt）
PARAMS = {
    "window": 15,
    "std_dev": 3.0,
    "atr_multiplier": 2.0,
    "max_hold_bars": 12,
    "rsi_threshold": 30,
    "entry_zone": 0.0,
    "use_macd": True,
    "macd_confirm_mode": "direction",
    "use_rsi_divergence": True,
    "rsi_divergence_lookback": 5,
    "use_macd_divergence": True,
    "macd_divergence_lookback": 8,
}


def load_data():
    try:
        import pyarrow.parquet as pq
        df = pq.read_table(DATA_PATH).to_pandas()
    except Exception:
        df = pd.read_parquet(DATA_PATH)
    return df


def analyze_signals(df, strategy, n_last=50):
    """分析最近 n_last 根 K 线的信号"""
    signals = strategy.generate_signals(df, enable_short=True)

    df = df.copy()
    df["signal"] = signals

    # 只看最近 n_last 根
    recent = df.iloc[-n_last:].copy()

    # 计算指标用于显示
    close = recent["close"].values
    rolling_mean = pd.Series(close).rolling(window=strategy.window, min_periods=strategy.window).mean()
    rolling_std = pd.Series(close).rolling(window=strategy.window, min_periods=strategy.window).std()
    upper = rolling_mean + strategy.std_dev * rolling_std
    lower = rolling_mean - strategy.std_dev * rolling_std

    recent["bb_upper"] = upper.values
    recent["bb_mid"] = rolling_mean.values
    recent["bb_lower"] = lower.values

    # 重新计算RSI和MACD用于显示
    def _compute_rsi(close_vals, period=14):
        deltas = np.diff(close_vals, prepend=close_vals[0])
        gain = np.where(deltas > 0, deltas, 0.0)
        loss = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.zeros(len(close_vals))
        avg_loss = np.zeros(len(close_vals))
        avg_gain[period] = np.mean(gain[1:period+1])
        avg_loss[period] = np.mean(loss[1:period+1])
        for i in range(period + 1, len(close_vals)):
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
        rsi = np.full(len(close_vals), 50.0)
        for i in range(period, len(close_vals)):
            if avg_loss[i] > 0:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100.0 - 100.0 / (1.0 + rs)
            else:
                rsi[i] = 100.0
        return rsi

    def _compute_ema(series, period):
        alpha = 2.0 / (period + 1)
        ema = np.empty_like(series, dtype=float)
        ema[0] = series[0]
        for i in range(1, len(series)):
            ema[i] = alpha * series[i] + (1 - alpha) * ema[i-1]
        return ema

    def _compute_macd(close_vals, fast=12, slow=26, signal=9):
        ema_fast = _compute_ema(close_vals, fast)
        ema_slow = _compute_ema(close_vals, slow)
        macd_line = ema_fast - ema_slow
        signal_line = _compute_ema(macd_line, signal)
        macd_hist = macd_line - signal_line
        return macd_line, signal_line, macd_hist

    recent["rsi"] = _compute_rsi(close)
    macd_line, macd_signal_line, macd_hist = _compute_macd(close)
    recent["macd"] = macd_line
    recent["macd_signal"] = macd_signal_line
    recent["macd_hist"] = macd_hist

    print("=" * 100)
    print(f"策略参数: window={strategy.window}, std_dev={strategy.std_dev}, "
          f"atr_mult={strategy.atr_multiplier}, max_hold={strategy.max_hold_bars}")
    print(f"活跃因子: MACD确认={strategy.use_macd}, "
          f"RSI背离={strategy.use_rsi_divergence}(lookback={strategy.rsi_divergence_lookback}), "
          f"MACD背离={strategy.use_macd_divergence}(lookback={strategy.macd_divergence_lookback})")
    print("=" * 100)

    # 打印所有信号非1（持有）的bar
    signal_map = {0: "平仓", 1: "持有", 2: "做多", 3: "做空"}
    interesting = recent[recent["signal"] != 1]
    if len(interesting) == 0:
        print(f"最近 {n_last} 根K线中没有任何交易信号（全为持有）")
    else:
        print(f"\n最近 {n_last} 根K线中的交易信号 ({len(interesting)} 个):")
        print("-" * 100)
        for idx, row in interesting.iterrows():
            dt = row.get("datetime", row.get("timestamp", idx))
            sig_name = signal_map.get(int(row["signal"]), "未知")
            print(f"{dt} | 信号: {sig_name:4s} | 收盘: {row['close']:.2f} | "
                  f"布林: [{row['bb_lower']:.2f}, {row['bb_mid']:.2f}, {row['bb_upper']:.2f}] | "
                  f"RSI: {row['rsi']:.1f} | MACD柱: {row['macd_hist']:.4f}")

    # 打印最后10根K线的详细信息
    print(f"\n最后 10 根K线详情:")
    print("-" * 120)
    print(f"{'时间':20s} | {'信号':4s} | {'收盘':>6s} | {'上轨':>6s} | {'中轨':>6s} | {'下轨':>6s} | {'RSI':>5s} | {'MACD柱':>8s} | {'备注':20s}")
    print("-" * 120)
    for idx, row in recent.tail(10).iterrows():
        dt = row.get("datetime", row.get("timestamp", idx))
        if hasattr(dt, 'strftime'):
            dt_str = dt.strftime("%m-%d %H:%M")
        else:
            dt_str = str(dt)
        sig_name = signal_map.get(int(row["signal"]), "未知")

        # 判断距离布林带的位置
        note = ""
        if row["close"] >= row["bb_upper"]:
            note = "触及上轨"
        elif row["close"] <= row["bb_lower"]:
            note = "触及下轨"
        elif row["close"] > row["bb_mid"]:
            note = "中轨上方"
        else:
            note = "中轨下方"

        print(f"{dt_str:20s} | {sig_name:4s} | {row['close']:6.2f} | "
              f"{row['bb_upper']:6.2f} | {row['bb_mid']:6.2f} | {row['bb_lower']:6.2f} | "
              f"{row['rsi']:5.1f} | {row['macd_hist']:8.4f} | {note}")

    # 统计最近50根的信号分布
    sig_counts = recent["signal"].value_counts().sort_index()
    print(f"\n最近 {n_last} 根K线信号分布:")
    for sig, cnt in sig_counts.items():
        print(f"  {signal_map.get(int(sig), '未知')}: {cnt} 次")

    # 检查是否有背离
    print(f"\n背离检测 (最近 {n_last} 根):")
    div_found = False
    for i in range(strategy.rsi_divergence_lookback * 2, len(recent)):
        idx = recent.index[i]
        row = recent.iloc[i]
        if strategy._detect_rsi_divergence(close, recent["rsi"].values, i, strategy.rsi_divergence_lookback, "bullish"):
            dt = row.get("datetime", row.get("timestamp", idx))
            print(f"  RSI底背离 @ {dt} (价格={row['close']:.2f}, RSI={row['rsi']:.1f})")
            div_found = True
        if strategy._detect_rsi_divergence(close, recent["rsi"].values, i, strategy.rsi_divergence_lookback, "bearish"):
            dt = row.get("datetime", row.get("timestamp", idx))
            print(f"  RSI顶背离 @ {dt} (价格={row['close']:.2f}, RSI={row['rsi']:.1f})")
            div_found = True
        if strategy.use_macd_divergence:
            if strategy._detect_macd_divergence(close, recent["macd_hist"].values, i, strategy.macd_divergence_lookback, "bullish"):
                dt = row.get("datetime", row.get("timestamp", idx))
                print(f"  MACD底背离 @ {dt} (价格={row['close']:.2f}, MACD柱={row['macd_hist']:.4f})")
                div_found = True
            if strategy._detect_macd_divergence(close, recent["macd_hist"].values, i, strategy.macd_divergence_lookback, "bearish"):
                dt = row.get("datetime", row.get("timestamp", idx))
                print(f"  MACD顶背离 @ {dt} (价格={row['close']:.2f}, MACD柱={row['macd_hist']:.4f})")
                div_found = True
    if not div_found:
        print("  无")

    return recent


def main():
    if not os.path.exists(DATA_PATH):
        print(f"数据文件不存在: {DATA_PATH}")
        print("请先运行: uv run python prepare_crypto.py")
        sys.exit(1)

    df = load_data()
    print(f"加载数据: {len(df)} 行, 时间范围: {df['datetime'].min()} ~ {df['datetime'].max()}")

    strategy = BollingerStrategy(**PARAMS)
    analyze_signals(df, strategy, n_last=100)


if __name__ == "__main__":
    main()
