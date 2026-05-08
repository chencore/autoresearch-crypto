"""
Multi-strategy evolution engine (ATLAS super-investor model).

Maintains 4 independent agents (Alpha/Beta/Gamma/Delta), each with its
own strategy type and parameter set.  Every N iterations the worst
performer learns from the best, while the best explores bolder
parameter shifts.  Output is an adaptive ensemble with dynamic weights.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from dex.strategies.base import StrategyEvaluator
from dex.strategies.trend import TrendStrategy
from dex.strategies.pure_action import PureActionStrategy
from dex.strategies.grid import GridStrategy
from dex.strategies.hybrid_mm import HybridMeanRevMomentumStrategy


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

@dataclass
class Agent:
    """A single evolving trading agent.

    Attributes:
        name: Human-readable identifier (Alpha/Beta/Gamma/Delta).
        style: Trading style description.
        strategy_cls: Strategy class this agent uses.
        params: Current parameter dict (genes).
        score_history: Rolling list of recent evaluation scores.
        weight: Current ensemble weight (0-1 range, sum across agents = 1).
    """

    name: str
    style: str
    strategy_cls: type
    params: Dict[str, Any]
    score_history: List[float] = field(default_factory=list)
    weight: float = 0.25
    generation: int = 0

    def recent_score(self, window: int = 3) -> float:
        """Average score over the last ``window`` evaluations."""
        if not self.score_history:
            return 0.0
        recent = self.score_history[-window:]
        return sum(recent) / len(recent)

    def clone(self) -> Agent:
        """Deep-copy this agent (including params)."""
        return Agent(
            name=self.name,
            style=self.style,
            strategy_cls=self.strategy_cls,
            params=copy.deepcopy(self.params),
            score_history=list(self.score_history),
            weight=self.weight,
            generation=self.generation,
        )


# ---------------------------------------------------------------------------
# Default agent factory
# ---------------------------------------------------------------------------

def create_default_agents() -> List[Agent]:
    """Create the four default ATLAS agents with sensible initial parameters."""
    return [
        Agent(
            name="Alpha",
            style="趋势跟踪",
            strategy_cls=TrendStrategy,
            params={
                "window": 15, "std_dev": 2.5, "atr_multiplier": 2.0,
                "max_hold_bars": 36, "rsi_threshold": 35, "entry_zone": 0.3,
                "use_adx": True, "adx_threshold": 20,
            },
        ),
        Agent(
            name="Beta",
            style="均值回归",
            strategy_cls=PureActionStrategy,
            params={
                "window": 20, "std_dev": 2.0, "atr_period": 14,
                "atr_multiplier": 2.5, "max_hold_bars": 24,
                "entry_zone": 0.0, "enable_short": True,
            },
        ),
        Agent(
            name="Gamma",
            style="网格交易",
            strategy_cls=GridStrategy,
            params={
                "grid_spacing_pct": 0.008, "grid_levels": 5,
                "base_size": 0.1, "atr_period": 14,
                "atr_spacing_mult": 0.5, "max_position": 1.0,
                "trend_ma_period": 100,
            },
        ),
        Agent(
            name="Delta",
            style="事件驱动",
            strategy_cls=HybridMeanRevMomentumStrategy,
            params={
                "rsi_period": 5, "rsi_low": 28, "rsi_high": 72,
                "ma_period": 25, "atr_period": 12,
                "atr_multiplier": 3.5, "max_hold_bars": 24,
                "enable_short": True,
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Evolution engine
# ---------------------------------------------------------------------------

class EvolutionEngine:
    """Manages the 4-agent evolution cycle.

    Each cycle:
    1. Evaluate all agents on recent validation data
    2. Rank by Sharpe / composite score
    3. Worst learns from best (crossover)
    4. Best explores (mutation with larger step)
    5. Rebalance ensemble weights via softmax on scores
    """

    def __init__(
        self,
        agents: Optional[List[Agent]] = None,
        evaluator: Optional[StrategyEvaluator] = None,
        evolution_interval: int = 5,
        mutation_scale: float = 0.10,
        crossover_rate: float = 0.30,
        temperature: float = 2.0,
        seed: int = 42,
    ):
        self.agents = agents or create_default_agents()
        self.evaluator = evaluator or StrategyEvaluator()
        self.evolution_interval = evolution_interval
        self.mutation_scale = mutation_scale
        self.crossover_rate = crossover_rate
        self.temperature = temperature
        self.rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Parameter space definition (mutation-aware)
    # ------------------------------------------------------------------

    _PARAM_BOUNDS: Dict[str, Tuple[float, float]] = {
        "window": (5, 50),
        "std_dev": (1.0, 4.0),
        "atr_period": (5, 30),
        "atr_multiplier": (1.0, 5.0),
        "max_hold_bars": (6, 72),
        "rsi_threshold": (20, 45),
        "entry_zone": (0.0, 1.5),
        "adx_threshold": (15, 40),
        "grid_spacing_pct": (0.002, 0.03),
        "grid_levels": (2, 12),
        "base_size": (0.05, 0.3),
        "atr_spacing_mult": (0.2, 1.5),
        "max_position": (0.5, 2.0),
        "trend_ma_period": (50, 300),
        "rsi_period": (3, 21),
        "rsi_low": (15, 40),
        "rsi_high": (60, 85),
        "ma_period": (10, 50),
    }

    _DISCRETE_PARAMS: set = {
        "window", "atr_period", "max_hold_bars", "rsi_threshold",
        "adx_threshold", "grid_levels", "trend_ma_period",
        "rsi_period", "rsi_low", "rsi_high", "ma_period",
    }

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_params(strategy_cls: type, params: Dict[str, Any]) -> Dict[str, Any]:
        """Filter params to only those accepted by the strategy's __init__."""
        import inspect
        sig = inspect.signature(strategy_cls.__init__)
        valid = set(sig.parameters.keys()) - {"self"}
        return {k: v for k, v in params.items() if k in valid}

    def _relaxed_score(
        self, signals: np.ndarray, prices: np.ndarray, df: pd.DataFrame
    ) -> Tuple[float, dict]:
        """Relaxed scoring that rewards market outperformance, not absolute profit."""
        equity, trades = self.evaluator.simulate(signals, prices, df)

        if len(equity) == 0 or not np.all(np.isfinite(equity)):
            return 0.0, {"total_return": 0, "sharpe_ratio": 0, "max_drawdown": -0.99, "win_rate": 0}

        metrics = self.evaluator.compute_metrics(equity, trades)
        ret = metrics["total_return"]
        dd = abs(metrics["max_drawdown"])
        sharpe = max(-3.0, min(5.0, metrics["sharpe_ratio"]))
        wr = metrics["win_rate"]
        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)

        if ret <= -0.90 or dd > 0.80 or n_trades < 1:
            return 0.0, metrics

        # Relative to market (buy & hold of the same slice)
        market_return = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0
        excess = ret - market_return
        excess_c = max(-0.50, min(2.0, excess))

        ret_score = max(0, min(1.0, (excess_c + 0.10) / 0.30))
        sharpe_score = max(0, min(1.0, (sharpe + 1.0) / 4.0))
        dd_score = max(0, min(1.0, 1.0 - dd / 0.50))
        wr_score = max(0, min(1.0, (wr - 0.35) / 0.30))
        trade_score = min(1.0, n_trades / 20.0)

        score = (ret_score * 0.30 + sharpe_score * 0.20 +
                 dd_score * 0.20 + wr_score * 0.15 + trade_score * 0.15)
        return score, metrics

    def evaluate_agent(
        self, agent: Agent, df: pd.DataFrame
    ) -> Tuple[float, dict]:
        """Evaluate a single agent on a dataset slice."""
        try:
            valid_params = self._filter_params(agent.strategy_cls, agent.params)
            strategy = agent.strategy_cls(**valid_params)
            signals = strategy.generate_signals(df)

            # GridStrategy returns float positions → convert to discrete
            if signals.dtype in (np.float64, np.float32, float):
                from dex.strategies.grid import grid_signals_to_discrete
                signals = grid_signals_to_discrete(
                    signals, df["close"].values.astype(float)
                )

            min_start = getattr(strategy, "window", 20) * 2
            prices = df["close"].values[min_start:].astype(float)
            valid_signals = signals[min_start:]

            if len(valid_signals) < 50:
                return 0.0, {}

            score, metrics = self._relaxed_score(
                valid_signals, prices,
                df.iloc[min_start:].reset_index(drop=True),
            )
            return score, metrics
        except Exception:
            return 0.0, {}

    # ------------------------------------------------------------------
    # Genetic operators
    # ------------------------------------------------------------------

    def _crossover(
        self, donor: Agent, recipient: Agent
    ) -> Dict[str, Any]:
        """Mix donor genes into recipient.

        Each gene has ``crossover_rate`` chance of being taken from
        the donor instead of the recipient.
        """
        new_params = copy.deepcopy(recipient.params)
        for key in new_params:
            if key in donor.params and self.rng.random() < self.crossover_rate:
                new_params[key] = donor.params[key]
        return new_params

    def _mutate(self, params: Dict[str, Any], scale: float = 1.0) -> Dict[str, Any]:
        """Perturb numeric parameters within their bounds.

        Args:
            params: Parameter dict to mutate.
            scale: Mutation strength multiplier (> 1 = bolder).

        Returns:
            Mutated parameter dict (new copy).
        """
        new_params = copy.deepcopy(params)
        for key, val in new_params.items():
            if key not in self._PARAM_BOUNDS:
                continue
            lo, hi = self._PARAM_BOUNDS[key]
            rng_range = hi - lo

            # Gaussian perturbation scaled by parameter range
            noise = self.rng.gauss(0, rng_range * self.mutation_scale * scale)
            new_val = val + noise
            new_val = max(lo, min(hi, new_val))

            if key in self._DISCRETE_PARAMS:
                new_val = int(round(new_val))

            new_params[key] = new_val
        return new_params

    # ------------------------------------------------------------------
    # Evolution step
    # ------------------------------------------------------------------

    def evolve(
        self, df: pd.DataFrame, generation: int
    ) -> List[Agent]:
        """Run one evolution cycle.

        Args:
            df: Full historical DataFrame for evaluation.
            generation: Current generation number.

        Returns:
            Updated list of agents.
        """
        n = len(df)
        seg = n // 4  # evaluate on recent quarter
        val_df = df.iloc[-seg:].reset_index(drop=True) if seg > 100 else df

        # 1. Evaluate all agents
        scores = []
        for agent in self.agents:
            score, metrics = self.evaluate_agent(agent, val_df)
            agent.score_history.append(score)
            agent.generation = generation
            scores.append(score)
            sharpe = metrics.get("sharpe_ratio", 0) if metrics else 0
            ret = metrics.get("total_return", 0) * 100 if metrics else 0
            print(f"  {agent.name} ({agent.style}): score={score:.4f} "
                  f"sharpe={sharpe:.2f} ret={ret:+.2f}%")

        # 2. Rank agents
        ranked = sorted(
            enumerate(self.agents), key=lambda x: x[1].recent_score(), reverse=True
        )
        best_idx = ranked[0][0]
        worst_idx = ranked[-1][0]

        print(f"  Best: {self.agents[best_idx].name} "
              f"(score={self.agents[best_idx].recent_score():.4f})")
        print(f"  Worst: {self.agents[worst_idx].name} "
              f"(score={self.agents[worst_idx].recent_score():.4f})")

        # 3. Worst learns from best
        if self.agents[best_idx].recent_score() > self.agents[worst_idx].recent_score():
            new_params = self._crossover(
                self.agents[best_idx], self.agents[worst_idx]
            )
            self.agents[worst_idx].params = new_params
            print(f"  Crossover: {self.agents[worst_idx].name} <- "
                  f"{self.agents[best_idx].name} genes")

        # 4. Best explores bolder
        self.agents[best_idx].params = self._mutate(
            self.agents[best_idx].params, scale=1.5
        )
        print(f"  Mutation: {self.agents[best_idx].name} explores bolder")

        # 5. Darwinian weight rebalancing (softmax)
        recent_scores = np.array([a.recent_score() for a in self.agents])
        recent_scores = np.maximum(recent_scores, 0.001)  # avoid zero
        # Softmax with temperature
        exp_scores = np.exp(recent_scores / max(0.01, np.std(recent_scores)) * self.temperature)
        weights = exp_scores / exp_scores.sum()
        for agent, w in zip(self.agents, weights):
            agent.weight = float(w)

        print("  Weights: " + " | ".join(
            f"{a.name}={a.weight:.2f}" for a in self.agents
        ))
        print()

        return self.agents

    # ------------------------------------------------------------------
    # Ensemble prediction
    # ------------------------------------------------------------------

    def ensemble_signal(
        self, df: pd.DataFrame, enable_short: bool = True
    ) -> np.ndarray:
        """Generate weighted ensemble signal.

        Each agent votes with its weight.  Long=+1, Short=-1, Hold=0.
        The ensemble signal is the weighted sum thresholded to
        discrete signals.

        Args:
            df: OHLCV DataFrame.
            enable_short: Whether short signals are allowed.

        Returns:
            Integer signal array (0=close, 1=hold, 2=long, 3=short).
        """
        n = len(df)
        weighted = np.zeros(n, dtype=float)

        for agent in self.agents:
            try:
                valid_params = self._filter_params(agent.strategy_cls, agent.params)
                strategy = agent.strategy_cls(**valid_params)
                signals = strategy.generate_signals(df)

                # GridStrategy returns floats → convert
                if signals.dtype in (np.float64, np.float32, float):
                    from dex.strategies.grid import grid_signals_to_discrete
                    signals = grid_signals_to_discrete(
                        signals, df["close"].values.astype(float)
                    )

                # Convert signals to -1/0/+1 for voting
                vote = np.zeros(n, dtype=float)
                vote[signals == 2] = 1.0
                if enable_short:
                    vote[signals == 3] = -1.0
                weighted += vote * agent.weight
            except Exception:
                continue

        # Threshold to discrete
        result = np.ones(n, dtype=int)  # default hold
        result[weighted > 0.3] = 2      # strong long consensus
        result[weighted < -0.3] = 3     # strong short consensus
        result[abs(weighted) < 0.15] = 1  # no consensus → hold

        return result


# ---------------------------------------------------------------------------
# Evolution runner
# ---------------------------------------------------------------------------

def run_evolution(
    df: pd.DataFrame,
    generations: int = 20,
    evolution_interval: int = 5,
    agents: Optional[List[Agent]] = None,
    verbose: bool = True,
) -> EvolutionEngine:
    """Run multi-generation evolution and return the trained engine.

    Args:
        df: Full historical OHLCV DataFrame.
        generations: Total evolution cycles to run.
        evolution_interval: Evaluate & evolve every N rounds.
        agents: Initial agent list (uses defaults if None).
        verbose: Print progress.

    Returns:
        Trained EvolutionEngine with evolved agents and weights.
    """
    engine = EvolutionEngine(
        agents=agents,
        evolution_interval=evolution_interval,
    )

    if verbose:
        print("=" * 60)
        print("ATLAS Multi-Strategy Evolution")
        print(f"Generations: {generations}  |  Interval: {evolution_interval}")
        print(f"Data: {len(df)} bars")
        print("=" * 60)

    for gen in range(1, generations + 1):
        if verbose:
            print(f"\n--- Generation {gen}/{generations} ---")

        # Every evolution_interval generations, run the full cycle
        if gen % evolution_interval == 0 or gen == 1:
            engine.evolve(df, gen)
        else:
            # Just evaluate and update scores (no parameter crossover)
            for agent in engine.agents:
                score, _ = engine.evaluate_agent(agent, df.iloc[-len(df)//4:])
                agent.score_history.append(score)

    # Final weight display
    if verbose:
        print("\n" + "=" * 60)
        print("Final Ensemble")
        print("=" * 60)
        for agent in engine.agents:
            print(f"  {agent.name} ({agent.style}): weight={agent.weight:.3f}  "
                  f"score={agent.recent_score():.4f}  gen={agent.generation}")
        print()

    return engine
