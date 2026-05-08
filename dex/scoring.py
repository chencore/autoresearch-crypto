"""
Risk-adjusted scoring with edge-case guards.

Replaces the naive composite score with a risk-penalised metric
that naturally rewards low-drawdown strategies and penalises
overfitting (too few trades) and excessive risk-taking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Edge-case flags
# ---------------------------------------------------------------------------


class EdgeFlag(Enum):
    """Markers for problematic evaluation results."""

    OVERFIT = auto()  # Too few trades — likely overfit
    RISKY = auto()  # Drawdown too large — excessive risk
    DEAD = auto()  # No improvement for multiple rounds — strategy exhausted


@dataclass
class ScoredResult:
    """Complete scoring output with edge-case diagnostics."""

    score: float
    raw_sharpe: float
    total_return: float
    max_drawdown: float
    win_rate: float
    n_trades: int

    # Edge flags
    flags: List[EdgeFlag] = field(default_factory=list)

    # Decomposed sub-scores for transparency
    sharpe_component: float = 0.0
    dd_component: float = 0.0
    trade_component: float = 0.0
    excess_component: float = 0.0

    @property
    def is_valid(self) -> bool:
        """Result passes all edge-case guards."""
        return EdgeFlag.RISKY not in self.flags and EdgeFlag.OVERFIT not in self.flags

    @property
    def is_dead(self) -> bool:
        """Strategy shows no sign of life."""
        return EdgeFlag.DEAD in self.flags


# ---------------------------------------------------------------------------
# Risk-adjusted scoring function
# ---------------------------------------------------------------------------


def risk_adjusted_score(
    sharpe: float,
    total_return: float,
    max_drawdown: float,
    win_rate: float,
    n_trades: int,
    market_return: float = 0.0,
    *,
    min_trades: int = 10,
    max_dd: float = 0.30,
    sharpe_weight: float = 0.30,
    dd_weight: float = 0.25,
    trade_weight: float = 0.20,
    excess_weight: float = 0.15,
    wr_weight: float = 0.10,
) -> ScoredResult:
    """Compute a risk-adjusted composite score with edge-case guards.

    Score = SharpeComp × 0.30 + DDComp × 0.25 + TradeComp × 0.20
          + ExcessComp × 0.15 + WRComp × 0.10

    Where:
    - **DDComp** = 1 / (1 + abs(MaxDD) / dd_penalty_scale) — higher DD = lower score
    - **TradeComp** = min(1.0, n_trades / min_trades)⁰·⁵ — too few = penalty
    - **SharpeComp** = clamp(Sharpe / 3.0, 0, 1)
    - **ExcessComp** = clamp((Return - MarketReturn + 0.10) / 0.30, 0, 1)

    Edge-case guards (score → 0):
    - ``n_trades < min_trades`` → OVERFIT flag
    - ``abs(MaxDD) > max_dd`` → RISKY flag
    - ``total_return < -0.90`` → hard fail

    Args:
        sharpe: Sharpe ratio.
        total_return: Total return as decimal (e.g. 0.15 = 15%).
        max_drawdown: Maximum drawdown as negative decimal.
        win_rate: Win rate as decimal.
        n_trades: Number of completed trades.
        market_return: Buy-and-hold return over the same period.
        min_trades: Minimum trades before OVERFIT flag.
        max_dd: Maximum acceptable drawdown before RISKY flag.
        sharpe_weight: Weight of Sharpe component.
        dd_weight: Weight of drawdown penalty component.
        trade_weight: Weight of trade count component.
        excess_weight: Weight of excess-return component.
        wr_weight: Weight of win-rate component.

    Returns:
        ScoredResult with score, flags, and component breakdown.
    """
    flags: List[EdgeFlag] = []

    # --- Edge-case guards ---
    if total_return <= -0.90:
        return ScoredResult(
            score=0.0,
            raw_sharpe=sharpe,
            total_return=total_return,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            n_trades=n_trades,
            flags=[EdgeFlag.RISKY],
        )

    if abs(max_drawdown) > max_dd:
        flags.append(EdgeFlag.RISKY)

    if n_trades < min_trades:
        flags.append(EdgeFlag.OVERFIT)

    # --- Component scores ---
    # Sharpe: 0..1 range, Sharpe=3.0 → 1.0
    sharpe_comp = max(0.0, min(1.0, (sharpe + 1.0) / 4.0))

    # Drawdown penalty: 1 / (1 + dd/scale)
    # 0% DD → 1.0, 15% DD → 0.57, 30% DD → 0.40
    dd_scale = 0.15
    dd_comp = 1.0 / (1.0 + abs(max_drawdown) / dd_scale)

    # Trade count: sqrt scaling — rewards more trades but with diminishing returns
    trade_comp = min(1.0, (n_trades / max(min_trades, 1)) ** 0.5)

    # Excess return vs market
    excess = total_return - market_return
    excess_comp = max(0.0, min(1.0, (excess + 0.10) / 0.30))

    # Win rate
    wr_comp = max(0.0, min(1.0, (win_rate - 0.35) / 0.30))

    # --- Composite score ---
    score = (
        sharpe_comp * sharpe_weight
        + dd_comp * dd_weight
        + trade_comp * trade_weight
        + excess_comp * excess_weight
        + wr_comp * wr_weight
    )

    # Apply hard gate after scoring (so we can still see what the score WOULD be)
    if EdgeFlag.RISKY in flags or EdgeFlag.OVERFIT in flags:
        score = 0.0

    return ScoredResult(
        score=score,
        raw_sharpe=sharpe,
        total_return=total_return,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        n_trades=n_trades,
        flags=flags,
        sharpe_component=sharpe_comp,
        dd_component=dd_comp,
        trade_component=trade_comp,
        excess_component=excess_comp,
    )


# ---------------------------------------------------------------------------
# Dead agent detection
# ---------------------------------------------------------------------------


def detect_dead_agent(
    score_history: List[float],
    dead_threshold: int = 5,
    epsilon: float = 0.001,
) -> bool:
    """Check if an agent's scores have plateaued.

    An agent is 'dead' if its score hasn't improved by more than
    ``epsilon`` over the last ``dead_threshold`` rounds.

    Args:
        score_history: List of recent scores, oldest first.
        dead_threshold: Number of rounds to look back.
        epsilon: Minimum score change to consider 'alive'.

    Returns:
        True if the agent appears to have plateaued.
    """
    if len(score_history) < dead_threshold:
        return False

    recent = score_history[-dead_threshold:]
    score_range = max(recent) - min(recent)
    return score_range < epsilon


# ---------------------------------------------------------------------------
# Revival strategy selector
# ---------------------------------------------------------------------------

REVIVAL_STRATEGIES = [
    "widen_param_space",  # Expand parameter search bounds by 2×
    "narrow_param_space",  # Contract around current best
    "switch_strategy_type",  # Pick a different strategy class
    "switch_timeframe",  # Change from 5m to 15m or 1h
    "switch_symbol",  # Change from ETH to BTC or SOL
    "add_indicator",  # Enable a previously-disabled indicator filter
    "remove_indicator",  # Disable an active indicator filter
    "invert_hypothesis",  # Try the opposite of the current best hypothesis
]


def pick_revival_action(
    agent_name: str,
    revival_count: int,
) -> Tuple[str, Dict]:
    """Select a revival action for a dead agent.

    Cycles through revival strategies, trying broader changes as
    the agent fails more times.

    Args:
        agent_name: Which agent needs revival.
        revival_count: How many times this agent has been revived.

    Returns:
        (action_name, action_params) tuple.
    """
    idx = revival_count % len(REVIVAL_STRATEGIES)
    action = REVIVAL_STRATEGIES[idx]

    params: Dict = {"agent": agent_name, "revival_attempt": revival_count + 1}

    if action == "widen_param_space":
        params["param_scale"] = 2.0 ** (revival_count + 1)
    elif action == "switch_strategy_type":
        params["new_strategy"] = "random_from_pool"
    elif action == "switch_timeframe":
        params["timeframes"] = ["15m", "1h", "4h"]
    elif action == "switch_symbol":
        params["symbols"] = ["BTCUSDT", "SOLUSDT"]
    elif action == "add_indicator":
        params["indicator_pool"] = ["use_adx", "use_macd", "use_volume", "use_trend_filter"]
    elif action == "remove_indicator":
        params["indicator_pool"] = ["use_adx", "use_macd", "use_volume"]

    return action, params
