"""
加密货币数据准备脚本。
从 Binance public API 下载 K线数据并转换为 Parquet 格式。

Usage:
    python prepare_crypto.py                    # 下载所有数据
    python prepare_crypto.py --symbol BTCUSDT  # 下载指定交易对
    python prepare_crypto.py --limit 100       # 限制下载天数

数据存储在 ~/.cache/autoresearch/data/crypto/
"""

import os
import time
import argparse

import requests
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

# 全局代理设置
PROXY = {"http": "http://127.0.0.1:50830", "https": "http://127.0.0.1:50830"}

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autoresearch")
DATA_DIR = os.path.join(CACHE_DIR, "data", "crypto")

# 默认交易对和周期
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_INTERVAL = "5m"
DEFAULT_START_DAYS = 60  # 默认下载60天数据

# ---------------------------------------------------------------------------
# 技术指标计算
# ---------------------------------------------------------------------------

def _ema(series, window):
    """计算指数移动平均"""
    alpha = 2 / (window + 1)
    ema = np.zeros(len(series), dtype=np.float64)
    ema[0] = series[0]
    for i in range(1, len(series)):
        ema[i] = alpha * series[i] + (1 - alpha) * ema[i-1]
    return ema.astype(np.float32)


def _rolling_mean(series, window):
    """计算滚动平均"""
    mean = np.zeros(len(series), dtype=np.float32)
    for i in range(window - 1, len(series)):
        mean[i] = np.mean(series[max(0, i-window+1):i+1])
    mean[:window-1] = mean[window-1]
    return mean


def _rolling_std(series, window):
    """计算滚动标准差"""
    std = np.zeros(len(series), dtype=np.float32)
    for i in range(window - 1, len(series)):
        std[i] = np.std(series[max(0, i-window+1):i+1])
    std[:window-1] = std[window-1]
    return std


def compute_features(df):
    """基于 OHLCV DataFrame 计算技术指标，返回带新列的 DataFrame"""
    close = df["close"].values.astype(np.float32)
    high = df["high"].values.astype(np.float32)
    low = df["low"].values.astype(np.float32)
    volume = df["volume"].values.astype(np.float32)
    n = len(close)

    # 收益率
    returns = np.zeros(n, dtype=np.float32)
    returns[1:] = (close[1:] - close[:-1]) / close[:-1]

    # 波动率 (滚动标准差, 窗口=12)
    volatility = np.zeros(n, dtype=np.float32)
    for i in range(12, n):
        volatility[i] = np.std(returns[max(0, i-12):i])
    volatility[:12] = volatility[12]

    # RSI (相对强弱指数, 窗口=14)
    rsi = np.full(n, 50.0, dtype=np.float32)
    gains = np.where(returns > 0, returns, 0.0)
    losses = np.where(returns < 0, -returns, 0.0)
    avg_gain = np.zeros(n, dtype=np.float32)
    avg_loss = np.zeros(n, dtype=np.float32)
    avg_gain[14] = np.mean(gains[1:15])
    avg_loss[14] = np.mean(losses[1:15])
    for i in range(15, n):
        avg_gain[i] = (avg_gain[i-1] * 13 + gains[i]) / 14
        avg_loss[i] = (avg_loss[i-1] * 13 + losses[i]) / 14
        if avg_loss[i] == 0:
            rsi[i] = 100
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema(macd, 9)
    macd_hist = macd - signal

    # 布林带 (窗口=20, ±2标准差)
    bb_mid = _rolling_mean(close, 20)
    bb_std = _rolling_std(close, 20)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # ATR (平均真实范围, 窗口=14)
    tr = np.zeros(n, dtype=np.float32)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
    atr = _rolling_mean(tr, 14)

    # 成交量变化率
    volume_ma = _rolling_mean(volume, 20)
    volume_ratio = volume / np.maximum(volume_ma, 1e-10)

    # 添加新列
    result = df.copy()
    result["returns"] = returns
    result["volatility"] = volatility
    result["rsi"] = rsi
    result["macd"] = macd
    result["macd_signal"] = signal
    result["macd_hist"] = macd_hist
    result["bb_upper"] = bb_upper
    result["bb_mid"] = bb_mid
    result["bb_lower"] = bb_lower
    result["atr"] = atr
    result["volume_ratio"] = volume_ratio

    return result


# ---------------------------------------------------------------------------
# 数据下载 (使用 CCXT)
# ---------------------------------------------------------------------------

# 支持的交易所配置
EXCHANGES = {
    "okx": {
        "kline_url": "https://www.okx.com/api/v5/market/candles",
        "params": {"instId": None},  # 会动态设置
    },
}


def download_with_requests(symbol, interval, start_time, end_time):
    """
    使用 requests 通过代理下载 K线数据

    Args:
        symbol: 交易对，如 "BTCUSDT"
        interval: K线周期，如 "5m", "1h", "1d"
        start_time: 开始时间 (Unix timestamp in milliseconds)
        end_time: 结束时间 (Unix timestamp in milliseconds)

    Returns:
        list of ohlcv records
    """
    # 转换交易对格式
    inst_id = symbol.replace("USDT", "-USDT")

    # 转换时间周期
    interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
    timeframe = interval_map.get(interval, "5m")

    all_ohlcv = []
    oldest_ts = end_time  # 初始化

    while len(all_ohlcv) < 10000:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                url = EXCHANGES["okx"]["kline_url"]
                params = {
                    "instId": inst_id,
                    "bar": timeframe,
                    "limit": "100",
                }

                # 第一次获取最新数据，之后用 after 分页获取更早数据
                if not all_ohlcv:
                    # 第一次获取最新
                    pass
                else:
                    # 用最旧的时间戳获取更早数据
                    params["after"] = str(oldest_ts)

                response = requests.get(url, params=params, proxies=PROXY, timeout=30)
                response.raise_for_status()
                data = response.json()

                if data.get("code") != "0":
                    raise Exception(f"API error: {data.get('msg')}")

                candles = data.get("data", [])
                if not candles:
                    break

                # 解析数据: [timestamp, open, high, low, close, volume, ...]
                for c in candles:
                    ts = int(c[0])
                    # 只添加在时间范围内的数据
                    if ts >= start_time:
                        all_ohlcv.append([
                            ts,
                            float(c[1]),
                            float(c[2]),
                            float(c[3]),
                            float(c[4]),
                            float(c[5]),
                        ])

                # 更新最旧时间戳
                oldest_ts = int(candles[-1][0])
                time.sleep(0.2)
                break

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

        # 如果最旧数据已经早于起始时间则退出
        if oldest_ts <= start_time:
            break

    return all_ohlcv


def parse_ohlcv_to_df(ohlcv_list):
    """解析 CCXT ohlcv 响应为 pandas DataFrame"""
    records = []
    for k in ohlcv_list:
        # CCXT 格式: [timestamp, open, high, low, close, volume]
        records.append({
            "timestamp": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "quote_volume": float(k[4]) * float(k[5]),  # 估算
            "num_trades": 0,  # CCXT 基础响应不含此字段
            "taker_buy_volume": float(k[5]) * 0.5,  # 估算
            "taker_buy_quote_volume": float(k[4]) * float(k[5]) * 0.5,  # 估算
        })
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def prepare_crypto_data(symbols=None, interval=None, start_days=None):
    """下载并处理加密货币数据"""
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    if interval is None:
        interval = DEFAULT_INTERVAL
    if start_days is None:
        start_days = DEFAULT_START_DAYS

    os.makedirs(DATA_DIR, exist_ok=True)

    end_time = int(time.time() * 1000)
    start_time = int((time.time() - start_days * 24 * 3600) * 1000)

    print(f"数据目录: {DATA_DIR}")
    print(f"下载周期: {interval}, 从 {start_days} 天前开始")
    print(f"交易对: {symbols}")
    print()

    for symbol in symbols:
        filepath = os.path.join(DATA_DIR, f"{symbol}_{interval}.parquet")

        # 检查是否已存在
        if os.path.exists(filepath):
            print(f"  {symbol}: 数据已存在，跳过")
            continue

        print(f"  {symbol}: 下载中...")

        # 使用 requests 下载数据
        ohlcv = download_with_requests(symbol, interval, start_time, end_time)

        if not ohlcv:
            print(f"  {symbol}: 无数据")
            continue

        print(f"    获取 {len(ohlcv)} 根K线")

        # 解析为 DataFrame
        df = parse_ohlcv_to_df(ohlcv)

        # 计算技术指标
        print(f"    计算技术指标...")
        df = compute_features(df)

        # 转换 timestamp 为 UTC 时间（方便查看）
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")

        # 保存为 Parquet
        table = pa.Table.from_pandas(df)
        pq.write_table(table, filepath)
        print(f"    保存至 {filepath}")

    print()
    print("数据准备完成!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="下载加密货币K线数据")
    parser.add_argument("--symbol", type=str, default=None, help="交易对，如 BTCUSDT")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1h, 4h, 1d")
    parser.add_argument("--limit", type=int, default=60, help="下载多少天的数据")
    parser.add_argument("--synthetic", action="store_true", help="生成合成数据（当API不可用时）")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS

    if args.synthetic:
        # 生成合成数据用于测试
        import numpy as np
        import pandas as pd

        os.makedirs(DATA_DIR, exist_ok=True)
        for symbol in symbols:
            filepath = os.path.join(DATA_DIR, f"{symbol}_{args.interval}.parquet")
            if os.path.exists(filepath):
                print(f"  {symbol}: 数据已存在，跳过")
                continue

            print(f"  {symbol}: 生成合成数据...")

            # 生成模拟K线数据
            n = 10000  # 约35天数据
            base_price = 50000 if "BTC" in symbol else 3000
            timestamps = [int(time.time() * 1000) - (n - i) * 5 * 60 * 1000 for i in range(n)]

            data = []
            price = base_price
            for i, ts in enumerate(timestamps):
                # 随机游走
                change = np.random.randn() * 0.002
                price = price * (1 + change)
                high = price * (1 + abs(np.random.randn()) * 0.001)
                low = price * (1 - abs(np.random.randn()) * 0.001)
                volume = np.random.lognormal(10, 1)

                data.append({
                    "timestamp": ts,
                    "open": price * (1 - abs(np.random.randn()) * 0.0005),
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": volume,
                    "quote_volume": volume * price,
                    "num_trades": int(np.random.lognormal(5, 1)),
                    "taker_buy_volume": volume * 0.5,
                    "taker_buy_quote_volume": volume * price * 0.5,
                })

            df = pd.DataFrame(data)
            df = compute_features(df)
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")

            table = pa.Table.from_pandas(df)
            pq.write_table(table, filepath)
            print(f"    保存至 {filepath}")

        print("\n合成数据生成完成!")
    else:
        prepare_crypto_data(symbols=symbols, interval=args.interval, start_days=args.limit)
