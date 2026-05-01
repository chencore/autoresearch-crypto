"""
布林带均值回归策略 Walk-Forward 回测脚本。
下载一段历史数据，逐根 K 线用策略生成信号并模拟交易。

Usage:
    uv run python backtest_quant.py --symbol BTCUSDT --interval 5m --days 7
    uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 14
"""

import os
import sys
import argparse
import math

import numpy as np
import pandas as pd
import torch

from train_quant import StrategyEvaluator, TrendStrategy as BollingerStrategy
from inference_quant import fetch_latest_data
from prepare_crypto import compute_features


class SimpleBacktest:
    """简化版回测引擎（只做多/空仓，支持手续费和滑点）"""

    def __init__(self, initial_capital=10000.0, commission=0.001, slippage=0.0005):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def run(self, df, signals):
        """
        Walk-forward 逐K线模拟交易
        """
        capital = self.initial_capital
        position = 0  # 0=空仓, 1=多头
        shares = 0.0
        equity_curve = []
        trades = []

        closes = df["close"].values
        datetimes = df["datetime"].values

        for i in range(len(signals)):
            signal = signals[i]
            price = closes[i]

            target_pos = position
            if signal == 2:
                target_pos = 1
            elif signal == 0:
                target_pos = 0
            elif signal == 1:
                target_pos = position

            if target_pos != position:
                if target_pos == 1 and position == 0:
                    exec_price = price * (1 + self.slippage)
                    shares = capital * (1 - self.commission) / exec_price
                    cost = capital * self.commission
                    capital = 0.0
                    trades.append({
                        "step": i,
                        "time": datetimes[i],
                        "type": "BUY",
                        "price": price,
                        "exec_price": exec_price,
                        "shares": shares,
                        "cost": cost,
                    })
                    position = 1

                elif target_pos == 0 and position == 1:
                    exec_price = price * (1 - self.slippage)
                    gross = shares * exec_price
                    cost = gross * self.commission
                    capital = gross - cost
                    trades.append({
                        "step": i,
                        "time": datetimes[i],
                        "type": "SELL",
                        "price": price,
                        "exec_price": exec_price,
                        "shares": shares,
                        "cost": cost,
                        "capital_after": capital,
                    })
                    shares = 0.0
                    position = 0

            if position == 1:
                current_equity = shares * price
            else:
                current_equity = capital

            equity_curve.append(current_equity)

        if position == 1:
            exec_price = closes[-1] * (1 - self.slippage)
            gross = shares * exec_price
            cost = gross * self.commission
            capital = gross - cost
            trades.append({
                "step": len(signals) - 1,
                "time": datetimes[-1],
                "type": "SELL (Final)",
                "price": closes[-1],
                "exec_price": exec_price,
                "shares": shares,
                "cost": cost,
                "capital_after": capital,
            })
            equity_curve[-1] = capital
            position = 0
            shares = 0.0

        results_df = pd.DataFrame({
            "datetime": datetimes[:len(signals)],
            "close": closes[:len(signals)],
            "signal": signals,
            "position": [1 if s == 2 else 0 for s in signals],
            "equity": equity_curve,
        })

        metrics = self._compute_metrics(equity_curve)
        return results_df, trades, metrics

    def _compute_metrics(self, equity_curve):
        equity = np.array(equity_curve)
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

        total_trades = len([r for r in returns if abs(r) > 1e-10])
        winning_trades = len([r for r in returns if r > 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_vol": annualized_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "final_equity": equity[-1],
            "initial_equity": equity[0],
        }


def backtest(strategy, df):
    """Walk-forward 回测：逐根 K 线生成信号"""
    signals = strategy.generate_signals(df)
    # 去掉前 window 条无法有效计算布林带的数据
    valid_start = strategy.window
    df_aligned = df.iloc[valid_start:].reset_index(drop=True)
    signals_aligned = signals[valid_start:]
    return signals_aligned, df_aligned


def main():
    parser = argparse.ArgumentParser(description="布林带均值回归 Walk-Forward 回测")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="交易对")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期")
    parser.add_argument("--days", type=int, default=7, help="回测多少天的数据")
    parser.add_argument("--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径")
    parser.add_argument("--output", type=str, default="backtest_result.csv", help="回测结果输出文件")
    args = parser.parse_args()

    # 加载策略参数
    print("=" * 60)
    print("加载策略参数...")
    print("=" * 60)
    if not os.path.exists(args.checkpoint):
        print(f"错误: 未找到 {args.checkpoint}")
        sys.exit(1)

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    params = checkpoint.get("params", {})
    strategy = BollingerStrategy(window=params.get("window", 20), std_dev=params.get("std_dev", 2.0))
    print(f"策略参数: 周期={strategy.window}, 标准差倍数={strategy.std_dev}")
    print()

    # 下载数据
    print("=" * 60)
    print(f"下载 {args.symbol} {args.interval} 最近 {args.days} 天数据...")
    print("=" * 60)
    limit = int(args.days * 24 * 60 / 5)
    df = fetch_latest_data(args.symbol, args.interval, limit=limit)
    print(f"获取到 {len(df)} 根K线")
    print()

    # 执行回测
    print("=" * 60)
    print("执行 Walk-Forward 回测...")
    print("=" * 60)
    signals, df_aligned = backtest(strategy, df)

    # 模拟交易
    engine = SimpleBacktest(initial_capital=10000.0, commission=0.001, slippage=0.0005)
    results_df, trades, metrics = engine.run(df_aligned, signals)

    # 输出结果
    print()
    print("=" * 60)
    print("回测结果")
    print("=" * 60)
    print(f"初始资金:    {metrics['initial_equity']:.2f} USDT")
    print(f"最终资金:    {metrics['final_equity']:.2f} USDT")
    print(f"总收益率:    {metrics['total_return']*100:.2f}%")
    print(f"年化收益率:  {metrics['annualized_return']*100:.2f}%")
    print(f"年化波动率:  {metrics['annualized_vol']*100:.2f}%")
    print(f"夏普比率:    {metrics['sharpe_ratio']:.4f}")
    print(f"最大回撤:    {metrics['max_drawdown']*100:.2f}%")
    print(f"胜率:        {metrics['win_rate']*100:.2f}%")
    print(f"交易次数:    {len(trades)}")
    print()

    if trades:
        print("=" * 60)
        print("交易记录 (最近 10 笔)")
        print("=" * 60)
        for t in trades[-10:]:
            ttype = t["type"]
            ttime = t["time"]
            tprice = t["price"]
            if "capital_after" in t:
                print(f"  [{ttime}] {ttype:12s} @ {tprice:10.2f}  资金: {t['capital_after']:,.2f}")
            else:
                print(f"  [{ttime}] {ttype:12s} @ {tprice:10.2f}")
        print()

    results_df.to_csv(args.output, index=False)
    print(f"详细回测结果已保存至: {args.output}")

    signal_counts = pd.Series(signals).value_counts().sort_index()
    print()
    print("=" * 60)
    print("信号统计")
    print("=" * 60)
    labels = {0: "卖出 (SELL)", 1: "持有 (HOLD)", 2: "买入 (BUY)"}
    for sid, count in signal_counts.items():
        pct = count / len(signals) * 100
        print(f"  {labels.get(sid, '未知'):15s}: {count:5d} 次 ({pct:5.2f}%)")
    print()

    return metrics


if __name__ == "__main__":
    result = main()
