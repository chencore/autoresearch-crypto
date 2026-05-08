"""
OKX Agent Trade Kit 实盘/模拟盘交易脚本（永续合约 SWAP）。
基于混合费率执行策略，每 5 分钟获取信号并自动下单。

混合费率执行策略:
    - 开仓: POST_ONLY (Maker) -- 挂限价单，享Maker低费率
    - 止盈: POST_ONLY (Maker) -- 自动挂止盈限价单，价格到达即成交
    - 止损: IOC (Taker) -- 必须保证成交，付Taker费率
    - 超时: POST_ONLY (Maker) -- 挂限价单平仓

Usage:
    # Demo Trading（模拟盘，推荐先用这个测试）
    uv run python live_okx_quant.py --symbol BTC-USDT-SWAP --interval 5m --demo --capital 100

    # 实盘交易（真钱！确认策略稳定后再用）
    export OKX_API_KEY="your_api_key"
    export OKX_API_SECRET="your_api_secret"
    export OKX_PASSPHRASE="your_passphrase"
    uv run python live_okx_quant.py --symbol BTC-USDT-SWAP --interval 5m --live --capital 500 --leverage 2

注意：
    - 默认使用 Demo Trading（--demo），不会损失真实资金
    - 切换到实盘前，务必先用 Demo 跑至少 1-2 天验证信号和下单逻辑
    - 本脚本交易 OKX 永续合约（SWAP），支持做多/做空双向
"""

import os
import sys
import time
import json
import math
import argparse
import functools
from datetime import datetime, timedelta
from decimal import Decimal

from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import torch

from okx.Account import AccountAPI
from okx.Trade import TradeAPI
from okx.MarketData import MarketAPI
from okx.PublicData import PublicAPI

from train_quant import (TrendStrategy, ScalpStrategy, HybridMeanRevMomentumStrategy,
                          AdaptiveHybridStrategy, RegimeStrategy)

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
STATE_FILE = os.path.join(LOG_DIR, "live_okx_state.json")
LOG_FILE = os.path.join(LOG_DIR, "live_okx_log.txt")
LOCK_FILE = os.path.join(LOG_DIR, "live_okx_quant.lock")

INTERVAL_SECONDS_MAP = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

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


def align_next_wake_time(interval_seconds, offset_seconds=10):
    now = datetime.now()
    epoch = datetime(1970, 1, 1)
    now_ts = (now - epoch).total_seconds()
    next_boundary = math.ceil(now_ts / interval_seconds) * interval_seconds
    next_wake_ts = next_boundary + offset_seconds
    return epoch + timedelta(seconds=next_wake_ts)


# ---------------------------------------------------------------------------
# OKX API 初始化
# ---------------------------------------------------------------------------

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
    public_api = PublicAPI(debug=False, proxy=proxy)
    log_message(f"使用 HTTP 代理: {proxy}")
    return account_api, trade_api, market_api, public_api


# ---------------------------------------------------------------------------
# OKX API 封装
# ---------------------------------------------------------------------------

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
def get_position(account_api, inst_id):
    """获取永续合约持仓（正=多, 负=空, 0=无）"""
    resp = account_api.get_positions(instType="SWAP", instId=inst_id)
    if resp.get("code") == "0" and resp.get("data"):
        pos = resp["data"][0]
        pos_side = pos.get("posSide", "net")
        pos_val = float(pos.get("pos", "0"))
        if pos_side == "short":
            pos_val = -abs(pos_val)
        return pos_val
    return 0.0


@retry_on_exception(max_retries=3, delay=1.0)
def set_leverage(account_api, inst_id, lever, mgn_mode="cross"):
    """设置合约杠杆"""
    resp = account_api.set_leverage(instId=inst_id, lever=str(lever), mgnMode=mgn_mode)
    if resp.get("code") == "0":
        log_message(f"杠杆设置成功: {inst_id} {mgn_mode} {lever}x")
        return True
    else:
        log_message(f"杠杆设置失败: {resp}")
        return False


@retry_on_exception(max_retries=3, delay=1.0)
def get_instrument_info(public_api, inst_id):
    """获取交易对信息 (tickSz, lotSz, minSz)"""
    resp = public_api.get_instruments(instType="SWAP", instId=inst_id)
    if resp.get("code") == "0" and resp.get("data"):
        info = resp["data"][0]
        return {
            "tickSz": float(info.get("tickSz", "0.01")),
            "lotSz": float(info.get("lotSz", "0.00001")),
            "minSz": float(info.get("minSz", "0.00001")),
            "ctVal": float(info.get("ctVal", "1")),
        }
    return {"tickSz": 0.01, "lotSz": 0.00001, "minSz": 0.00001, "ctVal": 1}


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


@retry_on_exception(max_retries=3, delay=1.0)
def get_orderbook(market_api, inst_id, depth=5):
    """获取订单簿，返回 (best_bid, best_ask) 浮点数"""
    resp = market_api.get_orderbook(instId=inst_id, sz=str(depth))
    if resp.get("code") == "0" and resp.get("data"):
        data = resp["data"][0]
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        best_bid = float(bids[0][0]) if bids else None
        best_ask = float(asks[0][0]) if asks else None
        return best_bid, best_ask
    return None, None


@retry_on_exception(max_retries=3, delay=1.0)
def get_order_status(trade_api, inst_id, order_id):
    """获取订单状态，返回 (state, fill_sz, avg_px)"""
    resp = trade_api.get_order(instId=inst_id, ordId=order_id)
    if resp.get("code") == "0" and resp.get("data"):
        order = resp["data"][0]
        state = order.get("state")
        fill_sz = float(order.get("fillSz", "0"))
        avg_px = float(order.get("avgPx", "0"))
        return state, fill_sz, avg_px
    return None, 0, 0


@retry_on_exception(max_retries=3, delay=1.0)
def get_open_orders(trade_api, inst_id):
    """获取当前挂单列表"""
    resp = trade_api.get_order_list(instId=inst_id, state="live")
    if resp.get("code") == "0":
        return resp.get("data", [])
    return []


def cancel_all_orders(trade_api, inst_id):
    """取消指定交易对的所有挂单"""
    try:
        orders = get_open_orders(trade_api, inst_id)
        for order in orders:
            ord_id = order.get("ordId")
            if ord_id:
                try:
                    trade_api.cancel_order(instId=inst_id, ordId=ord_id)
                except Exception as e:
                    log_message(f"取消订单失败: {ord_id}, {e}")
        if orders:
            log_message(f"已取消 {len(orders)} 个挂单: {inst_id}")
        return True
    except Exception as e:
        log_message(f"取消所有挂单失败: {e}")
        return False


@retry_on_exception(max_retries=3, delay=1.0)
def place_market_order(trade_api, inst_id, side, pos_side, sz, td_mode="cross"):
    """
    下市价单 (Taker)。
    side: buy / sell
    pos_side: long / short
    sz: 数量（张数或币数）
    td_mode: cross(全仓) / isolated(逐仓)
    """
    resp = trade_api.place_order(
        instId=inst_id,
        tdMode=td_mode,
        side=side,
        posSide=pos_side,
        ordType="market",
        sz=str(sz),
    )

    if resp.get("code") == "0":
        order_id = resp["data"][0].get("ordId")
        log_message(f"市价单成功 [{side.upper()} {pos_side}] 订单ID: {order_id}")
        return order_id
    else:
        log_message(f"市价单失败: {resp}")
        return None


@retry_on_exception(max_retries=3, delay=1.0)
def place_limit_order(trade_api, inst_id, side, pos_side, sz, px, td_mode="cross"):
    """下限价单 (Maker)"""
    resp = trade_api.place_order(
        instId=inst_id,
        tdMode=td_mode,
        side=side,
        posSide=pos_side,
        ordType="limit",
        px=str(px),
        sz=str(sz),
    )
    if resp.get("code") == "0":
        order_id = resp["data"][0].get("ordId")
        log_message(f"限价单成功 [{side.upper()} {pos_side}] 订单ID: {order_id} 价格={px} 数量={sz}")
        return order_id
    else:
        log_message(f"限价单失败: {resp}")
        return None


# ---------------------------------------------------------------------------
# 信号生成
# ---------------------------------------------------------------------------

def predict_signal(strategy, df, enable_short=False):
    """生成交易信号"""
    import inspect
    sig = inspect.signature(strategy.generate_signals)
    if 'enable_short' in sig.parameters:
        signals = strategy.generate_signals(df, enable_short=enable_short)
    else:
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


# ---------------------------------------------------------------------------
# 价格/数量精度处理
# ---------------------------------------------------------------------------

def round_to_tick(price, tick_size):
    """将价格对齐到 tick_size，避免浮点精度问题"""
    ticks = round(float(price) / float(tick_size))
    return float(Decimal(ticks) * Decimal(str(tick_size)))


def round_to_size(size, lot_size):
    """将数量对齐到 lot_size"""
    units = int(Decimal(str(size)) / Decimal(str(lot_size)))
    return float(units * Decimal(str(lot_size)))


def compute_order_price(side, best_bid, best_ask, tick_size):
    """计算 POST_ONLY 限价单价格（挂在 best bid/ask 提高成交率），对齐到 tick_size"""
    if side == "buy":
        price = best_bid
    else:
        price = best_ask
    return round_to_tick(price, tick_size) if price else None


def compute_ioc_price(side, best_bid, best_ask, tick_size):
    """计算 IOC 单价格（穿越盘口确保成交），对齐到 tick_size"""
    if side == "buy":
        price = best_ask + float(tick_size) * 2 if best_ask else None
    else:
        price = best_bid - float(tick_size) * 2 if best_bid else None
    if price is not None:
        price = round_to_tick(price, tick_size)
    return price


# ---------------------------------------------------------------------------
# 交易执行
# ---------------------------------------------------------------------------

def execute_trade(signal_id, trade_api, account_api, inst_id, tick_sz, lot_sz,
                  capital_per_trade, state, current_price, best_bid, best_ask,
                  td_mode="cross", force_ioc=False):
    """
    根据信号执行交易（支持多空双向）
    signal_id: 0=平仓, 1=持有, 2=做多, 3=做空
    force_ioc: True=强制用市价单(兜底模式), False=先尝试Maker挂单
    """
    position = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    trades = state.get("trades", [])
    MIN_ORDER_USDT = 5.0

    log_message(f"[交易] signal={signal_id} position={position} target计算中...")

    # 先取消所有现有挂单
    cancel_all_orders(trade_api, inst_id)

    # 同步实际持仓到 state
    actual_pos = get_position(account_api, inst_id)
    if abs(actual_pos) < lot_sz * 0.5:
        if position != 0:
            log_message(f"[持仓同步] 实际持仓为0，重置状态 (原state: {position})")
            state["position"] = 0
            state["strategy_size"] = 0.0
            state["entry_bar"] = 0
            position = 0
            strategy_size = 0.0
    else:
        actual_dir = 1 if actual_pos > 0 else -1
        if position != actual_dir:
            log_message(f"[持仓同步] 修正持仓方向: state={position} -> actual={actual_dir}")
            state["position"] = actual_dir
            state["strategy_size"] = abs(actual_pos)
            position = actual_dir
            strategy_size = abs(actual_pos)

    # 解析目标仓位
    target_pos = position
    if signal_id == 2:
        target_pos = 1
    elif signal_id == 3:
        target_pos = -1
    elif signal_id == 0:
        target_pos = 0

    log_message(f"[交易] target_pos={target_pos} position={position}")

    if target_pos == position:
        log_message("[交易] 无需换仓，跳过")
        state["last_signal"] = signal_id
        return state

    # --- 平掉当前仓位 ---
    # 判断盈亏以决定订单类型: 盈利->Maker(限价), 亏损->Taker(市价)
    entry_price = state.get("entry_price", 0)
    if position == 1 and entry_price > 0:
        pnl_pct = (current_price - entry_price) / entry_price
    elif position == -1 and entry_price > 0:
        pnl_pct = (entry_price - current_price) / entry_price
    else:
        pnl_pct = 0
    close_as_maker = pnl_pct > 0
    close_type_str = "Maker(TP)" if close_as_maker else "Taker(SL)"

    if position == 1 and target_pos <= 0:
        # 平多仓（卖出平多）
        if strategy_size > 0:
            actual_position = get_position(account_api, inst_id)
            sell_size = min(strategy_size, abs(actual_position)) if actual_position > 0 else strategy_size
            if sell_size > 0:
                sell_size = round_to_size(sell_size, lot_sz)
            if sell_size > 0:
                if close_as_maker:
                    order_price = compute_order_price("sell", best_bid, best_ask, tick_sz)
                    if order_price:
                        order_id = place_limit_order(trade_api, inst_id, "sell", "long", sell_size, order_price, td_mode)
                    else:
                        order_id = None
                else:
                    order_id = place_market_order(trade_api, inst_id, "sell", "long", sell_size, td_mode)
                    order_price = current_price
                if order_id:
                    trades.append({
                        "time": datetime.now().isoformat(),
                        "type": f"CLOSE_LONG_{close_type_str}",
                        "instId": inst_id,
                        "orderId": order_id,
                        "size": sell_size,
                        "price": order_price,
                    })
                    log_message(f"[平多{close_type_str}] pnl={pnl_pct*100:+.2f}% 下单卖出 size={sell_size:.8f} price={order_price}")
                else:
                    log_message(f"[平多失败] {close_type_str}单未成交")
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["entry_bar"] = 0

    elif position == -1 and target_pos >= 0:
        # 平空仓（买入平空）
        if strategy_size > 0:
            actual_position = get_position(account_api, inst_id)
            close_size = min(strategy_size, abs(actual_position)) if actual_position < 0 else strategy_size
            if close_size > 0:
                close_size = round_to_size(close_size, lot_sz)
            if close_size > 0:
                if close_as_maker:
                    order_price = compute_order_price("buy", best_bid, best_ask, tick_sz)
                    if order_price:
                        order_id = place_limit_order(trade_api, inst_id, "buy", "short", close_size, order_price, td_mode)
                    else:
                        order_id = None
                else:
                    order_id = place_market_order(trade_api, inst_id, "buy", "short", close_size, td_mode)
                    order_price = current_price
                if order_id:
                    trades.append({
                        "time": datetime.now().isoformat(),
                        "type": f"CLOSE_SHORT_{close_type_str}",
                        "instId": inst_id,
                        "orderId": order_id,
                        "size": close_size,
                        "price": order_price,
                    })
                    log_message(f"[平空{close_type_str}] pnl={pnl_pct*100:+.2f}% 下单买入 size={close_size:.8f} price={order_price}")
                else:
                    log_message(f"[平空失败] {close_type_str}单未成交")
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["entry_bar"] = 0

    # --- 开新仓 ---
    log_message(f"[交易] 检查开仓: target_pos={target_pos} state_position={state.get('position', 0)} force_ioc={force_ioc}")
    if target_pos == 1 and state.get("position", 0) == 0:
        if current_price <= 0:
            log_message("[跳过开多] 价格无效")
        else:
            raw_size = Decimal(str(capital_per_trade)) / Decimal(str(current_price))
            n_units = int(raw_size / Decimal(str(lot_sz)))
            order_size = float(n_units * Decimal(str(lot_sz)))
            if order_size > 0:
                notional = order_size * current_price
                if force_ioc:
                    order_id = place_market_order(trade_api, inst_id, "buy", "long", order_size, td_mode)
                    if order_id:
                        trades.append({
                            "time": datetime.now().isoformat(),
                            "type": "BUY_OPEN_IOC_FALLBACK",
                            "instId": inst_id,
                            "orderId": order_id,
                            "size": order_size,
                            "price": current_price,
                        })
                        state["position"] = 1
                        state["strategy_size"] = order_size
                        state["entry_price"] = current_price
                        state["entry_bar"] = state.get("bar_count", 0)
                        log_message(f"[开多市价兜底] size={order_size:.8f} price={current_price:.2f}")
                    else:
                        log_message("[开多市价失败] 兜底单也未成交")
                else:
                    order_price = compute_order_price("buy", best_bid, best_ask, tick_sz)
                    if order_price:
                        order_id = place_limit_order(trade_api, inst_id, "buy", "long", order_size, order_price, td_mode)
                        if order_id:
                            trades.append({
                                "time": datetime.now().isoformat(),
                                "type": "BUY_OPEN_MAKER",
                                "instId": inst_id,
                                "orderId": order_id,
                                "size": order_size,
                                "price": order_price,
                            })
                            state["pending_open"] = True
                            state["pending_order_id"] = order_id
                            state["pending_open_signal"] = 2
                            state["pending_open_price"] = current_price
                            state["pending_open_size"] = order_size
                            log_message(f"[开多Maker] 挂单买入 size={order_size:.8f} price={order_price:.2f} notional={notional:.2f}")
                        else:
                            log_message("[开多失败] 限价单被拒绝")
                    else:
                        log_message("[开多失败] 无有效盘口价格")

    elif target_pos == -1 and state.get("position", 0) == 0:
        if current_price <= 0:
            log_message("[跳过开空] 价格无效")
        else:
            raw_size = Decimal(str(capital_per_trade)) / Decimal(str(current_price))
            n_units = int(raw_size / Decimal(str(lot_sz)))
            order_size = float(n_units * Decimal(str(lot_sz)))
            if order_size > 0:
                notional = order_size * current_price
                if force_ioc:
                    order_id = place_market_order(trade_api, inst_id, "sell", "short", order_size, td_mode)
                    if order_id:
                        trades.append({
                            "time": datetime.now().isoformat(),
                            "type": "SELL_SHORT_IOC_FALLBACK",
                            "instId": inst_id,
                            "orderId": order_id,
                            "size": order_size,
                            "price": current_price,
                        })
                        state["position"] = -1
                        state["strategy_size"] = order_size
                        state["entry_price"] = current_price
                        state["entry_bar"] = state.get("bar_count", 0)
                        log_message(f"[开空市价兜底] size={order_size:.8f} price={current_price:.2f}")
                    else:
                        log_message("[开空市价失败] 兜底单也未成交")
                else:
                    order_price = compute_order_price("sell", best_bid, best_ask, tick_sz)
                    if order_price:
                        order_id = place_limit_order(trade_api, inst_id, "sell", "short", order_size, order_price, td_mode)
                        if order_id:
                            trades.append({
                                "time": datetime.now().isoformat(),
                                "type": "SELL_SHORT_MAKER",
                                "instId": inst_id,
                                "orderId": order_id,
                                "size": order_size,
                                "price": order_price,
                            })
                            state["pending_open"] = True
                            state["pending_order_id"] = order_id
                            state["pending_open_signal"] = 3
                            state["pending_open_price"] = current_price
                            state["pending_open_size"] = order_size
                            log_message(f"[开空Maker] 挂单卖出 size={order_size:.8f} price={order_price:.2f} notional={notional:.2f}")
                        else:
                            log_message("[开空失败] 限价单被拒绝")
                    else:
                        log_message("[开空失败] 无有效盘口价格")

    state["trades"] = trades
    state["last_signal"] = signal_id
    state["last_update"] = datetime.now().isoformat()
    # 平仓后清理 TP 状态
    if state.get("position", 0) == 0:
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
    return state


def manage_tp_order(trade_api, inst_id, tick_sz, lot_sz, strategy, state, td_mode="cross"):
    """
    管理止盈限价单 (Maker)。
    当持仓存在且无 TP 单时，自动挂止盈限价单。
    """
    pos = state.get("position", 0)
    entry_price = state.get("entry_price", 0)
    size = state.get("strategy_size", 0)
    tp_order_id = state.get("tp_order_id")

    if pos == 0 or entry_price == 0 or size == 0:
        if tp_order_id:
            try:
                trade_api.cancel_order(instId=inst_id, ordId=tp_order_id)
            except Exception:
                pass
            state["tp_order_id"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
        return state

    # 计算止盈价格和方向
    tp_pct = getattr(strategy, 'take_profit_pct', 0.02)
    if pos == 1:
        tp_price = round_to_tick(entry_price * (1 + tp_pct), tick_sz)
        tp_side = "sell"
        tp_pos_side = "long"
    else:
        tp_price = round_to_tick(entry_price * (1 - tp_pct), tick_sz)
        tp_side = "buy"
        tp_pos_side = "short"

    # 已有正确的 TP 单则跳过
    if tp_order_id and state.get("tp_price") == tp_price and state.get("tp_side") == tp_side:
        return state

    # 取消旧 TP 单
    if tp_order_id:
        try:
            trade_api.cancel_order(instId=inst_id, ordId=tp_order_id)
        except Exception:
            pass
        state["tp_order_id"] = None

    # 对齐 size
    order_size = round_to_size(size, lot_sz)
    if order_size <= 0:
        return state

    # 挂止盈限价单 (Maker)
    order_id = place_limit_order(trade_api, inst_id, tp_side, tp_pos_side, order_size, tp_price, td_mode)
    if order_id:
        state["tp_order_id"] = order_id
        state["tp_price"] = tp_price
        state["tp_side"] = tp_side
        log_message(f"[TP挂单] {tp_side.upper()} {tp_pos_side} size={order_size:.8f} @ {tp_price:.2f} (入场={entry_price:.2f}, TP={tp_pct*100:.1f}%)")

    return state


def cancel_tp_order(trade_api, inst_id, state):
    """取消止盈限价单"""
    tp_order_id = state.get("tp_order_id")
    if tp_order_id:
        try:
            trade_api.cancel_order(instId=inst_id, ordId=tp_order_id)
        except Exception:
            pass
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
    return state


def check_stop_loss(state, current_price, stop_loss_pct=0.03, max_hold_bars=48):
    """检查止损和时间退出条件（支持多空双向）"""
    pos = state.get("position", 0)
    if pos == 0:
        return False, ""

    entry_price = state.get("entry_price", 0)
    entry_bar = state.get("entry_bar", 0)
    current_bar = state.get("bar_count", 0)

    if pos == 1:
        # 多头止损：价格下跌超过阈值
        if entry_price > 0 and (entry_price - current_price) / entry_price >= stop_loss_pct:
            return True, f"多头止损: 跌幅 {(entry_price - current_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%"
    elif pos == -1:
        # 空头止损：价格上涨超过阈值
        if entry_price > 0 and (current_price - entry_price) / entry_price >= stop_loss_pct:
            return True, f"空头止损: 涨幅 {(current_price - entry_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%"

    if current_bar - entry_bar >= max_hold_bars:
        pos_name = "多头" if pos == 1 else "空头"
        return True, f"{pos_name}时间退出: 持仓 {current_bar - entry_bar} 根K线 >= {max_hold_bars}"

    return False, ""


def force_close(trade_api, account_api, inst_id, state, current_price,
                best_bid, best_ask, tick_sz, lot_sz, reason, use_maker=False, td_mode="cross"):
    """
    强制平仓。
    use_maker=False: 市价单 (Taker) -- 止损场景，必须保证成交
    use_maker=True: 限价单 (Maker) -- 超时场景，可挂单等成交
    """
    pos = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    if pos == 0 or strategy_size <= 0:
        return state

    # 取消 TP 单和所有挂单
    cancel_tp_order(trade_api, inst_id, state)
    cancel_all_orders(trade_api, inst_id)

    actual_position = get_position(account_api, inst_id)
    close_size = min(strategy_size, abs(actual_position)) if abs(actual_position) > 0 else strategy_size
    if close_size > 0:
        close_size = round_to_size(close_size, lot_sz)
    if close_size <= 0:
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["entry_bar"] = 0
        return state

    # 确定平仓方向
    if pos == 1:
        side = "sell"
        pos_side = "long"
    elif pos == -1:
        side = "buy"
        pos_side = "short"
    else:
        return state

    # 根据场景选择订单类型
    if use_maker:
        order_price = compute_order_price(side, best_bid, best_ask, tick_sz)
        if order_price:
            order_id = place_limit_order(trade_api, inst_id, side, pos_side, close_size, order_price, td_mode)
        else:
            order_id = None
        fee_label = "Maker"
    else:
        order_id = place_market_order(trade_api, inst_id, side, pos_side, close_size, td_mode)
        order_price = current_price
        fee_label = "Taker"

    if order_id:
        trades = state.get("trades", [])
        pos_name = "多头" if pos == 1 else "空头"
        trades.append({
            "time": datetime.now().isoformat(),
            "type": f"FORCE_CLOSE_{fee_label}",
            "instId": inst_id,
            "orderId": order_id,
            "size": close_size,
            "price": order_price,
            "reason": reason,
        })
        state["trades"] = trades
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["entry_bar"] = 0
        state["last_signal"] = 0
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
        log_message(f"[强制平仓-{fee_label}] {reason}，{pos_name}{side} {close_size:.8f} @ {order_price}")

    state["last_update"] = datetime.now().isoformat()
    return state


def print_status(account_api, inst_id, state, leverage=1.0):
    """打印账户状态（合约模式）"""
    avail_usdt, eq_usdt = get_balance(account_api, "USDT")

    # 获取合约持仓
    try:
        position = get_position(account_api, inst_id)
    except Exception:
        position = 0.0

    signal_names = {0: "平仓 (FLAT)", 1: "持有 (HOLD)", 2: "做多 (BUY)", 3: "做空 (SELL)"}
    signal_str = signal_names.get(state.get("last_signal", 1), "未知")

    pos = state.get("position", 0)
    if pos == 1:
        pos_str = "多头"
    elif pos == -1:
        pos_str = "空头"
    elif state.get("pending_open"):
        pos_str = "等待Maker成交"
    else:
        pos_str = "空仓"

    # 计算未实现盈亏
    entry_price = state.get("entry_price", 0)
    last_price = state.get("last_price", 0)
    unrealized_pnl = 0.0
    if abs(position) > 0 and entry_price > 0 and last_price > 0:
        if position > 0:
            unrealized_pnl = (last_price - entry_price) * abs(position)
        else:
            unrealized_pnl = (entry_price - last_price) * abs(position)

    log_message("-" * 50)
    log_message(f"当前信号: {signal_str}")
    log_message(f"持仓状态: {pos_str}")
    if state.get("pending_open"):
        pending_dir = "做多" if state.get('pending_open_signal') == 2 else "做空"
        log_message(f"挂单方向: {pending_dir}")
        log_message(f"挂单价格: {state.get('pending_open_price', 0):.2f}")
    log_message(f"合约持仓: {position:.6f} | 杠杆: {leverage}x")
    if abs(position) > 0 and entry_price > 0:
        log_message(f"入场价格: {entry_price:.2f}")
        log_message(f"未实现盈亏: {unrealized_pnl:.2f} USDT")
    log_message(f"USDT 余额: {eq_usdt:.2f} (可用: {avail_usdt:.2f})")
    log_message(f"交易次数: {len(state.get('trades', []))}")
    log_message("-" * 50)


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    lock_fd = acquire_lock()
    parser = argparse.ArgumentParser(description="OKX Agent Trade Kit 永续合约混合费率实盘交易")
    parser.add_argument("--symbol", type=str, default="BTC-USDT-SWAP", help="交易对，如 BTC-USDT-SWAP, ETH-USDT-SWAP")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1H, 4H, 1D")
    parser.add_argument("--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径")
    parser.add_argument("--capital", type=float, default=100.0, help="每次交易保证金（USDT）")
    parser.add_argument("--leverage", type=float, default=1.0, help="杠杆倍数")
    parser.add_argument("--margin-mode", type=str, default="cross", choices=["cross", "isolated"], help="保证金模式: cross(全仓) / isolated(逐仓)")
    parser.add_argument("--demo", action="store_true", help="Demo Trading 模拟盘（默认）")
    parser.add_argument("--live", action="store_true", help="实盘交易（真钱！）")
    parser.add_argument("--once", action="store_true", help="只运行一次然后退出")
    parser.add_argument("--stop-loss", type=float, default=0.03, help="止损百分比（默认 3%%）")
    parser.add_argument("--max-hold", type=int, default=48, help="最大持仓K线数（默认 48）")
    parser.add_argument("--short", action="store_true", default=True, help="启用做空（默认开启）")
    parser.add_argument("--long-only", action="store_true", help="只做多，不做空")
    args = parser.parse_args()

    if args.live:
        flag = "0"
        mode_name = "实盘交易"
    else:
        flag = "1"
        mode_name = "Demo Trading 模拟盘"

    interval_seconds = INTERVAL_SECONDS_MAP.get(args.interval, 300)
    enable_short = not args.long_only
    mode_str = "多空双向" if enable_short else "只做多"

    # 加载策略参数
    log_message("=" * 50)
    log_message(f"启动 {mode_name} ({mode_str})")
    log_message("混合费率: 开仓=Maker, 止盈=Maker, 止损=Taker, 超时=Maker")
    log_message(f"保证金模式: {args.margin_mode}, 杠杆: {args.leverage}x")
    if not os.path.exists(args.checkpoint):
        log_message(f"错误: 未找到 {args.checkpoint}，请先运行 train_quant.py 训练策略")
        sys.exit(1)

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    params = checkpoint.get("params", {})
    # 手动放宽RSI阈值（同live_nado_quant.py）：震荡市中rsi_low过低会导致永久空仓
    if "rsi_low" in params:
        params["rsi_low"] = max(params["rsi_low"], 30)
    strategy_type = checkpoint.get("strategy", "bollinger_trend_filter")

    if strategy_type == "scalp":
        strategy = ScalpStrategy(
            window=params.get("window", 10),
            std_dev=params.get("std_dev", 1.2),
            take_profit_pct=params.get("take_profit_pct", 0.005),
            stop_loss_pct=params.get("stop_loss_pct", 0.003),
            max_hold_bars=params.get("max_hold_bars", 6),
            use_volume_filter=params.get("use_volume_filter", False),
            volume_threshold=params.get("volume_threshold", 0.8),
            rsi_extreme_low=params.get("rsi_extreme_low", 20),
            rsi_extreme_high=params.get("rsi_extreme_high", 80),
            use_rsi_entry=params.get("use_rsi_entry", False),
            rsi_entry_low=params.get("rsi_entry_low", 30),
            rsi_entry_high=params.get("rsi_entry_high", 70),
            use_trend_align=params.get("use_trend_align", False),
            trend_ma_period=params.get("trend_ma_period", 50),
            use_session_filter=params.get("use_session_filter", False),
            session_start=params.get("session_start", 13),
            session_end=params.get("session_end", 23),
            rsi_period=params.get("rsi_period", 14),
        )
        active_indicators = [k for k in ["use_volume_filter", "use_rsi_entry",
                                          "use_trend_align", "use_session_filter"]
                             if getattr(strategy, k)]
        indicators_str = ", ".join(active_indicators) if active_indicators else "无"
        session_str = ""
        if strategy.use_session_filter:
            session_str = f", session={strategy.session_start}-{strategy.session_end} UTC"
        log_message("策略模式: ScalpStrategy (高频剥头皮)")
        log_message(f"参数: w={strategy.window}, std={strategy.std_dev}, "
                    f"TP={strategy.take_profit_pct*100:.1f}%, SL={strategy.stop_loss_pct*100:.1f}%, "
                    f"hold={strategy.max_hold_bars}, 指标=[{indicators_str}]{session_str}")
    elif strategy_type == "hybrid_mm":
        strategy = HybridMeanRevMomentumStrategy(
            rsi_period=params.get("rsi_period", 14),
            rsi_low=params.get("rsi_low", 25),
            rsi_high=params.get("rsi_high", 75),
            ma_period=params.get("ma_period", 20),
            atr_period=params.get("atr_period", 14),
            atr_multiplier=params.get("atr_multiplier", 2.0),
            max_hold_bars=params.get("max_hold_bars", args.max_hold),
            enable_short=params.get("enable_short", True),
        )
        log_message("策略模式: HybridMeanRevMomentumStrategy (混合均值回归+动量)")
        log_message(f"参数: RSI=({strategy.rsi_low},{strategy.rsi_high}), MA={strategy.ma_period}, "
                    f"ATR={strategy.atr_multiplier}, hold={strategy.max_hold_bars}, short={strategy.enable_short}")
    elif strategy_type == "adaptive":
        strategy = AdaptiveHybridStrategy(
            rsi_period=params.get("rsi_period", 14),
            rsi_low=params.get("rsi_low", 30),
            rsi_high=params.get("rsi_high", 70),
            ma_period=params.get("ma_period", 20),
            trend_long_ma=params.get("trend_long_ma", 100),
            trend_pull_ma=params.get("trend_pull_ma", 20),
            adx_period=params.get("adx_period", 14),
            adx_threshold=params.get("adx_threshold", 25),
            atr_period=params.get("atr_period", 14),
            atr_multiplier=params.get("atr_multiplier", 2.0),
            max_hold_bars=params.get("max_hold_bars", args.max_hold),
            enable_short=params.get("enable_short", True),
        )
        log_message("策略模式: AdaptiveHybrid (ADX判市 + RSI均值回归/EMA趋势跟随)")
        log_message(f"参数: RSI=({strategy.rsi_low},{strategy.rsi_high}), trendL={strategy.trend_long_ma}, "
                    f"ADX_th={strategy.adx_threshold}, ATR={strategy.atr_multiplier}, hold={strategy.max_hold_bars}")
    elif strategy_type == "regime":
        ranging_params = params.get("ranging_params", {})
        trending_params = params.get("trending_params", {})
        adx_threshold = params.get("adx_threshold", 20)
        strategy = RegimeStrategy(
            ranging_params=ranging_params,
            trending_params=trending_params,
            adx_threshold=adx_threshold,
            enable_short=params.get("enable_short", True),
        )
        rp = strategy.ranging
        tp = strategy.trending
        log_message(f"策略模式: Regime (动态ADX切换: ADX<={adx_threshold}=RSI均值回归, ADX>{adx_threshold}=EMA趋势跟随)")
        log_message(f"  震荡市: RSI({rp.rsi_low},{rp.rsi_high}) MA={rp.ma_period} ATRx{rp.atr_multiplier}")
        log_message(f"  趋势市: longMA={tp.long_ma_period} pullMA={tp.pull_ma_period} ATRx{tp.atr_multiplier}")
    else:
        strategy = TrendStrategy(
            window=params.get("window", 20),
            std_dev=params.get("std_dev", 2.0),
            atr_multiplier=params.get("atr_multiplier", 2.5),
            max_hold_bars=params.get("max_hold_bars", args.max_hold),
            adx_threshold=params.get("adx_threshold", 25),
            entry_zone=params.get("entry_zone", 0.0),
            rsi_threshold=params.get("rsi_threshold", 30),
            use_adx=params.get("use_adx", False),
            use_volume=params.get("use_volume", False),
            volume_threshold=params.get("volume_threshold", 1.2),
            use_macd=params.get("use_macd", False),
            macd_confirm_mode=params.get("macd_confirm_mode", "direction"),
            use_ma_cross=params.get("use_ma_cross", False),
            use_mfi=params.get("use_mfi", False),
            mfi_period=params.get("mfi_period", 14),
            mfi_threshold=params.get("mfi_threshold", 20),
            use_stochastic=params.get("use_stochastic", False),
            stoch_period=params.get("stoch_period", 14),
            stoch_threshold=params.get("stoch_threshold", 20),
            use_rsi_divergence=params.get("use_rsi_divergence", False),
            rsi_divergence_lookback=params.get("rsi_divergence_lookback", 5),
            use_macd_divergence=params.get("use_macd_divergence", False),
            macd_divergence_lookback=params.get("macd_divergence_lookback", 5),
            use_trend_filter=params.get("use_trend_filter", False),
            trend_window=params.get("trend_window", 50),
            use_obv_trend=params.get("use_obv_trend", False),
            obv_ma_period=params.get("obv_ma_period", 20),
            use_volume_spike=params.get("use_volume_spike", False),
            volume_spike_threshold=params.get("volume_spike_threshold", 2.0),
            use_vwap=params.get("use_vwap", False),
            vwap_period=params.get("vwap_period", 20),
            use_htf_macd=params.get("use_htf_macd", False),
            htf_macd_fast=params.get("htf_macd_fast", 12),
            htf_macd_slow=params.get("htf_macd_slow", 26),
            htf_macd_signal=params.get("htf_macd_signal", 9),
            use_resonance=params.get("use_resonance", False),
            resonance_min_score=params.get("resonance_min_score", 3),
        )
        active_indicators = [k for k in ["use_adx", "use_volume", "use_macd", "use_ma_cross", "use_mfi", "use_stochastic", "use_rsi_divergence", "use_macd_divergence", "use_trend_filter", "use_obv_trend", "use_volume_spike", "use_vwap", "use_htf_macd", "use_resonance"] if getattr(strategy, k)]
        indicators_str = ", ".join(active_indicators) if active_indicators else "无"
        log_message("策略模式: TrendStrategy")
        log_message(f"参数: 周期={strategy.window}, 标准差={strategy.std_dev}, "
                    f"ATR止损={strategy.atr_multiplier}, 最大持仓={strategy.max_hold_bars}根K线, "
                    f"RSI阈值={strategy.rsi_threshold}, 活跃指标=[{indicators_str}]")

    # 初始化 OKX API
    account_api, trade_api, market_api, public_api = init_okx_api(flag=flag)
    log_message("OKX API 连接成功")

    # 设置杠杆
    set_leverage(account_api, args.symbol, args.leverage, args.margin_mode)

    # 获取交易对信息
    inst_info = get_instrument_info(public_api, args.symbol)
    tick_sz = inst_info["tickSz"]
    lot_sz = inst_info["lotSz"]
    min_sz = inst_info["minSz"]
    ct_val = inst_info.get("ctVal", 1)
    log_message(f"交易对信息: {args.symbol}, Tick: {tick_sz}, Lot: {lot_sz}, Min: {min_sz}, CtVal: {ct_val}")

    # 加载或初始化状态
    state = load_state()
    if state is None:
        # 首次运行：用当前 USDT 权益作为本金基准
        avail_usdt, eq_usdt = get_balance(account_api, "USDT")
        ticker_resp = market_api.get_ticker(instId=args.symbol)
        last_px = float(ticker_resp["data"][0]["last"]) if ticker_resp.get("code") == "0" else 0.0

        state = {
            "symbol": args.symbol,
            "interval": args.interval,
            "initial_capital": args.capital,
            "initial_equity": eq_usdt,
            "position": 0,
            "strategy_size": 0.0,
            "trades": [],
            "last_signal": 1,
            "last_price": last_px,
            "bar_count": 0,
            "entry_price": 0.0,
            "entry_bar": 0,
            "tp_order_id": None,
            "tp_price": 0.0,
            "tp_side": None,
            "pending_open": False,
            "pending_order_id": None,
            "pending_open_signal": 0,
            "pending_open_price": 0.0,
            "pending_open_size": 0.0,
        }
        log_message(f"初始化账户，保证金: {args.capital:.2f} USDT, 杠杆: {args.leverage}x, "
                    f"实际名义价值: {args.capital * args.leverage:.2f} USDT")
    else:
        log_message("恢复上一次交易状态")
        # 兼容旧状态文件
        state.setdefault("strategy_size", 0.0)
        state.setdefault("bar_count", 0)
        state.setdefault("entry_price", 0.0)
        state.setdefault("entry_bar", 0)
        state.setdefault("tp_order_id", None)
        state.setdefault("tp_price", 0.0)
        state.setdefault("tp_side", None)
        state.setdefault("pending_open", False)
        state.setdefault("pending_order_id", None)
        state.setdefault("pending_open_signal", 0)
        state.setdefault("pending_open_price", 0.0)
        state.setdefault("pending_open_size", 0.0)
        if "strategy_btc" in state and "strategy_size" not in state:
            state["strategy_size"] = state.pop("strategy_btc")
        if "initial_equity" not in state:
            _, eq_usdt = get_balance(account_api, "USDT")
            state["initial_equity"] = eq_usdt
            log_message(f"补录初始权益基准: {eq_usdt:.2f} USDT")

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
                # 0. 同步实际持仓（防止过期 state 导致误判）
                actual_pos = get_position(account_api, args.symbol)
                stale_pos = state.get("position", 0)
                if abs(actual_pos) < lot_sz * 0.5 and stale_pos != 0:
                    if state.get("tp_order_id"):
                        # 有 TP 单且持仓归零 -> TP 可能真的成交了
                        entry_p = state.get("entry_price", 0)
                        if stale_pos == 1 and entry_p > 0:
                            tp_pnl = (state.get("last_price", 0) - entry_p) / entry_p * 100
                        elif stale_pos == -1 and entry_p > 0:
                            tp_pnl = (entry_p - state.get("last_price", 0)) / entry_p * 100
                        else:
                            tp_pnl = 0
                        if abs(tp_pnl) > 0.5:
                            log_message(f"[TP成交] 止盈限价单已成交! 盈亏={tp_pnl:+.2f}%")
                        else:
                            log_message(f"[持仓同步] 实际持仓=0, state={stale_pos}, PnL={tp_pnl:+.2f}% -> 可能挂单未成交，重置状态")
                    else:
                        log_message(f"[持仓同步] 实际持仓=0, state={stale_pos} -> 重置状态")
                    state["position"] = 0
                    state["strategy_size"] = 0.0
                    state["entry_bar"] = 0
                    state["tp_order_id"] = None
                    state["tp_price"] = 0.0
                    state["tp_side"] = None
                    state["pending_open"] = False
                elif abs(actual_pos) >= lot_sz * 0.5 and state.get("pending_open"):
                    # pending_open 挂单成交了（但还没被主循环检测到）
                    pos_dir = 1 if actual_pos > 0 else -1
                    log_message(f"[持仓同步] 检测到挂单已成交: 实际={actual_pos:.6f}")
                    state["position"] = pos_dir
                    state["strategy_size"] = abs(actual_pos)
                    if state.get("entry_price", 0) == 0:
                        state["entry_price"] = state.get("pending_open_price", state.get("last_price", 0))
                    if state.get("entry_bar", 0) == 0:
                        state["entry_bar"] = state.get("bar_count", 0) - 1
                    state["pending_open"] = False
                    state["pending_order_id"] = None

                # 1. 获取 K 线数据
                df = fetch_candles(market_api, args.symbol, bar=args.interval, limit=500)
                if df is None or len(df) < strategy.window + 10:
                    log_message("数据不足，跳过本轮")
                else:
                    # 2. 生成信号
                    signal_id, bb_info = predict_signal(strategy, df, enable_short=enable_short)
                    current_price = bb_info["price"]
                    current_time = df.iloc[-1]["datetime"]
                    state["last_price"] = current_price

                    log_message(f"K线时间: {current_time} | 价格: {current_price:.2f}")
                    log_message(f"布林带: 上轨={bb_info['upper']:.2f} 中轨={bb_info['mid']:.2f} 下轨={bb_info['lower']:.2f}")

                    state["bar_count"] = state.get("bar_count", 0) + 1

                    # 3. 检查止损/时间退出
                    should_exit, exit_reason = check_stop_loss(
                        state, current_price,
                        stop_loss_pct=args.stop_loss,
                        max_hold_bars=strategy.max_hold_bars,
                    )

                    # 4. 获取盘口数据
                    best_bid, best_ask = get_orderbook(market_api, args.symbol, depth=1)
                    if best_bid:
                        log_message(f"盘口: bid={best_bid:.2f} ask={best_ask:.2f}")
                    else:
                        log_message("盘口数据不可用")

                    if should_exit:
                        # 超时 -> Maker, 止损 -> Taker
                        is_timeout = "时间退出" in exit_reason
                        state["pending_open"] = False
                        state = force_close(
                            trade_api, account_api, args.symbol, state,
                            current_price, best_bid, best_ask, tick_sz, lot_sz,
                            exit_reason, use_maker=is_timeout, td_mode=args.margin_mode,
                        )
                    else:
                        # === 检查 pending_open 状态 ===
                        pending = state.get("pending_open", False)
                        pending_order_id = state.get("pending_order_id")
                        if pending and pending_order_id:
                            # 检查挂单是否成交
                            order_state, fill_sz, avg_px = get_order_status(trade_api, args.symbol, pending_order_id)
                            if order_state == "filled":
                                # Maker 挂单成交了!
                                pos_dir = 1 if fill_sz > 0 else -1  # 根据 pending 信号判断方向
                                pending_signal = state.get("pending_open_signal", 2)
                                pos_dir = 1 if pending_signal == 2 else -1
                                state["position"] = pos_dir
                                state["strategy_size"] = abs(fill_sz) if fill_sz != 0 else state.get("pending_open_size", 0)
                                state["entry_price"] = avg_px if avg_px > 0 else state.get("pending_open_price", current_price)
                                state["entry_bar"] = state.get("bar_count", 0) - 1
                                state["pending_open"] = False
                                state["pending_order_id"] = None
                                pos_name = "多" if pos_dir == 1 else "空"
                                log_message(f"[Maker成交] 入场成功 {pos_name} @{state['entry_price']:.2f} size={state['strategy_size']:.8f}")
                                # 挂 TP 单
                                state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state, td_mode=args.margin_mode)
                            elif order_state in ("live", "partially_filled"):
                                # Maker 没成交完 -> IOC 兜底
                                pending_signal = state.get("pending_open_signal", 0)
                                cancel_all_orders(trade_api, args.symbol)
                                state["pending_open"] = False
                                state["pending_order_id"] = None

                                if signal_id == pending_signal:
                                    log_message(f"[市价兜底] Maker未成交，切换市价单 signal={signal_id}")
                                    state = execute_trade(
                                        signal_id, trade_api, account_api, args.symbol,
                                        tick_sz, lot_sz,
                                        args.capital * args.leverage, state, current_price,
                                        best_bid, best_ask,
                                        td_mode=args.margin_mode, force_ioc=True,
                                    )
                                    if state.get("position", 0) != 0:
                                        state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state, td_mode=args.margin_mode)
                                else:
                                    log_message(f"[信号变化] 挂单期间信号改变 ({pending_signal}->{signal_id})，取消挂单")
                                    state = execute_trade(
                                        signal_id, trade_api, account_api, args.symbol,
                                        tick_sz, lot_sz,
                                        args.capital * args.leverage, state, current_price,
                                        best_bid, best_ask,
                                        td_mode=args.margin_mode,
                                    )
                            elif order_state == "canceled":
                                # 订单已被取消
                                state["pending_open"] = False
                                state["pending_order_id"] = None
                                log_message("[挂单已取消] 外部取消或已处理")
                                # 重新执行当前信号
                                state = execute_trade(
                                    signal_id, trade_api, account_api, args.symbol,
                                    tick_sz, lot_sz,
                                    args.capital * args.leverage, state, current_price,
                                    best_bid, best_ask,
                                    td_mode=args.margin_mode,
                                )
                                if state.get("position", 0) != 0:
                                    state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state, td_mode=args.margin_mode)
                        else:
                            # === 正常流程 (无 pending) ===
                            # 检查 TP 单是否已成交
                            if state.get("position", 0) != 0 and state.get("tp_order_id"):
                                tp_order_id = state["tp_order_id"]
                                tp_state, _, _ = get_order_status(trade_api, args.symbol, tp_order_id)
                                if tp_state == "filled":
                                    entry_p = state.get("entry_price", 0)
                                    pos_dir = state.get("position", 0)
                                    if pos_dir == 1 and entry_p > 0:
                                        tp_pnl = (current_price - entry_p) / entry_p * 100
                                    elif pos_dir == -1 and entry_p > 0:
                                        tp_pnl = (entry_p - current_price) / entry_p * 100
                                    else:
                                        tp_pnl = 0
                                    if abs(tp_pnl) > 0.5:
                                        log_message(f"[TP成交] 止盈限价单已成交! 盈亏={tp_pnl:+.2f}%")
                                    else:
                                        log_message(f"[状态修正] 持仓已归零但PnL={tp_pnl:+.2f}%异常，重置状态")
                                    state["position"] = 0
                                    state["strategy_size"] = 0.0
                                    state["entry_bar"] = 0
                                    state["tp_order_id"] = None
                                    state["tp_price"] = 0.0
                                    state["tp_side"] = None
                                elif tp_state == "canceled":
                                    log_message("[TP取消] 止盈单被外部取消")
                                    state["tp_order_id"] = None

                            # 正常交易 (先尝试 Maker)
                            state = execute_trade(
                                signal_id, trade_api, account_api, args.symbol,
                                tick_sz, lot_sz,
                                args.capital * args.leverage, state, current_price,
                                best_bid, best_ask,
                                td_mode=args.margin_mode,
                            )

                            # 挂单等待 or 已成交 -> 管理 TP
                            if state.get("pending_open"):
                                log_message("[挂单等待] Maker单已挂出，下轮检查成交")
                            elif state.get("position", 0) != 0:
                                state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state, td_mode=args.margin_mode)

                    # 6. 打印状态
                    print_status(account_api, args.symbol, state, leverage=args.leverage)
                    save_state(state)

            except Exception as e:
                log_message(f"本轮执行异常: {e}")
                import traceback
                log_message(traceback.format_exc())

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
