from __future__ import annotations

import inspect
from typing import Any, Callable, Tuple

import numpy as np
import pandas as pd

from dex.config import DATA_DIR
from dex.evolution import EvolutionEngine, create_default_agents
from dex.reflection import ReflectionEngine
from dex.strategies.base import StrategyEvaluator
from dex.strategies.grid import GridStrategy, grid_signals_to_discrete
from dex.strategies.hybrid_mm import HybridMeanRevMomentumStrategy
from dex.strategies.pure_action import PureActionStrategy
from dex.strategies.trend import TrendStrategy

from app.services.evolution_manager import evolution_manager


class EvolveRunnerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _load_data(symbol: str, interval: str, days: int) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_{interval}_{days}d.parquet"
    if not path.exists():
        raise EvolveRunnerError("data_not_found", f"data file not found: {path.name}")
    df = pd.read_parquet(path)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


def _check_data_exists(symbol: str, interval: str, days: int) -> None:
    path = DATA_DIR / f"{symbol}_{interval}_{days}d.parquet"
    if not path.exists():
        raise EvolveRunnerError("data_not_found", f"data file not found: {path.name}")


def make_evaluate_fn(
    df: pd.DataFrame,
) -> Callable[[dict[str, Any]], Tuple[float, float, float, float]]:
    evaluator = StrategyEvaluator()
    n = len(df)
    seg = n // 4
    val_df = df.iloc[-seg:].reset_index(drop=True) if seg > 100 else df
    val_prices = val_df["close"].values.astype(float)

    def _relaxed_score(
        signals: np.ndarray, prices: np.ndarray, df_slice: pd.DataFrame
    ) -> Tuple[float, float, float, float]:
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
        ret_score = max(0.0, min(1.0, (excess_c + 0.10) / 0.30))
        sharpe_score = max(0.0, min(1.0, (sharpe + 1.0) / 4.0))
        dd_score = max(0.0, min(1.0, 1.0 - abs(dd) / 0.50))
        wr_score = max(0.0, min(1.0, (wr - 0.35) / 0.30))
        trade_score = min(1.0, n_trades / 20.0)
        score = (
            ret_score * 0.30
            + sharpe_score * 0.20
            + dd_score * 0.20
            + wr_score * 0.15
            + trade_score * 0.15
        )
        return score, sharpe, ret, dd

    def evaluate_fn(params: dict[str, Any]) -> Tuple[float, float, float, float]:
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

        try:
            strategy = cls(**valid_params)
            signals = strategy.generate_signals(val_df)
            if signals.dtype in (np.float64, np.float32, float):
                signals = grid_signals_to_discrete(signals, val_prices)
            min_start = getattr(strategy, "window", 20) * 2
            return _relaxed_score(
                signals[min_start:],
                val_prices[min_start:],
                val_df.iloc[min_start:].reset_index(drop=True),
            )
        except Exception:
            return 0.0, 0.0, 0.0, -0.99

    return evaluate_fn


def _run_atlas(run_id: str, config: dict[str, Any], df: pd.DataFrame) -> None:
    engine = EvolutionEngine(
        evolution_interval=config.get("evolution_interval", 5)
    )
    total = config["generations"]
    evolution_manager.broadcast(
        run_id,
        {"type": "started", "run_id": run_id, "config": config, "total": total},
    )

    for gen in range(1, total + 1):
        run = evolution_manager.get_run(run_id)
        if run and run.cancel_event.is_set():
            evolution_manager.broadcast(
                run_id,
                {"type": "stopped", "run_id": run_id, "generation": gen - 1},
            )
            evolution_manager.mark_stopped(run_id)
            return

        evolution_manager.update_gen(run_id, gen)

        if gen % engine.evolution_interval == 0 or gen == 1:
            engine.evolve(df, gen)
        else:
            for agent in engine.agents:
                score, _ = engine.evaluate_agent(
                    agent, df.iloc[-len(df) // 4 :]
                )
                agent.score_history.append(score)

        agents_state = [
            {
                "name": a.name,
                "style": a.style,
                "score": float(a.recent_score()),
                "weight": float(a.weight),
                "generation": int(a.generation),
                "params": dict(a.params),
            }
            for a in engine.agents
        ]
        evolution_manager.update_agents(run_id, agents_state)
        evolution_manager.broadcast(
            run_id,
            {
                "type": "generation",
                "run_id": run_id,
                "generation": gen,
                "total": total,
                "agents": agents_state,
            },
        )

    best = max(engine.agents, key=lambda a: a.recent_score())
    evolution_manager.broadcast(
        run_id,
        {
            "type": "completed",
            "run_id": run_id,
            "best_agent": {
                "name": best.name,
                "style": best.style,
                "score": float(best.recent_score()),
                "weight": float(best.weight),
                "params": dict(best.params),
            },
            "final_weights": {a.name: float(a.weight) for a in engine.agents},
        },
    )


def _run_gepa(run_id: str, config: dict[str, Any], df: pd.DataFrame) -> None:
    agents = create_default_agents()
    engine = ReflectionEngine()
    evaluate_fn = make_evaluate_fn(df)
    total = config["generations"]
    evolution_manager.broadcast(
        run_id,
        {"type": "started", "run_id": run_id, "config": config, "total": total},
    )

    for cycle in range(1, total + 1):
        run = evolution_manager.get_run(run_id)
        if run and run.cancel_event.is_set():
            evolution_manager.broadcast(
                run_id,
                {"type": "stopped", "run_id": run_id, "cycle": cycle - 1},
            )
            evolution_manager.mark_stopped(run_id)
            return

        evolution_manager.update_gen(run_id, cycle)

        agent = agents[(cycle - 1) % len(agents)]
        hypothesis = engine.hypotheses[(cycle - 1) % len(engine.hypotheses)]
        log = engine.run_experiment(
            agent_name=agent.name,
            hypothesis=hypothesis,
            params_before=dict(agent.params),
            evaluate_fn=evaluate_fn,
        )
        accepted = log.score_after > log.score_before
        if accepted:
            agent.params = log.params_after

        agents_state = [
            {
                "name": a.name,
                "style": a.style,
                "score": 0.0,
                "weight": 0.25,
                "generation": cycle,
                "params": dict(a.params),
            }
            for a in agents
        ]
        evolution_manager.update_agents(run_id, agents_state)
        evolution_manager.broadcast(
            run_id,
            {
                "type": "cycle",
                "run_id": run_id,
                "cycle": cycle,
                "total": total,
                "agent": agent.name,
                "hypothesis": hypothesis.text,
                "score_before": float(log.score_before),
                "score_after": float(log.score_after),
                "accepted": bool(accepted),
                "reflection": log.reflection,
            },
        )

        if cycle % 5 == 0:
            summary, _ = engine.meta_reflect()
            evolution_manager.broadcast(
                run_id,
                {
                    "type": "meta_reflection",
                    "run_id": run_id,
                    "cycle": cycle,
                    "summary": summary,
                },
            )

    evolution_manager.broadcast(
        run_id,
        {
            "type": "completed",
            "run_id": run_id,
            "experiment_count": len(engine.experiment_logs),
            "meta_count": len(engine.meta_reflections),
            "blind_spots": list(engine.blind_spots[-3:]) if engine.blind_spots else [],
        },
    )


def run_evolution_thread(run_id: str, config: dict[str, Any]) -> None:
    try:
        _check_data_exists(config["symbol"], config["interval"], config["days"])
        df = _load_data(config["symbol"], config["interval"], config["days"])
        engine = config["engine"]
        if engine == "atlas":
            _run_atlas(run_id, config, df)
        elif engine == "gepa":
            _run_gepa(run_id, config, df)
        else:
            raise EvolveRunnerError(
                "invalid_engine",
                f"engine must be 'atlas' or 'gepa', got: {engine}",
            )
        evolution_manager.mark_completed(run_id)
    except EvolveRunnerError as e:
        evolution_manager.mark_failed(run_id, e.message)
        evolution_manager.broadcast(
            run_id,
            {"type": "error", "run_id": run_id, "code": e.code, "message": e.message},
        )
    except Exception as e:
        evolution_manager.mark_failed(run_id, str(e))
        evolution_manager.broadcast(
            run_id,
            {
                "type": "error",
                "run_id": run_id,
                "code": "runner_failed",
                "message": str(e),
            },
        )
