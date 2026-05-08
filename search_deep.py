"""
对 Top-3 策略做细粒度深度参数搜索。
在粗搜索最优参数附近展开更密的网格。
"""

import sys
import os
import time
import json
import itertools
import numpy as np
import pyarrow.parquet as pq
import warnings

warnings.filterwarnings("ignore")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from train_quant import (
    HybridMeanRevMomentumStrategy,
    AdaptiveHybridStrategy,
    PureActionStrategy,
    StrategyEvaluator,
)

DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "crypto", "ETHUSDT_5m.parquet"
)


def evaluate_strategy(strategy, df, evaluator, min_start=None):
    """宽松评估：相对市场基准评分"""
    signals = strategy.generate_signals(df)
    if min_start is None:
        min_start = getattr(strategy, "window", 20) * 2
    prices = df["close"].values[min_start:].astype(float)
    valid_signals = signals[min_start:]
    if len(valid_signals) < 50:
        return 0.0, {}, [], signals

    equity, trades = evaluator.simulate(
        valid_signals, prices, df.iloc[min_start:].reset_index(drop=True)
    )
    if len(equity) == 0 or not np.all(np.isfinite(equity)):
        return 0.0, {"total_return": 0}, [], signals

    metrics = evaluator.compute_metrics(equity, trades)
    ret = metrics["total_return"]
    dd = abs(metrics["max_drawdown"])
    wr = metrics["win_rate"]
    sharpe = max(-3.0, min(5.0, metrics["sharpe_ratio"]))
    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n_trades = len(trade_pnls)

    market_return = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0

    if ret <= -0.90 or dd > 0.80 or n_trades < 3:
        return 0.0, metrics, trades, signals

    excess = ret - market_return
    excess_clamped = max(-0.50, min(2.0, excess))

    ret_score = max(0, min(1.0, (excess_clamped + 0.10) / 0.30))
    sharpe_score = max(0, min(1.0, (sharpe + 1.0) / 4.0))
    dd_score = max(0, min(1.0, 1.0 - dd / 0.50))
    wr_score = max(0, min(1.0, (wr - 0.35) / 0.30))
    trade_score = min(1.0, n_trades / 20.0)

    score = (
        ret_score * 0.30
        + sharpe_score * 0.20
        + dd_score * 0.20
        + wr_score * 0.15
        + trade_score * 0.15
    )
    return score, metrics, trades, signals


def grid_search(strategy_cls, grid, df, eval_budget=300, use_full_data=True):
    """网格搜索最优参数组合"""
    evaluator = StrategyEvaluator()
    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    if not use_full_data:
        n = len(df)
        val_start = int(n * 0.85)
        df_eval = df.iloc[val_start:].reset_index(drop=True)
    else:
        df_eval = df

    best_score = -1
    best_params = None
    best_metrics = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > eval_budget:
            break
        params = dict(zip(grid.keys(), combo))
        try:
            strategy = strategy_cls(**params)
            score, metrics, trades, _ = evaluate_strategy(strategy, df_eval, evaluator)
        except Exception:
            score = 0
            metrics = {}
        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    ret = best_metrics.get("total_return", 0) * 100 if best_metrics else 0
    dd = best_metrics.get("max_drawdown", 0) * 100 if best_metrics else 0
    sharpe = best_metrics.get("sharpe_ratio", 0) if best_metrics else 0
    n_trades = (
        len(
            [
                t
                for t in (best_metrics.get("trades", []) if best_metrics else [])
                if t.get("pnl") is not None
            ]
        )
        if best_metrics
        else 0
    )

    print(
        f"  {strategy_cls.__name__}: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"score={best_score:.4f} ret={ret:+.2f}% sharpe={sharpe:.2f} DD={dd:.1f}% trades={n_trades}"
    )
    return best_params, best_score, best_metrics


def main():
    print("=" * 60)
    print("Top-3 策略细粒度深度参数搜索")
    print("=" * 60)

    # 加载数据
    table = pq.read_table(DATA_FILE)
    df = table.to_pandas()
    for col in ["close", "high", "low", "open", "volume"]:
        df[col] = df[col].astype(float)

    # 只用最近60天（用户原始需求）
    df_60d = df.iloc[-288 * 60 :].reset_index(drop=True)
    print(f"\n数据: 最近60天, {len(df_60d)} 根K线")
    print(f"价格: {df_60d['close'].iloc[0]:.1f} → {df_60d['close'].iloc[-1]:.1f}")
    mkt_ret = (df_60d["close"].iloc[-1] / df_60d["close"].iloc[0] - 1) * 100
    print(f"市场收益: {mkt_ret:+.2f}%")

    # ====================================================================
    print("\n" + "-" * 60)
    print("[1/3] HybridMM 深度搜索 (冠军)")
    print("-" * 60)

    # 基于粗搜最优参数展开细网格
    hmm_grid = {
        "rsi_period": [5, 7, 9, 10, 14],
        "rsi_low": [25, 28, 30, 32, 35],
        "rsi_high": [70, 72, 75, 78, 80],
        "ma_period": [20, 25, 30, 40, 50],
        "atr_period": [10, 12, 14, 16],
        "atr_multiplier": [2.0, 2.3, 2.5, 2.8, 3.0, 3.5],
        "max_hold_bars": [12, 18, 24, 30, 36, 48],
    }
    hmm_params, hmm_score, hmm_metrics = grid_search(
        HybridMeanRevMomentumStrategy, hmm_grid, df_60d, eval_budget=300
    )

    # ====================================================================
    print("\n" + "-" * 60)
    print("[2/3] Adaptive 深度搜索 (最佳绝对收益)")
    print("-" * 60)

    adp_grid = {
        "rsi_period": [14],
        "rsi_low": [25, 28, 30, 32, 35],
        "rsi_high": [65, 68, 70, 72, 75, 78],
        "ma_period": [10, 15, 20, 25],
        "trend_long_ma": [50, 80, 100, 150, 200],
        "trend_pull_ma": [8, 10, 12, 15, 18, 20],
        "adx_threshold": [18, 20, 22, 25, 28, 30],
        "adx_period": [14],
        "atr_period": [10, 12, 14, 16],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0, 3.5],
        "max_hold_bars": [12, 18, 24, 30, 36],
    }
    adp_params, adp_score, adp_metrics = grid_search(
        AdaptiveHybridStrategy, adp_grid, df_60d, eval_budget=300
    )

    # ====================================================================
    print("\n" + "-" * 60)
    print("[3/3] PureAction 深度搜索 (最简单最稳健)")
    print("-" * 60)

    pa_grid = {
        "window": [10, 12, 15, 18, 20, 24],
        "std_dev": [1.5, 1.8, 2.0, 2.2, 2.5, 2.8, 3.0],
        "atr_period": [7, 10, 14, 20],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 18, 24, 30, 36, 48],
        "entry_zone": [0.0, 0.2, 0.3, 0.5],
        "trend_ma_period": [None, 50, 100, 200],
        "adx_threshold": [None, 20, 25, 30],
    }
    pa_params, pa_score, pa_metrics = grid_search(
        PureActionStrategy, pa_grid, df_60d, eval_budget=300
    )

    # ====================================================================
    # 汇总
    print("\n" + "=" * 60)
    print("深度搜索最终排名")
    print("=" * 60)

    results = [
        ("HybridMM", hmm_params, hmm_score, hmm_metrics),
        ("Adaptive", adp_params, adp_score, adp_metrics),
        ("PureAction", pa_params, pa_score, pa_metrics),
    ]
    results.sort(key=lambda x: x[2], reverse=True)

    print(f"{'排名':<5} {'策略':<14} {'评分':>8} {'收益':>8} {'回撤':>7} {'夏普':>7} {'交易':>6}")
    print("-" * 58)
    for i, (name, p, s, m) in enumerate(results):
        ret = m.get("total_return", 0) * 100 if m else 0
        dd = m.get("max_drawdown", 0) * 100 if m else 0
        sr = m.get("sharpe_ratio", 0) if m else 0
        tr = (
            len([t for t in (m.get("trades", []) if m else []) if t.get("pnl") is not None])
            if m
            else 0
        )
        print(f"{i + 1:<5} {name:<14} {s:>8.4f} {ret:>+7.2f}% {dd:>+6.1f}% {sr:>7.2f} {tr:>6}")

    if results:
        champ = results[0]
        print(f"\n最优策略: {champ[0]}")
        print(f"参数: {json.dumps(champ[1], indent=2, default=str)}")
        ret = champ[3].get("total_return", 0) * 100 if champ[3] else 0
        print(f"60天收益: {ret:+.2f}% (vs 市场 {mkt_ret:+.2f}%)")
        if champ[3]:
            print(f"超额收益: {ret - mkt_ret:+.2f}%")

    print("\n完成!")


if __name__ == "__main__":
    main()
