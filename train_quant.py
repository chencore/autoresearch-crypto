"""
加密货币量化策略训练脚本。
基于 autoresearch 架构，使用布林带均值回归策略，并在时间预算内自动搜索最优参数。

Usage:
    uv run python train_quant.py
"""

import os
import math
import time
import json

import numpy as np
import pandas as pd
import torch

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "crypto")
TOKENIZER_DIR = os.path.join(PROJECT_DIR, "tokenizer")

TIME_BUDGET = 300       # 训练/搜索时间预算（秒）
INITIAL_CAPITAL = 10000.0
COMMISSION = 0.001       # 0.1% 手续费
SLIPPAGE = 0.0005       # 0.05% 滑点

# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def list_crypto_files():
    """列出所有加密货币数据文件"""
    if not os.path.exists(DATA_DIR):
        return []
    return [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.endswith(".parquet")]


def load_crypto_data(filepath):
    """加载单个 Parquet 文件"""
    import pyarrow.parquet as pq
    table = pq.read_table(filepath)
    return table.to_pandas()


# ---------------------------------------------------------------------------
# 策略：布林带均值回归
# ---------------------------------------------------------------------------

class BollingerStrategy:
    """
    稳健型布林带策略（只做多/空仓）
    核心逻辑：
    1. 价格触及下轨买入，触及上轨卖出（均值回归）
    2. ATR 追踪止损：根据市场波动率动态调整止损
    3. 趋势过滤：ADX 判断市场是否有趋势，避免在强趋势中抄底
    4. 时间退出：持仓过久不盈利则平仓
    """

    def __init__(self, window=20, std_dev=2.0,
                 atr_period=14, atr_multiplier=2.5,
                 max_hold_bars=48, adx_threshold=25):
        self.window = window
        self.std_dev = std_dev
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.adx_threshold = adx_threshold  # ADX > 此值表示强趋势，不买入

    def _compute_atr(self, df, period):
        """计算 ATR"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]

        atr = np.zeros(len(tr))
        atr[period-1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        return atr

    def _compute_adx(self, df, period=14):
        """计算 ADX"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        plus_dm = np.zeros(len(high))
        minus_dm = np.zeros(len(high))

        for i in range(1, len(high)):
            up = high[i] - high[i-1]
            down = low[i-1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0

        atr = self._compute_atr(df, period)

        plus_di = np.zeros(len(high))
        minus_di = np.zeros(len(high))
        for i in range(period, len(high)):
            if atr[i] > 0:
                plus_di[i] = 100 * np.mean(plus_dm[i-period+1:i+1]) / atr[i]
                minus_di[i] = 100 * np.mean(minus_dm[i-period+1:i+1]) / atr[i]

        dx = np.zeros(len(high))
        for i in range(period, len(high)):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * np.abs(plus_di[i] - minus_di[i]) / di_sum

        adx = np.zeros(len(high))
        adx[period*2-1] = np.mean(dx[period:period*2])
        for i in range(period * 2, len(high)):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

        return adx

    def generate_signals(self, df):
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        n = len(close)

        rolling_mean = pd.Series(close).rolling(window=self.window, min_periods=self.window).mean()
        rolling_std = pd.Series(close).rolling(window=self.window, min_periods=self.window).std()
        upper = rolling_mean + self.std_dev * rolling_std
        lower = rolling_mean - self.std_dev * rolling_std

        atr = self._compute_atr(df, self.atr_period)
        adx = self._compute_adx(df, self.atr_period)

        signals = np.ones(n, dtype=int)
        position = 0
        entry_price = 0.0
        entry_bar = 0
        highest_after_entry = 0.0

        for i in range(self.window, n):
            price = close[i]

            # 强趋势判断：ADX > threshold 表示趋势强劲，均值回归策略应避免买入
            is_strong_trend = adx[i] > self.adx_threshold if i >= self.atr_period * 2 else False

            if position == 1:
                # 更新持仓期间的最高价（用于追踪止损）
                if high[i] > highest_after_entry:
                    highest_after_entry = high[i]

                # 1. 触及上轨 → 止盈
                if price >= upper.iloc[i]:
                    signals[i] = 0
                    position = 0
                    continue

                # 2. ATR 追踪止损（从最高点回落 atr_multiplier * ATR）
                if highest_after_entry > 0:
                    atr_stop = highest_after_entry - self.atr_multiplier * atr[i]
                    if price < atr_stop:
                        signals[i] = 0
                        position = 0
                        continue

                # 3. 时间退出（最多持仓 max_hold_bars 根K线）
                if i - entry_bar >= self.max_hold_bars:
                    signals[i] = 0
                    position = 0
                    continue

                signals[i] = 2
                continue

            if position == 0:
                # 只在以下情况买入：
                # 1. 价格触及下轨（均值回归）
                # 2. 不是强趋势市场
                if price <= lower.iloc[i] and not is_strong_trend:
                    signals[i] = 1
                    position = 1
                    entry_price = price
                    entry_bar = i
                    highest_after_entry = high[i]
                    continue

                # 其他情况保持观望
                signals[i] = 1  # 观望/持有

        return signals


# ---------------------------------------------------------------------------
# 评估器
# ---------------------------------------------------------------------------

class StrategyEvaluator:
    """策略绩效评估器"""

    def __init__(self, initial_capital=INITIAL_CAPITAL, commission=COMMISSION, slippage=SLIPPAGE):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def simulate(self, signals, prices, df=None):
        """
        模拟交易（只做多/空仓，不做空）
        支持 ATR 止损和时间退出
        """
        capital = self.initial_capital
        shares = 0.0
        position = 0
        equity = []
        trades = []
        entry_cost_basis = 0.0
        entry_price = 0.0
        entry_step = 0

        for i in range(len(signals)):
            signal = signals[i]
            price = prices[i]

            if signal == 2:
                target_pos = 1
            elif signal == 0:
                target_pos = 0
            else:
                target_pos = position

            if target_pos != position:
                if target_pos == 1 and position == 0:
                    exec_price = price * (1 + self.slippage)
                    shares = capital * (1 - self.commission) / exec_price
                    entry_cost_basis = capital
                    entry_price = exec_price
                    entry_step = i
                    capital = 0.0
                    trades.append({"type": "buy", "step": i})
                    position = 1
                elif target_pos == 0 and position == 1:
                    exec_price = price * (1 - self.slippage)
                    gross = shares * exec_price
                    cost = gross * self.commission
                    capital = gross - cost
                    pnl = capital - entry_cost_basis
                    trades.append({"type": "sell", "step": i, "pnl": float(pnl)})
                    shares = 0.0
                    position = 0

            current_equity = capital + shares * price
            equity.append(current_equity)

        if position == 1:
            exec_price = prices[-1] * (1 - self.slippage)
            gross = shares * exec_price
            cost = gross * self.commission
            capital = gross - cost
            pnl = capital - entry_cost_basis
            trades.append({"type": "sell_final", "step": len(signals) - 1, "pnl": float(pnl)})
            equity[-1] = capital
            position = 0
            shares = 0.0

        return np.array(equity), trades

    def compute_metrics(self, equity_curve, trades):
        """计算绩效指标"""
        equity = equity_curve
        returns = np.diff(equity) / equity[:-1]

        total_return = (equity[-1] / equity[0]) - 1

        n_steps = len(equity)
        years = n_steps * 5 / (288 * 365)
        if years < 0.01:
            years = 0.01
        annualized_return = (1 + total_return) ** (1 / years) - 1
        annualized_return = max(-10.0, min(10.0, annualized_return))

        annualized_vol = np.std(returns) * math.sqrt(288 * 365) if len(returns) > 0 else 0
        sharpe = annualized_return / annualized_vol if annualized_vol > 0 else 0

        peak = equity[0]
        max_drawdown = 0
        for e in equity:
            if e > peak:
                peak = e
            dd = (e - peak) / peak
            if dd < max_drawdown:
                max_drawdown = dd

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        total_trades = len(trade_pnls)
        winning_trades = len([t for t in trade_pnls if t["pnl"] > 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.5

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_vol": annualized_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
        }

    def evaluate(self, signals, prices, df=None):
        """评估一组信号"""
        equity, trades = self.simulate(signals, prices, df)
        metrics = self.compute_metrics(equity, trades)

        # 惩罚大回撤：如果最大回撤超过 20%，大幅降低评分
        dd_penalty = max(0, 1 - abs(metrics["max_drawdown"]) / 0.20) if metrics["max_drawdown"] < 0 else 1.0

        score = (
            max(0, metrics["sharpe_ratio"]) * 0.35 +
            max(0, metrics["total_return"]) * 0.25 +
            metrics["win_rate"] * 0.10 +
            dd_penalty * (1 + metrics["max_drawdown"]) * 0.20 +
            min(1.0, len(trades) / 20.0) * 0.10
        )

        return score, metrics, trades


# ---------------------------------------------------------------------------
# 参数搜索
# ---------------------------------------------------------------------------

def grid_search(df, time_budget=TIME_BUDGET):
    """
    在时间预算内网格搜索最优布林带参数。
    数据集划分：最后 10% 作为验证集（按时间顺序）。
    """
    n = len(df)
    train_size = int(n * 0.9)
    train_df = df.iloc[:train_size].reset_index(drop=True)
    val_df = df.iloc[train_size:].reset_index(drop=True)

    param_grid = {
        "window": [15, 20, 25, 30],
        "std_dev": [1.8, 2.0, 2.2, 2.5],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "max_hold_bars": [24, 36, 48, 60],
        "adx_threshold": [20, 25, 30],
    }

    evaluator = StrategyEvaluator()
    best_score = -float("inf")
    best_params = None
    best_metrics = None

    print(f"开始参数搜索 (训练集 {len(train_df)} 条, 验证集 {len(val_df)} 条)")
    print(f"时间预算: {time_budget}s")
    print()

    t_start = time.time()
    total_combos = (
        len(param_grid["window"]) *
        len(param_grid["std_dev"]) *
        len(param_grid["atr_multiplier"]) *
        len(param_grid["max_hold_bars"]) *
        len(param_grid["adx_threshold"])
    )
    tried = 0

    for window in param_grid["window"]:
        for std_dev in param_grid["std_dev"]:
            for atr_mult in param_grid["atr_multiplier"]:
                for max_hold in param_grid["max_hold_bars"]:
                    for adx_th in param_grid["adx_threshold"]:
                        if time.time() - t_start > time_budget * 0.9:
                            print("时间预算即将耗尽，提前结束搜索")
                            break

                        strategy = BollingerStrategy(
                            window=window, std_dev=std_dev,
                            atr_multiplier=atr_mult, max_hold_bars=max_hold,
                            adx_threshold=adx_th
                        )
                        signals = strategy.generate_signals(val_df)
                        prices = val_df["close"].values

                        valid_signals = signals[window:]
                        valid_prices = prices[window:]
                        valid_df = val_df.iloc[window:].reset_index(drop=True)

                        if len(valid_signals) < 50:
                            continue

                        score, metrics, trades = evaluator.evaluate(valid_signals, valid_prices, valid_df)

                        tried += 1
                        print(f"[{tried}/{total_combos}] w={window} std={std_dev} atr={atr_mult} hold={max_hold} adx={adx_th} | "
                              f"评分={score:.4f} | 收益={metrics['total_return']*100:.2f}% | 夏普={metrics['sharpe_ratio']:.2f} | DD={metrics['max_drawdown']*100:.1f}% | 交易={len(trades)}")

                        if score > best_score:
                            best_score = score
                            best_params = {
                                "window": window,
                                "std_dev": std_dev,
                                "atr_multiplier": atr_mult,
                                "max_hold_bars": max_hold,
                                "adx_threshold": adx_th,
                            }
                            best_metrics = metrics

    print()
    return best_params, best_score, best_metrics


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    t_start = time.time()

    print("=" * 60)
    print("加密货币量化策略训练 (布林带均值回归)")
    print("=" * 60)

    data_files = list_crypto_files()
    if not data_files:
        print("错误: 未找到数据文件。请先运行 python prepare_crypto.py")
        return

    print(f"找到 {len(data_files)} 个数据文件")
    for f in data_files:
        print(f"  {os.path.basename(f)}")

    print("\n加载数据...")
    dfs = [load_crypto_data(fp) for fp in data_files]
    df = pd.concat(dfs, ignore_index=True)
    df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    print(f"总数据量: {len(df)} 条K线")

    best_params, best_score, best_metrics = grid_search(df, TIME_BUDGET)

    # 保存最优参数
    checkpoint_dir = os.path.join(PROJECT_DIR, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, "quant_model.pt")

    checkpoint = {
        "strategy": "bollinger_atr",
        "params": best_params,
        "score": best_score,
        "metrics": best_metrics,
    }
    torch.save(checkpoint, checkpoint_path)
    print(f"最优参数已保存: {checkpoint_path}")

    print("\n" + "=" * 60)
    print("最优参数与回测结果")
    print("=" * 60)
    if best_params:
        print(f"布林带周期:     {best_params['window']}")
        print(f"标准差倍数:     {best_params['std_dev']}")
        print(f"ATR止损倍数:    {best_params['atr_multiplier']}")
        print(f"最大持仓K线:   {best_params['max_hold_bars']}")
        print(f"ADX阈值:       {best_params['adx_threshold']}")
        print(f"综合评分:       {best_score:.6f}")
        print(f"夏普比率:       {best_metrics['sharpe_ratio']:.4f}")
        print(f"总收益率:       {best_metrics['total_return']*100:.2f}%")
        print(f"年化收益率:     {best_metrics['annualized_return']*100:.2f}%")
        print(f"年化波动率:     {best_metrics['annualized_vol']*100:.2f}%")
        print(f"最大回撤:       {best_metrics['max_drawdown']*100:.2f}%")
        print(f"胜率:           {best_metrics['win_rate']*100:.1f}%")
    else:
        print("未找到有效参数组合")
    print(f"总耗时:         {time.time() - t_start:.1f}s")

    return best_score, best_metrics


if __name__ == "__main__":
    main()
