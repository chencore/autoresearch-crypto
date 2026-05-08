"""
ETH 60天5分钟数据 — 最优策略全面搜索。
按策略类型分类搜索，然后横向对比选出最优。

策略池:
  1. TrendStrategy      — 布林带 + 多指标过滤（原有）
  2. ScalpStrategy      — 高频剥头皮（TP/SL固定）
  3. PureActionStrategy — 纯价格行为（无因子约束）
  4. HybridStrategy     — ADX判市自适应
  5. TrendFollowStrategy— EMA趋势跟随
  6. HybridMMStrategy   — RSI均值回归+动量
  7. AdaptiveHybridStrategy — ADX判市 + RSI/EMA切换

Usage:
    uv run python search_eth_optimal.py
    uv run python search_eth_optimal.py --quick  # 快速模式，缩短时间预算
"""

import os
import sys
import math
import time
import json
import argparse
import itertools
from datetime import datetime

# Fix Windows GBK encoding issues
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pyarrow.parquet as pq
import torch

# 导入所有策略
from train_quant import (
    TrendStrategy,
    ScalpStrategy,
    PureActionStrategy,
    HybridStrategy,
    TrendFollowStrategy,
    HybridMeanRevMomentumStrategy,
    AdaptiveHybridStrategy,
    StrategyEvaluator,
)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(PROJECT_DIR, "data", "crypto", "ETHUSDT_5m.parquet")
RESULTS_DIR = os.path.join(PROJECT_DIR, "search_results")
os.makedirs(RESULTS_DIR, exist_ok=True)

INITIAL_CAPITAL = 10000.0
COMMISSION = 0.0002
SLIPPAGE = 0.0002


# ============================================================================
# 评分函数
# ============================================================================


def compute_final_score(metrics, trades, prefer_frequency=False):
    """
    综合评分：奖励高收益、低回撤、高胜率、足够交易次数。
    prefer_frequency=True: 适合高频策略，提高交易次数权重。
    """
    ret = metrics["total_return"]
    sharpe = max(0, min(5.0, metrics["sharpe_ratio"]))
    dd = abs(metrics["max_drawdown"])
    wr = metrics["win_rate"]
    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n_trades = len(trade_pnls)

    if ret <= -0.05:  # 放宽到-5%（相比买持-42%已经大幅跑赢）
        return 0.0, metrics

    if dd > 0.35:  # 放宽到35%
        return 0.0, metrics

    if n_trades < 3:
        return 0.0, metrics

    # 核心得分
    ret_score = min(1.0, max(0, ret / 0.20))  # 20%收益满分
    sharpe_score = min(1.0, sharpe / 3.0)  # Sharpe 3.0 满分
    dd_score = max(0, 1 - dd / 0.20)  # 回撤<20%满分
    wr_score = max(0, (wr - 0.40) / 0.30)  # 40%→0, 70%→1

    if prefer_frequency:
        trade_score = min(1.0, n_trades / 80.0)
        score = (
            ret_score * 0.20
            + sharpe_score * 0.15
            + dd_score * 0.15
            + wr_score * 0.15
            + trade_score * 0.35
        )
    else:
        trade_score = min(1.0, n_trades / 30.0)
        score = (
            ret_score * 0.30
            + sharpe_score * 0.25
            + dd_score * 0.20
            + wr_score * 0.10
            + trade_score * 0.15
        )

    return score, metrics


# ============================================================================
# 数据加载
# ============================================================================


def load_data():
    table = pq.read_table(DATA_FILE)
    df = table.to_pandas()
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["open"] = df["open"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df


# ============================================================================
# 通用搜索框架
# ============================================================================


def evaluate_strategy(strategy, df, evaluator, min_start=None):
    """评估策略在完整数据上的表现（使用相对市场基准的宽松评分）"""
    signals = strategy.generate_signals(df)

    if min_start is None:
        min_start = getattr(strategy, "window", 20) * 2

    prices = df["close"].values[min_start:].astype(float)
    valid_signals = signals[min_start:]

    if len(valid_signals) < 50:
        return 0.0, {}, [], signals

    # 使用模拟器计算权益曲线
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

    # 自动计算同期市场基准收益
    market_return = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0

    # 硬过滤：权益归零或回撤过大
    if ret <= -0.90:
        return 0.0, metrics, trades, signals
    if dd > 0.80:
        return 0.0, metrics, trades, signals
    if n_trades < 3:
        return 0.0, metrics, trades, signals

    # 相对评分：以买持为基准。策略跑赢买持即使亏钱也应得分
    excess_return = ret - market_return  # 超额收益
    excess_clamped = max(-0.50, min(2.0, excess_return))

    # 收益评分（相对基准）：超额10%满分
    ret_score = max(0, min(1.0, (excess_clamped + 0.10) / 0.30))

    # Sharpe评分
    sharpe_score = max(0, min(1.0, (sharpe + 1.0) / 4.0))

    # 回撤评分
    dd_score = max(0, min(1.0, 1.0 - dd / 0.50))

    # 胜率评分
    wr_score = max(0, min(1.0, (wr - 0.35) / 0.30))

    # 交易数量评分
    trade_score = min(1.0, n_trades / 20.0)

    score = (
        ret_score * 0.30
        + sharpe_score * 0.20
        + dd_score * 0.20
        + wr_score * 0.15
        + trade_score * 0.15
    )

    return score, metrics, trades, signals


def walk_forward_eval(strategy_cls, params, df, n_windows=3, evaluator=None):
    """Walk-forward 评估，返回平均分和稳定性指标"""
    if evaluator is None:
        evaluator = StrategyEvaluator()

    n = len(df)
    seg = n // (n_windows + 1)
    scores = []
    returns_list = []

    for w in range(n_windows):
        train_end = (w + 1) * seg
        val_start = train_end
        val_end = min(val_start + seg, n)

        if val_end - val_start < 100:
            continue

        val_df = df.iloc[val_start:val_end].reset_index(drop=True)

        strategy = strategy_cls(**params)
        signals = strategy.generate_signals(val_df)
        min_start = getattr(strategy, "window", 20) * 2
        prices = val_df["close"].values[min_start:].astype(float)

        s, m, t = evaluator.evaluate(
            signals[min_start:],
            prices,
            val_df.iloc[min_start:].reset_index(drop=True),
        )
        scores.append(s)
        returns_list.append(m.get("total_return", 0))

    if not scores:
        return 0, 0, 0

    avg_score = np.mean(scores)
    # 稳定性 = 1 - CV(变异系数)
    if len(scores) > 1 and np.std(scores) > 0:
        cv = np.std(scores) / (np.mean(scores) + 1e-8)
        stability = max(0, 1 - cv)
    else:
        stability = 0.5

    wf_score = avg_score * (0.5 + 0.5 * stability)
    return wf_score, avg_score, stability


# ============================================================================
# 各策略特定搜索
# ============================================================================


def search_trend(df, time_budget=120, market_return=0.0):
    """TrendStrategy 参数搜索"""
    evaluator = StrategyEvaluator()
    n = len(df)
    val_start = int(n * 0.85)
    val_df = df.iloc[val_start:].reset_index(drop=True)
    val_ret = val_df["close"].iloc[-1] / val_df["close"].iloc[0] - 1

    # 扩展参数空间
    grid = {
        "window": [12, 15, 18, 20, 24, 30],
        "std_dev": [1.5, 1.8, 2.0, 2.2, 2.5, 3.0],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 18, 24, 36, 48],
        "rsi_threshold": [25, 30, 35, 40],
        "entry_zone": [0.0, 0.2, 0.5],
    }

    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    best_score = -1
    best_params = None
    best_metrics = None
    best_trades = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > time_budget:
            break

        params = dict(zip(grid.keys(), combo))
        strategy = TrendStrategy(
            window=params["window"],
            std_dev=params["std_dev"],
            atr_multiplier=params["atr_multiplier"],
            max_hold_bars=params["max_hold_bars"],
            rsi_threshold=params["rsi_threshold"],
            entry_zone=params["entry_zone"],
        )
        score, metrics, trades, _ = evaluate_strategy(strategy, val_df, evaluator)

        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics
            best_trades = trades

    n_trades = len([t for t in (best_trades or []) if t.get("pnl") is not None])
    print(
        f"  TrendStrategy: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"best score={best_score:.4f} ret={best_metrics.get('total_return', 0) * 100:+.2f}% "
        f"sharpe={best_metrics.get('sharpe_ratio', 0):.2f} DD={best_metrics.get('max_drawdown', 0) * 100:.1f}% "
        f"trades={n_trades}"
    )
    return best_params, best_score, best_metrics


def search_pure(df, time_budget=120):
    """PureActionStrategy 参数搜索"""
    evaluator = StrategyEvaluator()
    n = len(df)
    val_start = int(n * 0.85)
    val_df = df.iloc[val_start:].reset_index(drop=True)

    grid = {
        "window": [10, 15, 20, 24],
        "std_dev": [1.5, 2.0, 2.5, 3.0],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5],
        "max_hold_bars": [12, 18, 24, 36],
        "entry_zone": [0.0, 0.3, 0.5],
        "trend_ma_period": [None, 50, 100],
        "adx_threshold": [None, 25, 30],
    }

    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    best_score = -1
    best_params = None
    best_metrics = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > time_budget:
            break

        params = dict(zip(grid.keys(), combo))
        strategy = PureActionStrategy(
            window=params["window"],
            std_dev=params["std_dev"],
            atr_period=params["atr_period"],
            atr_multiplier=params["atr_multiplier"],
            max_hold_bars=params["max_hold_bars"],
            entry_zone=params["entry_zone"],
            enable_short=True,
            trend_ma_period=params["trend_ma_period"],
            adx_threshold=params["adx_threshold"],
        )
        score, metrics, trades, _ = evaluate_strategy(strategy, val_df, evaluator)

        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    n_trades = (
        len(
            [
                t
                for t in (best_metrics and best_metrics.get("trades", []) or [])
                if t.get("pnl") is not None
            ]
        )
        if best_metrics
        else 0
    )
    print(
        f"  PureAction: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"best score={best_score:.4f} ret={best_metrics.get('total_return', 0) * 100:+.2f}% "
        f"sharpe={best_metrics.get('sharpe_ratio', 0):.2f} DD={best_metrics.get('max_drawdown', 0) * 100:.1f}% "
        f"trades={n_trades}"
    )
    return best_params, best_score, best_metrics


def search_hybrid(df, time_budget=120):
    """HybridStrategy 参数搜索"""
    evaluator = StrategyEvaluator()
    n = len(df)
    val_start = int(n * 0.85)
    val_df = df.iloc[val_start:].reset_index(drop=True)

    grid = {
        "window": [15, 20, 24],
        "std_dev": [1.8, 2.0, 2.5],
        "atr_period": [7, 14],
        "atr_multiplier": [2.0, 2.5, 3.0],
        "max_hold_bars": [18, 24, 36],
        "entry_zone": [0.0, 0.3],
        "trend_ma_period": [50, 100, 200],
        "adx_threshold": [20, 25, 30],
    }

    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    best_score = -1
    best_params = None
    best_metrics = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > time_budget:
            break

        params = dict(zip(grid.keys(), combo))
        strategy = HybridStrategy(
            window=params["window"],
            std_dev=params["std_dev"],
            atr_period=params["atr_period"],
            atr_multiplier=params["atr_multiplier"],
            max_hold_bars=params["max_hold_bars"],
            entry_zone=params["entry_zone"],
            enable_short=True,
            trend_ma_period=params["trend_ma_period"],
            adx_threshold=params["adx_threshold"],
        )
        score, metrics, trades, _ = evaluate_strategy(strategy, val_df, evaluator)

        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    n_trades = (
        len(
            [
                t
                for t in (best_metrics and best_metrics.get("trades", []) or [])
                if t.get("pnl") is not None
            ]
        )
        if best_metrics
        else 0
    )
    print(
        f"  Hybrid: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"best score={best_score:.4f} ret={best_metrics.get('total_return', 0) * 100:+.2f}% "
        f"sharpe={best_metrics.get('sharpe_ratio', 0):.2f} DD={best_metrics.get('max_drawdown', 0) * 100:.1f}% "
        f"trades={n_trades}"
    )
    return best_params, best_score, best_metrics


def search_trendfollow(df, time_budget=120):
    """TrendFollowStrategy 参数搜索"""
    evaluator = StrategyEvaluator()
    n = len(df)
    val_start = int(n * 0.85)
    val_df = df.iloc[val_start:].reset_index(drop=True)

    grid = {
        "long_ma_period": [50, 100, 200],
        "pull_ma_period": [10, 15, 20, 25],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 18, 24, 36],
        "entry_zone": [0.0, 0.5, 1.0],
    }

    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    best_score = -1
    best_params = None
    best_metrics = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > time_budget:
            break

        params = dict(zip(grid.keys(), combo))
        strategy = TrendFollowStrategy(
            long_ma_period=params["long_ma_period"],
            pull_ma_period=params["pull_ma_period"],
            atr_period=params["atr_period"],
            atr_multiplier=params["atr_multiplier"],
            max_hold_bars=params["max_hold_bars"],
            entry_zone=params["entry_zone"],
        )
        score, metrics, trades, _ = evaluate_strategy(strategy, val_df, evaluator)

        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    n_trades = (
        len(
            [
                t
                for t in (best_metrics and best_metrics.get("trades", []) or [])
                if t.get("pnl") is not None
            ]
        )
        if best_metrics
        else 0
    )
    print(
        f"  TrendFollow: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"best score={best_score:.4f} ret={best_metrics.get('total_return', 0) * 100:+.2f}% "
        f"sharpe={best_metrics.get('sharpe_ratio', 0):.2f} DD={best_metrics.get('max_drawdown', 0) * 100:.1f}% "
        f"trades={n_trades}"
    )
    return best_params, best_score, best_metrics


def search_hybrid_mm(df, time_budget=120):
    """HybridMeanRevMomentumStrategy 参数搜索"""
    evaluator = StrategyEvaluator()
    n = len(df)
    val_start = int(n * 0.85)
    val_df = df.iloc[val_start:].reset_index(drop=True)

    grid = {
        "rsi_period": [7, 10, 14],
        "rsi_low": [20, 25, 30],
        "rsi_high": [65, 70, 75, 80],
        "ma_period": [10, 20, 30],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [12, 18, 24, 36],
    }

    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    best_score = -1
    best_params = None
    best_metrics = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > time_budget:
            break

        params = dict(zip(grid.keys(), combo))
        strategy = HybridMeanRevMomentumStrategy(
            rsi_period=params["rsi_period"],
            rsi_low=params["rsi_low"],
            rsi_high=params["rsi_high"],
            ma_period=params["ma_period"],
            atr_period=params["atr_period"],
            atr_multiplier=params["atr_multiplier"],
            max_hold_bars=params["max_hold_bars"],
        )
        score, metrics, trades, _ = evaluate_strategy(strategy, val_df, evaluator)

        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    n_trades = (
        len(
            [
                t
                for t in (best_metrics and best_metrics.get("trades", []) or [])
                if t.get("pnl") is not None
            ]
        )
        if best_metrics
        else 0
    )
    print(
        f"  HybridMM: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"best score={best_score:.4f} ret={best_metrics.get('total_return', 0) * 100:+.2f}% "
        f"sharpe={best_metrics.get('sharpe_ratio', 0):.2f} DD={best_metrics.get('max_drawdown', 0) * 100:.1f}% "
        f"trades={n_trades}"
    )
    return best_params, best_score, best_metrics


def search_adaptive(df, time_budget=120):
    """AdaptiveHybridStrategy 参数搜索"""
    evaluator = StrategyEvaluator()
    n = len(df)
    val_start = int(n * 0.85)
    val_df = df.iloc[val_start:].reset_index(drop=True)

    grid = {
        "rsi_period": [14],
        "rsi_low": [25, 30, 35],
        "rsi_high": [65, 70, 75],
        "ma_period": [10, 20],
        "trend_long_ma": [50, 100, 200],
        "trend_pull_ma": [10, 15, 20],
        "adx_threshold": [20, 25, 30],
        "adx_period": [14],
        "atr_period": [7, 14],
        "atr_multiplier": [1.5, 2.0, 2.5],
        "max_hold_bars": [12, 18, 24, 36],
    }

    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    best_score = -1
    best_params = None
    best_metrics = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > time_budget:
            break

        params = dict(zip(grid.keys(), combo))
        strategy = AdaptiveHybridStrategy(
            rsi_period=params["rsi_period"],
            rsi_low=params["rsi_low"],
            rsi_high=params["rsi_high"],
            ma_period=params["ma_period"],
            trend_long_ma=params["trend_long_ma"],
            trend_pull_ma=params["trend_pull_ma"],
            adx_threshold=params["adx_threshold"],
            adx_period=params["adx_period"],
            atr_period=params["atr_period"],
            atr_multiplier=params["atr_multiplier"],
            max_hold_bars=params["max_hold_bars"],
        )
        score, metrics, trades, _ = evaluate_strategy(strategy, val_df, evaluator)

        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    n_trades = (
        len(
            [
                t
                for t in (best_metrics and best_metrics.get("trades", []) or [])
                if t.get("pnl") is not None
            ]
        )
        if best_metrics
        else 0
    )
    print(
        f"  Adaptive: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"best score={best_score:.4f} ret={best_metrics.get('total_return', 0) * 100:+.2f}% "
        f"sharpe={best_metrics.get('sharpe_ratio', 0):.2f} DD={best_metrics.get('max_drawdown', 0) * 100:.1f}% "
        f"trades={n_trades}"
    )
    return best_params, best_score, best_metrics


def search_scalp(df, time_budget=120):
    """ScalpStrategy 参数搜索"""
    evaluator = StrategyEvaluator()
    n = len(df)
    val_start = int(n * 0.85)
    val_df = df.iloc[val_start:].reset_index(drop=True)

    grid = {
        "window": [8, 10, 12, 15],
        "std_dev": [1.0, 1.2, 1.5, 1.8],
        "take_profit_pct": [0.005, 0.008, 0.012, 0.015],
        "stop_loss_pct": [0.003, 0.004, 0.005, 0.006],
        "max_hold_bars": [6, 8, 12, 18],
        "rsi_period": [7, 10, 14],
    }

    all_combos = list(itertools.product(*grid.values()))
    total = len(all_combos)

    best_score = -1
    best_params = None
    best_metrics = None

    t0 = time.time()
    tried = 0

    for combo in all_combos:
        if time.time() - t0 > time_budget:
            break

        params = dict(zip(grid.keys(), combo))
        strategy = ScalpStrategy(
            window=params["window"],
            std_dev=params["std_dev"],
            take_profit_pct=params["take_profit_pct"],
            stop_loss_pct=params["stop_loss_pct"],
            max_hold_bars=params["max_hold_bars"],
            rsi_period=params["rsi_period"],
        )
        try:
            score, metrics, trades, _ = evaluate_strategy(strategy, val_df, evaluator)
        except Exception:
            score = 0
            metrics = {}
            trades = []

        tried += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    n_trades = (
        len(
            [
                t
                for t in (best_metrics and best_metrics.get("trades", []) or [])
                if t.get("pnl") is not None
            ]
        )
        if best_metrics
        else 0
    )
    print(
        f"  Scalp: {tried}/{total} combos in {time.time() - t0:.0f}s | "
        f"best score={best_score:.4f} ret={best_metrics.get('total_return', 0) * 100:+.2f}% "
        f"sharpe={best_metrics.get('sharpe_ratio', 0):.2f} DD={best_metrics.get('max_drawdown', 0) * 100:.1f}% "
        f"trades={n_trades}"
    )
    return best_params, best_score, best_metrics


# ============================================================================
# 冠军全量验证
# ============================================================================


def full_validation(strategy_cls, params, df, strategy_name, evaluator=None):
    """在完整数据上验证最优参数"""
    if evaluator is None:
        evaluator = StrategyEvaluator()

    strategy = strategy_cls(**params)
    score, metrics, trades, signals = evaluate_strategy(strategy, df, evaluator)

    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n_trades = len(trade_pnls)

    print(f"\n  [{strategy_name}] 全量验证:")
    print(
        f"    评分: {score:.4f} | 收益: {metrics['total_return'] * 100:+.2f}% | "
        f"夏普: {metrics['sharpe_ratio']:.2f} | 回撤: {metrics['max_drawdown'] * 100:.1f}% | "
        f"胜率: {metrics['win_rate'] * 100:.1f}% | 交易: {n_trades}"
    )

    if trade_pnls:
        pnls_arr = np.array([t["pnl"] for t in trade_pnls])
        total_pnl = pnls_arr.sum()
        avg_pnl = pnls_arr.mean()
        max_win = pnls_arr.max()
        max_loss = pnls_arr.min()
        print(
            f"    总PnL: {total_pnl:+.2f} | 平均PnL: {avg_pnl:+.2f} | "
            f"最大盈利: {max_win:+.2f} | 最大亏损: {max_loss:+.2f}"
        )

    # Walk-forward 稳定性
    wf_score, avg_score, stability = walk_forward_eval(
        strategy_cls, params, df, n_windows=3, evaluator=evaluator
    )
    print(f"    WF评分: {wf_score:.4f} | 窗口平均: {avg_score:.4f} | 稳定性: {stability:.2f}")

    return {
        "name": strategy_name,
        "params": params,
        "score": score,
        "metrics": metrics,
        "n_trades": n_trades,
        "wf_score": wf_score,
        "wf_stability": stability,
    }


# ============================================================================
# 主程序
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="ETH 最优策略搜索")
    parser.add_argument("--quick", action="store_true", help="快速模式（缩短时间预算）")
    parser.add_argument("--full", action="store_true", help="全量模式（更长时间预算）")
    args = parser.parse_args()

    if args.quick:
        TIME_PER_STRATEGY = 30
    elif args.full:
        TIME_PER_STRATEGY = 600
    else:
        TIME_PER_STRATEGY = 180  # 默认每策略3分钟

    print("=" * 70)
    print("ETH 60天 5分钟数据 — 最优策略全面搜索")
    print(f"时间预算: {TIME_PER_STRATEGY}s/策略 × 7策略 = {TIME_PER_STRATEGY * 7}s")
    print("=" * 70)

    # 加载数据
    print("\n[1/3] 加载数据...")
    df = load_data()
    print(f"  数据: {len(df)} 根K线, {df['datetime'].min()} → {df['datetime'].max()}")
    print(f"  价格范围: {df['close'].min():.1f} - {df['close'].max():.1f}")
    print(f"  价格变化: {(df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100:+.2f}%")

    # 市场特征分析
    rets = df["close"].pct_change().dropna()
    vol_annual = rets.std() * math.sqrt(288 * 365)
    print(f"  年化波动率: {vol_annual * 100:.1f}%")

    # 计算ADX
    from train_quant import analyze_market_regime

    regime, info = analyze_market_regime(df)
    print(
        f"  市场状态: {regime} (ADX={info['adx']:.1f}, 波动={info['volatility_annualized'] * 100:.1f}%)"
    )

    evaluator = StrategyEvaluator()

    # 策略池
    strategy_configs = [
        ("TrendStrategy", TrendStrategy, search_trend, False),
        ("PureAction", PureActionStrategy, search_pure, False),
        ("Hybrid", HybridStrategy, search_hybrid, False),
        ("TrendFollow", TrendFollowStrategy, search_trendfollow, False),
        ("HybridMM", HybridMeanRevMomentumStrategy, search_hybrid_mm, False),
        ("Adaptive", AdaptiveHybridStrategy, search_adaptive, False),
        ("Scalp", ScalpStrategy, search_scalp, True),
    ]

    # 运行搜索
    print(f"\n[2/3] 并行搜索 {len(strategy_configs)} 个策略...")
    print("-" * 70)

    results = {}
    t_search_start = time.time()

    for name, cls, search_fn, is_scalp in strategy_configs:
        print(f"\n>> {name} (预算 {TIME_PER_STRATEGY}s)...")
        try:
            best_params, best_score, best_metrics = search_fn(df, TIME_PER_STRATEGY)
            if best_params and best_score > 0.01:
                results[name] = {
                    "cls": cls,
                    "params": best_params,
                    "score": best_score,
                    "metrics": best_metrics,
                }
                print(f"   [OK] 有效参数: score={best_score:.4f}")
            else:
                print(f"   [SKIP] 未找到有效参数 (score={best_score:.4f})")
                results[name] = None
        except Exception as e:
            print(f"   [ERR] 搜索异常: {e}")
            import traceback

            traceback.print_exc()
            results[name] = None

    search_time = time.time() - t_search_start
    print(f"\n  搜索耗时: {search_time:.0f}s")

    # 全量验证 + Walk-Forward
    print("\n[3/3] 冠军策略全量验证 + Walk-Forward...")
    print("-" * 70)

    validated = []
    for name, result in results.items():
        if result is None:
            continue
        print(f"\n>> {name} 全量验证...")
        try:
            val = full_validation(result["cls"], result["params"], df, name, evaluator)
            validated.append(val)
        except Exception as e:
            print(f"   [ERR] 验证异常: {e}")

    # 排序：按WF评分优先
    validated.sort(key=lambda x: x["wf_score"], reverse=True)

    # 汇总报告
    print("\n" + "=" * 70)
    print("最终排名 (按 Walk-Forward 稳定性评分)")
    print("=" * 70)
    print(
        f"{'排名':<5} {'策略':<15} {'评分':>8} {'WF评分':>8} {'收益':>8} {'夏普':>7} {'回撤':>7} {'胜率':>7} {'交易':>6} {'稳定':>6}"
    )
    print("-" * 90)

    for i, v in enumerate(validated):
        m = v["metrics"]
        print(
            f"{i + 1:<5} {v['name']:<15} {v['score']:>8.4f} {v['wf_score']:>8.4f} "
            f"{m['total_return'] * 100:>+7.2f}% {m['sharpe_ratio']:>7.2f} "
            f"{m['max_drawdown'] * 100:>+6.1f}% {m['win_rate'] * 100:>6.1f}% "
            f"{v['n_trades']:>6} {v['wf_stability']:>6.2f}"
        )

    # 保存最优策略
    if validated:
        champion = validated[0]
        print(f"\n{'=' * 70}")
        print(f"🏆 冠军策略: {champion['name']}")
        print(f"{'=' * 70}")
        print(f"参数: {json.dumps(champion['params'], indent=2, default=str)}")
        print(f"全量评分: {champion['score']:.4f} | WF评分: {champion['wf_score']:.4f}")

        # 保存checkpoint
        checkpoint = {
            "strategy": champion["name"].lower(),
            "params": champion["params"],
            "score": champion["score"],
            "wf_score": champion["wf_score"],
            "metrics": champion["metrics"],
            "all_results": [
                {
                    "name": v["name"],
                    "score": v["score"],
                    "wf_score": v["wf_score"],
                    "ret": v["metrics"]["total_return"],
                    "sharpe": v["metrics"]["sharpe_ratio"],
                    "dd": v["metrics"]["max_drawdown"],
                }
                for v in validated
            ],
            "search_time": search_time,
            "timestamp": datetime.now().isoformat(),
        }

        checkpoint_path = os.path.join(PROJECT_DIR, "checkpoints", "eth_optimal.pt")
        torch.save(checkpoint, checkpoint_path)
        print(f"\n最优参数已保存: {checkpoint_path}")

        # 保存可读报告
        report_path = os.path.join(RESULTS_DIR, "eth_optimal_report.json")
        with open(report_path, "w") as f:
            json.dump(
                {
                    "champion": champion["name"],
                    "params": {
                        k: (str(v) if not isinstance(v, (int, float, bool, type(None))) else v)
                        for k, v in champion["params"].items()
                    },
                    "score": champion["score"],
                    "wf_score": champion["wf_score"],
                    "metrics": {
                        k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
                        for k, v in champion["metrics"].items()
                    },
                    "ranking": [
                        {
                            "rank": i + 1,
                            "name": v["name"],
                            "score": v["score"],
                            "wf_score": v["wf_score"],
                        }
                        for i, v in enumerate(validated)
                    ],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"报告已保存: {report_path}")

    print("\n完成!")


if __name__ == "__main__":
    main()
