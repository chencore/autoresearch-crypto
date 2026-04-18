"""
OKX Agent Trade Kit 实盘/模拟盘交易脚本。
基于布林带均值回归策略，每 5 分钟获取信号并自动下单。

Usage:
    # Demo Trading（模拟盘，推荐先用这个测试）
    uv run python live_okx_quant.py --symbol BTC-USDT --interval 5m --demo --capital 100

    # 实盘交易（真钱！确认策略稳定后再用）
    export OKX_API_KEY="your_api_key"
    export OKX_API_SECRET="your_api_secret"
    export OKX_PASSPHRASE="your_passphrase"
    uv run python live_okx_quant.py --symbol BTC-USDT --interval 5m --live --capital 500

注意：
    - 默认使用 Demo Trading（--demo），不会损失真实资金
    - 切换到实盘前，务必先用 Demo 跑至少 1-2 天验证信号和下单逻辑
    - OKX 现货最小下单金额约为 5-10 USDT（视币种而定）
"""

import os
import sys
import time
import json
import math
import argparse
import functools
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import torch

from okx.Account import AccountAPI
from okx.Trade import TradeAPI
from okx.MarketData import MarketAPI

from train_quant import BollingerStrategy

STATE_FILE = "live_okx_state.json"
LOG_FILE = "live_okx_log.txt"
LOCK_FILE = "live_okx_quant.lock"
MIN_ORDER_USDT = 5.0  # OKX 现货最小下单金额（USDT")

try:
    import msvcrt

    def acquire_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            print("错误: 已有另一个 live_okx_quant 实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd
except ImportError:
    # Unix fallback (just in case)
    import fcntl

    def acquire_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            print("错误: 已有另一个 live_okx_quant 实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def log_message(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def retry_on_exception(max_retries=3, delay=1.0, exceptions=(Exception,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise
                    log_message(f"{func.__name__} 失败 (尝试 {attempt+1}/{max_retries}): {e}，{delay}s 后重试...")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator


def get_interval_seconds(interval):
    mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
    return mapping.get(interval, 300)


def align_next_wake_time(interval_seconds, offset_seconds=10):
    now = datetime.now()
    epoch = datetime(1970, 1, 1)
    now_ts = (now - epoch).total_seconds()
    next_boundary = math.ceil(now_ts / interval_seconds) * interval_seconds
    next_wake_ts = next_boundary + offset_seconds
    return epoch + timedelta(seconds=next_wake_ts)


def get_okx_credentials():
    """从环境变量读取 API 凭证"""
    key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_API_SECRET", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")
    return key, secret, passphrase


def init_okx_api(flag="1"):
    """初始化 OKX API"""
    key, secret, passphrase = get_okx_credentials()
    if not all([key, secret, passphrase]):
        log_message("错误: 未设置 OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE 环境变量")
        sys.exit(1)

    proxy = os.environ.get("OKX_HTTP_PROXY", "http://127.0.0.1:50830")
    account_api = AccountAPI(key, secret, passphrase, flag=flag, debug=False, proxy=proxy)
    trade_api = TradeAPI(key, secret, passphrase, flag=flag, debug=False, proxy=proxy)
    market_api = MarketAPI(flag=flag, debug=False, proxy=proxy)
    log_message(f"使用 HTTP 代理: {proxy}")
    return account_api, trade_api, market_api


@retry_on_exception(max_retries=3, delay=1.0)
def get_balance(account_api, ccy="USDT"):
    """查询指定币种余额"""
    resp = account_api.get_account_balance(ccy=ccy)
    if resp.get("code") == "0" and resp.get("data"):
        details = resp["data"][0].get("details", [])
        for item in details:
            if item.get("ccy") == ccy:
                avail = float(item.get("availBal", 0))
                eq = float(item.get("eq", 0))
                return avail, eq
    return 0.0, 0.0


@retry_on_exception(max_retries=3, delay=1.0)
def fetch_candles(market_api, inst_id, bar="5m", limit=300):
    """获取 OKX K 线数据"""
    resp = market_api.get_candlesticks(instId=inst_id, bar=bar, limit=str(limit))
    if resp.get("code") != "0":
        log_message(f"获取K线失败: {resp}")
        return None

    candles = resp.get("data", [])
    if not candles:
        return None

    # OKX 返回顺序是最新的在前，需要反转
    candles = list(reversed(candles))

    records = []
    for c in candles:
        records.append({
            "timestamp": int(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
            "quote_volume": float(c[6]),
            "datetime": pd.to_datetime(int(c[0]), unit="ms"),
        })

    df = pd.DataFrame(records)
    return df


def predict_signal(strategy, df):
    """生成布林带信号"""
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


@retry_on_exception(max_retries=3, delay=1.0)
def place_market_order(trade_api, inst_id, side, sz, tgt_ccy="quote_ccy"):
    """
    下市价单。
    side: buy / sell
    sz: 数量
    tgt_ccy: quote_ccy 表示按计价货币(USDT)金额买入；base_ccy 表示按基础货币(BTC)数量卖出
    """
    if side == "buy":
        resp = trade_api.place_order(
            instId=inst_id,
            tdMode="cash",
            side="buy",
            ordType="market",
            sz=str(sz),
            tgtCcy=tgt_ccy,
        )
    else:
        resp = trade_api.place_order(
            instId=inst_id,
            tdMode="cash",
            side="sell",
            ordType="market",
            sz=str(sz),
        )

    if resp.get("code") == "0":
        order_id = resp["data"][0].get("ordId")
        log_message(f"下单成功 [{side.upper()}] 订单ID: {order_id}")
        return order_id
    else:
        log_message(f"下单失败: {resp}")
        return None


def check_stop_loss(state, current_price, stop_loss_pct=0.03, max_hold_bars=48):
    """检查止损和时间退出条件"""
    if state.get("position", 0) != 1:
        return False, ""

    entry_price = state.get("entry_price", 0)
    entry_bar = state.get("entry_bar", 0)
    current_bar = state.get("bar_count", 0)

    # 固定百分比止损
    if entry_price > 0 and (entry_price - current_price) / entry_price >= stop_loss_pct:
        return True, f"止损触发: 跌幅 {(entry_price - current_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%"

    # 时间退出
    if current_bar - entry_bar >= max_hold_bars:
        return True, f"时间退出: 持仓 {current_bar - entry_bar} 根K线 >= {max_hold_bars}"

    return False, ""


def execute_trade(signal_id, trade_api, account_api, inst_id, capital_per_trade, state, current_price):
    """根据信号执行真实交易，策略仓位与账户总仓位隔离"""
    position = state.get("position", 0)  # 0=空仓, 1=多头
    strategy_btc = state.get("strategy_btc", 0.0)
    trades = state.get("trades", [])

    # 先检查止损/时间退出（强制平仓）
    should_exit, exit_reason = check_stop_loss(state, current_price)
    if should_exit:
        sell_btc = strategy_btc
        if sell_btc > 0:
            avail_btc, _ = get_balance(account_api, inst_id.split("-")[0])
            sell_btc = min(sell_btc, avail_btc)
            if sell_btc > 0:
                order_id = place_market_order(trade_api, inst_id, "sell", f"{sell_btc:.8f}", tgt_ccy="base_ccy")
                if order_id:
                    trades.append({
                        "time": datetime.now().isoformat(),
                        "type": "SELL",
                        "instId": inst_id,
                        "orderId": order_id,
                        "sz": sell_btc,
                        "reason": exit_reason,
                    })
                    state["position"] = 0
                    state["strategy_btc"] = 0.0
                    log_message(f"[强制平仓] {exit_reason}，卖出 {sell_btc:.8f} {inst_id.split('-')[0]}")
        state["trades"] = trades
        state["last_update"] = datetime.now().isoformat()
        # 止损后本轮不再开新仓
        return state

    # 目标仓位
    target_pos = position
    if signal_id == 2:
        target_pos = 1
    elif signal_id == 0:
        target_pos = 0
    elif signal_id == 1:
        target_pos = position

    if target_pos == position:
        return state

    if target_pos == 1 and position == 0:
        # 买入
        avail_usdt, _ = get_balance(account_api, "USDT")
        order_sz = min(capital_per_trade, avail_usdt)
        if order_sz < MIN_ORDER_USDT:
            log_message(f"[跳过买入] 可用 USDT 不足最小下单额: {avail_usdt:.2f} < {MIN_ORDER_USDT}")
            return state

        order_id = place_market_order(trade_api, inst_id, "buy", f"{order_sz:.2f}", tgt_ccy="quote_ccy")
        if order_id:
            estimated_btc = order_sz / current_price * 0.9985
            strategy_btc += estimated_btc
            trades.append({
                "time": datetime.now().isoformat(),
                "type": "BUY",
                "instId": inst_id,
                "orderId": order_id,
                "sz": order_sz,
                "estimated_btc": estimated_btc,
            })
            state["position"] = 1
            state["strategy_btc"] = strategy_btc
            state["entry_price"] = current_price
            state["entry_bar"] = state.get("bar_count", 0)
            log_message(f"[买入开多] 使用 {order_sz:.2f} USDT 市价买入 {inst_id}，估算 BTC: {estimated_btc:.8f}")

    elif target_pos == 0 and position == 1:
        # 卖出平仓 — 只卖策略自己持有的 BTC
        sell_btc = strategy_btc
        if sell_btc <= 0:
            log_message(f"[跳过卖出] 策略无可售仓位")
            return state

        avail_btc, _ = get_balance(account_api, inst_id.split("-")[0])
        sell_btc = min(sell_btc, avail_btc)
        if sell_btc <= 0:
            log_message(f"[跳过卖出] 账户可用 {inst_id.split('-')[0]} 为零")
            return state

        order_id = place_market_order(trade_api, inst_id, "sell", f"{sell_btc:.8f}", tgt_ccy="base_ccy")
        if order_id:
            trades.append({
                "time": datetime.now().isoformat(),
                "type": "SELL",
                "instId": inst_id,
                "orderId": order_id,
                "sz": sell_btc,
            })
            state["position"] = 0
            state["strategy_btc"] = 0.0
            log_message(f"[卖出平仓] 市价卖出 {sell_btc:.8f} {inst_id.split('-')[0]}")

    state["trades"] = trades
    state["last_signal"] = signal_id
    state["last_update"] = datetime.now().isoformat()
    return state


def print_status(account_api, inst_id, state):
    """打印账户状态"""
    ccy = inst_id.split("-")[0]
    avail_usdt, eq_usdt = get_balance(account_api, "USDT")
    avail_btc, eq_btc = get_balance(account_api, ccy)

    # 简单估算总权益
    total_equity = eq_usdt + eq_btc * state.get("last_price", 0)
    initial = state.get("initial_equity", total_equity)
    ret = (total_equity / initial) - 1 if initial > 0 else 0

    signal_names = {0: "卖出 (SELL)", 1: "持有 (HOLD)", 2: "买入 (BUY)"}
    signal_str = signal_names.get(state.get("last_signal", 1), "未知")

    log_message("-" * 50)
    log_message(f"当前信号: {signal_str}")
    log_message(f"持仓状态: {'多头' if state.get('position', 0) == 1 else '空仓'}")
    log_message(f"USDT 余额: {eq_usdt:.2f} (可用: {avail_usdt:.2f})")
    log_message(f"{ccy} 余额: {eq_btc:.6f} (可用: {avail_btc:.6f})")
    log_message(f"预估权益: {total_equity:.2f} USDT")
    log_message(f"累计收益: {ret*100:.2f}%")
    log_message(f"交易次数: {len(state.get('trades', []))}")
    log_message("-" * 50)


def main():
    lock_fd = acquire_lock()
    parser = argparse.ArgumentParser(description="OKX Agent Trade Kit 布林带实盘交易")
    parser.add_argument("--symbol", type=str, default="BTC-USDT", help="交易对，如 BTC-USDT, ETH-USDT")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1H, 4H, 1D")
    parser.add_argument("--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径")
    parser.add_argument("--capital", type=float, default=100.0, help="每次交易金额（USDT）")
    parser.add_argument("--demo", action="store_true", help="Demo Trading 模拟盘（默认）")
    parser.add_argument("--live", action="store_true", help="实盘交易（真钱！）")
    parser.add_argument("--once", action="store_true", help="只运行一次然后退出")
    args = parser.parse_args()

    if args.live:
        flag = "0"
        mode_name = "实盘交易"
    else:
        flag = "1"
        mode_name = "Demo Trading 模拟盘"

    interval_seconds = get_interval_seconds(args.interval)

    # 加载策略参数
    log_message("=" * 50)
    log_message(f"启动 {mode_name}")
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
        adx_threshold=params.get("adx_threshold", 25),
    )
    log_message(f"策略参数: 周期={strategy.window}, 标准差倍数={strategy.std_dev}, ATR止损={strategy.atr_multiplier}, 最大持仓={strategy.max_hold_bars}根K线, ADX阈值={strategy.adx_threshold}")

    # 初始化 OKX API
    account_api, trade_api, market_api = init_okx_api(flag=flag)
    log_message("OKX API 连接成功")

    # 加载或初始化状态
    state = load_state()
    if state is None:
        # 首次运行：用当前账户总权益作为本金基准
        ccy = args.symbol.split("-")[0]
        avail_usdt, eq_usdt = get_balance(account_api, "USDT")
        avail_btc, eq_btc = get_balance(account_api, ccy)
        # 用当前市价估算总权益（先获取一次价格）
        ticker_resp = market_api.get_ticker(instId=args.symbol)
        last_px = float(ticker_resp["data"][0]["last"]) if ticker_resp.get("code") == "0" else 0.0
        initial_equity = eq_usdt + eq_btc * last_px

        state = {
            "symbol": args.symbol,
            "interval": args.interval,
            "initial_capital": args.capital,
            "initial_equity": initial_equity,
            "position": 0,
            "strategy_btc": 0.0,
            "trades": [],
            "last_signal": 1,
            "last_price": last_px,
            "bar_count": 0,
            "entry_price": 0.0,
            "entry_bar": 0,
        }
        log_message(f"初始化账户，单次下单金额: {args.capital:.2f} USDT，初始权益: {initial_equity:.2f} USDT")
    else:
        log_message("恢复上一次交易状态")
        if "initial_equity" not in state:
            # 兼容旧状态文件：补录初始权益
            ccy = args.symbol.split("-")[0]
            avail_usdt, eq_usdt = get_balance(account_api, "USDT")
            avail_btc, eq_btc = get_balance(account_api, ccy)
            ticker_resp = market_api.get_ticker(instId=args.symbol)
            last_px = float(ticker_resp["data"][0]["last"]) if ticker_resp.get("code") == "0" else state.get("last_price", 0)
            initial_equity = eq_usdt + eq_btc * last_px
            state["initial_equity"] = initial_equity
            log_message(f"补录初始权益基准: {initial_equity:.2f} USDT")
        if "strategy_btc" not in state:
            state["strategy_btc"] = 0.0
        if "bar_count" not in state:
            state["bar_count"] = 0
        if "entry_price" not in state:
            state["entry_price"] = 0.0
        if "entry_bar" not in state:
            state["entry_bar"] = 0

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
                df = fetch_candles(market_api, args.symbol, bar=args.interval, limit=500)
                if df is None or len(df) < strategy.window + 10:
                    log_message("数据不足，跳过本轮")
                else:
                    signal_id, bb_info = predict_signal(strategy, df)
                    current_price = bb_info["price"]
                    current_time = df.iloc[-1]["datetime"]
                    state["last_price"] = current_price

                    log_message(f"K线时间: {current_time} | 价格: {current_price:.2f}")
                    log_message(f"布林带: 上轨={bb_info['upper']:.2f} 中轨={bb_info['mid']:.2f} 下轨={bb_info['lower']:.2f}")

                    state["bar_count"] = state.get("bar_count", 0) + 1
                    state = execute_trade(signal_id, trade_api, account_api, args.symbol, args.capital, state, current_price)
                    print_status(account_api, args.symbol, state)
                    save_state(state)

            except Exception as e:
                log_message(f"本轮执行异常: {e}")

            if args.once:
                log_message("--once 模式，运行一次后退出")
                break

            next_wake = align_next_wake_time(interval_seconds, offset_seconds=15)
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
