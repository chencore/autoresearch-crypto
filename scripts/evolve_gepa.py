"""
GEPA Reflective Evolution runner.

Combines the ATLAS multi-agent system with the GEPA reflection engine.
Each experiment is hypothesis-driven, with auto-generated reflections
and periodic meta-analysis.

Usage:
    uv run python scripts/evolve_gepa.py
    uv run python scripts/evolve_gepa.py --cycles 20 --days 60
"""

from __future__ import annotations

import argparse
import os
import sys
import json
import inspect
import warnings
from datetime import datetime
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dex.evolution import create_default_agents
from dex.reflection import ReflectionEngine, gepa_evolve
from dex.strategies.base import StrategyEvaluator
from dex.strategies.grid import grid_signals_to_discrete
from dex.config import DATA_DIR


def make_evaluate_fn(df: pd.DataFrame):
    """Build an evaluate_fn closure for the GEPA engine.

    Returns a function (params) -> (score, sharpe, return, max_dd).
    """
    evaluator = StrategyEvaluator()
    n = len(df)
    seg = n // 4
    val_df = df.iloc[-seg:].reset_index(drop=True) if seg > 100 else df
    val_prices = val_df["close"].values.astype(float)

    def _relaxed_score(signals, prices, df_slice):
        equity, trades = evaluator.simulate(signals, prices, df_slice)
        if len(equity) == 0 or not np.all(np.isfinite(equity)):
            return 0.0, 0.0, 0.0, -0.99
        metrics = evaluator.compute_metrics(equity, trades)
        ret = metrics["total_return"]
        dd = metrics["max_drawdown"]
        sharpe = max(-3.0, min(5.0, metrics["sharpe_ratio"]))
        wr = metrics["win_rate"]
        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)
        if ret <= -0.90 or dd < -0.80 or n_trades < 1:
            return 0.0, sharpe, ret, dd
        market_return = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0
        excess = ret - market_return
        excess_c = max(-0.50, min(2.0, excess))
        ret_score = max(0, min(1.0, (excess_c + 0.10) / 0.30))
        sharpe_score = max(0, min(1.0, (sharpe + 1.0) / 4.0))
        dd_score = max(0, min(1.0, 1.0 - abs(dd) / 0.50))
        wr_score = max(0, min(1.0, (wr - 0.35) / 0.30))
        trade_score = min(1.0, n_trades / 20.0)
        score = (ret_score * 0.30 + sharpe_score * 0.20 +
                 dd_score * 0.20 + wr_score * 0.15 + trade_score * 0.15)
        return score, sharpe, ret, dd

    def evaluate_fn(params: Dict) -> Tuple[float, float, float, float]:
        """Evaluate params -> (score, sharpe, return, max_dd)."""
        # Need strategy_cls — we determine it by checking which keys are present
        # This is called per-agent, so we need the agent's class
        # For now, try TrendStrategy first, then others
        from dex.strategies.trend import TrendStrategy
        from dex.strategies.pure_action import PureActionStrategy
        from dex.strategies.grid import GridStrategy
        from dex.strategies.hybrid_mm import HybridMeanRevMomentumStrategy

        # Determine class from params
        if "grid_spacing_pct" in params:
            cls = GridStrategy
        elif "rsi_low" in params and "rsi_high" in params:
            cls = HybridMeanRevMomentumStrategy
        elif "trend_ma_period" in params and "adx_threshold" in params:
            cls = PureActionStrategy
        else:
            cls = TrendStrategy

        sig_params = inspect.signature(cls.__init__).parameters.keys()
        valid_params = {k: v for k, v in params.items() if k in sig_params}

        strategy = cls(**valid_params)
        signals = strategy.generate_signals(val_df)

        if signals.dtype in (np.float64, np.float32, float):
            signals = grid_signals_to_discrete(signals, val_prices)

        min_start = getattr(strategy, "window", 20) * 2
        return _relaxed_score(
            signals[min_start:], val_prices[min_start:],
            val_df.iloc[min_start:].reset_index(drop=True),
        )

    return evaluate_fn


def main():
    parser = argparse.ArgumentParser(description="GEPA Reflective Evolution")
    parser.add_argument("--cycles", type=int, default=15)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--data", type=str, default=None)
    args = parser.parse_args()

    # Load data
    data_path = args.data or os.path.join(str(DATA_DIR), "ETHUSDT_5m.parquet")
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found")
        sys.exit(1)

    table = pq.read_table(data_path)
    df = table.to_pandas()
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df = df.iloc[-288 * args.days :].reset_index(drop=True)

    print(f"Data: {len(df)} bars ({args.days} days)")
    print(f"Price: {df['close'].iloc[0]:.1f} -> {df['close'].iloc[-1]:.1f} "
          f"({(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.2f}%)")

    # Create agents and engines
    agents = create_default_agents()
    evaluate_fn = make_evaluate_fn(df)
    reflection = ReflectionEngine()

    # Run GEPA evolution
    engine = gepa_evolve(
        agents=agents,
        evaluate_fn=evaluate_fn,
        engine=reflection,
        cycles=args.cycles,
        verbose=True,
    )

    # Final report
    print("\n" + "=" * 60)
    print("最终参数与反思摘要")
    print("=" * 60)
    for agent in agents:
        score, sharpe, ret, dd = evaluate_fn(agent.params)
        print(f"\n{agent.name} ({agent.style}):")
        print(f"  Score={score:.4f}  Sharpe={sharpe:.2f}  "
              f"Return={ret*100:+.2f}%  DD={dd*100:.1f}%")
        print(f"  Params: {json.dumps(agent.params, indent=2, default=str)[:200]}")

    print(f"\n总实验数: {len(engine.experiment_logs)}")
    print(f"元反思数: {len(engine.meta_reflections)}")
    print(f"发现盲点: {len(engine.blind_spots)}")
    if engine.blind_spots:
        print("  最新盲点:")
        for bs in engine.blind_spots[-3:]:
            print(f"    - {bs}")

    # Save
    out = {
        "timestamp": datetime.now().isoformat(),
        "agents": [
            {"name": a.name, "style": a.style, "params": a.params,
             "score": evaluate_fn(a.params)[0]}
            for a in agents
        ],
        "n_experiments": len(engine.experiment_logs),
        "n_meta": len(engine.meta_reflections),
        "blind_spots": engine.blind_spots[-5:],
    }
    out_path = os.path.join(
        os.path.dirname(__file__), "..", "search_results", "gepa_result.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
