"""
独立回测脚本：分别对每个币种运行回测，避免数据混合问题。
"""

import os
import math
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import torch

from train_quant import BollingerStrategy, StrategyEvaluator


def load_crypto_data(filepath):
    return pq.read_table(filepath).to_pandas()


def backtest_single(df, params, enable_short=True):
    """对单一币种回测"""
    strategy = BollingerStrategy(**params)
    signals = strategy.generate_signals(df, enable_short=enable_short)
    prices = df["close"].values

    evaluator = StrategyEvaluator()
    window = params.get("window", 20)
    valid_signals = signals[window * 2:]
    valid_prices = prices[window * 2:]
    valid_df = df.iloc[window * 2:].reset_index(drop=True)

    if len(valid_signals) < 50:
        return None, None, None

    score, metrics, trades = evaluator.evaluate(valid_signals, valid_prices, valid_df)
    return score, metrics, trades


def main():
    checkpoint = torch.load("checkpoints/quant_model.pt", map_location="cpu", weights_only=False)
    params = checkpoint.get("params", {})

    data_dir = "data/crypto"
    files = [f for f in os.listdir(data_dir) if f.endswith(".parquet")]

    print("=" * 60)
    print(f"回测参数: window={params.get('window')}, std_dev={params.get('std_dev')}, "
          f"atr_mult={params.get('atr_multiplier')}, max_hold={params.get('max_hold_bars')}, "
          f"rsi={params.get('rsi_threshold')}")
    print("=" * 60)

    all_scores = []
    all_metrics = []

    for f in sorted(files):
        filepath = os.path.join(data_dir, f)
        df = load_crypto_data(filepath)
        df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)

        score, metrics, trades = backtest_single(df, params, enable_short=True)
        if score is None:
            continue

        n_trades = len([t for t in trades if t.get("pnl") is not None])
        all_scores.append(score)
        all_metrics.append(metrics)

        print(f"\n{f}:")
        print(f"  数据量: {len(df)} 条K线")
        print(f"  综合评分: {score:.4f}")
        print(f"  总收益率: {metrics['total_return'] * 100:.2f}%")
        print(f"  夏普比率: {metrics['sharpe_ratio']:.4f}")
        print(f"  最大回撤: {metrics['max_drawdown'] * 100:.2f}%")
        print(f"  胜率: {metrics['win_rate'] * 100:.1f}%")
        print(f"  交易笔数: {n_trades}")

        # 打印最近5笔交易
        completed = [t for t in trades if t.get("pnl") is not None]
        if completed:
            print("  最近5笔交易:")
            for t in completed[-5:]:
                entry = t.get('entry_price', 0)
                exit_p = t.get('exit_price', 0)
                print(f"    {t['type']:12s} 入场={entry:10.2f} 出场={exit_p:10.2f} "
                      f"PnL={t['pnl']:10.4f} 步数={t.get('duration', 0)}")

    if all_scores:
        avg_score = np.mean(all_scores)
        avg_return = np.mean([m['total_return'] for m in all_metrics])
        avg_sharpe = np.mean([m['sharpe_ratio'] for m in all_metrics])
        avg_dd = np.mean([m['max_drawdown'] for m in all_metrics])
        avg_win = np.mean([m['win_rate'] for m in all_metrics])

        print("\n" + "=" * 60)
        print("综合回测结果（各币种平均）")
        print("=" * 60)
        print(f"平均评分:       {avg_score:.4f}")
        print(f"平均收益率:     {avg_return * 100:.2f}%")
        print(f"平均夏普:       {avg_sharpe:.4f}")
        print(f"平均最大回撤:   {avg_dd * 100:.2f}%")
        print(f"平均胜率:       {avg_win * 100:.1f}%")
    else:
        print("\n无有效回测结果")


if __name__ == "__main__":
    main()
