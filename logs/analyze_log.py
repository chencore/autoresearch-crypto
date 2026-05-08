import re
import csv
import math
import numpy as np


def parse_log(filepath):
    """解析日志文件提取价格和指标数据（支持多行同周期数据）"""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    entries = []
    current = {}
    last_ts = None

    for line in lines:
        line = line.strip()
        ts_match = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if ts_match:
            ts = ts_match.group(1)
            if ts != last_ts and current:
                # Save previous cycle if complete
                if all(
                    k in current
                    for k in ("ts", "price", "bid", "ask", "bb_upper", "bb_mid", "bb_lower")
                ):
                    entries.append(current)
                current = {}
            last_ts = ts
            current["ts"] = ts

        # Match '价格: 2297.10' - using Chinese characters
        price_match = re.search(r"价格:\s+([\d.]+)", line)
        if price_match:
            current["price"] = float(price_match.group(1))

        # Match BB values
        bb_match = re.search(r"上轨=([\d.]+)\s+中轨=([\d.]+)\s+下轨=([\d.]+)", line)
        if bb_match:
            current["bb_upper"] = float(bb_match.group(1))
            current["bb_mid"] = float(bb_match.group(2))
            current["bb_lower"] = float(bb_match.group(3))

        # Match bid/ask
        ba_match = re.search(r"bid=([\d.]+)\s+ask=([\d.]+)", line)
        if ba_match:
            current["bid"] = float(ba_match.group(1))
            current["ask"] = float(ba_match.group(2))

    # Save last cycle
    if current and all(
        k in current for k in ("ts", "price", "bid", "ask", "bb_upper", "bb_mid", "bb_lower")
    ):
        entries.append(current)

    return entries


def backtest_bb_strategy(
    entries, window=None, std_dev=None, tp=0.015, sl=0.005, hold=36, trend_ma=None
):
    """回测BB+MA趋势过滤策略"""
    # Use provided BB values from log, or compute MA trend
    prices = [e["price"] for e in entries]
    bids = [e["bid"] for e in entries]
    asks = [e["ask"] for e in entries]
    bb_lowers = [e["bb_lower"] for e in entries]
    bb_mids = [e["bb_mid"] for e in entries]

    # Compute MA trend if specified
    ma_trend = None
    if trend_ma:
        import pandas as pd

        ma_trend = pd.Series(prices).rolling(window=trend_ma, min_periods=trend_ma).mean().tolist()

    COMMISSION = 0.0002
    SLIPPAGE = 0.0002
    INITIAL = 10000.0

    capital = INITIAL
    shares = 0.0
    position = 0
    equity = []
    trade_log = []
    entry_price = 0.0
    entry_bar = 0

    start = max(trend_ma, 10) if trend_ma else 10

    for i in range(start, len(entries)):
        price = prices[i]
        bid = bids[i]

        if position == 1:
            hold_bars = i - entry_bar
            ret = price / entry_price - 1

            if ret >= tp or ret <= -sl or hold_bars >= hold:
                # Close position
                exec_price = bid * (1 - SLIPPAGE)
                gross = shares * exec_price
                cost = gross * COMMISSION
                capital = gross - cost
                position = 0
                trade_log.append(
                    {
                        "entry": entry_price,
                        "exit": price,
                        "ret": ret,
                        "hold": hold_bars,
                        "reason": "TP" if ret >= tp else ("SL" if ret <= -sl else "TIME"),
                    }
                )

        if position == 0:
            # Check entry: price <= BB lower AND price > MA trend
            if price > bb_lowers[i]:
                pass  # Not below lower band
            elif ma_trend and price < ma_trend[i]:
                pass  # Below trend MA
            else:
                # Enter long
                exec_price = asks[i] * (1 + SLIPPAGE)
                shares = capital * (1 - COMMISSION) / exec_price
                capital = 0.0
                position = 1
                entry_price = price
                entry_bar = i

        # Track equity
        if position == 1:
            eq = shares * price
        else:
            eq = capital
        equity.append(eq)

    # Close final position
    if position == 1:
        exec_price = bids[-1] * (1 - SLIPPAGE)
        gross = shares * exec_price
        cost = gross * COMMISSION
        capital = gross - cost
        equity[-1] = capital
        ret = prices[-1] / entry_price - 1
        trade_log.append(
            {
                "entry": entry_price,
                "exit": prices[-1],
                "ret": ret,
                "hold": len(entries) - entry_bar,
                "reason": "FINAL",
            }
        )

    equity = np.array(equity)
    if len(equity) > 0 and equity[0] > 0:
        total_ret = equity[-1] / equity[0] - 1
    else:
        total_ret = 0

    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else [0]
    years = max(0.01, len(equity) * 5 / (288 * 365))
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else 0
    ann_vol = np.std(returns) * math.sqrt(288 * 365) if len(returns) > 0 else 0
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    peak = equity[0] if len(equity) > 0 else INITIAL
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = (e - peak) / peak
        if dd < max_dd:
            max_dd = dd

    wins = sum(1 for t in trade_log if t["ret"] > 0)
    losses = sum(1 for t in trade_log if t["ret"] <= 0)
    win_rate = wins / len(trade_log) * 100 if trade_log else 0
    avg_win = np.mean([t["ret"] for t in trade_log if t["ret"] > 0]) * 100 if wins > 0 else 0
    avg_loss = np.mean([t["ret"] for t in trade_log if t["ret"] <= 0]) * 100 if losses > 0 else 0

    return {
        "equity": equity,
        "total_ret": total_ret,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "trades": trade_log,
        "n_trades": len(trade_log),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "final": equity[-1] if len(equity) > 0 else INITIAL,
    }


def main():
    log_file = "live_nado_log_20260502_012536.txt"
    entries = parse_log(log_file)

    print(f"Extracted {len(entries)} data points from log")
    if not entries:
        print("No data found!")
        return

    prices = [e["price"] for e in entries]
    print(f"Time range: {entries[0]['ts']} ~ {entries[-1]['ts']}")
    print(
        f"Price: {prices[0]:.2f} -> {prices[-1]:.2f} ({(prices[-1] / prices[0] - 1) * 100:+.2f}%)"
    )
    print(f"Price range: {min(prices):.2f} ~ {max(prices):.2f}")
    print()

    # Save raw data
    with open("log_prices.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ts", "price", "bid", "ask", "bb_upper", "bb_mid", "bb_lower"]
        )
        writer.writeheader()
        writer.writerows(entries)
    print("Raw data saved to log_prices.csv")
    print()

    # Strategy 1: Original log strategy (HybridMM) - just hold cash
    print("=" * 60)
    print("Strategy 1: Original HybridMM (as-run, no trades)")
    print("=" * 60)
    print("Result: 0 trades, cash held, PnL = 0%")
    print()

    # Strategy 2: Our optimal BB+MA100 strategy
    print("=" * 60)
    print("Strategy 2: BB Mean Reversion + MA100 Trend Filter")
    print("=" * 60)
    print("Params: w=40, std=2.0, tp=1.5%, sl=0.5%, hold=36, trend=100")
    result = backtest_bb_strategy(entries, tp=0.015, sl=0.005, hold=36, trend_ma=100)
    print(f"Total Return: {result['total_ret'] * 100:+.2f}%")
    print(f"Sharpe:       {result['sharpe']:.2f}")
    print(f"Max DD:       {result['max_dd'] * 100:.1f}%")
    print(f"Trades:       {result['n_trades']} (Win: {result['wins']}, Loss: {result['losses']})")
    print(f"Win Rate:     {result['win_rate']:.1f}%")
    if result["wins"] > 0:
        print(f"Avg Win:      {result['avg_win']:.2f}%")
    if result["losses"] > 0:
        print(f"Avg Loss:     {result['avg_loss']:.2f}%")
    print(f"Final Equity: ${result['final']:.2f}")
    print()

    if result["trades"]:
        print("Trade details:")
        for i, t in enumerate(result["trades"]):
            status = "WIN " if t["ret"] > 0 else "LOSS"
            print(
                f"  {i + 1}. {status}: entry=${t['entry']:.2f} exit=${t['exit']:.2f} ret={t['ret'] * 100:+.2f}% hold={t['hold']}x5min reason={t['reason']}"
            )

    # Strategy 3: Try other parameters for this specific period
    print()
    print("=" * 60)
    print("Strategy 3: Parameter sweep for this log period")
    print("=" * 60)

    best = None
    for tp_val in [0.01, 0.015, 0.02, 0.03]:
        for sl_val in [0.003, 0.005, 0.01]:
            for hold_val in [12, 24, 36, 48]:
                for trend_val in [50, 100, 200]:
                    r = backtest_bb_strategy(
                        entries, tp=tp_val, sl=sl_val, hold=hold_val, trend_ma=trend_val
                    )
                    if r["n_trades"] == 0:
                        continue
                    score = (
                        r["sharpe"] * 0.4 + max(0, r["total_ret"]) * 0.3 + (1 + r["max_dd"]) * 0.3
                    )
                    if best is None or score > best["score"]:
                        best = {
                            "score": score,
                            "result": r,
                            "params": {
                                "tp": tp_val,
                                "sl": sl_val,
                                "hold": hold_val,
                                "trend": trend_val,
                            },
                        }

    if best:
        print(f"Best params for this period: {best['params']}")
        print(
            f"Return: {best['result']['total_ret'] * 100:+.2f}%  Sharpe: {best['result']['sharpe']:.2f}  DD: {best['result']['max_dd'] * 100:.1f}%"
        )
        print(f"Trades: {best['result']['n_trades']}  WinRate: {best['result']['win_rate']:.1f}%")


if __name__ == "__main__":
    main()
