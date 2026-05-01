"""Detailed backtest: ScalpStrategy TP=1.5% SL=1.5% on ETH 5m"""
import pandas as pd
import numpy as np
from train_quant import ScalpStrategy, StrategyEvaluator


def load_data(path="data/crypto/ETHUSDT_5m.parquet"):
    df = pd.read_parquet(path)
    df = df.dropna(subset=["datetime", "close"]).reset_index(drop=True)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def detailed_backtest(df, tp=0.015, sl=0.015, w=8, sd=1.0, hold=48):
    prices = df["close"].values
    datetimes = df["datetime"].values

    strategy = ScalpStrategy(
        window=w, std_dev=sd,
        take_profit_pct=tp, stop_loss_pct=sl,
        max_hold_bars=hold,
    )
    signals = strategy.generate_signals(df, enable_short=True)

    # Evaluator for overall metrics
    evaluator = StrategyEvaluator(initial_capital=10000.0, commission=0.0002, slippage=0.0002)
    score, metrics, sim_trades = evaluator.evaluate(signals, prices, df)
    equity, _ = evaluator.simulate(signals, prices, df)

    # Detailed trade-by-trade analysis
    position = 0
    entry_i = 0
    entry_price = 0.0
    trade_log = []

    for i in range(len(signals)):
        sig = signals[i]
        price = prices[i]
        dt = datetimes[i]

        if sig in (2, 3) and position == 0:
            position = 1 if sig == 2 else -1
            entry_i = i
            entry_price = price
        elif position != 0 and sig == 0:
            if position == 1:
                pnl_pct = (price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - price) / entry_price * 100

            bars_held = i - entry_i
            if pnl_pct >= tp * 99:
                exit_type = "TP"
            elif pnl_pct <= -sl * 101:
                exit_type = "SL"
            else:
                exit_type = "TIMEOUT"

            direction = "LONG" if position == 1 else "SHORT"
            trade_log.append({
                "dir": direction,
                "entry_dt": str(datetimes[entry_i])[:16],
                "exit_dt": str(dt)[:16],
                "entry": entry_price,
                "exit": price,
                "pnl%": pnl_pct,
                "bars": bars_held,
                "exit_type": exit_type,
            })
            position = 0

    return score, metrics, equity, trade_log


def print_results(score, metrics, equity, trade_log, tp, sl):
    print("=" * 80)
    print(f"  ScalpStrategy Backtest: ETH-USDT 5m")
    print(f"  TP={tp*100:.1f}%  SL={sl*100:.1f}%  w=8  std=1.0  hold=48")
    print("=" * 80)

    # Overall metrics
    print(f"\n--- Overall Metrics ---")
    print(f"  Score:          {score:.4f}")
    print(f"  Total Return:   {metrics['total_return']:.2%}")
    print(f"  Ann. Return:    {metrics['annualized_return']:.2%}")
    print(f"  Ann. Volatility:{metrics['annualized_vol']:.2%}")
    print(f"  Sharpe Ratio:   {metrics['sharpe_ratio']:.2f}")
    print(f"  Max Drawdown:   {metrics['max_drawdown']:.2%}")
    print(f"  Win Rate:       {metrics['win_rate']:.1%}")

    # Trade breakdown
    total = len(trade_log)
    if total == 0:
        print("\n  No trades.")
        return

    tp_trades = [t for t in trade_log if t["exit_type"] == "TP"]
    sl_trades = [t for t in trade_log if t["exit_type"] == "SL"]
    to_trades = [t for t in trade_log if t["exit_type"] == "TIMEOUT"]
    longs = [t for t in trade_log if t["dir"] == "LONG"]
    shorts = [t for t in trade_log if t["dir"] == "SHORT"]

    wins = [t for t in trade_log if t["pnl%"] > 0]
    losses = [t for t in trade_log if t["pnl%"] <= 0]

    print(f"\n--- Trade Breakdown ---")
    print(f"  Total Trades:   {total}")
    print(f"  Win / Loss:     {len(wins)} / {len(losses)}  ({len(wins)/total:.1%} win rate)")
    print(f"  Long / Short:   {len(longs)} / {len(shorts)}")

    print(f"\n  By Exit Type:")
    for name, group in [("TP", tp_trades), ("SL", sl_trades), ("TIMEOUT", to_trades)]:
        if not group:
            print(f"    {name:>8}:   0 trades")
            continue
        avg_pnl = np.mean([t["pnl%"] for t in group])
        total_pnl = sum(t["pnl%"] for t in group)
        avg_bars = np.mean([t["bars"] for t in group])
        print(f"    {name:>8}: {len(group):>4} trades  avg={avg_pnl:>+7.3f}%  sum={total_pnl:>+8.2f}%  avg_bars={avg_bars:.0f}")

    print(f"\n  By Direction:")
    for name, group in [("LONG", longs), ("SHORT", shorts)]:
        if not group:
            continue
        avg_pnl = np.mean([t["pnl%"] for t in group])
        total_pnl = sum(t["pnl%"] for t in group)
        wr = len([t for t in group if t["pnl%"] > 0]) / len(group)
        print(f"    {name:>8}: {len(group):>4} trades  avg={avg_pnl:>+7.3f}%  sum={total_pnl:>+8.2f}%  WR={wr:.1%}")

    # Equity curve stats
    print(f"\n--- Equity Curve ---")
    print(f"  Start:  {equity[0]:.2f}")
    print(f"  End:    {equity[-1]:.2f}")
    print(f"  Peak:   {np.max(equity):.2f}")
    print(f"  Min:    {np.min(equity):.2f}")

    # Monthly breakdown
    print(f"\n--- Monthly Breakdown ---")
    months = {}
    for t in trade_log:
        m = t["entry_dt"][:7]  # YYYY-MM
        months.setdefault(m, {"trades": [], "pnl": 0})
        months[m]["trades"].append(t)
        months[m]["pnl"] += t["pnl%"]

    for m in sorted(months):
        grp = months[m]
        wr = len([t for t in grp["trades"] if t["pnl%"] > 0]) / len(grp["trades"])
        print(f"    {m}: {len(grp['trades']):>4} trades  PnL={grp['pnl']:>+8.2f}%  WR={wr:.1%}")

    # Recent 30 trades
    print(f"\n--- Last 30 Trades ---")
    print(f"  {'#':>3} {'Dir':>5} {'Entry Time':>16} {'Exit Time':>16} {'Entry':>8} {'Exit':>8} {'PnL%':>8} {'Bars':>5} {'Exit':>7}")
    print(f"  {'-'*78}")
    for idx, t in enumerate(trade_log[-30:], 1):
        print(f"  {idx:>3} {t['dir']:>5} {t['entry_dt']:>16} {t['exit_dt']:>16} "
              f"{t['entry']:>8.2f} {t['exit']:>8.2f} {t['pnl%']:>+7.3f}% {t['bars']:>5} {t['exit_type']:>7}")

    # Top 10 best and worst
    print(f"\n--- Top 10 Best Trades ---")
    best = sorted(trade_log, key=lambda t: t["pnl%"], reverse=True)[:10]
    for idx, t in enumerate(best, 1):
        print(f"  {idx:>2}. {t['dir']:>5} {t['entry_dt']:>16} -> {t['exit_dt']:>16}  {t['pnl%']:>+7.3f}%  ({t['exit_type']})")

    print(f"\n--- Top 10 Worst Trades ---")
    worst = sorted(trade_log, key=lambda t: t["pnl%"])[:10]
    for idx, t in enumerate(worst, 1):
        print(f"  {idx:>2}. {t['dir']:>5} {t['entry_dt']:>16} -> {t['exit_dt']:>16}  {t['pnl%']:>+7.3f}%  ({t['exit_type']})")

    # Distribution
    print(f"\n--- PnL Distribution ---")
    pnls = [t["pnl%"] for t in trade_log]
    bins = [(-999, -1.5), (-1.5, -1.0), (-1.0, -0.5), (-0.5, 0), (0, 0.3), (0.3, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 999)]
    for lo, hi in bins:
        cnt = len([p for p in pnls if lo <= p < hi])
        bar = "#" * cnt
        label = f"[{lo:+.1f}%,{hi:+.1f}%)" if hi < 999 else f"[{lo:+.1f}%,+inf)"
        print(f"  {label:>18}: {cnt:>4}  {bar}")


def main():
    df = load_data()
    print(f"Data: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    print(f"Bars: {len(df)}")

    tp = 0.015  # 1.5%
    sl = 0.015  # 1.5%
    score, metrics, equity, trade_log = detailed_backtest(df, tp=tp, sl=sl)
    print_results(score, metrics, equity, trade_log, tp, sl)


if __name__ == "__main__":
    main()
