"""
加密货币数据准备脚本。
从 Binance public API 下载 K线数据并转换为 Parquet 格式。

Usage:
    python prepare_crypto.py                    # 下载所有数据
    python prepare_crypto.py --symbol BTCUSDT  # 下载指定交易对
    python prepare_crypto.py --limit 100       # 限制下载天数

数据存储在项目目录下的 data/crypto/
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
PROXY = {}

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 数据存储在项目目录下的 data/crypto/
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "crypto")

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
# 数据下载
# ---------------------------------------------------------------------------

# 支持的交易所配置
EXCHANGES = {
    "binance": {
        "kline_url": "https://api.binance.us/api/v3/klines",
    },
}


def download_binance(symbol, interval, start_ts, end_ts):
    """
    使用 Binance API 下载 K线数据

    Binance API 特点：
    - 支持 startTime/endTime 指定范围
    - 每次最多返回 1000 条（我们用 100）
    - 数据从 startTime 到 endTime 按时间顺序
    """
    interval_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "1h": "1h", "4h": "4h", "1d": "1d"
    }
    timeframe = interval_map.get(interval, "5m")

    all_candles = []
    current_start = start_ts

    print(f"    开始下载 {symbol} {interval} 从 {days_between(start_ts, end_ts):.1f} 天前...", flush=True)

    while current_start < end_ts:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                params = {
                    "symbol": symbol,
                    "interval": timeframe,
                    "startTime": current_start,
                    "endTime": end_ts,
                    "limit": 100,
                }

                response = requests.get(
                    EXCHANGES["binance"]["kline_url"],
                    params=params,
                    proxies=PROXY,
                    timeout=30
                )

                if response.status_code == 451:
                    # Binance 451 错误通常是地区限制，尝试不同端点
                    print("    451 错误，尝试备用端点...", flush=True)
                    response = requests.get(
                        "https://api.binance.us/api/v3/klines",
                        params=params,
                        timeout=30
                    )

                response.raise_for_status()
                data = response.json()

                if not data:
                    print("    无更多数据，停止", flush=True)
                    return all_candles

                # Binance 返回格式:
                # [open_time, open, high, low, close, volume, close_time, ...]
                for c in data:
                    ts = int(c[0])
                    if ts >= start_ts and ts <= end_ts:
                        all_candles.append(c)

                print(f"    +{len(data)} (累计 {len(all_candles)})", flush=True)

                # 更新下次开始时间
                current_start = int(data[-1][0]) + 1
                time.sleep(0.2)

                # 如果返回数据少于 limit，说明到头了
                if len(data) < 100:
                    return all_candles
                break

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    print(f"    失败: {e}", flush=True)
                    return all_candles

    return all_candles


def days_between(ts1, ts2):
    """计算天数差"""
    return (ts2 - ts1) / (24 * 3600 * 1000)


def prepare_crypto_data_streaming(symbol, interval, start_days, force=False):
    """
    使用 Binance API 下载数据。
    文件按 {symbol}_{interval}_{days}d.parquet 格式保存，避免重复下载。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    filepath_parquet = os.path.join(DATA_DIR, f"{symbol}_{interval}_{start_days}d.parquet")

    # 兼容旧格式（无天数标识）
    old_filepath = os.path.join(DATA_DIR, f"{symbol}_{interval}.parquet")

    # 检查是否已存在相同天数的数据文件
    if os.path.exists(filepath_parquet) and not force:
        print(f"  {symbol}: {start_days}天数据已存在 ({filepath_parquet})，跳过", flush=True)
        return True

    # 如果旧格式文件存在但新格式不存在，提示用户
    if os.path.exists(old_filepath) and not os.path.exists(filepath_parquet):
        print(f"  {symbol}: 发现旧格式数据文件，将重新下载 {start_days} 天数据到新格式", flush=True)

    end_time = int(time.time() * 1000)
    start_time = int((time.time() - start_days * 24 * 3600) * 1000)

    print(f"  {symbol}: 使用 Binance API 下载 {start_days} 天数据...", flush=True)

    # 使用 Binance API 下载
    candles = download_binance(symbol, interval, start_time, end_time)

    if not candles:
        print(f"  {symbol}: 无数据", flush=True)
        return False

    print(f"  共获取 {len(candles)} 根K线", flush=True)

    # Binance 返回已按时间排序（从早到晚）
    records = []
    for c in candles:
        ts = int(c[0])
        dt = pd.to_datetime(ts, unit="ms")
        records.append({
            "timestamp": ts,
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
            "quote_volume": float(c[4]) * float(c[5]),
            "num_trades": int(c[8]) if len(c) > 8 else 0,
            "taker_buy_volume": float(c[9]) if len(c) > 9 else float(c[5]) * 0.5,
            "taker_buy_quote_volume": float(c[10]) if len(c) > 10 else float(c[4]) * float(c[5]) * 0.5,
            "datetime": dt,
        })

    df = pd.DataFrame(records)

    # 去重（按 timestamp）
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

    # 计算技术指标
    print("  计算技术指标...", flush=True)
    df = compute_features(df)

    # 保存（新格式，带天数标识）
    table = pa.Table.from_pandas(df)
    pq.write_table(table, filepath_parquet)
    print(f"  保存至 {filepath_parquet}", flush=True)
    return True


def prepare_crypto_data(symbols=None, interval=None, start_days=None, force=False):
    """下载并处理加密货币数据"""
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    if interval is None:
        interval = DEFAULT_INTERVAL
    if start_days is None:
        start_days = DEFAULT_START_DAYS

    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"数据目录: {DATA_DIR}")
    print(f"下载周期: {interval}, 从 {start_days} 天前开始")
    print(f"交易对: {symbols}")
    print()

    for symbol in symbols:
        prepare_crypto_data_streaming(symbol, interval, start_days, force=force)

    print()
    print("数据准备完成!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="下载加密货币K线数据")
    parser.add_argument("--symbol", type=str, default=None, help="交易对，如 BTCUSDT")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1h, 4h, 1d")
    parser.add_argument("--limit", type=int, default=60, help="下载多少天的数据")
    parser.add_argument("--force", action="store_true", help="强制重新下载，即使文件已存在")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS
    prepare_crypto_data(symbols=symbols, interval=args.interval, start_days=args.limit, force=args.force)
