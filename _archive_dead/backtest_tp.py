"""Quick backtest: TP / SL grid search for ScalpStrategy on ETH 5m"""
import pandas as pd
import numpy as np
from train_quant import ScalpStrategy, StrategyEvaluator


def load_data(path="data/crypto/ETHUSDT_5m.parquet"):
    df = pd.read_parquet(path)
    df = df.dropna(subset=["datetime", "close"]).reset_index(drop=True)
    df = df.sort_values("datetime").reset_index(drop=True)
    print(f"Data: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    print(f"Bars: {len(df)}, Price: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    return df


def analyze_exits(signals, prices, tp, sl):
    """Classify each exit as TP/SL/timeout and compute avg PnL"""
    position = 0
    entry_i = 0
    n_tp = n_sl = n_timeout = 0
    tp_pnls = []
    sl_pnls = []
    timeout_pnls = []

    for i in range(len(signals)):
        sig = signals[i]
        price = prices[i]
        if position != 0 and sig == 0:
            pnl = ((price - prices[entry_i]) / prices[entry_i]) if position == 1 \
                else ((prices[entry_i] - price) / prices[entry_i])
            if pnl >= tp * 0.99:
                n_tp += 1; tp_pnls.append(pnl)
            elif pnl <= -sl * 1.01:
                n_sl += 1; sl_pnls.append(pnl)
            else:
                n_timeout += 1; timeout_pnls.append(pnl)
            position = 0
        elif sig in (2, 3) and position == 0:
            position = 1 if sig == 2 else -1
            entry_i = i

    return {
        "n_tp": n_tp, "n_sl": n_sl, "n_timeout": n_timeout,
        "avg_tp": np.mean(tp_pnls) * 100 if tp_pnls else 0,
        "avg_sl": np.mean(sl_pnls) * 100 if sl_pnls else 0,
        "avg_to": np.mean(timeout_pnls) * 100 if timeout_pnls else 0,
    }


def backtest_grid(df, tp_values, sl_values, w=8, sd=1.0, hold=48):
    prices = df["close"].values
    evaluator = StrategyEvaluator(initial_capital=10000.0, commission=0.0002, slippage=0.0002)

    results = []
    for tp in tp_values:
        for sl in sl_values:
            strategy = ScalpStrategy(window=w, std_dev=sd,
                                     take_profit_pct=tp, stop_loss_pct=sl,
                                     max_hold_bars=hold)
            signals = strategy.generate_signals(df, enable_short=True)
            score, metrics, trades = evaluator.evaluate(signals, prices, df)
            exit_info = analyze_exits(signals, prices, tp, sl)
            total = exit_info["n_tp"] + exit_info["n_sl"] + exit_info["n_timeout"]

            results.append({
                "TP": tp, "SL": sl,
                "ret": metrics["total_return"],
                "sharpe": metrics["sharpe_ratio"],
                "wr": metrics["win_rate"],
                "dd": metrics["max_drawdown"],
                "trades": total,
                **exit_info,
            })
    return results


def main():
    df = load_data()
    print()

    tp_values = [0.003, 0.005, 0.008, 0.010, 0.015, 0.020, 0.030]
    sl_values = [0.003, 0.005, 0.008, 0.010, 0.015]

    results = backtest_grid(df, tp_values, sl_values, w=8, sd=1.0, hold=48)

    # Sort by return (best first)
    results.sort(key=lambda r: r["ret"], reverse=True)

    # Print top 20
    hdr = f"{'TP%':>5} {'SL%':>5} | {'Return':>8} {'Sharpe':>7} {'WR':>6} {'DD':>7} | {'Trades':>6} {'TP_ex':>5} {'SL_ex':>5} {'TO_ex':>5} | {'TP_avg':>7} {'SL_avg':>7} {'TO_avg':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in results[:25]:
        print(f"{r['TP']*100:5.1f} {r['SL']*100:5.1f} | "
              f"{r['ret']:>7.2%} {r['sharpe']:>7.2f} {r['wr']:>5.1%} {r['dd']:>6.2%} | "
              f"{r['trades']:>6} {r['n_tp']:>5} {r['n_sl']:>5} {r['n_timeout']:>5} | "
              f"{r['avg_tp']:>+6.3f}% {r['avg_sl']:>+6.3f}% {r['avg_to']:>+6.3f}%")


if __name__ == "__main__":
    main()
