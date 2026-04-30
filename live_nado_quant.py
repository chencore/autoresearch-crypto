"""
Nado.xyz 去中心化交易所实盘交易脚本。
基于布林带均值回归策略，每 5 分钟获取信号并自动下单（Perp 永续合约）。

Usage:
    # Mainnet 实盘交易
    # 在 .env 中设置 NADO_PRIVATE_KEY="0x_your_private_key_here"
    uv run python live_nado_quant.py --ticker ETH --interval 5m --mainnet --capital 100

    # 只运行一次（调试用）
    uv run python live_nado_quant.py --ticker BTC --interval 5m --mainnet --capital 100 --once

混合费率执行策略:
    - 开仓: POST_ONLY (Maker) — 挂限价单，享Maker低费率
    - 止盈: POST_ONLY (Maker) — 自动挂止盈限价单，价格到达即成交
    - 止损: IOC (Taker) — 必须保证成交，付Taker费率
    - 超时: POST_ONLY (Maker) — 挂限价单平仓
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

from nado_protocol.client import create_nado_client, NadoClientMode
from nado_protocol.utils.subaccount import SubaccountParams
from nado_protocol.engine_client.types import OrderParams
from nado_protocol.engine_client.types.execute import (
    PlaceOrderParams, CancelOrdersParams, CancelProductOrdersParams,
)
from nado_protocol.utils.bytes32 import subaccount_to_hex
from nado_protocol.utils.expiration import get_expiration_timestamp
from nado_protocol.utils.math import from_x18
from nado_protocol.utils.nonce import gen_order_nonce
from nado_protocol.utils.order import build_appendix, OrderType
from nado_protocol.indexer_client.types import IndexerCandlesticksGranularity
from nado_protocol.indexer_client.types.query import IndexerCandlesticksParams

from train_quant import TrendStrategy, ScalpStrategy

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
STATE_FILE = os.path.join(LOG_DIR, "live_nado_state.json")
LOG_FILE = os.path.join(LOG_DIR, f"live_nado_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
LOCK_FILE = os.path.join(LOG_DIR, "live_nado_quant.lock")

GRANULARITY_MAP = {
    "1m": IndexerCandlesticksGranularity.ONE_MINUTE,
    "5m": IndexerCandlesticksGranularity.FIVE_MINUTES,
    "15m": IndexerCandlesticksGranularity.FIFTEEN_MINUTES,
    "1h": IndexerCandlesticksGranularity.ONE_HOUR,
    "4h": IndexerCandlesticksGranularity.FOUR_HOURS,
    "1d": IndexerCandlesticksGranularity.ONE_DAY,
}

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
            print("错误: 已有另一个 live_nado_quant 实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd
except ImportError:
    import fcntl

    def acquire_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            print("错误: 已有另一个 live_nado_quant 实例在运行，请先停止后再启动")
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
# Nado API 封装
# ---------------------------------------------------------------------------

class NadoTrader:
    """Nado DEX 交易接口封装"""

    def __init__(self, subaccount_name="default"):
        private_key = os.environ.get("NADO_PRIVATE_KEY", "")
        if not private_key:
            log_message("错误: 未设置 NADO_PRIVATE_KEY 环境变量")
            sys.exit(1)

        # Nado SDK 目前只支持 MAINNET（DEVNET 缺少 deployment 文件）
        self.client = create_nado_client(NadoClientMode.MAINNET, private_key)
        self.owner = self.client.context.engine_client.signer.address
        self.subaccount_name = subaccount_name
        self.subaccount_params = SubaccountParams(
            subaccount_owner=self.owner,
            subaccount_name=self.subaccount_name,
        )
        self.sender_hex = subaccount_to_hex(self.subaccount_params)
        log_message(f"Nado Mainnet 连接成功 | Address: {self.owner}")

    def get_product_id(self, ticker):
        """根据 ticker (如 ETH) 查找 product_id"""
        symbols = self.client.market.get_all_product_symbols()
        target = f"{ticker.upper()}-PERP"
        for symbol in symbols:
            symbol_str = symbol.symbol if hasattr(symbol, 'symbol') else str(symbol)
            if symbol_str == target:
                pid = symbol.product_id if hasattr(symbol, 'product_id') else None
                if pid is not None:
                    log_message(f"找到 {target} product_id={pid}")
                    return pid
        log_message(f"错误: 未找到 {target} 市场")
        return None

    def get_tick_size(self, product_id):
        """获取 tick_size"""
        all_markets = self.client.market.get_all_engine_markets()
        markets = all_markets.perp_products
        for market in markets:
            if market.product_id == product_id:
                tick_x18 = market.book_info.price_increment_x18
                return Decimal(str(from_x18(tick_x18)))
        return Decimal("0.01")

    def get_size_increment(self, product_id):
        """获取最小下单数量增量"""
        all_markets = self.client.market.get_all_engine_markets()
        markets = all_markets.perp_products
        for market in markets:
            if market.product_id == product_id:
                return float(from_x18(int(market.book_info.size_increment)))
        return 0.1

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_latest_price(self, product_id):
        """获取最新中间价"""
        price_data = self.client.market.get_latest_market_price(product_id=product_id)
        if price_data:
            bid_x18 = getattr(price_data, 'bid_x18', None)
            ask_x18 = getattr(price_data, 'ask_x18', None)
            if bid_x18 and ask_x18:
                bid = float(from_x18(bid_x18))
                ask = float(from_x18(ask_x18))
                return (bid + ask) / 2
        return None

    @retry_on_exception(max_retries=3, delay=1.0)
    def fetch_candles(self, product_id, granularity=IndexerCandlesticksGranularity.FIVE_MINUTES, limit=300):
        """获取 K 线数据"""
        params = IndexerCandlesticksParams(
            product_id=product_id,
            granularity=granularity,
            limit=limit,
        )
        candles_data = self.client.market.get_candlesticks(params)
        candles = candles_data.candlesticks if hasattr(candles_data, 'candlesticks') else candles_data
        if not candles:
            return None

        records = []
        for c in candles:
            ts = int(getattr(c, 'timestamp', 0))
            o = float(from_x18(getattr(c, 'open_x18', 0)))
            h = float(from_x18(getattr(c, 'high_x18', 0)))
            l = float(from_x18(getattr(c, 'low_x18', 0)))
            cl = float(from_x18(getattr(c, 'close_x18', 0)))
            vol = float(from_x18(getattr(c, 'volume', 0)))
            records.append({
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": cl,
                "volume": vol,
                "datetime": pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=8),
            })

        df = pd.DataFrame(records)
        # 按 timestamp 升序排列（API 返回可能是倒序）
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_orderbook(self, product_id, depth=5):
        """获取订单簿，返回 (best_bid, best_ask) 浮点数"""
        liquidity = self.client.market.get_market_liquidity(product_id=product_id, depth=depth)
        if not liquidity:
            return None, None
        bids = liquidity.bids if hasattr(liquidity, 'bids') else []
        asks = liquidity.asks if hasattr(liquidity, 'asks') else []
        best_bid = float(from_x18(bids[0][0])) if bids else None
        best_ask = float(from_x18(asks[0][0])) if asks else None
        return best_bid, best_ask

    def get_position(self, product_id):
        """获取当前永续合约持仓（正=多, 负=空, 0=无）"""
        try:
            account_data = self.client.subaccount.get_engine_subaccount_summary(self.sender_hex)
            position_data = account_data.perp_balances
            for position in position_data:
                if position.product_id == product_id:
                    amount = position.balance.amount
                    return float(from_x18(amount))
            return 0.0
        except Exception as e:
            log_message(f"获取持仓失败: {e}")
            return 0.0

    def get_open_orders(self, product_id):
        """获取当前挂单"""
        try:
            orders_data = self.client.market.get_subaccount_open_orders(
                product_id=product_id,
                sender=self.sender_hex,
            )
            if not orders_data:
                return []
            order_list = orders_data if isinstance(orders_data, list) else getattr(orders_data, 'orders', [])
            return order_list
        except Exception as e:
            log_message(f"获取挂单失败: {e}")
            return []

    def cancel_all_orders(self, product_id):
        """取消指定 product 的所有挂单"""
        try:
            self.client.market.cancel_product_orders(CancelProductOrdersParams(
                productIds=[product_id],
                sender=self.subaccount_params,
            ))
            log_message(f"已取消所有挂单: product_id={product_id}")
            return True
        except Exception as e:
            log_message(f"取消挂单失败: {e}")
            return False

    def cancel_order_by_digest(self, product_id, digest):
        """取消指定订单"""
        try:
            self.client.market.cancel_orders(CancelOrdersParams(
                productIds=[product_id],
                digests=[digest],
                sender=self.subaccount_params,
            ))
            return True
        except Exception as e:
            log_message(f"取消订单失败: {e}")
            return False

    def place_order(self, product_id, side, size, price, order_type=OrderType.POST_ONLY, expire_seconds=3600):
        """
        下单
        side: "buy" / "sell"
        size: 数量 (正数)
        price: 价格 (浮点数)
        """
        try:
            # 使用 Decimal 避免浮点精度问题，确保 amount_x18 能被链上 size_increment 整除
            size_dec = Decimal(str(size))
            price_dec = Decimal(str(price))
            amount_x18 = int(size_dec * (Decimal(10) ** 18))
            if side == "sell":
                amount_x18 = -amount_x18
            price_x18 = int(price_dec * (Decimal(10) ** 18))

            order = OrderParams(
                sender=self.subaccount_params,
                priceX18=price_x18,
                amount=amount_x18,
                expiration=get_expiration_timestamp(expire_seconds),
                nonce=gen_order_nonce(),
                appendix=build_appendix(order_type=order_type),
            )

            result = self.client.market.place_order(PlaceOrderParams(
                product_id=product_id,
                order=order,
            ))

            digest = result.data.digest if result and hasattr(result, 'data') and hasattr(result.data, 'digest') else None
            log_message(f"下单成功 [{side.upper()}] product_id={product_id} price={price:.2f} size={size:.6f} digest={digest}")
            return digest
        except Exception as e:
            error_str = str(e)
            if ("error_code:2008" in error_str or "crosses the book" in error_str) and order_type == OrderType.POST_ONLY:
                log_message(f"[POST_ONLY降级] error_code:2008，自动重试 IOC")
                tick = float(self.get_tick_size(product_id))
                if side == "buy":
                    new_price = price + tick
                else:
                    new_price = price - tick
                # 对齐 tick_size
                ticks = round(new_price / tick)
                new_price = float(Decimal(ticks) * Decimal(str(tick)))
                return self.place_order(product_id, side, size, new_price, order_type=OrderType.IOC, expire_seconds=60)
            import traceback
            log_message(f"下单失败: {e}")
            log_message(traceback.format_exc())
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
# 交易执行
# ---------------------------------------------------------------------------

def round_to_tick(price, tick_size):
    """将价格对齐到 tick_size，避免浮点精度问题"""
    ticks = round(float(price) / float(tick_size))
    return float(Decimal(ticks) * Decimal(str(tick_size)))


def compute_order_price(side, best_bid, best_ask, tick_size):
    """计算 POST_ONLY 限价单价格（挂在 best bid/ask 提高成交率），对齐到 tick_size"""
    if side == "buy":
        # 买单挂在 best_bid（排队第一档），防止穿越盘口
        price = best_bid
        if price >= best_ask:
            price = best_ask - float(tick_size)
    else:
        # 卖单挂在 best_ask（排队第一档），防止穿越盘口
        price = best_ask
        if price <= best_bid:
            price = best_bid + float(tick_size)
    return round_to_tick(price, tick_size)


def compute_ioc_price(side, best_bid, best_ask, tick_size):
    """计算 IOC 单价格（穿越盘口确保成交），对齐到 tick_size"""
    if side == "buy":
        # 买入价格略高于 best_ask 确保吃单成交
        price = best_ask + float(tick_size) * 2 if best_ask else None
    else:
        # 卖出价格略低于 best_bid 确保吃单成交
        price = best_bid - float(tick_size) * 2 if best_bid else None
    if price is not None:
        price = round_to_tick(price, tick_size)
    return price


def execute_trade(signal_id, trader, product_id, tick_size, size_increment, capital_per_trade, state, current_price, best_bid, best_ask, force_ioc=False):
    """
    根据信号执行交易
    signal_id: 0=平仓, 1=持有, 2=做多, 3=做空
    force_ioc: True=强制用IOC(兜底模式), False=先尝试Maker挂单

    下单策略:
    - 计算订单名义价值，若 >= min_size (100 USDT)，优先使用 POST_ONLY 做挂单（省手续费）
    - 若 < min_size，直接用 IOC 吃单（Nado 只对挂单检查 min_size，IOC 不受此限制）
    - 所有价格严格对齐到 tick_size，避免浮点精度错误
    """
    # pending_close 期间完全跳过交易执行，防止重复平仓或状态混乱
    if state.get("pending_close"):
        log_message("[交易] pending_close 进行中，跳过交易执行")
        state["last_signal"] = signal_id
        return state

    position = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    trades = state.get("trades", [])
    MIN_NOTIONAL = 100.0  # Nado min_size: 100 USDT

    log_message(f"[交易] signal={signal_id} position={position} target计算中...")

    # 同步实际持仓到 state（POST_ONLY 订单可能未成交）
    actual_position = trader.get_position(product_id)
    if abs(actual_position) < size_increment * 0.5:
        if position != 0:
            log_message(f"[持仓同步] 实际持仓为0，重置状态 (原state: {position})")
            state["position"] = 0
            state["strategy_size"] = 0.0
            state["entry_bar"] = 0
            state["entry_price"] = 0.0
            state["confirmed_entry_bar"] = 0
            state["confirmed_entry_price"] = 0.0
            position = 0
            strategy_size = 0.0
    else:
        actual_dir = 1 if actual_position > 0 else -1
        if position != actual_dir:
            log_message(f"[持仓同步] 修正持仓方向: state={position} -> actual={actual_dir}")
            state["position"] = actual_dir
            state["strategy_size"] = abs(actual_position)
            position = actual_dir
            strategy_size = abs(actual_position)
            # 当 state 为 0 但实际有持仓时（如 Maker 单成交未检测到），
            # 不应恢复旧的 confirmed_entry_bar，否则时间退出计数器会继续累加。
            # 方向反转时也不应恢复旧值（如从多头修正为空头）。
            # 只有同方向修正时才恢复 confirmed_entry_bar。
            old_pos = 1 if state.get("position", 0) == 1 else (-1 if state.get("position", 0) == -1 else 0)
            if old_pos == 0:
                # 发现新持仓：重置 entry_bar/entry_price 为当前值
                state["entry_bar"] = max(0, state.get("bar_count", 0) - 1)
                state["entry_price"] = current_price
                state["confirmed_entry_bar"] = state["entry_bar"]
                state["confirmed_entry_price"] = state["entry_price"]
                log_message(f"[持仓同步] 发现新持仓，重置 entry_bar={state['entry_bar']}, entry_price={state['entry_price']:.2f}")
            elif old_pos != actual_dir:
                # 方向反转保护：使用当前值，避免旧数据污染
                state["entry_bar"] = max(0, state.get("bar_count", 0) - 1)
                state["entry_price"] = current_price
                state["confirmed_entry_bar"] = state["entry_bar"]
                state["confirmed_entry_price"] = state["entry_price"]
                log_message(f"[持仓同步] 方向反转保护，重置 entry_bar={state['entry_bar']}, entry_price={state['entry_price']:.2f}")
            elif state.get("confirmed_entry_bar", 0) > 0:
                # 同方向修正，恢复已确认值
                state["entry_bar"] = state["confirmed_entry_bar"]
                state["entry_price"] = state["confirmed_entry_price"]

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
        log_message(f"[交易] 无需换仓，跳过")
        state["last_signal"] = signal_id
        return state

    # 需要换仓，先取消所有现有挂单
    trader.cancel_all_orders(product_id)

    # --- 平掉当前仓位 ---
    # 判断盈亏以决定订单类型: 盈利→Maker(POST_ONLY), 亏损→Taker(IOC)
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
        # 平多仓（卖出）
        if strategy_size > 0:
            actual_position = trader.get_position(product_id)
            sell_size = min(strategy_size, abs(actual_position)) if actual_position > 0 else strategy_size
            if sell_size > 0:
                n_units = int(Decimal(str(sell_size)) / Decimal(str(size_increment)))
                sell_size = float(n_units * Decimal(str(size_increment)))
            if sell_size > 0:
                notional = sell_size * current_price
                if close_as_maker:
                    # 盈利平仓 → Maker (POST_ONLY)
                    if best_bid and best_ask:
                        order_price = compute_order_price("sell", best_bid, best_ask, tick_size)
                    else:
                        order_price = round_to_tick(current_price * 0.999, tick_size)
                    if order_price:
                        digest = trader.place_order(
                            product_id=product_id, side="sell", size=sell_size,
                            price=order_price, order_type=OrderType.POST_ONLY, expire_seconds=60,
                        )
                else:
                    # 亏损平仓 → Taker (IOC)
                    if best_bid and best_ask:
                        order_price = compute_ioc_price("sell", best_bid, best_ask, tick_size)
                    else:
                        order_price = round_to_tick(current_price * 0.999, tick_size)
                    if order_price:
                        digest = trader.place_order(
                            product_id=product_id, side="sell", size=sell_size,
                            price=order_price, order_type=OrderType.IOC, expire_seconds=60,
                        )
                if digest:
                    trade_type = f"CLOSE_LONG_{close_type_str}"
                    trades.append({
                        "time": datetime.now().isoformat(), "type": trade_type,
                        "product_id": product_id, "digest": digest,
                        "size": sell_size, "price": order_price,
                        "reason": f"pnl={pnl_pct*100:+.2f}%",
                    })
                    log_message(f"[平多{close_type_str}] pnl={pnl_pct*100:+.2f}% 下单卖出 size={sell_size:.6f} price={order_price:.2f}")
                    if close_as_maker:
                        # POST_ONLY 平仓单不保证立即成交，设置 pending_close 等待链上确认
                        state["pending_close"] = True
                        state["pending_close_bar"] = state.get("bar_count", 0)
                        state["pending_close_attempts"] = 0
                        log_message("[平多Maker] 平仓单已挂出，设置 pending_close 等待链上确认")
                    else:
                        # IOC 平仓假设立即成交，直接清零
                        state["position"] = 0
                        state["strategy_size"] = 0.0
                        state["entry_bar"] = 0
                        state["entry_price"] = 0.0
                        state["confirmed_entry_bar"] = 0
                        state["confirmed_entry_price"] = 0.0
                else:
                    log_message(f"[平多失败] {close_type_str}单未成交")

    elif position == -1 and target_pos >= 0:
        # 平空仓（买入）
        if strategy_size > 0:
            actual_position = trader.get_position(product_id)
            close_size = min(strategy_size, abs(actual_position)) if actual_position < 0 else strategy_size
            if close_size > 0:
                n_units = int(Decimal(str(close_size)) / Decimal(str(size_increment)))
                close_size = float(n_units * Decimal(str(size_increment)))
            if close_size > 0:
                notional = close_size * current_price
                if close_as_maker:
                    # 盈利平仓 → Maker (POST_ONLY)
                    if best_bid and best_ask:
                        order_price = compute_order_price("buy", best_bid, best_ask, tick_size)
                    else:
                        order_price = round_to_tick(current_price * 1.001, tick_size)
                    if order_price:
                        digest = trader.place_order(
                            product_id=product_id, side="buy", size=close_size,
                            price=order_price, order_type=OrderType.POST_ONLY, expire_seconds=60,
                        )
                else:
                    # 亏损平仓 → Taker (IOC)
                    if best_bid and best_ask:
                        order_price = compute_ioc_price("buy", best_bid, best_ask, tick_size)
                    else:
                        order_price = round_to_tick(current_price * 1.001, tick_size)
                    if order_price:
                        digest = trader.place_order(
                            product_id=product_id, side="buy", size=close_size,
                            price=order_price, order_type=OrderType.IOC, expire_seconds=60,
                        )
                if digest:
                    trade_type = f"CLOSE_SHORT_{close_type_str}"
                    trades.append({
                        "time": datetime.now().isoformat(), "type": trade_type,
                        "product_id": product_id, "digest": digest,
                        "size": close_size, "price": order_price,
                        "reason": f"pnl={pnl_pct*100:+.2f}%",
                    })
                    log_message(f"[平空{close_type_str}] pnl={pnl_pct*100:+.2f}% 下单买入 size={close_size:.6f} price={order_price:.2f}")
                    if close_as_maker:
                        # POST_ONLY 平仓单不保证立即成交，设置 pending_close 等待链上确认
                        state["pending_close"] = True
                        state["pending_close_bar"] = state.get("bar_count", 0)
                        state["pending_close_attempts"] = 0
                        log_message("[平空Maker] 平仓单已挂出，设置 pending_close 等待链上确认")
                    else:
                        # IOC 平仓假设立即成交，直接清零
                        state["position"] = 0
                        state["strategy_size"] = 0.0
                        state["entry_bar"] = 0
                        state["entry_price"] = 0.0
                        state["confirmed_entry_bar"] = 0
                        state["confirmed_entry_price"] = 0.0
                else:
                    log_message(f"[平空失败] {close_type_str}单未成交")

    # --- 开新仓 ---
    log_message(f"[交易] 检查开仓: target_pos={target_pos} state_position={state.get('position', 0)} force_ioc={force_ioc}")
    if target_pos == 1 and state.get("position", 0) == 0:
        if current_price <= 0:
            log_message("[跳过开多] 价格无效")
        else:
            raw_size = Decimal(str(capital_per_trade)) / Decimal(str(current_price))
            n_units = int(raw_size / Decimal(str(size_increment)))
            order_size = float(n_units * Decimal(str(size_increment)))
            if order_size > 0:
                notional = order_size * current_price
                if force_ioc:
                    # IOC 兜底模式 (Taker) — 保证成交
                    if best_bid and best_ask:
                        order_price = compute_ioc_price("buy", best_bid, best_ask, tick_size)
                    else:
                        order_price = round_to_tick(current_price * 1.001, tick_size)
                    if order_price:
                        digest = trader.place_order(
                            product_id=product_id, side="buy", size=order_size,
                            price=order_price, order_type=OrderType.IOC, expire_seconds=60,
                        )
                        if digest:
                            trades.append({"time": datetime.now().isoformat(), "type": "BUY_OPEN_IOC_FALLBACK",
                                           "product_id": product_id, "digest": digest,
                                           "size": order_size, "price": order_price})
                            state["position"] = 1
                            state["strategy_size"] = order_size
                            state["entry_price"] = current_price
                            state["entry_bar"] = state.get("bar_count", 0)
                            state["confirmed_entry_price"] = current_price
                            state["confirmed_entry_bar"] = state.get("bar_count", 0)
                            log_message(f"[开多IOC兜底] size={order_size:.6f} price={order_price:.2f}")
                        else:
                            log_message("[开多IOC失败] 兜底单也未成交")
                else:
                    # POST_ONLY 挂单模式 (Maker) — 不立即设 position
                    if best_bid and best_ask:
                        maker_price = compute_order_price("buy", best_bid, best_ask, tick_size)
                    else:
                        maker_price = round_to_tick(current_price * 0.999, tick_size)
                    if maker_price:
                        digest = trader.place_order(
                            product_id=product_id, side="buy", size=order_size,
                            price=maker_price, order_type=OrderType.POST_ONLY, expire_seconds=300,
                        )
                        if digest:
                            trades.append({"time": datetime.now().isoformat(), "type": "BUY_OPEN_MAKER",
                                           "product_id": product_id, "digest": digest,
                                           "size": order_size, "price": maker_price})
                            state["pending_open"] = True
                            state["pending_open_signal"] = 2
                            state["pending_open_price"] = current_price
                            state["pending_open_size"] = order_size
                            log_message(f"[开多Maker] 挂单买入 size={order_size:.6f} price={maker_price:.2f} notional={notional:.2f}")
                        else:
                            log_message("[开多失败] POST_ONLY单被拒绝")

    elif target_pos == -1 and state.get("position", 0) == 0:
        if current_price <= 0:
            log_message("[跳过开空] 价格无效")
        else:
            raw_size = Decimal(str(capital_per_trade)) / Decimal(str(current_price))
            n_units = int(raw_size / Decimal(str(size_increment)))
            order_size = float(n_units * Decimal(str(size_increment)))
            if order_size > 0:
                notional = order_size * current_price
                if force_ioc:
                    # IOC 兜底模式 (Taker)
                    if best_bid and best_ask:
                        order_price = compute_ioc_price("sell", best_bid, best_ask, tick_size)
                    else:
                        order_price = round_to_tick(current_price * 0.999, tick_size)
                    if order_price:
                        digest = trader.place_order(
                            product_id=product_id, side="sell", size=order_size,
                            price=order_price, order_type=OrderType.IOC, expire_seconds=60,
                        )
                        if digest:
                            trades.append({"time": datetime.now().isoformat(), "type": "SELL_SHORT_IOC_FALLBACK",
                                           "product_id": product_id, "digest": digest,
                                           "size": order_size, "price": order_price})
                            state["position"] = -1
                            state["strategy_size"] = order_size
                            state["entry_price"] = current_price
                            state["entry_bar"] = state.get("bar_count", 0)
                            state["confirmed_entry_price"] = current_price
                            state["confirmed_entry_bar"] = state.get("bar_count", 0)
                            log_message(f"[开空IOC兜底] size={order_size:.6f} price={order_price:.2f}")
                        else:
                            log_message("[开空IOC失败] 兜底单也未成交")
                else:
                    # POST_ONLY 挂单模式 (Maker)
                    if best_bid and best_ask:
                        maker_price = compute_order_price("sell", best_bid, best_ask, tick_size)
                    else:
                        maker_price = round_to_tick(current_price * 1.001, tick_size)
                    if maker_price:
                        digest = trader.place_order(
                            product_id=product_id, side="sell", size=order_size,
                            price=maker_price, order_type=OrderType.POST_ONLY, expire_seconds=300,
                        )
                        if digest:
                            trades.append({"time": datetime.now().isoformat(), "type": "SELL_SHORT_MAKER",
                                           "product_id": product_id, "digest": digest,
                                           "size": order_size, "price": maker_price})
                            state["pending_open"] = True
                            state["pending_open_signal"] = 3
                            state["pending_open_price"] = current_price
                            state["pending_open_size"] = order_size
                            log_message(f"[开空Maker] 挂单卖出 size={order_size:.6f} price={maker_price:.2f} notional={notional:.2f}")
                        else:
                            log_message("[开空失败] POST_ONLY单被拒绝")

    state["trades"] = trades
    state["last_signal"] = signal_id
    state["last_update"] = datetime.now().isoformat()
    # 平仓后清理 TP 状态
    if state.get("position", 0) == 0:
        state["tp_digest"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
    return state


def manage_tp_order(trader, product_id, tick_size, size_increment, strategy, state):
    """
    管理止盈限价单 (Maker)。
    当持仓存在且无 TP 单时，自动挂止盈限价单。
    """
    pos = state.get("position", 0)
    entry_price = state.get("entry_price", 0)
    size = state.get("strategy_size", 0)
    tp_digest = state.get("tp_digest")

    if pos == 0 or entry_price == 0 or size == 0:
        if tp_digest:
            trader.cancel_order_by_digest(product_id, tp_digest)
            state["tp_digest"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
        return state

    # 计算止盈价格
    if pos == 1:
        tp_price = round_to_tick(entry_price * (1 + strategy.take_profit_pct), tick_size)
        tp_side = "sell"
    else:
        tp_price = round_to_tick(entry_price * (1 - strategy.take_profit_pct), tick_size)
        tp_side = "buy"

    # 已有正确的 TP 单则跳过
    if tp_digest and state.get("tp_price") == tp_price and state.get("tp_side") == tp_side:
        return state

    # 取消旧 TP 单
    if tp_digest:
        trader.cancel_order_by_digest(product_id, tp_digest)
        state["tp_digest"] = None

    # 对齐 size
    n_units = int(Decimal(str(size)) / Decimal(str(size_increment)))
    order_size = float(n_units * Decimal(str(size_increment)))
    if order_size <= 0:
        return state

    # 挂止盈限价单 (POST_ONLY = Maker)
    digest = trader.place_order(
        product_id=product_id, side=tp_side, size=order_size,
        price=tp_price, order_type=OrderType.POST_ONLY, expire_seconds=3600 * 4,
    )
    if digest:
        state["tp_digest"] = digest
        state["tp_price"] = tp_price
        state["tp_side"] = tp_side
        log_message(f"[TP挂单] {tp_side.upper()} size={order_size:.6f} @ {tp_price:.2f} (入场={entry_price:.2f}, TP={strategy.take_profit_pct*100:.1f}%)")

    return state


def cancel_tp_order(trader, product_id, state):
    """取消止盈限价单"""
    tp_digest = state.get("tp_digest")
    if tp_digest:
        trader.cancel_order_by_digest(product_id, tp_digest)
        state["tp_digest"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
    return state


def check_stop_loss(state, current_price, kline_low=None, kline_high=None, stop_loss_pct=0.03, max_hold_bars=48):
    """检查止损和时间退出条件（支持多空双向）"""
    pos = state.get("position", 0)
    if pos == 0:
        return False, ""

    # pending_close 期间跳过止损检查，避免重复触发
    if state.get("pending_close"):
        return False, ""

    entry_price = state.get("entry_price", 0)
    # 优先使用已确认的 entry_bar，若不存在则回退到 entry_bar
    entry_bar = state.get("confirmed_entry_bar", 0) or state.get("entry_bar", 0)
    current_bar = state.get("bar_count", 0)

    if pos == 1:
        # 多头止损：优先使用 K线 low 捕捉K线内穿仓，再用 close 价兜底
        if entry_price > 0:
            stop_price = entry_price * (1 - stop_loss_pct)
            if kline_low is not None and kline_low <= stop_price:
                return True, f"多头止损: K线low={kline_low:.2f} <= 止损价={stop_price:.2f}"
            if (entry_price - current_price) / entry_price >= stop_loss_pct:
                return True, f"多头止损: 跌幅 {(entry_price - current_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%"
    elif pos == -1:
        # 空头止损：优先使用 K线 high 捕捉K线内穿仓，再用 close 价兜底
        if entry_price > 0:
            stop_price = entry_price * (1 + stop_loss_pct)
            if kline_high is not None and kline_high >= stop_price:
                return True, f"空头止损: K线high={kline_high:.2f} >= 止损价={stop_price:.2f}"
            if (current_price - entry_price) / entry_price >= stop_loss_pct:
                return True, f"空头止损: 涨幅 {(current_price - entry_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%"

    # 异常保护：若 entry_bar 丢失或计算异常，跳过时间退出判断
    bars_held = current_bar - entry_bar
    if entry_bar > 0 and bars_held > max_hold_bars * 2:
        log_message(f"[异常报警] 持仓 K 线数 {bars_held} 超过 2*max_hold={max_hold_bars*2}，跳过时间退出判断")
        return False, ""

    if bars_held >= max_hold_bars:
        pos_name = "多头" if pos == 1 else "空头"
        return True, f"{pos_name}时间退出: 持仓 {bars_held} 根K线 >= {max_hold_bars}"

    return False, ""


def force_close(trader, product_id, state, current_price, best_bid, best_ask, tick_size, size_increment, reason, use_maker=False):
    """
    强制平仓。
    use_maker=False: IOC (Taker) — 止损场景，必须保证成交
    use_maker=True: POST_ONLY (Maker) — 超时场景，可挂单等成交
    """
    pos = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    if pos == 0 or strategy_size <= 0:
        return state

    # 取消 TP 单和所有挂单
    cancel_tp_order(trader, product_id, state)
    trader.cancel_all_orders(product_id)

    actual_position = trader.get_position(product_id)
    close_size = min(strategy_size, abs(actual_position)) if abs(actual_position) > 0 else strategy_size
    if close_size > 0:
        n_units = int(Decimal(str(close_size)) / Decimal(str(size_increment)))
        close_size = float(n_units * Decimal(str(size_increment)))
    if close_size <= 0:
        log_message("[强制平仓] 平仓数量为0，跳过")
        return state

    # 确定平仓方向
    if pos == 1:
        side = "sell"
    elif pos == -1:
        side = "buy"
    else:
        return state

    # 根据场景选择订单类型
    if use_maker:
        order_type = OrderType.POST_ONLY
        if best_bid and best_ask:
            order_price = compute_order_price(side, best_bid, best_ask, tick_size)
        elif current_price > 0:
            order_price = round_to_tick(current_price * (0.999 if side == "sell" else 1.001), tick_size)
        else:
            log_message("[强制平仓] 无有效价格，跳过")
            return state
        fee_label = "Maker"
    else:
        order_type = OrderType.IOC
        if best_bid and best_ask:
            order_price = compute_ioc_price(side, best_bid, best_ask, tick_size)
        elif current_price > 0:
            order_price = round_to_tick(current_price * (0.99 if side == "sell" else 1.01), tick_size)
        else:
            log_message("[强制平仓] 无有效价格，跳过")
            return state
        fee_label = "Taker"

    digest = trader.place_order(
        product_id=product_id, side=side, size=close_size,
        price=order_price, order_type=order_type, expire_seconds=60,
    )

    if digest:
        pos_name = "多头" if pos == 1 else "空头"
        trades = state.get("trades", [])
        trades.append({
            "time": datetime.now().isoformat(),
            "type": f"FORCE_CLOSE_{fee_label}",
            "product_id": product_id,
            "digest": digest,
            "size": close_size,
            "price": order_price,
            "reason": reason,
        })
        state["trades"] = trades
        if use_maker:
            # POST_ONLY 订单被接受不代表已成交，设置 pending_close 等待链上确认
            # 保留 position/entry_bar/entry_price，避免 execute_trade 中"修正持仓方向"时恢复旧值导致重复平仓
            state["pending_close"] = True
            state["pending_close_bar"] = state.get("bar_count", 0)
            state["pending_close_attempts"] = 0
            state["last_signal"] = 0
            log_message(f"[强制平仓-{fee_label}] {reason}，{pos_name}{side} {close_size:.6f} @ {order_price:.2f}，等待链上确认...")
        else:
            # IOC 订单假设立即成交，直接清零 state
            state["position"] = 0
            state["strategy_size"] = 0.0
            state["entry_bar"] = 0
            state["entry_price"] = 0.0
            state["confirmed_entry_bar"] = 0
            state["confirmed_entry_price"] = 0.0
            state["last_signal"] = 0
            state["tp_digest"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
            log_message(f"[强制平仓-{fee_label}] {reason}，{pos_name}{side} {close_size:.6f} @ {order_price:.2f}")

    state["last_update"] = datetime.now().isoformat()
    return state


def print_status(trader, product_id, ticker, state):
    """打印账户状态"""
    try:
        position = trader.get_position(product_id)
        price = trader.get_latest_price(product_id)
        open_orders = trader.get_open_orders(product_id)

        signal_names = {0: "平仓", 1: "持有", 2: "做多 (BUY)", 3: "做空 (SELL)"}
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

        # 计算未实现盈亏（仅在实际持仓时）
        entry_price = state.get("entry_price", 0)
        strategy_size = state.get("strategy_size", 0)
        unrealized_pnl = 0.0
        actual_has_pos = abs(position) > 0
        if actual_has_pos and entry_price > 0 and price:
            if position > 0:
                unrealized_pnl = (price - entry_price) * abs(position)
            else:
                unrealized_pnl = (entry_price - price) * abs(position)

        log_message("-" * 50)
        log_message(f"当前信号: {signal_str}")
        log_message(f"持仓状态: {pos_str}")
        if state.get("pending_open"):
            log_message(f"挂单方向: {'做多' if state.get('pending_open_signal') == 2 else '做空'}")
            log_message(f"挂单价格: {state.get('pending_open_price', 0):.2f}")
        log_message(f"实际持仓: {position:.6f} {ticker}")
        if price:
            log_message(f"最新价格: {price:.2f}")
        if actual_has_pos and entry_price > 0:
            log_message(f"入场价格: {entry_price:.2f}")
            log_message(f"未实现盈亏: {unrealized_pnl:.2f} USDT")
        log_message(f"挂单数量: {len(open_orders)}")
        # 交易次数只统计有 pnl 的记录（真实成交）
        real_trades = [t for t in state.get("trades", []) if "pnl" in t or "reason" in t]
        log_message(f"交易次数: {len(real_trades)} (挂单记录: {len(state.get('trades', []))})")
        log_message("-" * 50)
    except Exception as e:
        log_message(f"状态打印失败: {e}")


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    lock_fd = acquire_lock()
    parser = argparse.ArgumentParser(description="Nado.xyz 布林带永续合约交易")
    parser.add_argument("--ticker", type=str, default="ETH", help="标的资产，如 BTC, ETH, SOL")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1h, 4h, 1d")
    parser.add_argument("--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径")
    parser.add_argument("--capital", type=float, default=100.0, help="每次交易保证金（USDT）")
    parser.add_argument("--leverage", type=float, default=1.0, help="杠杆倍数，实际下单=capital×leverage")
    parser.add_argument("--mainnet", action="store_true", default=True, help="Mainnet（默认）")
    parser.add_argument("--once", action="store_true", help="只运行一次然后退出")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损百分比（默认使用策略参数）")
    parser.add_argument("--max-hold", type=int, default=None, help="最大持仓K线数（默认使用策略参数）")
    parser.add_argument("--short", action="store_true", default=True, help="启用做空（默认开启）")
    parser.add_argument("--long-only", action="store_true", help="只做多，不做空")
    args = parser.parse_args()

    interval_seconds = INTERVAL_SECONDS_MAP.get(args.interval, 300)
    granularity = GRANULARITY_MAP.get(args.interval, IndexerCandlesticksGranularity.FIVE_MINUTES)

    # 加载策略参数
    log_message("=" * 50)
    enable_short = not args.long_only
    mode_str = "多空双向" if enable_short else "只做多"
    log_message(f"启动 Nado Mainnet 实盘交易 ({mode_str})")
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
        take_profit_pct=params.get("take_profit_pct", 0.05),
        stop_loss_pct=params.get("stop_loss_pct", args.stop_loss if args.stop_loss is not None else 0.03),
        # 多指标扩展参数
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
        # 背离指标
        use_rsi_divergence=params.get("use_rsi_divergence", False),
        rsi_divergence_lookback=params.get("rsi_divergence_lookback", 5),
        use_macd_divergence=params.get("use_macd_divergence", False),
        macd_divergence_lookback=params.get("macd_divergence_lookback", 5),
        # 趋势过滤
        use_trend_filter=params.get("use_trend_filter", False),
        trend_window=params.get("trend_window", 50),
        # P5: 成交量因子
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
        log_message(f"策略参数: 周期={strategy.window}, 标准差={strategy.std_dev}, "
                    f"ATR止损={strategy.atr_multiplier}, 最大持仓={strategy.max_hold_bars}根K线, "
                    f"RSI阈值={strategy.rsi_threshold}, 活跃指标=[{indicators_str}]")

    # 初始化 Nado 客户端
    trader = NadoTrader()

    # 查找 product_id 和 tick_size
    product_id = trader.get_product_id(args.ticker)
    if product_id is None:
        log_message("无法找到交易对，退出")
        sys.exit(1)

    tick_size = trader.get_tick_size(product_id)
    size_increment = trader.get_size_increment(product_id)
    log_message(f"Product ID: {product_id}, Tick Size: {tick_size}, Size Increment: {size_increment}")

    # 加载或初始化状态
    state = load_state()
    if state is None:
        price = trader.get_latest_price(product_id)
        state = {
            "ticker": args.ticker,
            "product_id": product_id,
            "interval": args.interval,
            "initial_capital": args.capital,
            "position": 0,
            "strategy_size": 0.0,
            "trades": [],
            "last_signal": 1,
            "last_price": price or 0,
            "bar_count": 0,
            "entry_price": 0.0,
            "entry_bar": 0,
            "confirmed_entry_price": 0.0,
            "confirmed_entry_bar": 0,
            "tp_digest": None,
            "tp_price": 0.0,
            "tp_side": None,
            "pending_open": False,
            "pending_open_signal": 0,
            "pending_open_price": 0.0,
            "pending_open_size": 0.0,
            "pending_close": False,
            "pending_close_bar": 0,
            "pending_close_attempts": 0,
        }
        log_message(f"初始化状态，保证金: {args.capital:.2f} USDT, 杠杆: {args.leverage}x, 实际下单: {args.capital * args.leverage:.2f} USDT")
    else:
        log_message("恢复上一次交易状态")
        state.setdefault("strategy_size", 0.0)
        state.setdefault("bar_count", 0)
        state.setdefault("entry_price", 0.0)
        state.setdefault("entry_bar", 0)
        state.setdefault("confirmed_entry_price", 0.0)
        state.setdefault("confirmed_entry_bar", 0)
        state.setdefault("last_price", 0)
        state.setdefault("product_id", product_id)
        state.setdefault("tp_digest", None)
        state.setdefault("tp_price", 0.0)
        state.setdefault("tp_side", None)
        state.setdefault("pending_open", False)
        state.setdefault("pending_open_signal", 0)
        state.setdefault("pending_open_price", 0.0)
        state.setdefault("pending_open_size", 0.0)
        state.setdefault("pending_close", False)
        state.setdefault("pending_close_bar", 0)
        state.setdefault("pending_close_attempts", 0)

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
                # 0. 同步实际持仓（防止过期 state 导致误判）
                actual_pos = trader.get_position(product_id)
                stale_pos = state.get("position", 0)

                # === 处理 pending_close ===
                if state.get("pending_close"):
                    if abs(actual_pos) < size_increment * 0.5:
                        log_message("[pending_close] 平仓链上确认成功")
                        state["position"] = 0
                        state["strategy_size"] = 0.0
                        state["entry_bar"] = 0
                        state["entry_price"] = 0.0
                        state["confirmed_entry_bar"] = 0
                        state["confirmed_entry_price"] = 0.0
                        state["pending_close"] = False
                        state["pending_close_attempts"] = 0
                        state["tp_digest"] = None
                        state["tp_price"] = 0.0
                        state["tp_side"] = None
                        state["last_signal"] = 0
                    else:
                        attempts = state.get("pending_close_attempts", 0) + 1
                        state["pending_close_attempts"] = attempts
                        if attempts >= 3:
                            log_message(f"[pending_close] 第 {attempts} 轮仍未平仓，切换 IOC 兜底")
                            state["pending_close"] = False
                            state["pending_close_attempts"] = 0
                            latest_price = trader.get_latest_price(product_id) or 0
                            state = force_close(trader, product_id, state,
                                                latest_price, None, None, tick_size, size_increment,
                                                "pending_close IOC 兜底", use_maker=False)

                if abs(actual_pos) < size_increment * 0.5 and stale_pos != 0 and not state.get("pending_close"):
                    if state.get("tp_digest"):
                        # 有 TP 单且持仓归零 → TP 可能真的成交了
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
                            log_message(f"[持仓同步] 实际持仓=0, state={stale_pos}, PnL={tp_pnl:+.2f}% → 可能挂单未成交，重置状态")
                    else:
                        log_message(f"[持仓同步] 实际持仓=0, state={stale_pos} → 重置状态")
                    state["position"] = 0
                    state["strategy_size"] = 0.0
                    state["entry_bar"] = 0
                    state["entry_price"] = 0.0
                    state["confirmed_entry_bar"] = 0
                    state["confirmed_entry_price"] = 0.0
                    state["tp_digest"] = None
                    state["tp_price"] = 0.0
                    state["tp_side"] = None
                    state["pending_open"] = False
                elif abs(actual_pos) >= size_increment * 0.5 and state.get("pending_open"):
                    # pending_open 挂单成交了（但还没被主循环检测到）
                    pos_dir = 1 if actual_pos > 0 else -1
                    log_message(f"[持仓同步] 检测到挂单已成交: 实际={actual_pos:.6f}")
                    state["position"] = pos_dir
                    state["strategy_size"] = abs(actual_pos)
                    if state.get("entry_price", 0) == 0:
                        state["entry_price"] = state.get("pending_open_price", state.get("last_price", 0))
                    if state.get("entry_bar", 0) == 0:
                        state["entry_bar"] = state.get("bar_count", 0) - 1
                    # 同步 confirmed_entry
                    state["confirmed_entry_price"] = state["entry_price"]
                    state["confirmed_entry_bar"] = state["entry_bar"]
                    state["pending_open"] = False
                    log_message("[持仓同步] pending_open 已成交，挂 TP 单")
                    state = manage_tp_order(trader, product_id, tick_size, size_increment, strategy, state)

                # 1. 获取 K 线数据
                df = trader.fetch_candles(product_id, granularity=granularity, limit=500)
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

                    # pending_close 期间跳过止损检查和交易执行，避免重复触发
                    skip_trading = state.get("pending_close", False)

                    # 3. 检查止损/时间退出（使用K线low/high捕捉K线内穿仓）
                    should_exit = False
                    exit_reason = ""
                    if not skip_trading:
                        # 优先使用策略自身参数，args 仅作为覆盖
                        strategy_stop_loss = getattr(strategy, 'stop_loss_pct', None)
                        stop_loss_pct = args.stop_loss if args.stop_loss is not None else (strategy_stop_loss if strategy_stop_loss is not None else 0.03)
                        max_hold_bars = args.max_hold if args.max_hold is not None else strategy.max_hold_bars
                        latest_low = df.iloc[-1]["low"]
                        latest_high = df.iloc[-1]["high"]
                        should_exit, exit_reason = check_stop_loss(
                            state, current_price,
                            kline_low=latest_low, kline_high=latest_high,
                            stop_loss_pct=stop_loss_pct,
                            max_hold_bars=max_hold_bars,
                        )

                    # 4. 获取盘口数据
                    best_bid, best_ask = trader.get_orderbook(product_id, depth=1)
                    if best_bid:
                        log_message(f"盘口: bid={best_bid:.2f} ask={best_ask:.2f}")
                    else:
                        log_message("盘口数据不可用")

                    if skip_trading:
                        log_message("[pending_close] 等待平仓链上确认，跳过交易执行")
                    elif should_exit:
                        # 强制退出均用 IOC (Taker)，确保立即成交
                        state["pending_open"] = False
                        state = force_close(trader, product_id, state,
                                            current_price, best_bid, best_ask, tick_size, size_increment,
                                            exit_reason, use_maker=False)
                    else:
                        # === 检查 pending_open 状态 ===
                        pending = state.get("pending_open", False)
                        if pending:
                            actual_pos = trader.get_position(product_id)
                            if abs(actual_pos) >= size_increment * 0.5:
                                # Maker 挂单成交了!
                                pos_dir = 1 if actual_pos > 0 else -1
                                state["position"] = pos_dir
                                state["strategy_size"] = abs(actual_pos)
                                state["entry_price"] = state.get("pending_open_price", current_price)
                                state["entry_bar"] = state.get("bar_count", 0) - 1
                                state["pending_open"] = False
                                log_message(f"[Maker成交] 入场成功 {('多' if pos_dir == 1 else '空')} @{state['entry_price']:.2f}")
                                # 挂 TP 单
                                state = manage_tp_order(trader, product_id, tick_size, size_increment, strategy, state)
                            else:
                                # Maker 没成交 → IOC 兜底
                                pending_signal = state.get("pending_open_signal", 0)
                                trader.cancel_all_orders(product_id)
                                state["pending_open"] = False

                                if signal_id == pending_signal:
                                    log_message(f"[IOC兜底] Maker未成交，切换Taker signal={signal_id}")
                                    state = execute_trade(
                                        signal_id, trader, product_id, tick_size, size_increment,
                                        args.capital * args.leverage, state, current_price, best_bid, best_ask,
                                        force_ioc=True,
                                    )
                                    if state.get("position", 0) != 0:
                                        state = manage_tp_order(trader, product_id, tick_size, size_increment, strategy, state)
                                else:
                                    log_message(f"[信号变化] 挂单期间信号改变 ({pending_signal}→{signal_id})，取消挂单")
                                    state = execute_trade(
                                        signal_id, trader, product_id, tick_size, size_increment,
                                        args.capital * args.leverage, state, current_price, best_bid, best_ask,
                                    )
                        else:
                            # === 正常流程 (无 pending) ===
                            # 检查 TP 单是否已成交
                            if state.get("position", 0) != 0 and state.get("tp_digest"):
                                actual_pos = trader.get_position(product_id)
                                if abs(actual_pos) < size_increment * 0.5:
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
                                    state["tp_digest"] = None
                                    state["tp_price"] = 0.0
                                    state["tp_side"] = None

                            # 正常交易 (先尝试 Maker)
                            state = execute_trade(
                                signal_id, trader, product_id, tick_size, size_increment,
                                args.capital * args.leverage, state, current_price, best_bid, best_ask,
                            )

                            # 挂单等待 or 已成交 → 管理 TP
                            if state.get("pending_open"):
                                log_message("[挂单等待] Maker单已挂出，下轮检查成交")
                            elif state.get("position", 0) != 0:
                                state = manage_tp_order(trader, product_id, tick_size, size_increment, strategy, state)

                    # 6. 打印状态
                    print_status(trader, product_id, args.ticker, state)
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
