"""
SOL 最近 30 天回测验证脚本
基于策略.md：P6 高级别MACD + P7 多因子共振
"""
import os
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from train_quant import TrendStrategy, StrategyEvaluator

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "crypto")

# 加载数据
df_all = pq.read_table(os.path.join(DATA_DIR, "SOLUSDT_5m.parquet")).to_pandas()
ts = pd.to_datetime(df_all["timestamp"], unit="ms")
print(f"数据范围: {ts.iloc[0]} ~ {ts.iloc[-1]}")
print(f"总K线数: {len(df_all)}")

# 最近 30 天
last30_start = ts.iloc[-1] - pd.Timedelta(days=30)
mask = ts >= last30_start
df_30d = df_all[mask].reset_index(drop=True)
ts_30d = ts[mask].reset_index(drop=True)
print(f"\n最近 30 天: {ts_30d.iloc[0]} ~ {ts_30d.iloc[-1]}")
print(f"K线数: {len(df_30d)}")
print(f"SOL 价格: {df_30d['close'].iloc[0]:.2f} -> {df_30d['close'].iloc[-1]:.2f} "
      f"({(df_30d['close'].iloc[-1]/df_30d['close'].iloc[0]-1)*100:.2f}%)")

evaluator = StrategyEvaluator()

# 基础参数
base = {"window": 15, "std_dev": 2.5, "atr_multiplier": 1.5,
        "max_hold_bars": 12, "rsi_threshold": 30, "entry_zone": 0.0}

# 测试组合 — 按策略.md维度组织
configs = [
    # === 对照组 ===
    ("Baseline (无因子)", base),

    # === P4: 趋势过滤 ===
    ("TrendFilter w=50", {**base, "use_trend_filter": True, "trend_window": 50}),
    ("TrendFilter w=25", {**base, "use_trend_filter": True, "trend_window": 25}),

    # === P6: 高级别MACD趋势（策略.md 方法9）===
    ("HTF MACD", {**base, "use_htf_macd": True}),
    ("HTF MACD + Trend50", {**base, "use_htf_macd": True, "use_trend_filter": True, "trend_window": 50}),
    ("HTF MACD + Trend25", {**base, "use_htf_macd": True, "use_trend_filter": True, "trend_window": 25}),
    ("HTF MACD + Volume", {**base, "use_htf_macd": True, "use_volume": True, "volume_threshold": 1.0}),
    ("HTF MACD + OBV", {**base, "use_htf_macd": True, "use_obv_trend": True, "obv_ma_period": 20}),

    # === P7: 多因子共振（策略.md 核心规则，需搭配实际因子）===
    ("Res(MACD+Vol, min=3)", {**base, "use_resonance": True, "resonance_min_score": 3,
                               "use_macd": True, "macd_confirm_mode": "direction",
                               "use_volume": True, "volume_threshold": 1.0}),
    ("Res(MACD+OBV, min=3)", {**base, "use_resonance": True, "resonance_min_score": 3,
                               "use_macd": True, "macd_confirm_mode": "direction",
                               "use_obv_trend": True, "obv_ma_period": 20}),
    ("Res(Vol+OBV, min=3)", {**base, "use_resonance": True, "resonance_min_score": 3,
                              "use_volume": True, "volume_threshold": 1.0,
                              "use_obv_trend": True, "obv_ma_period": 20}),
    ("Res(MACD+Vol+OBV, min=3)", {**base, "use_resonance": True, "resonance_min_score": 3,
                                    "use_macd": True, "macd_confirm_mode": "direction",
                                    "use_volume": True, "volume_threshold": 1.0,
                                    "use_obv_trend": True, "obv_ma_period": 20}),
    ("Res(全因子, min=4)", {**base, "use_resonance": True, "resonance_min_score": 4,
                            "use_macd": True, "macd_confirm_mode": "direction",
                            "use_volume": True, "volume_threshold": 1.0,
                            "use_obv_trend": True, "obv_ma_period": 20,
                            "use_adx": True, "adx_threshold": 25}),

    # === P6+P7: 高级别MACD + 共振 ===
    ("HTF + Res(MACD, min=2)", {**base, "use_htf_macd": True, "use_resonance": True, "resonance_min_score": 2,
                                 "use_macd": True, "macd_confirm_mode": "direction"}),
    ("HTF + Res(Vol+OBV, min=3)", {**base, "use_htf_macd": True, "use_resonance": True, "resonance_min_score": 3,
                                    "use_volume": True, "volume_threshold": 1.0,
                                    "use_obv_trend": True, "obv_ma_period": 20}),

    # === P6+P7+P4: 三重过滤 ===
    ("HTF + Res(3) + Trend50", {**base, "use_htf_macd": True, "use_resonance": True, "resonance_min_score": 3,
                                 "use_trend_filter": True, "trend_window": 50}),
    ("HTF + Res(4) + Trend50", {**base, "use_htf_macd": True, "use_resonance": True, "resonance_min_score": 4,
                                 "use_trend_filter": True, "trend_window": 50}),
    ("HTF + Res(3) + Trend25", {**base, "use_htf_macd": True, "use_resonance": True, "resonance_min_score": 3,
                                 "use_trend_filter": True, "trend_window": 25}),

    # === P5 对照: 成交量因子 ===
    ("OBV + Trend50", {**base, "use_obv_trend": True, "obv_ma_period": 20,
                        "use_trend_filter": True, "trend_window": 50}),
    ("Volume Spike", {**base, "use_volume_spike": True, "volume_spike_threshold": 1.5}),
]

# === 同时跑 180 天全量数据对比 ===
print(f"\n{'='*90}")
print(f"{'='*30} SOL 30天回测 {'='*30}")
print(f"{'='*90}")
print(f"{'组合':<30} {'交易数':>6} {'胜率':>6} {'收益率':>8} {'夏普':>6} {'最大回撤':>8}")
print(f"{'='*90}")

results_30d = []
for name, params in configs:
    try:
        strategy = TrendStrategy(**params)
        signals = strategy.generate_signals(df_30d, enable_short=True)
        prices = df_30d["close"].values.astype(float)
        equity, trades = evaluator.simulate(signals, prices)
        metrics = evaluator.compute_metrics(equity, trades)

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)
        win_rate = metrics["win_rate"] * 100
        ret = metrics["total_return"] * 100
        sharpe = metrics["sharpe_ratio"]
        dd = metrics["max_drawdown"] * 100

        print(f"{name:<30} {n_trades:>6} {win_rate:>5.1f}% {ret:>+7.2f}% {sharpe:>6.2f} {dd:>+7.2f}%")
        results_30d.append((name, n_trades, win_rate, ret, sharpe, dd, params))
    except Exception as e:
        print(f"{name:<30} ERROR: {e}")

# === 180 天全量对比 ===
print(f"\n{'='*90}")
print(f"{'='*30} SOL 180天回测 {'='*30}")
print(f"{'='*90}")
print(f"{'组合':<30} {'交易数':>6} {'胜率':>6} {'收益率':>8} {'夏普':>6} {'最大回撤':>8}")
print(f"{'='*90}")

results_180d = []
for name, params in configs:
    try:
        strategy = TrendStrategy(**params)
        signals = strategy.generate_signals(df_all, enable_short=True)
        prices = df_all["close"].values.astype(float)
        equity, trades = evaluator.simulate(signals, prices)
        metrics = evaluator.compute_metrics(equity, trades)

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)
        win_rate = metrics["win_rate"] * 100
        ret = metrics["total_return"] * 100
        sharpe = metrics["sharpe_ratio"]
        dd = metrics["max_drawdown"] * 100

        print(f"{name:<30} {n_trades:>6} {win_rate:>5.1f}% {ret:>+7.2f}% {sharpe:>6.2f} {dd:>+7.2f}%")
        results_180d.append((name, n_trades, win_rate, ret, sharpe, dd, params))
    except Exception as e:
        print(f"{name:<30} ERROR: {e}")

print(f"{'='*90}")

# === 汇总 ===
if results_30d:
    best30 = max(results_30d, key=lambda x: x[4])
    print(f"\n30天最佳: {best30[0]} | 交易:{best30[1]} 胜率:{best30[2]:.1f}% 收益:{best30[3]:+.2f}% 夏普:{best30[4]:.2f} 回撤:{best30[5]:+.2f}%")
if results_180d:
    best180 = max(results_180d, key=lambda x: x[4])
    print(f"180天最佳: {best180[0]} | 交易:{best180[1]} 胜率:{best180[2]:.1f}% 收益:{best180[3]:+.2f}% 夏普:{best180[4]:.2f} 回撤:{best180[5]:+.2f}%")

# 打印30天最佳的交易明细
if results_30d:
    best = max(results_30d, key=lambda x: x[4])
    strategy = TrendStrategy(**best[6])
    signals = strategy.generate_signals(df_30d, enable_short=True)
    prices = df_30d["close"].values.astype(float)
    equity, trades = evaluator.simulate(signals, prices)

    print(f"\n--- {best[0]} 30天交易明细 ---")
    for t in trades:
        step = t["step"]
        ts_str = ts_30d.iloc[step] if step < len(ts_30d) else "N/A"
        price = prices[step] if step < len(prices) else 0
        ttype = t["type"]
        pnl = t.get("pnl", None)
        if pnl is not None:
            print(f"  [{ts_str}] {ttype:>12} @ {price:.2f}  PnL: {pnl:+.2f} ({pnl/100:.2f}%)")
        else:
            print(f"  [{ts_str}] {ttype:>12} @ {price:.2f}")
