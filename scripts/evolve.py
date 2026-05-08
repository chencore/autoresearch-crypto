"""
ATLAS multi-strategy evolution runner.

Runs the 4-agent evolution on ETH 5m data and reports the final
ensemble weights, parameter sets, and backtest performance.

Usage:
    uv run python scripts/evolve.py
    uv run python scripts/evolve.py --generations 30
"""

from __future__ import annotations

import argparse
import os
import sys
import json
import warnings
from datetime import datetime

import pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dex.evolution import (
    EvolutionEngine,
    run_evolution,
)
from dex.strategies.base import StrategyEvaluator
from dex.config import DATA_DIR


def evaluate_ensemble(engine: EvolutionEngine, df: pd.DataFrame) -> dict:
    """Full backtest of the ensemble on a dataset."""
    evaluator = StrategyEvaluator()
    signals = engine.ensemble_signal(df, enable_short=True)

    min_start = 50
    prices = df["close"].values[min_start:].astype(float)
    valid_signals = signals[min_start:]

    equity, trades = evaluator.simulate(valid_signals, prices)
    metrics = evaluator.compute_metrics(equity, trades)

    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    return {
        "total_return": metrics["total_return"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "n_trades": len(trade_pnls),
    }


def compare_individual(engine: EvolutionEngine, df: pd.DataFrame) -> list[dict]:
    """Evaluate each agent individually."""
    evaluator = StrategyEvaluator()
    results = []

    prices = df["close"].values.astype(float)
    min_start = 50

    for agent in engine.agents:
        try:
            s = agent.strategy_cls(**agent.params)
            signals = s.generate_signals(df)
            equity, trades = evaluator.simulate(signals[min_start:], prices[min_start:])
            metrics = evaluator.compute_metrics(equity, trades)
            trade_pnls = [t for t in trades if t.get("pnl") is not None]
            results.append(
                {
                    "name": agent.name,
                    "style": agent.style,
                    "weight": agent.weight,
                    "total_return": metrics["total_return"],
                    "sharpe_ratio": metrics["sharpe_ratio"],
                    "max_drawdown": metrics["max_drawdown"],
                    "n_trades": len(trade_pnls),
                    "params": agent.params,
                }
            )
        except Exception:
            results.append({"name": agent.name, "error": True})

    return results


def main():
    parser = argparse.ArgumentParser(description="ATLAS Multi-Strategy Evolution")
    parser.add_argument(
        "--generations", type=int, default=15, help="Number of evolution generations"
    )
    parser.add_argument("--data", type=str, default=None, help="Path to parquet data file")
    parser.add_argument("--days", type=int, default=60, help="Number of recent days to use")
    args = parser.parse_args()

    # Load data
    data_path = args.data or os.path.join(str(DATA_DIR), "ETHUSDT_5m.parquet")
    if not os.path.exists(data_path):
        print(f"Error: data file not found: {data_path}")
        sys.exit(1)

    table = pq.read_table(data_path)
    df = table.to_pandas()
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)

    # Use recent N days
    bars_per_day = 288
    df = df.iloc[-bars_per_day * args.days :].reset_index(drop=True)

    print(f"Data: {len(df)} bars ({args.days} days)")
    print(
        f"Price: {df['close'].iloc[0]:.1f} -> {df['close'].iloc[-1]:.1f} "
        f"({(df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100:+.2f}%)"
    )
    print()

    # Run evolution
    engine = run_evolution(
        df,
        generations=args.generations,
        evolution_interval=5,
        verbose=True,
    )

    # Compare individual agents
    print("=" * 60)
    print("Individual Agent Performance (full period)")
    print("=" * 60)
    individual = compare_individual(engine, df)
    print(f"{'Agent':<8} {'Weight':>7} {'Return':>8} {'Sharpe':>7} {'DD':>7} {'Trades':>7}")
    print("-" * 52)
    for r in individual:
        if r.get("error"):
            print(f"{r['name']:<8} {'ERROR':>7}")
            continue
        print(
            f"{r['name']:<8} {r['weight']:>7.3f} "
            f"{r['total_return'] * 100:>+7.2f}% {r['sharpe_ratio']:>7.2f} "
            f"{r['max_drawdown'] * 100:>+6.1f}% {r['n_trades']:>7}"
        )

    # Evaluate ensemble
    print("\n" + "=" * 60)
    print("Ensemble Performance (full period)")
    print("=" * 60)
    ensemble_metrics = evaluate_ensemble(engine, df)
    mkt_ret = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    print(
        f"  Return:    {ensemble_metrics['total_return'] * 100:+.2f}%  "
        f"(market: {mkt_ret * 100:+.2f}%)"
    )
    print(f"  Sharpe:    {ensemble_metrics['sharpe_ratio']:.2f}")
    print(f"  Max DD:    {ensemble_metrics['max_drawdown'] * 100:.1f}%")
    print(f"  Win Rate:  {ensemble_metrics['win_rate'] * 100:.1f}%")
    print(f"  Trades:    {ensemble_metrics['n_trades']}")
    print(f"  Alpha:     {ensemble_metrics['total_return'] - mkt_ret:+.4f}")

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "agents": individual,
        "ensemble": ensemble_metrics,
        "market_return": float(mkt_ret),
    }
    out_path = os.path.join(
        os.path.dirname(__file__), "..", "search_results", "evolution_result.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    main()
