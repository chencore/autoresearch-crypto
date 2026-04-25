"""
OKX Agent Trade Kit 实盘/模拟盘交易脚本。
基于混合费率执行策略，每 5 分钟获取信号并自动下单。

混合费率执行策略:
    - 开仓: POST_ONLY (Maker) -- 挂限价单，享Maker低费率
    - 止盈: POST_ONLY (Maker) -- 自动挂止盈限价单，价格到达即成交
    - 止损: IOC (Taker) -- 必须保证成交，付Taker费率
    - 超时: POST_ONLY (Maker) -- 挂限价单平仓

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
from decimal import Decimal

from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import torch

from okx.Account import AccountAPI
from okx.Trade import TradeAPI
from okx.MarketData import MarketAPI
from okx.PublicData import PublicAPI

from train_quant import TrendStrategy, ScalpStrategy

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
def get_instrument_info(public_api, inst_id):
    """获取交易对信息 (tickSz, lotSz, minSz)"""
    resp = public_api.get_instruments(instType="SPOT", instId=inst_id)
    if resp.get("code") == "0" and resp.get("data"):
        info = resp["data"][0]
        return {
            "tickSz": float(info.get("tickSz", "0.01")),
            "lotSz": float(info.get("lotSz", "0.00001")),
            "minSz": float(info.get("minSz", "0.00001")),
        }
    return {"tickSz": 0.01, "lotSz": 0.00001, "minSz": 0.00001}


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
    resp = market_api.get_books(instId=inst_id, sz=str(depth))
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
def place_market_order(trade_api, inst_id, side, sz, tgt_ccy="quote_ccy"):
    """
    下市价单 (Taker)。
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
        log_message(f"市价单成功 [{side.upper()}] 订单ID: {order_id}")
        return order_id
    else:
        log_message(f"市价单失败: {resp}")
        return None


@retry_on_exception(max_retries=3, delay=1.0)
def place_limit_order(trade_api, inst_id, side, sz, px):
    """下限价单 (Maker)"""
    resp = trade_api.place_order(
        instId=inst_id,
        tdMode="cash",
        side=side,
        ordType="limit",
        px=str(px),
        sz=str(sz),
    )
    if resp.get("code") == "0":
        order_id = resp["data"][0].get("ordId")
        log_message(f"限价单成功 [{side.upper()}] 订单ID: {order_id} 价格={px} 数量={sz}")
        return order_id
    else:
        log_message(f"限价单失败: {resp}")
        return None


# ---------------------------------------------------------------------------
# 信号生成
# ---------------------------------------------------------------------------

def predict_signal(strategy, df, enable_short=False):
    """生成布林带信号"""
    signals = strategy.generate_signals(df, enable_short=enable_short)
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
                  force_ioc=False):
    """
    根据信号执行交易
    signal_id: 0=平仓, 1=持有, 2=做多
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
    base_ccy = inst_id.split("-")[0]
    avail_btc, eq_btc = get_balance(account_api, base_ccy)
    if abs(avail_btc) < lot_sz * 0.5:
        if position != 0:
            log_message(f"[持仓同步] 实际持仓为0，重置状态 (原state: {position})")
            state["position"] = 0
            state["strategy_size"] = 0.0
            position = 0
            strategy_size = 0.0

    # 解析目标仓位（OKX 现货只做多）
    target_pos = position
    if signal_id == 2:
        target_pos = 1
    elif signal_id == 0:
        target_pos = 0

    log_message(f"[交易] target_pos={target_pos} position={position}")

    if target_pos == position:
        log_message(f"[交易] 无需换仓，跳过")
        state["last_signal"] = signal_id
        return state

    # --- 平掉当前仓位 ---
    if target_pos == 0 and position == 1:
        sell_btc = strategy_size
        if sell_btc <= 0:
            log_message(f"[跳过卖出] 策略无可售仓位")
            return state

        sell_btc = min(sell_btc, avail_btc)
        if sell_btc > 0:
            sell_btc = round_to_size(sell_btc, lot_sz)
        if sell_btc <= 0:
            log_message(f"[跳过卖出] 账户可用 {base_ccy} 为零")
            return state

        # 判断盈亏以决定订单类型: 盈利->Maker(限价), 亏损->Taker(市价)
        entry_price = state.get("entry_price", 0)
        if entry_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = 0
        close_as_maker = pnl_pct > 0
        close_type_str = "Maker(TP)" if close_as_maker else "Taker(SL)"

        if close_as_maker:
            # 盈利平仓 -> Maker (限价单)
            order_price = compute_order_price("sell", best_bid, best_ask, tick_sz)
            if order_price:
                order_id = place_limit_order(trade_api, inst_id, "sell", sell_btc, order_price)
            else:
                order_id = None
        else:
            # 亏损平仓 -> Taker (市价单)
            order_id = place_market_order(trade_api, inst_id, "sell", f"{sell_btc:.8f}", tgt_ccy="base_ccy")
            order_price = current_price

        if order_id:
            trades.append({
                "time": datetime.now().isoformat(),
                "type": f"CLOSE_LONG_{close_type_str}",
                "instId": inst_id,
                "orderId": order_id,
                "size": sell_btc,
                "price": order_price,
            })
            log_message(f"[平仓{close_type_str}] pnl={pnl_pct*100:+.2f}% 下单卖出 size={sell_btc:.8f} price={order_price}")
        else:
            log_message(f"[平仓失败] {close_type_str}单未成交")

        state["position"] = 0
        state["strategy_size"] = 0.0

    # --- 开新仓 ---
    log_message(f"[交易] 检查开仓: target_pos={target_pos} state_position={state.get('position', 0)} force_ioc={force_ioc}")
    if target_pos == 1 and state.get("position", 0) == 0:
        if current_price <= 0:
            log_message("[跳过开多] 价格无效")
        else:
            # 计算可买入的 base 数量
            avail_usdt, _ = get_balance(account_api, "USDT")
            order_usdt = min(capital_per_trade, avail_usdt)
            if order_usdt < MIN_ORDER_USDT:
                log_message(f"[跳过买入] 可用 USDT 不足最小下单额: {avail_usdt:.2f} < {MIN_ORDER_USDT}")
                state["last_signal"] = signal_id
                return state

            raw_size = Decimal(str(order_usdt)) / Decimal(str(current_price))
            n_units = int(raw_size / Decimal(str(lot_sz)))
            order_size = float(n_units * Decimal(str(lot_sz)))

            if order_size > 0:
                notional = order_size * current_price
                if force_ioc:
                    # IOC 兜底模式 (Taker) -- 保证成交
                    order_id = place_market_order(trade_api, inst_id, "buy", f"{order_usdt:.2f}", tgt_ccy="quote_ccy")
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
                    # POST_ONLY 挂单模式 (Maker) -- 不立即设 position
                    order_price = compute_order_price("buy", best_bid, best_ask, tick_sz)
                    if order_price:
                        order_id = place_limit_order(trade_api, inst_id, "buy", order_size, order_price)
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

    state["trades"] = trades
    state["last_signal"] = signal_id
    state["last_update"] = datetime.now().isoformat()
    # 平仓后清理 TP 状态
    if state.get("position", 0) == 0:
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
    return state


def manage_tp_order(trade_api, inst_id, tick_sz, lot_sz, strategy, state):
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

    # 计算止盈价格
    tp_pct = getattr(strategy, 'take_profit_pct', 0.02)
    tp_price = round_to_tick(entry_price * (1 + tp_pct), tick_sz)
    tp_side = "sell"

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
    order_id = place_limit_order(trade_api, inst_id, tp_side, order_size, tp_price)
    if order_id:
        state["tp_order_id"] = order_id
        state["tp_price"] = tp_price
        state["tp_side"] = tp_side
        log_message(f"[TP挂单] {tp_side.upper()} size={order_size:.8f} @ {tp_price:.2f} (入场={entry_price:.2f}, TP={tp_pct*100:.1f}%)")

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
    """检查止损和时间退出条件"""
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

    if current_bar - entry_bar >= max_hold_bars:
        return True, f"时间退出: 持仓 {current_bar - entry_bar} 根K线 >= {max_hold_bars}"

    return False, ""


def force_close(trade_api, account_api, inst_id, state, current_price,
                best_bid, best_ask, tick_sz, lot_sz, reason, use_maker=False):
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

    base_ccy = inst_id.split("-")[0]
    avail_btc, _ = get_balance(account_api, base_ccy)
    close_size = min(strategy_size, avail_btc) if avail_btc > 0 else strategy_size
    if close_size > 0:
        close_size = round_to_size(close_size, lot_sz)
    if close_size <= 0:
        state["position"] = 0
        state["strategy_size"] = 0.0
        return state

    side = "sell"

    # 根据场景选择订单类型
    if use_maker:
        order_price = compute_order_price(side, best_bid, best_ask, tick_sz)
        if order_price:
            order_id = place_limit_order(trade_api, inst_id, side, close_size, order_price)
        else:
            order_id = None
        fee_label = "Maker"
    else:
        order_id = place_market_order(trade_api, inst_id, side, f"{close_size:.8f}", tgt_ccy="base_ccy")
        order_price = current_price
        fee_label = "Taker"

    if order_id:
        trades = state.get("trades", [])
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
        state["last_signal"] = 0
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
        log_message(f"[强制平仓-{fee_label}] {reason}，卖出 {close_size:.8f} {base_ccy} @ {order_price}")

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

    signal_names = {0: "平仓 (FLAT)", 1: "持有 (HOLD)", 2: "做多 (BUY)", 3: "做空 (SELL)"}
    signal_str = signal_names.get(state.get("last_signal", 1), "未知")

    pos_str = "多头" if state.get("position", 0) == 1 else ("空仓" if state.get("pending_open") else "空仓")
    if state.get("pending_open"):
        pos_str = "等待Maker成交"

    log_message("-" * 50)
    log_message(f"当前信号: {signal_str}")
    log_message(f"持仓状态: {pos_str}")
    if state.get("pending_open"):
        log_message(f"挂单方向: 做多")
        log_message(f"挂单价格: {state.get('pending_open_price', 0):.2f}")
    log_message(f"USDT 余额: {eq_usdt:.2f} (可用: {avail_usdt:.2f})")
    log_message(f"{ccy} 余额: {eq_btc:.8f} (可用: {avail_btc:.8f})")
    log_message(f"预估权益: {total_equity:.2f} USDT")
    log_message(f"累计收益: {ret*100:.2f}%")
    log_message(f"交易次数: {len(state.get('trades', []))}")
    log_message("-" * 50)


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    lock_fd = acquire_lock()
    parser = argparse.ArgumentParser(description="OKX Agent Trade Kit 混合费率实盘交易")
    parser.add_argument("--symbol", type=str, default="BTC-USDT", help="交易对，如 BTC-USDT, ETH-USDT")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1H, 4H, 1D")
    parser.add_argument("--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径")
    parser.add_argument("--capital", type=float, default=100.0, help="每次交易保证金（USDT）")
    parser.add_argument("--leverage", type=float, default=1.0, help="杠杆倍数，实际下单=capital×leverage")
    parser.add_argument("--demo", action="store_true", help="Demo Trading 模拟盘（默认）")
    parser.add_argument("--live", action="store_true", help="实盘交易（真钱！）")
    parser.add_argument("--once", action="store_true", help="只运行一次然后退出")
    parser.add_argument("--stop-loss", type=float, default=0.03, help="止损百分比（默认 3%%）")
    parser.add_argument("--max-hold", type=int, default=48, help="最大持仓K线数（默认 48）")
    args = parser.parse_args()

    if args.live:
        flag = "0"
        mode_name = "实盘交易"
    else:
        flag = "1"
        mode_name = "Demo Trading 模拟盘"

    interval_seconds = INTERVAL_SECONDS_MAP.get(args.interval, 300)

    # 加载策略参数
    log_message("=" * 50)
    log_message(f"启动 {mode_name}")
    log_message(f"混合费率: 开仓=Maker, 止盈=Maker, 止损=Taker, 超时=Maker")
    if not os.path.exists(args.checkpoint):
        log_message(f"错误: 未找到 {args.checkpoint}，请先运行 train_quant.py 训练策略")
        sys.exit(1)

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    params = checkpoint.get("params", {})
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
        log_message(f"策略模式: ScalpStrategy (高频剥头皮)")
        log_message(f"参数: w={strategy.window}, std={strategy.std_dev}, "
                    f"TP={strategy.take_profit_pct*100:.1f}%, SL={strategy.stop_loss_pct*100:.1f}%, "
                    f"hold={strategy.max_hold_bars}, 指标=[{indicators_str}]{session_str}")
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
        log_message(f"策略模式: TrendStrategy")
        log_message(f"参数: 周期={strategy.window}, 标准差={strategy.std_dev}, "
                    f"ATR止损={strategy.atr_multiplier}, 最大持仓={strategy.max_hold_bars}根K线, "
                    f"RSI阈值={strategy.rsi_threshold}, 活跃指标=[{indicators_str}]")

    # 初始化 OKX API
    account_api, trade_api, market_api, public_api = init_okx_api(flag=flag)
    log_message("OKX API 连接成功")

    # 获取交易对信息
    inst_info = get_instrument_info(public_api, args.symbol)
    tick_sz = inst_info["tickSz"]
    lot_sz = inst_info["lotSz"]
    min_sz = inst_info["minSz"]
    log_message(f"交易对信息: {args.symbol}, Tick Size: {tick_sz}, Lot Size: {lot_sz}, Min Size: {min_sz}")

    # 加载或初始化状态
    state = load_state()
    if state is None:
        # 首次运行：用当前账户总权益作为本金基准
        ccy = args.symbol.split("-")[0]
        avail_usdt, eq_usdt = get_balance(account_api, "USDT")
        avail_btc, eq_btc = get_balance(account_api, ccy)
        # 用当前市价估算总权益
        ticker_resp = market_api.get_ticker(instId=args.symbol)
        last_px = float(ticker_resp["data"][0]["last"]) if ticker_resp.get("code") == "0" else 0.0
        initial_equity = eq_usdt + eq_btc * last_px

        state = {
            "symbol": args.symbol,
            "interval": args.interval,
            "initial_capital": args.capital,
            "initial_equity": initial_equity,
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
                    f"实际下单: {args.capital * args.leverage:.2f} USDT，初始权益: {initial_equity:.2f} USDT")
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
        # 兼容旧字段 strategy_btc -> strategy_size
        if "strategy_btc" in state and "strategy_size" not in state:
            state["strategy_size"] = state.pop("strategy_btc")
        if "initial_equity" not in state:
            ccy = args.symbol.split("-")[0]
            avail_usdt, eq_usdt = get_balance(account_api, "USDT")
            avail_btc, eq_btc = get_balance(account_api, ccy)
            ticker_resp = market_api.get_ticker(instId=args.symbol)
            last_px = float(ticker_resp["data"][0]["last"]) if ticker_resp.get("code") == "0" else state.get("last_price", 0)
            initial_equity = eq_usdt + eq_btc * last_px
            state["initial_equity"] = initial_equity
            log_message(f"补录初始权益基准: {initial_equity:.2f} USDT")

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
                # 0. 同步实际持仓（防止过期 state 导致误判）
                base_ccy = args.symbol.split("-")[0]
                avail_btc, eq_btc = get_balance(account_api, base_ccy)
                stale_pos = state.get("position", 0)
                if abs(avail_btc) < lot_sz * 0.5 and stale_pos != 0:
                    log_message(f"[持仓同步] 实际持仓=0, state={stale_pos} -> 重置状态")
                    state["position"] = 0
                    state["strategy_size"] = 0.0
                    state["tp_order_id"] = None
                    state["tp_price"] = 0.0
                    state["tp_side"] = None
                    state["pending_open"] = False

                # 1. 获取 K 线数据
                df = fetch_candles(market_api, args.symbol, bar=args.interval, limit=500)
                if df is None or len(df) < strategy.window + 10:
                    log_message("数据不足，跳过本轮")
                else:
                    # 2. 生成信号（OKX 现货只做多）
                    signal_id, bb_info = predict_signal(strategy, df, enable_short=False)
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
                            exit_reason, use_maker=is_timeout,
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
                                state["position"] = 1
                                state["strategy_size"] = fill_sz if fill_sz > 0 else state.get("pending_open_size", 0)
                                state["entry_price"] = avg_px if avg_px > 0 else state.get("pending_open_price", current_price)
                                state["entry_bar"] = state.get("bar_count", 0) - 1
                                state["pending_open"] = False
                                state["pending_order_id"] = None
                                log_message(f"[Maker成交] 入场成功 多 @{state['entry_price']:.2f} size={state['strategy_size']:.8f}")
                                # 挂 TP 单
                                state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state)
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
                                        force_ioc=True,
                                    )
                                    if state.get("position", 0) != 0:
                                        state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state)
                                else:
                                    log_message(f"[信号变化] 挂单期间信号改变 ({pending_signal}->{signal_id})，取消挂单")
                                    state = execute_trade(
                                        signal_id, trade_api, account_api, args.symbol,
                                        tick_sz, lot_sz,
                                        args.capital * args.leverage, state, current_price,
                                        best_bid, best_ask,
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
                                )
                                if state.get("position", 0) != 0:
                                    state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state)
                        else:
                            # === 正常流程 (无 pending) ===
                            # 检查 TP 单是否已成交
                            if state.get("position", 0) != 0 and state.get("tp_order_id"):
                                tp_order_id = state["tp_order_id"]
                                tp_state, _, _ = get_order_status(trade_api, args.symbol, tp_order_id)
                                if tp_state == "filled":
                                    entry_p = state.get("entry_price", 0)
                                    if entry_p > 0:
                                        tp_pnl = (current_price - entry_p) / entry_p * 100
                                    else:
                                        tp_pnl = 0
                                    log_message(f"[TP成交] 止盈限价单已成交! 盈亏={tp_pnl:+.2f}%")
                                    state["position"] = 0
                                    state["strategy_size"] = 0.0
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
                            )

                            # 挂单等待 or 已成交 -> 管理 TP
                            if state.get("pending_open"):
                                log_message("[挂单等待] Maker单已挂出，下轮检查成交")
                            elif state.get("position", 0) != 0:
                                state = manage_tp_order(trade_api, args.symbol, tick_sz, lot_sz, strategy, state)

                    # 6. 打印状态
                    print_status(account_api, args.symbol, state)
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
