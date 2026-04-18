"""
布林带均值回归模拟实盘（Paper Trading）脚本。
每 5 分钟（或指定 interval）获取最新数据，生成信号并模拟交易。

Usage:
    uv run python paper_trade_quant.py --symbol BTCUSDT --interval 5m
    uv run python paper_trade_quant.py --symbol ETHUSDT --interval 15m --capital 5000
"""

import os
import sys
import time
import json
import math
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import torch

from inference_quant import fetch_latest_data
from train_quant import BollingerStrategy

STATE_FILE = "paper_trade_state.json"
LOG_FILE = "paper_trade_log.txt"


def load_state():
    """加载或初始化交易状态"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    """保存交易状态"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def log_message(msg):
    """打印并记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_interval_seconds(interval):
    """将 K 线周期字符串转为秒数"""
    mapping = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }
    return mapping.get(interval, 300)


def align_next_wake_time(interval_seconds, offset_seconds=10):
    """计算下一次唤醒时间，对齐到 K 线周期的整数倍"""
    now = datetime.now()
    epoch = datetime(1970, 1, 1)
    now_ts = (now - epoch).total_seconds()
    next_boundary = math.ceil(now_ts / interval_seconds) * interval_seconds
    next_wake_ts = next_boundary + offset_seconds
    next_wake = epoch + timedelta(seconds=next_wake_ts)
    return next_wake


def predict_signal(strategy, df):
    """生成交易信号并返回布林带信息"""
    signals = strategy.generate_signals(df)
    signal_id = int(signals[-1])

    close = df["close"].values
    window = strategy.window
    rolling_mean = pd.Series(close).rolling(window=window, min_periods=window).mean()
    rolling_std = pd.Series(close).rolling(window=window, min_periods=window).std()
    upper = rolling_mean + strategy.std_dev * rolling_std
    lower = rolling_mean - strategy.std_dev * rolling_std

    bb_info = {
        "price": close[-1],
        "upper": upper.iloc[-1],
        "mid": rolling_mean.iloc[-1],
        "lower": lower.iloc[-1],
    }
    return signal_id, bb_info


def execute_trade(signal_id, current_price, state, commission=0.001, slippage=0.0005):
    """根据信号执行模拟交易，更新状态"""
    position = state["position"]
    capital = state["capital"]
    shares = state["shares"]
    trades = state.get("trades", [])

    target_pos = position
    if signal_id == 2:
        target_pos = 1
    elif signal_id == 0:
        target_pos = 0
    elif signal_id == 1:
        target_pos = position

    if target_pos != position:
        if target_pos == 1 and position == 0:
            exec_price = current_price * (1 + slippage)
            shares = capital * (1 - commission) / exec_price
            cost = capital * commission
            capital = 0.0
            trades.append({
                "time": datetime.now().isoformat(),
                "type": "BUY",
                "price": current_price,
                "exec_price": exec_price,
                "shares": shares,
                "cost": cost,
            })
            log_message(f"[买入开多] @ {exec_price:.2f} USDT, 数量: {shares:.6f}")
            position = 1

        elif target_pos == 0 and position == 1:
            exec_price = current_price * (1 - slippage)
            gross = shares * exec_price
            cost = gross * commission
            initial = state.get("initial_capital", 10000.0)
            pnl = gross - cost - initial
            capital = gross - cost
            trades.append({
                "time": datetime.now().isoformat(),
                "type": "SELL",
                "price": current_price,
                "exec_price": exec_price,
                "shares": shares,
                "cost": cost,
                "pnl": pnl,
                "capital_after": capital,
            })
            log_message(f"[卖出平仓] @ {exec_price:.2f} USDT, 资金: {capital:.2f} USDT, 盈亏: {pnl:.2f} USDT")
            shares = 0.0
            position = 0

    state["position"] = position
    state["capital"] = capital
    state["shares"] = shares
    state["trades"] = trades
    state["last_signal"] = signal_id
    state["last_price"] = current_price
    state["last_update"] = datetime.now().isoformat()
    return state


def print_status(state, current_price):
    """打印当前账户状态"""
    position = state["position"]
    capital = state["capital"]
    shares = state["shares"]

    if position == 1:
        equity = shares * current_price
    else:
        equity = capital

    initial = state.get("initial_capital", 10000.0)
    ret = (equity / initial) - 1
    signal_names = {0: "卖出 (SELL)", 1: "持有 (HOLD)", 2: "买入 (BUY)"}
    signal_str = signal_names.get(state.get("last_signal", 1), "未知")

    log_message("-" * 50)
    log_message(f"当前信号: {signal_str}")
    log_message(f"当前价格: {current_price:.2f} USDT")
    log_message(f"持仓状态: {'多头' if position == 1 else '空仓'}")
    log_message(f"当前权益: {equity:.2f} USDT")
    log_message(f"累计收益: {ret*100:.2f}%")
    log_message(f"交易次数: {len(state.get('trades', []))}")
    log_message("-" * 50)


def main():
    parser = argparse.ArgumentParser(description="布林带均值回归模拟实盘")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="交易对")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期")
    parser.add_argument("--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径")
    parser.add_argument("--capital", type=float, default=10000.0, help="初始资金")
    parser.add_argument("--once", action="store_true", help="只运行一次然后退出（用于测试）")
    args = parser.parse_args()

    interval_seconds = get_interval_seconds(args.interval)

    # 加载策略参数
    log_message("=" * 50)
    log_message("加载策略参数...")
    if not os.path.exists(args.checkpoint):
        log_message(f"错误: 未找到 {args.checkpoint}")
        sys.exit(1)

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    params = checkpoint.get("params", {})
    strategy = BollingerStrategy(
        window=params.get("window", 20),
        std_dev=params.get("std_dev", 2.0),
        atr_multiplier=params.get("atr_multiplier", 2.5),
        max_hold_bars=params.get("max_hold_bars", 48),
        adx_threshold=params.get("adx_threshold", 25)
    )
    log_message(f"策略参数: 周期={strategy.window}, 标准差倍数={strategy.std_dev}, ATR止损={strategy.atr_multiplier}, 最大持仓={strategy.max_hold_bars}, ADX阈值={strategy.adx_threshold}")

    # 加载或初始化状态
    state = load_state()
    if state is None or state.get("symbol") != args.symbol or state.get("interval") != args.interval:
        # 交易对或周期不匹配，重置状态
        if state is not None and state.get("symbol") != args.symbol:
            log_message(f"交易对已更换 ({state.get('symbol')} -> {args.symbol})，重置状态")
        state = {
            "symbol": args.symbol,
            "interval": args.interval,
            "initial_capital": args.capital,
            "capital": args.capital,
            "shares": 0.0,
            "position": 0,
            "trades": [],
            "last_signal": 1,
            "last_price": 0.0,
        }
        log_message(f"初始化账户: {args.capital:.2f} USDT")
    else:
        pos_val = state.get("shares", 0) * state.get("last_price", 0)
        cash = state.get("capital", 0)
        log_message(f"恢复账户状态: 权益 {cash + pos_val:.2f} USDT")

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
                df = fetch_latest_data(args.symbol, args.interval, limit=500)
                signal_id, bb_info = predict_signal(strategy, df)
                current_price = bb_info["price"]
                current_time = df.iloc[-1]["datetime"]

                log_message(f"K线时间: {current_time} | 价格: {current_price:.2f}")
                log_message(f"布林带: 上轨={bb_info['upper']:.2f} 中轨={bb_info['mid']:.2f} 下轨={bb_info['lower']:.2f}")

                state = execute_trade(signal_id, current_price, state)
                print_status(state, current_price)
                save_state(state)

            except Exception as e:
                log_message(f"推理或交易出错: {e}")

            if args.once:
                log_message("--once 模式，运行一次后退出")
                break

            next_wake = align_next_wake_time(interval_seconds, offset_seconds=10)
            sleep_seconds = (next_wake - datetime.now()).total_seconds()
            if sleep_seconds > 0:
                log_message(f"下次运行时间: {next_wake.strftime('%H:%M:%S')}，休眠 {sleep_seconds:.0f} 秒")
                time.sleep(sleep_seconds)
            else:
                log_message("处理耗时较长，立即进入下一轮")

    except KeyboardInterrupt:
        log_message("收到中断信号，保存状态并退出...")
        save_state(state)
        sys.exit(0)


if __name__ == "__main__":
    main()
