"""
Nado.xyz 去中心化交易所实盘交易脚本。
基于布林带均值回归策略，每 5 分钟获取信号并自动下单（Perp 永续合约）。

Usage:
    # Mainnet 实盘交易
    # 在 .env 中设置 NADO_PRIVATE_KEY="0x_your_private_key_here"
    uv run python live_nado_quant.py --ticker ETH --interval 5m --mainnet --capital 100

    # 只运行一次（调试用）
    uv run python live_nado_quant.py --ticker BTC --interval 5m --mainnet --capital 100 --once

注意：
    - Nado 是永续合约 DEX，基于 EIP-712 签名认证（私钥即账号）
    - 开仓使用 IOC 单确保快速成交（避免 POST_ONLY 未成交导致信号丢失）
    - 强制平仓使用 IOC 单确保快速成交
    - 支持止损和时间退出
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

from train_quant import TrendStrategy

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
    """计算 POST_ONLY 限价单价格（确保不穿越盘口），对齐到 tick_size"""
    if side == "buy":
        # 买单价格要低于 best_bid
        price = best_bid - float(tick_size) * 2
    else:
        # 卖单价格要高于 best_ask
        price = best_ask + float(tick_size) * 2
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


def execute_trade(signal_id, trader, product_id, tick_size, size_increment, capital_per_trade, state, current_price, best_bid, best_ask):
    """
    根据信号执行交易
    signal_id: 0=平仓, 1=持有, 2=做多, 3=做空
    position: 1=多头, -1=空头, 0=空仓

    下单策略:
    - 计算订单名义价值，若 >= min_size (100 USDT)，优先使用 POST_ONLY 做挂单（省手续费）
    - 若 < min_size，直接用 IOC 吃单（Nado 只对挂单检查 min_size，IOC 不受此限制）
    - 所有价格严格对齐到 tick_size，避免浮点精度错误
    """
    position = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    trades = state.get("trades", [])
    MIN_NOTIONAL = 100.0  # Nado min_size: 100 USDT

    log_message(f"[交易] signal={signal_id} position={position} target计算中...")

    # 先取消所有现有挂单
    trader.cancel_all_orders(product_id)

    # 同步实际持仓到 state（POST_ONLY 订单可能未成交）
    actual_position = trader.get_position(product_id)
    if abs(actual_position) < size_increment * 0.5:
        if position != 0:
            log_message(f"[持仓同步] 实际持仓为0，重置状态 (原state: {position})")
            state["position"] = 0
            state["strategy_size"] = 0.0
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

    # --- 平掉当前仓位 ---
    if position == 1 and target_pos <= 0:
        # 平多仓（卖出）
        if strategy_size > 0:
            actual_position = trader.get_position(product_id)
            sell_size = min(strategy_size, abs(actual_position)) if actual_position > 0 else strategy_size
            if sell_size > 0:
                # 对齐到 size_increment，避免浮点精度导致下单失败
                n_units = int(Decimal(str(sell_size)) / Decimal(str(size_increment)))
                sell_size = float(n_units * Decimal(str(size_increment)))
            if sell_size > 0:
                notional = sell_size * current_price
                # 平仓用 IOC 确保快速成交
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
                        trades.append({
                            "time": datetime.now().isoformat(), "type": "CLOSE_LONG_IOC",
                            "product_id": product_id, "digest": digest,
                            "size": sell_size, "price": order_price,
                        })
                        log_message(f"[平多IOC] 下单卖出 size={sell_size:.6f} price={order_price:.2f} notional={notional:.2f}")
                    else:
                        log_message(f"[平多失败] IOC单也未成交")
        state["position"] = 0
        state["strategy_size"] = 0.0

    elif position == -1 and target_pos >= 0:
        # 平空仓（买入）
        if strategy_size > 0:
            actual_position = trader.get_position(product_id)
            close_size = min(strategy_size, abs(actual_position)) if actual_position < 0 else strategy_size
            if close_size > 0:
                # 对齐到 size_increment，避免浮点精度导致下单失败
                n_units = int(Decimal(str(close_size)) / Decimal(str(size_increment)))
                close_size = float(n_units * Decimal(str(size_increment)))
            if close_size > 0:
                notional = close_size * current_price
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
                        trades.append({
                            "time": datetime.now().isoformat(), "type": "CLOSE_SHORT_IOC",
                            "product_id": product_id, "digest": digest,
                            "size": close_size, "price": order_price,
                        })
                        log_message(f"[平空IOC] 下单买入 size={close_size:.6f} price={order_price:.2f} notional={notional:.2f}")
                    else:
                        log_message(f"[平空失败] IOC单也未成交")
        state["position"] = 0
        state["strategy_size"] = 0.0

    # --- 开新仓 ---
    log_message(f"[交易] 检查开仓: target_pos={target_pos} state_position={state.get('position', 0)}")
    if target_pos == 1 and state.get("position", 0) == 0:
        # 开多仓
        log_message(f"[开多] 准备下单 price={current_price} capital={capital_per_trade}")
        if current_price <= 0:
            log_message("[跳过开多] 价格无效")
        else:
            raw_size = Decimal(str(capital_per_trade)) / Decimal(str(current_price))
            n_units = int(raw_size / Decimal(str(size_increment)))
            order_size = float(n_units * Decimal(str(size_increment)))
            if order_size > 0:
                notional = order_size * current_price
                log_message(f"[开多] order_size={order_size:.6f} SOL, notional={notional:.2f} USDT, min={MIN_NOTIONAL}")

                if best_bid and best_ask:
                    ioc_price = compute_ioc_price("buy", best_bid, best_ask, tick_size)
                else:
                    ioc_price = round_to_tick(current_price * 1.001, tick_size)
                if ioc_price:
                    digest = trader.place_order(
                        product_id=product_id, side="buy", size=order_size,
                        price=ioc_price, order_type=OrderType.IOC, expire_seconds=60,
                    )
                    if digest:
                        trades.append({
                            "time": datetime.now().isoformat(), "type": "BUY_OPEN_IOC",
                            "product_id": product_id, "digest": digest,
                            "size": order_size, "price": ioc_price,
                        })
                        state["position"] = 1
                        state["strategy_size"] = order_size
                        state["entry_price"] = current_price
                        state["entry_bar"] = state.get("bar_count", 0)
                        log_message(f"[开多IOC] 下单买入 size={order_size:.6f} price={ioc_price:.2f} notional={notional:.2f}")
                    else:
                        log_message(f"[开多失败] IOC单未成交，放弃")

    elif target_pos == -1 and state.get("position", 0) == 0:
        # 开空仓
        if current_price <= 0:
            log_message("[跳过开空] 价格无效")
        else:
            raw_size = Decimal(str(capital_per_trade)) / Decimal(str(current_price))
            n_units = int(raw_size / Decimal(str(size_increment)))
            order_size = float(n_units * Decimal(str(size_increment)))
            if order_size > 0:
                notional = order_size * current_price
                log_message(f"[开空] order_size={order_size:.6f} SOL, notional={notional:.2f} USDT, min={MIN_NOTIONAL}")

                if best_bid and best_ask:
                    ioc_price = compute_ioc_price("sell", best_bid, best_ask, tick_size)
                else:
                    ioc_price = round_to_tick(current_price * 0.999, tick_size)
                if ioc_price:
                    digest = trader.place_order(
                        product_id=product_id, side="sell", size=order_size,
                        price=ioc_price, order_type=OrderType.IOC, expire_seconds=60,
                    )
                    if digest:
                        trades.append({
                            "time": datetime.now().isoformat(), "type": "SELL_SHORT_IOC",
                            "product_id": product_id, "digest": digest,
                            "size": order_size, "price": ioc_price,
                        })
                        state["position"] = -1
                        state["strategy_size"] = order_size
                        state["entry_price"] = current_price
                        state["entry_bar"] = state.get("bar_count", 0)
                        log_message(f"[开空IOC] 下单卖出 size={order_size:.6f} price={ioc_price:.2f} notional={notional:.2f}")
                    else:
                        log_message(f"[开空失败] IOC单未成交，放弃")

    state["trades"] = trades
    state["last_signal"] = signal_id
    state["last_update"] = datetime.now().isoformat()
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


def force_close(trader, product_id, state, current_price, best_bid, best_ask, tick_size, size_increment, reason):
    """强制平仓（使用 IOC 单快速成交，支持多空）"""
    pos = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    if pos == 0 or strategy_size <= 0:
        return state

    trader.cancel_all_orders(product_id)

    actual_position = trader.get_position(product_id)
    close_size = min(strategy_size, abs(actual_position)) if abs(actual_position) > 0 else strategy_size
    if close_size > 0:
        # 对齐到 size_increment，避免浮点精度导致下单失败
        n_units = int(Decimal(str(close_size)) / Decimal(str(size_increment)))
        close_size = float(n_units * Decimal(str(size_increment)))
    if close_size <= 0:
        state["position"] = 0
        state["strategy_size"] = 0.0
        return state

    # 确定平仓方向和滑点
    if pos == 1:
        # 平多：卖出，价格往下给滑点确保成交
        side = "sell"
        if best_bid and best_ask:
            order_price = compute_ioc_price("sell", best_bid, best_ask, tick_size)
        elif current_price > 0:
            order_price = round_to_tick(current_price * 0.99, tick_size)
        else:
            log_message("[强制平仓] 无有效价格，跳过")
            return state
    elif pos == -1:
        # 平空：买入，价格往上给滑点确保成交
        side = "buy"
        if best_bid and best_ask:
            order_price = compute_ioc_price("buy", best_bid, best_ask, tick_size)
        elif current_price > 0:
            order_price = round_to_tick(current_price * 1.01, tick_size)
        else:
            log_message("[强制平仓] 无有效价格，跳过")
            return state
    else:
        return state

    digest = trader.place_order(
        product_id=product_id, side=side, size=close_size,
        price=order_price, order_type=OrderType.IOC, expire_seconds=60,
    )

    if digest:
        pos_name = "多头" if pos == 1 else "空头"
        trades = state.get("trades", [])
        trades.append({
            "time": datetime.now().isoformat(),
            "type": "FORCE_CLOSE",
            "product_id": product_id,
            "digest": digest,
            "size": close_size,
            "price": order_price,
            "reason": reason,
        })
        state["trades"] = trades
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["last_signal"] = 0
        log_message(f"[强制平仓] {reason}，{pos_name}{side} {close_size:.6f} @ {order_price:.2f}")

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
        else:
            pos_str = "空仓"

        # 计算未实现盈亏
        entry_price = state.get("entry_price", 0)
        strategy_size = state.get("strategy_size", 0)
        unrealized_pnl = 0.0
        if pos != 0 and entry_price > 0 and price and strategy_size > 0:
            if pos == 1:
                unrealized_pnl = (price - entry_price) * strategy_size
            elif pos == -1:
                unrealized_pnl = (entry_price - price) * strategy_size

        log_message("-" * 50)
        log_message(f"当前信号: {signal_str}")
        log_message(f"持仓状态: {pos_str}")
        log_message(f"策略持仓: {strategy_size:.6f} {ticker}")
        log_message(f"实际持仓: {position:.6f} {ticker}")
        if price:
            log_message(f"最新价格: {price:.2f}")
        if pos != 0 and entry_price > 0:
            log_message(f"入场价格: {entry_price:.2f}")
            log_message(f"未实现盈亏: {unrealized_pnl:.2f} USDT")
        log_message(f"挂单数量: {len(open_orders)}")
        log_message(f"交易次数: {len(state.get('trades', []))}")
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
    parser.add_argument("--stop-loss", type=float, default=0.03, help="止损百分比（默认 3%%）")
    parser.add_argument("--max-hold", type=int, default=48, help="最大持仓K线数（默认 48）")
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
    if not os.path.exists(args.checkpoint):
        log_message(f"错误: 未找到 {args.checkpoint}，请先运行 train_quant.py 训练策略")
        sys.exit(1)

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    params = checkpoint.get("params", {})
    strategy = TrendStrategy(
        window=params.get("window", 20),
        std_dev=params.get("std_dev", 2.0),
        atr_multiplier=params.get("atr_multiplier", 2.5),
        max_hold_bars=params.get("max_hold_bars", args.max_hold),
        adx_threshold=params.get("adx_threshold", 25),
        entry_zone=params.get("entry_zone", 0.0),
        rsi_threshold=params.get("rsi_threshold", 30),
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
    )
    active_indicators = [k for k in ["use_adx", "use_volume", "use_macd", "use_ma_cross", "use_mfi", "use_stochastic", "use_rsi_divergence", "use_macd_divergence", "use_trend_filter"] if getattr(strategy, k)]
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
        }
        log_message(f"初始化状态，保证金: {args.capital:.2f} USDT, 杠杆: {args.leverage}x, 实际下单: {args.capital * args.leverage:.2f} USDT")
    else:
        log_message("恢复上一次交易状态")
        state.setdefault("strategy_size", 0.0)
        state.setdefault("bar_count", 0)
        state.setdefault("entry_price", 0.0)
        state.setdefault("entry_bar", 0)
        state.setdefault("last_price", 0)
        state.setdefault("product_id", product_id)

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
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

                    # 3. 检查止损/时间退出
                    should_exit, exit_reason = check_stop_loss(
                        state, current_price,
                        stop_loss_pct=args.stop_loss,
                        max_hold_bars=strategy.max_hold_bars,
                    )

                    # 4. 获取盘口数据
                    best_bid, best_ask = trader.get_orderbook(product_id, depth=1)
                    if best_bid:
                        log_message(f"盘口: bid={best_bid:.2f} ask={best_ask:.2f}")
                    else:
                        log_message("盘口数据不可用")

                    if should_exit:
                        state = force_close(trader, product_id, state,
                                            current_price, best_bid, best_ask, tick_size, size_increment, exit_reason)
                    else:
                        # 5. 正常交易
                        state = execute_trade(
                            signal_id, trader, product_id, tick_size, size_increment,
                            args.capital * args.leverage, state, current_price, best_bid, best_ask,
                        )

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
