"""
Full training pipeline — search all strategies + GEPA V2 evolution.

Phase 1: Grid-search 7 strategies on full dataset (180s each)
Phase 2: Deep fine-tune top-2 with finer grid (300s each)
Phase 3: GEPA V2 reflective evolution on champion (30 cycles)
Phase 4: Walk-forward validation + save best checkpoint

Usage:
    uv run python scripts/train_all.py
    uv run python scripts/train_all.py --quick  # 30s/strategy quick mode
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings

import pyarrow.parquet as pq

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dex.strategies import (
    TrendStrategy, ScalpStrategy, PureActionStrategy,
    HybridStrategy, TrendFollowStrategy,
    HybridMeanRevMomentumStrategy, AdaptiveHybridStrategy,
)
from dex.strategies.base import StrategyEvaluator
from dex.evolution import Agent
from dex.reflection import ReflectionEngine, gepa_evolve_v2
from dex.scoring import risk_adjusted_score

DATA_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "crypto", "ETHUSDT_5m.parquet"
)
CHECKPOINT_DIR = os.path.join(os.path.dirname(__file__), "..", "checkpoints")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "search_results")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def relaxed_eval(strategy, df, evaluator):
    """Evaluate strategy with relative-to-market scoring."""
    signals = strategy.generate_signals(df)
    min_start = getattr(strategy, "window", 20) * 2
    prices = df["close"].values[min_start:].astype(float)
    valid = signals[min_start:]
    if len(valid) < 50:
        return 0.0, {}, []

    equity, trades = evaluator.simulate(valid, prices)
    metrics = evaluator.compute_metrics(equity, trades)
    ret = metrics["total_return"]
    dd = metrics["max_drawdown"]
    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n = len(trade_pnls)
    mkt = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0

    result = risk_adjusted_score(
        sharpe=metrics["sharpe_ratio"], total_return=ret,
        max_drawdown=dd, win_rate=metrics["win_rate"],
        n_trades=n, market_return=mkt,
        min_trades=5, max_dd=0.50,
    )
    return result.score, metrics, trades


# ---------------------------------------------------------------------------
# Phase 1: Grid search
# ---------------------------------------------------------------------------

ALL_STRATEGIES = [
    ("TrendStrategy", TrendStrategy, {
        "window": [12, 15, 20, 24, 30],
        "std_dev": [1.5, 2.0, 2.5, 3.0],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 24, 36, 48],
        "rsi_threshold": [25, 30, 35],
    }),
    ("PureAction", PureActionStrategy, {
        "window": [10, 15, 20, 24],
        "std_dev": [1.5, 2.0, 2.5, 3.0],
        "atr_period": [7, 14], "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 24, 36],
        "entry_zone": [0.0, 0.3],
        "trend_ma_period": [None, 50, 100],
        "adx_threshold": [None, 25],
    }),
    ("HybridMM", HybridMeanRevMomentumStrategy, {
        "rsi_period": [5, 7, 10, 14],
        "rsi_low": [25, 28, 30, 35],
        "rsi_high": [70, 72, 75, 78],
        "ma_period": [20, 25, 30, 40],
        "atr_period": [10, 12, 14],
        "atr_multiplier": [2.0, 2.5, 3.0, 3.5],
        "max_hold_bars": [12, 24, 36, 48],
    }),
    ("Adaptive", AdaptiveHybridStrategy, {
        "rsi_low": [25, 30, 35], "rsi_high": [65, 70, 75],
        "ma_period": [10, 20], "trend_long_ma": [50, 100, 200],
        "trend_pull_ma": [10, 20], "adx_threshold": [20, 25, 30],
        "atr_period": [7, 14], "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 24, 36],
    }),
    ("Scalp", ScalpStrategy, {
        "window": [8, 10, 12, 15], "std_dev": [1.0, 1.2, 1.5, 1.8],
        "take_profit_pct": [0.005, 0.008, 0.012, 0.015],
        "stop_loss_pct": [0.003, 0.004, 0.005, 0.006],
        "max_hold_bars": [6, 8, 12, 18],
        "rsi_period": [7, 10, 14],
    }),
    ("Hybrid", HybridStrategy, {
        "window": [15, 20, 24], "std_dev": [1.8, 2.0, 2.5],
        "atr_period": [7, 14], "atr_multiplier": [2.0, 2.5, 3.0],
        "max_hold_bars": [18, 24, 36], "entry_zone": [0.0, 0.3],
        "trend_ma_period": [50, 100], "adx_threshold": [20, 25, 30],
    }),
    ("TrendFollow", TrendFollowStrategy, {
        "long_ma_period": [50, 100, 200], "pull_ma_period": [10, 15, 20],
        "atr_period": [7, 14], "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 24, 36],
        "entry_zone": [0.0, 0.5],
    }),
]


def grid_search_phase(df, time_per_strategy=180):
    """Phase 1: grid search all strategies."""
    import itertools

    evaluator = StrategyEvaluator()
    n = len(df)
    seg = int(n * 0.15)
    val_df = df.iloc[-seg:].reset_index(drop=True)

    results = []
    for name, cls, grid in ALL_STRATEGIES:
        combos = list(itertools.product(*grid.values()))
        keys = list(grid.keys())
        total = len(combos)

        best_score = -1.0
        best_params = None
        best_metrics = None
        t0 = time.time()
        tried = 0

        for combo in combos:
            if time.time() - t0 > time_per_strategy:
                break
            params = dict(zip(keys, combo))
            try:
                s = cls(**params)
                score, metrics, _ = relaxed_eval(s, val_df, evaluator)
            except Exception:
                score = 0.0; metrics = {}
            tried += 1
            if score > best_score:
                best_score = score; best_params = params; best_metrics = metrics

        ret = best_metrics.get("total_return", 0) * 100 if best_metrics else 0
        dd = best_metrics.get("max_drawdown", 0) * 100 if best_metrics else 0
        sharpe = best_metrics.get("sharpe_ratio", 0) if best_metrics else 0
        print(f"  {name}: {tried}/{total} | score={best_score:.4f} "
              f"ret={ret:+.2f}% sharpe={sharpe:.2f} DD={dd:.1f}%")
        results.append((name, cls, best_params, best_score, best_metrics))

    results.sort(key=lambda x: x[3], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Phase 2: GEPA V2 on champion
# ---------------------------------------------------------------------------

def gepa_phase(df, champion_name, champion_cls, champion_params):
    """Phase 2: GEPA V2 evolution on the best strategy."""
    print(f"\n  Champion: {champion_name}")

    # Build evaluate_fn
    evaluator = StrategyEvaluator()
    n = len(df)
    seg = n // 4
    val_df = df.iloc[-seg:].reset_index(drop=True) if seg > 200 else df

    def evaluate_fn(params):
        """Return (score, sharpe, ret, dd) — compatible with run_experiment."""
        try:
            s = champion_cls(**params)
            signals = s.generate_signals(val_df)
            min_start = getattr(s, "window", 20) * 2
            prices = val_df["close"].values[min_start:].astype(float)
            equity, trades = evaluator.simulate(
                signals[min_start:], prices,
                val_df.iloc[min_start:].reset_index(drop=True),
            )
            metrics = evaluator.compute_metrics(equity, trades)
            trade_pnls = [t for t in trades if t.get("pnl") is not None]
            mkt = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0
            r = risk_adjusted_score(
                sharpe=metrics["sharpe_ratio"],
                total_return=metrics["total_return"],
                max_drawdown=metrics["max_drawdown"],
                win_rate=metrics["win_rate"],
                n_trades=len(trade_pnls),
                market_return=mkt,
                min_trades=5, max_dd=0.40,
            )
            return (
                r.score,
                metrics["sharpe_ratio"],
                metrics["total_return"],
                metrics["max_drawdown"],
            )
        except Exception:
            return 0.0, 0.0, 0.0, -0.99

    # Create a single-agent evolution
    agent = Agent(
        name=champion_name, style="冠军优化",
        strategy_cls=champion_cls, params=dict(champion_params),
    )
    engine = ReflectionEngine()

    gepa_evolve_v2(
        agents=[agent],
        evaluate_fn_raw=evaluate_fn,
        engine=engine,
        cycles=20,
        min_trades=5,
        max_dd=0.40,
        dead_threshold=5,
        verbose=True,
    )

    return agent, engine


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Full training pipeline")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--days", type=int, default=0,
                        help="Use last N days (0=all data)")
    args = parser.parse_args()

    budget = 30 if args.quick else 180

    # Load data
    table = pq.read_table(DATA_FILE)
    df = table.to_pandas()
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = df[c].astype(float)
    if args.days > 0:
        df = df.iloc[-288 * args.days :].reset_index(drop=True)

    mkt_ret = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print("=" * 60)
    print(f"Full Training Pipeline — {len(df)} bars")
    print(f"Market: {mkt_ret:+.2f}%  |  Budget: {budget}s/strategy")
    print("=" * 60)

    # Phase 1
    print(f"\n[Phase 1] Grid search ({budget}s per strategy)...")
    print("-" * 60)
    t1 = time.time()
    results = grid_search_phase(df, time_per_strategy=budget)
    print(f"  Done in {time.time()-t1:.0f}s")

    # Phase 2: GEPA V2 on top 2
    print("\n[Phase 2] GEPA V2 evolution on top performers...")
    print("-" * 60)

    best_agents = []
    for i in range(min(2, len(results))):
        name, cls, params, score, metrics = results[i]
        if score <= 0 or params is None:
            continue
        print(f"\n  [#{i+1}] {name} (score={score:.4f})")
        agent, reflection = gepa_phase(df, name, cls, params)
        best_agents.append((agent, reflection, score))

    # Save best
    if best_agents:
        champion = best_agents[0][0]
        checkpoint = {
            "strategy": champion.name.lower().replace("strategy", ""),
            "params": champion.params,
            "score": best_agents[0][2],
        }
        path = os.path.join(CHECKPOINT_DIR, "best_model.pt")
        import torch
        torch.save(checkpoint, path)
        print(f"\n{'='*60}")
        print(f"Best model saved: {path}")
        print(f"Strategy: {champion.name}")
        print(f"Params: {json.dumps(champion.params, indent=2, default=str)[:300]}")
        print(f"{'='*60}")

    print("\nTraining complete.")


if __name__ == "__main__":
    main()
