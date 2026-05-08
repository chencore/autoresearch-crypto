"""
GEPA Reflective Evolution Engine (反思式进化).

Instead of random parameter mutation, each agent follows a scientific
method cycle:

    1. Observe → review recent experiment logs
    2. Hypothesise → propose a causal hypothesis
    3. Experiment → design and run a targeted parameter change
    4. Reflect → log the outcome and update beliefs

Every 5 experiments, the meta-reflection loop reads the last 5 logs,
identifies blind spots, and proposes a new research direction.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ExperimentLog:
    """A single experiment record with reflection."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    agent: str = ""  # Agent name (Alpha/Beta/Gamma/Delta)
    hypothesis: str = ""  # What we believed before running
    tried: str = ""  # What parameter change was made
    params_before: Dict[str, Any] = field(default_factory=dict)
    params_after: Dict[str, Any] = field(default_factory=dict)
    score_before: float = 0.0
    score_after: float = 0.0
    sharpe_before: float = 0.0
    sharpe_after: float = 0.0
    ret_before: float = 0.0
    ret_after: float = 0.0
    dd_before: float = 0.0
    dd_after: float = 0.0
    result_summary: str = ""  # Human-readable result
    reflection: str = ""  # Why it worked/failed, what to try next
    edge_flags: List[str] = field(default_factory=list)  # RISKY / OVERFIT / DEAD


@dataclass
class Hypothesis:
    """A research hypothesis with proposed verification."""

    text: str  # The hypothesis statement
    agent: str  # Target agent
    param_changes: Dict[str, Any]  # Concrete parameter changes to test
    rationale: str  # Why this should work
    blind_spot: str = ""  # What market condition we're missing


# ---------------------------------------------------------------------------
# Hypothesis templates (domain knowledge)
# ---------------------------------------------------------------------------

HYPOTHESIS_TEMPLATES = [
    # Volatility-adaptive
    Hypothesis(
        text="在低波动环境下收紧入场阈值可以减少假信号",
        agent="Beta",
        param_changes={"std_dev": 2.4, "entry_zone": 0.3},
        rationale="低波动时布林带收窄，价格容易频繁触发上下轨",
        blind_spot="需要定义'低波动'的量化标准，可以用 ATR/price 比率",
    ),
    Hypothesis(
        text="高波动环境下应放宽止损，避免被噪音震出",
        agent="Alpha",
        param_changes={"atr_multiplier": 3.0},
        rationale="高波动时短期噪音大，紧止损会被频繁触发",
        blind_spot="需要区分'趋势波动'和'震荡波动'",
    ),
    # Trend/momentum
    Hypothesis(
        text="强趋势市中减少逆势交易，增加顺势仓位",
        agent="Alpha",
        param_changes={"use_adx": True, "adx_threshold": 20},
        rationale="ADX 过滤可以在趋势市避免逆势入场",
        blind_spot="ADX 阈值固定可能不适应不同周期",
    ),
    Hypothesis(
        text="RSI 超卖线应根据近期波动率动态调整",
        agent="Delta",
        param_changes={"rsi_low": 25},
        rationale="高波动时 RSI 容易到达极端值，收紧避免假信号",
        blind_spot="需要回测确认 vol_high 的分界线",
    ),
    # Mean reversion
    Hypothesis(
        text="均值回归在震荡市表现好，趋势市应降低权重",
        agent="Beta",
        param_changes={"max_hold_bars": 12, "entry_zone": 0.3},
        rationale="震荡市中价格回归快，缩短持仓和提前入场更有效",
        blind_spot="如何实时判断震荡 vs 趋势？需要 ADX 辅助",
    ),
    # Grid optimization
    Hypothesis(
        text="网格间距应根据近期 ATR 而非固定百分比",
        agent="Gamma",
        param_changes={"atr_spacing_mult": 0.8, "grid_spacing_pct": 0.003},
        rationale="ATR 自适应间距在市场波动变化时保持网格有效性",
        blind_spot="单边趋势中网格会持续亏损，需要趋势过滤器",
    ),
    # Multi-factor
    Hypothesis(
        text="启用多因子共振可以过滤单一指标的假信号",
        agent="Alpha",
        param_changes={"use_resonance": True, "resonance_min_score": 3},
        rationale="多个独立指标同向时信号可靠性显著提升",
        blind_spot="过多因子可能导致交易机会过少",
    ),
    # Market regime
    Hypothesis(
        text="不同市场状态应使用不同的策略参数集",
        agent="Beta",
        param_changes={"trend_ma_period": 100},
        rationale="用长期 MA 判断大趋势方向，避免逆大势交易",
        blind_spot="需要实现市场状态自动检测和参数切换",
    ),
]


# ---------------------------------------------------------------------------
# Reflection engine
# ---------------------------------------------------------------------------


class ReflectionEngine:
    """GEPA reflective evolution manager.

    Attributes:
        experiment_logs: Full history of experiments.
        hypotheses: Active hypotheses waiting to be tested.
        meta_reflections: Periodic synthesis of recent experiments.
    """

    def __init__(
        self,
        log_dir: str = "reflection_logs",
        seed: int = 42,
    ):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.rng = random.Random(seed)

        self.experiment_logs: List[ExperimentLog] = []
        self.hypotheses: List[Hypothesis] = list(HYPOTHESIS_TEMPLATES)
        self.meta_reflections: List[str] = []
        self.blind_spots: List[str] = []

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_experiment(self, log: ExperimentLog) -> None:
        """Record an experiment and its reflection."""
        self.experiment_logs.append(log)
        self._save_log(log)

    def _save_log(self, log: ExperimentLog) -> None:
        """Persist a single experiment log."""
        path = os.path.join(
            self.log_dir,
            f"exp_{log.timestamp.replace(':', '-')}_{log.agent}.json",
        )
        data = {
            "timestamp": log.timestamp,
            "agent": log.agent,
            "hypothesis": log.hypothesis,
            "tried": log.tried,
            "params_before": log.params_before,
            "params_after": log.params_after,
            "score_before": log.score_before,
            "score_after": log.score_after,
            "sharpe_before": log.sharpe_before,
            "sharpe_after": log.sharpe_after,
            "ret_before": log.ret_before,
            "ret_after": log.ret_after,
            "dd_before": log.dd_before,
            "dd_after": log.dd_after,
            "result_summary": log.result_summary,
            "reflection": log.reflection,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Reflection generation (rule-based — simulates LLM reasoning)
    # ------------------------------------------------------------------

    def _generate_reflection(self, log: ExperimentLog) -> str:
        """Generate a structured reflection from experiment results.

        This simulates what an LLM would produce when asked to reflect
        on a parameter change experiment.  In production this would be
        replaced by an actual LLM call.
        """
        score_delta = log.score_after - log.score_before
        sharpe_delta = log.sharpe_after - log.sharpe_before
        ret_delta = log.ret_after - log.ret_before
        dd_delta = log.dd_after - log.dd_before

        reflections = []

        # Analyse what happened
        if score_delta > 0.05:
            reflections.append(f"改进显著 (score +{score_delta:.3f})。参数调整方向正确。")
        elif score_delta > 0:
            reflections.append(f"轻微改善 (score +{score_delta:.3f})。方向对但幅度不够。")
        elif score_delta > -0.05:
            reflections.append(f"变化不大 (score {score_delta:+.3f})。该参数可能不是关键因子。")
        else:
            reflections.append(f"明显恶化 (score {score_delta:+.3f})。这个调整方向是错误的。")

        # Sharpe analysis
        if sharpe_delta > 0.2:
            reflections.append(f"夏普提升 {sharpe_delta:+.2f}，风险调整收益改善。")
        if dd_delta < -0.02:  # drawdown got worse (more negative)
            reflections.append(
                f"注意回撤扩大了 {abs(dd_delta) * 100:.1f}%。可能是参数调整增加了尾部风险暴露。"
            )

        # Suggest next step
        if score_delta > 0.03:
            reflections.append(
                "下一步：继续在这个方向上加大调整幅度，或将该参数与其他参数联动优化。"
            )
        elif ret_delta > 0 and sharpe_delta < 0:
            reflections.append("收益提升了但风险也增加了。下一步：尝试在保持该参数的同时收紧止损。")
        else:
            reflections.append(
                "下一步：回退该参数，尝试调整其他相关参数。考虑市场状态是否发生了变化。"
            )

        return " ".join(reflections)

    # ------------------------------------------------------------------
    # Meta-reflection (every 5 experiments)
    # ------------------------------------------------------------------

    def meta_reflect(self) -> Tuple[str, Hypothesis]:
        """Analyse the last 5 experiments and propose a new hypothesis.

        Returns:
            (summary_text, new_hypothesis)
        """
        recent = (
            self.experiment_logs[-5:] if len(self.experiment_logs) >= 5 else self.experiment_logs
        )
        if len(recent) < 3:
            return "Not enough data for meta-reflection.", self.hypotheses[0]

        # --- Pattern detection ---
        # 1. Which agent improved most?
        agent_deltas: Dict[str, List[float]] = {}
        for log in recent:
            agent_deltas.setdefault(log.agent, []).append(log.score_after - log.score_before)
        avg_deltas = {a: sum(d) / len(d) for a, d in agent_deltas.items()}

        # 2. What kind of changes worked?
        score_improvements = [log.score_after - log.score_before for log in recent]
        sharpe_improvements = [log.sharpe_after - log.sharpe_before for log in recent]

        # 3. Detect blind spots
        blind_spots = []
        all_reflections = " ".join(log.reflection for log in recent)

        if "趋势" in all_reflections and "震荡" in all_reflections:
            blind_spots.append("缺少市场状态自动检测：无法在趋势和震荡策略间自动切换")
        if "止损" in all_reflections or "回撤" in all_reflections:
            blind_spots.append("风险控制在各 Agent 间不统一：需要全局风险预算分配")
        if "volatility" in all_reflections.lower() or "波动" in all_reflections:
            blind_spots.append("波动率是核心变量但未建模：应加入 VIX-like 波动率分支")

        if not blind_spots:
            blind_spots.append("当前参数搜索空间可能太窄，建议探索策略类型本身的变化")

        self.blind_spots.extend(blind_spots)

        # --- Generate hypothesis ---
        best_agent = max(avg_deltas, key=avg_deltas.get)
        worst_agent = min(avg_deltas, key=avg_deltas.get)

        # Pick a hypothesis template that matches the observed pattern
        if any("sharpe" in log.reflection.lower() or "夏普" in log.reflection for log in recent):
            new_h = Hypothesis(
                text=f"风险调整后的收益仍有优化空间，{worst_agent}应向{best_agent}的对冲逻辑学习",
                agent=worst_agent,
                param_changes={"atr_multiplier": 3.0, "max_hold_bars": 12},
                rationale=f"{best_agent}的风控参数产生了更好的风险调整收益",
                blind_spot=blind_spots[0] if blind_spots else "",
            )
        else:
            # Cycle to next unused hypothesis
            idx = len(self.meta_reflections) % len(self.hypotheses)
            new_h = self.hypotheses[idx]

        # --- Summary ---
        summary_lines = [
            f"=== Meta-Reflection (实验 {len(self.experiment_logs) - len(recent) + 1}-{len(self.experiment_logs)}) ===",
            f"最近 {len(recent)} 次实验：",
            f"  平均 score 变化: {np.mean(score_improvements):+.3f}",
            f"  平均 sharpe 变化: {np.mean(sharpe_improvements):+.2f}",
            f"  最佳 Agent: {best_agent}",
            f"  需关注 Agent: {worst_agent}",
            "",
            "发现的盲点:",
        ]
        for bs in blind_spots[-3:]:
            summary_lines.append(f"  - {bs}")
        summary_lines.append("")
        summary_lines.append(f"新假设: {new_h.text}")
        summary_lines.append(f"验证实验: {new_h.agent} 调整 {new_h.param_changes}")

        summary = "\n".join(summary_lines)
        self.meta_reflections.append(summary)

        return summary, new_h

    # ------------------------------------------------------------------
    # Experiment runner
    # ------------------------------------------------------------------

    def run_experiment(
        self,
        agent_name: str,
        hypothesis: Hypothesis,
        params_before: Dict[str, Any],
        evaluate_fn: Callable[[Dict[str, Any]], Tuple[float, float, float, float]],
    ) -> ExperimentLog:
        """Run one hypothesis-driven experiment.

        Args:
            agent_name: Which agent is being tested.
            hypothesis: The hypothesis being verified.
            params_before: Current parameter values.
            evaluate_fn: (params) -> (score, sharpe, return, max_dd).

        Returns:
            ExperimentLog with results and auto-generated reflection.
        """
        # Apply parameter changes
        params_after = dict(params_before)
        for key, val in hypothesis.param_changes.items():
            if isinstance(val, str):
                # Try to evaluate as expression with "current" variable
                current = params_before.get(key, 0)
                try:
                    result = eval(
                        val.replace("current", str(current)),
                        {"__builtins__": {}, "vol_high": False, "vol_low": False},
                        {"current": current},
                    )
                    params_after[key] = float(result) if not isinstance(result, bool) else result
                except Exception:
                    params_after[key] = params_before.get(key, val)  # keep original
            elif isinstance(val, bool) and key not in params_before:
                params_after[key] = val  # new boolean param
            else:
                params_after[key] = val

        # Ensure discrete params stay int
        discrete = {
            "window",
            "atr_period",
            "max_hold_bars",
            "rsi_threshold",
            "adx_threshold",
            "grid_levels",
            "trend_ma_period",
            "rsi_period",
            "rsi_low",
            "rsi_high",
            "ma_period",
        }
        for k in discrete:
            if k in params_after and isinstance(params_after[k], float):
                params_after[k] = int(round(params_after[k]))

        # Evaluate BEFORE
        score_before, sharpe_before, ret_before, dd_before = evaluate_fn(params_before)

        # Evaluate AFTER
        score_after, sharpe_after, ret_after, dd_after = evaluate_fn(params_after)

        # Build log
        log = ExperimentLog(
            agent=agent_name,
            hypothesis=hypothesis.text,
            tried=", ".join(f"{k}={v}" for k, v in hypothesis.param_changes.items()),
            params_before=dict(params_before),
            params_after=params_after,
            score_before=score_before,
            score_after=score_after,
            sharpe_before=sharpe_before,
            sharpe_after=sharpe_after,
            ret_before=ret_before,
            ret_after=ret_after,
            dd_before=dd_before,
            dd_after=dd_after,
            result_summary=(
                f"score: {score_before:.3f} → {score_after:.3f} "
                f"({score_after - score_before:+.3f}), "
                f"sharpe: {sharpe_before:.2f} → {sharpe_after:.2f} "
                f"({sharpe_after - sharpe_before:+.2f}), "
                f"ret: {ret_before * 100:+.2f}% → {ret_after * 100:+.2f}%, "
                f"DD: {dd_before * 100:.1f}% → {dd_after * 100:.1f}%"
            ),
        )

        # Auto-generate reflection
        log.reflection = self._generate_reflection(log)

        # Record
        self.log_experiment(log)

        return log


# ---------------------------------------------------------------------------
# GEPA evolution loop (integrates with EvolutionEngine)
# ---------------------------------------------------------------------------


def gepa_evolve(
    agents: List[Any],  # List of Agent dataclass instances
    evaluate_fn: Callable,
    engine: ReflectionEngine,
    cycles: int = 10,
    verbose: bool = True,
) -> ReflectionEngine:
    """Run the full GEPA reflective evolution loop.

    Each cycle:
    1. Pick an agent and a hypothesis
    2. Run experiment with reflection
    3. Every 5 cycles: meta-reflect, update research direction

    Args:
        agents: List of Agent objects (from dex.evolution).
        evaluate_fn: (params_dict) -> (score, sharpe, return, max_dd).
        engine: ReflectionEngine instance.
        cycles: Number of experiment cycles to run.
        verbose: Print progress.

    Returns:
        Updated ReflectionEngine with full experiment log.
    """
    if verbose:
        print("=" * 60)
        print("GEPA 反思式进化 (Reflective Evolution)")
        print(f"Cycles: {cycles}  |  Meta-reflection every 5")
        print("=" * 60)

    for cycle in range(1, cycles + 1):
        if verbose:
            print(f"\n--- Cycle {cycle}/{cycles} ---")

        # Pick agent — rotate through all agents
        agent = agents[(cycle - 1) % len(agents)]

        # Pick hypothesis — use the engine's current hypothesis queue
        h_idx = (cycle - 1) % len(engine.hypotheses)
        hypothesis = engine.hypotheses[h_idx]

        if verbose:
            print(f"  Agent: {agent.name} ({agent.style})")
            print(f"  假设: {hypothesis.text}")
            print(f"  实验: {hypothesis.param_changes}")

        # Run experiment
        log = engine.run_experiment(
            agent_name=agent.name,
            hypothesis=hypothesis,
            params_before=dict(agent.params),
            evaluate_fn=evaluate_fn,
        )

        # Update agent params if improved
        if log.score_after > log.score_before:
            agent.params = log.params_after
            if verbose:
                print(f"  ✓ 接受新参数 (score {log.score_before:.3f} → {log.score_after:.3f})")
        else:
            if verbose:
                print(f"  ✗ 拒绝新参数 (score {log.score_before:.3f} → {log.score_after:.3f})")

        if verbose:
            print(f"  反思: {log.reflection[:120]}...")

        # Meta-reflection every 5 cycles
        if cycle % 5 == 0:
            if verbose:
                print(f"\n  {'=' * 50}")
            summary, new_h = engine.meta_reflect()
            if verbose:
                print(summary)
                print(f"  {'=' * 50}")

    if verbose:
        print(f"\n{'=' * 60}")
        print(
            f"GEPA 进化完成。共 {len(engine.experiment_logs)} 次实验，"
            f"{len(engine.meta_reflections)} 次元反思。"
        )
        print(f"{'=' * 60}")

    return engine


# ---------------------------------------------------------------------------
# GEPA V2 — with edge guards, mandatory reflection, and never-stop loop
# ---------------------------------------------------------------------------

from dex.scoring import (
    risk_adjusted_score,
    EdgeFlag,
    detect_dead_agent,
    pick_revival_action,
)


def gepa_evolve_v2(
    agents: List[Any],
    evaluate_fn_raw: Callable,  # (params) -> (score, sharpe, ret, max_dd)
    engine: ReflectionEngine,
    cycles: int = 30,
    min_trades: int = 10,
    max_dd: float = 0.30,
    dead_threshold: int = 5,
    verbose: bool = True,
) -> ReflectionEngine:
    """GEPA V2: risk-adjusted scoring + edge guards + never-stop revival.

    Args:
        agents: List of Agent objects.
        evaluate_fn_raw: (params) -> (score, sharpe, ret, max_dd).
        engine: ReflectionEngine instance.
        cycles: Number of experiment cycles.
        min_trades: Minimum trades before OVERFIT flag.
        max_dd: Maximum acceptable drawdown before RISKY flag.
        dead_threshold: Rounds of no improvement before DEAD flag.
        verbose: Print progress.
    """
    if verbose:
        print("=" * 60)
        print("GEPA V2 反思式进化 (Risk-Adjusted + Edge Guards + Revival)")
        print(
            f"Metric: Sharpe x (1-DD)^-1  |  MinTrades={min_trades}  "
            f"MaxDD={max_dd * 100:.0f}%  |  DeadThreshold={dead_threshold}"
        )
        print("=" * 60)

    agent_states: Dict[str, dict] = {
        a.name: {
            "revival_count": 0,
            "consecutive_rejections": 0,
            "last_reflection": "",
            "reflection_repeats": 0,
            "score_history": [],
        }
        for a in agents
    }

    for cycle in range(1, cycles + 1):
        if verbose:
            print(f"\n--- Cycle {cycle}/{cycles} ---")

        agent = agents[(cycle - 1) % len(agents)]
        state = agent_states[agent.name]
        h_idx = (cycle - 1) % len(engine.hypotheses)
        hypothesis = engine.hypotheses[h_idx]

        if verbose:
            print(f"  Agent: {agent.name} ({agent.style})")
            print(f"  假设: {hypothesis.text}")

        # --- Edge guard: dead agent check ---
        is_dead = detect_dead_agent(state["score_history"], dead_threshold)
        if is_dead and state["revival_count"] < 3:
            action, action_params = pick_revival_action(agent.name, state["revival_count"])
            state["revival_count"] += 1
            if verbose:
                print(f"  💤 DEAD detected — revival action: {action}")
                print(f"     params: {action_params}")

            if action == "widen_param_space":
                scale = action_params.get("param_scale", 2.0)
                for k in agent.params:
                    if isinstance(agent.params[k], (int, float)):
                        agent.params[k] = agent.params[k] * (
                            1 + np.random.uniform(-0.5, 0.5) * scale
                        )
            elif action == "add_indicator":
                pool = action_params.get("indicator_pool", ["use_adx"])
                key = pool[state["revival_count"] % len(pool)]
                if key in agent.params:
                    agent.params[key] = True

        # --- Run experiment ---
        log = engine.run_experiment(
            agent_name=agent.name,
            hypothesis=hypothesis,
            params_before=dict(agent.params),
            evaluate_fn=evaluate_fn_raw,
        )

        # Compute risk-adjusted score for edge guard display
        scored = risk_adjusted_score(
            sharpe=log.sharpe_after,
            total_return=log.ret_after,
            max_drawdown=log.dd_after,
            win_rate=0.45,
            n_trades=10,
            min_trades=min_trades,
            max_dd=max_dd,
        )

        # --- Edge guard: mark flags on log ---
        edge_flags_str = []
        if EdgeFlag.RISKY in scored.flags:
            edge_flags_str.append("RISKY")
            state["consecutive_rejections"] += 1
            if verbose:
                print(f"  🔥 RISKY — DD={log.dd_after * 100:.1f}% > {max_dd * 100:.0f}%")
        if EdgeFlag.OVERFIT in scored.flags:
            edge_flags_str.append("OVERFIT")
            if verbose:
                print("  ⚠ OVERFIT — too few trades")

        # Update log with edge info
        log.edge_flags = edge_flags_str

        # --- Mandatory reflection enforcement ---
        if log.reflection == state["last_reflection"]:
            state["reflection_repeats"] += 1
            if state["reflection_repeats"] >= 3:
                if verbose:
                    print("  ⚠ 连续3轮反思重复 — 标记为DEAD")
                state["score_history"] = []  # Force dead detection next cycle
        else:
            state["reflection_repeats"] = 0
        state["last_reflection"] = log.reflection

        # Update params if improved
        if scored.score > 0 and log.score_after > log.score_before:
            agent.params = log.params_after
            state["consecutive_rejections"] = 0
            if verbose:
                print(f"  ✓ 接受 (score {log.score_before:.3f}→{log.score_after:.3f})")
                print(
                    f"     risk-adj score={scored.score:.4f} "
                    f"sharpe_comp={scored.sharpe_component:.2f} "
                    f"dd_comp={scored.dd_component:.2f}"
                )
        else:
            state["consecutive_rejections"] += 1
            if verbose:
                print(f"  ✗ 拒绝 (score {log.score_before:.3f}→{log.score_after:.3f})")

        state["score_history"].append(scored.score)
        if verbose:
            flags_display = f" [{','.join(edge_flags_str)}]" if edge_flags_str else ""
            print(f"  反思{flags_display}: {log.reflection[:100]}...")

        # --- Meta-reflection every 5 cycles ---
        if cycle % 5 == 0:
            if verbose:
                print(f"\n  {'=' * 50}")
            summary, new_h = engine.meta_reflect()

            # Check if all agents are dead → escalate
            dead_count = sum(
                1
                for s in agent_states.values()
                if detect_dead_agent(s["score_history"], dead_threshold)
            )
            if dead_count >= len(agents) * 0.75:
                summary += (
                    f"\n\n  ⚠ 全部 {dead_count}/{len(agents)} Agent 枯竭！"
                    f"\n  触发全局探索模式：扩大参数空间 + 启用新指标"
                )
                for a in agents:
                    for k in a.params:
                        if isinstance(a.params[k], (int, float)):
                            a.params[k] = a.params[k] * (0.5 + np.random.random())

            if verbose:
                print(summary)
                print(f"  {'=' * 50}")

    if verbose:
        print(f"\n{'=' * 60}")
        print(
            f"GEPA V2 完成。{len(engine.experiment_logs)} 实验, "
            f"{len(engine.meta_reflections)} 元反思"
        )
        # Summary of agent states
        for name, st in agent_states.items():
            flags = []
            if detect_dead_agent(st["score_history"], dead_threshold):
                flags.append("DEAD")
            if st["reflection_repeats"] >= 3:
                flags.append("STALE")
            flag_str = f" [{','.join(flags)}]" if flags else ""
            print(
                f"  {name}: {len(st['score_history'])} rounds, "
                f"{st['revival_count']} revivals{flag_str}"
            )
        print(f"{'=' * 60}")

    return engine
