"""
加密货币量化策略实时推理脚本（布林带均值回归）。
加载训练好的策略参数，获取最新K线数据，输出交易信号。

Usage:
    uv run python inference_quant.py --symbol BTCUSDT --interval 5m
    uv run python inference_quant.py --symbol ETHUSDT --interval 15m
"""

import os
import sys
import time
import argparse

import numpy as np
import pandas as pd
import torch

from prepare_crypto import download_binance, compute_features
from train_quant import TrendStrategy as BollingerStrategy


def load_checkpoint(checkpoint_path="checkpoints/quant_model.pt"):
    """加载训练好的策略参数"""
    if not os.path.exists(checkpoint_path):
        print(f"错误: 未找到策略参数文件 {checkpoint_path}")
        print("请先运行: uv run python train_quant.py")
        sys.exit(1)

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    params = checkpoint.get("params", {})
    if not params:
        print("错误: 检查点中未找到策略参数")
        sys.exit(1)
    return params


def fetch_latest_data(symbol, interval, limit=400):
    """获取最新的K线数据"""
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - limit * 5 * 60) * 1000)

    print(f"正在下载 {symbol} {interval} 最近数据...")
    candles = download_binance(symbol, interval, start_time, end_time)

    if not candles or len(candles) < 100:
        print("错误: 未能获取足够的数据")
        sys.exit(1)

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
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def predict(strategy, df):
    """
    生成交易信号。
    返回信号ID、名称、以及当前布林带位置信息。
    """
    signals = strategy.generate_signals(df)
    signal_id = int(signals[-1])
    signal_names = ["卖出 (SELL)", "持有 (HOLD)", "买入 (BUY)"]
    signal_name = signal_names[signal_id]

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
    return signal_id, signal_name, bb_info


def main():
    parser = argparse.ArgumentParser(description="布林带均值回归实时信号生成")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="交易对")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期")
    parser.add_argument("--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径")
    args = parser.parse_args()

    print("=" * 60)
    print("加载策略参数...")
    print("=" * 60)
    params = load_checkpoint(args.checkpoint)
    strategy = BollingerStrategy(window=params["window"], std_dev=params["std_dev"])
    print(f"策略参数: 周期={params['window']}, 标准差倍数={params['std_dev']}")
    print()

    print("=" * 60)
    print(f"获取 {args.symbol} {args.interval} 实时数据...")
    print("=" * 60)
    df = fetch_latest_data(args.symbol, args.interval)
    print(f"获取到 {len(df)} 根K线")
    print()

    print("=" * 60)
    print("生成交易信号...")
    print("=" * 60)
    current_price = df.iloc[-1]["close"]
    current_time = df.iloc[-1]["datetime"]
    print(f"当前时间: {current_time}")
    print(f"当前价格: {current_price:.2f} USDT")
    print()

    signal_id, signal_name, bb_info = predict(strategy, df)

    print("=" * 60)
    print("交易信号")
    print("=" * 60)
    print(f"建议操作: {signal_name}")
    print()
    print("布林带位置:")
    print(f"  上轨: {bb_info['upper']:.2f}")
    print(f"  中轨: {bb_info['mid']:.2f}")
    print(f"  下轨: {bb_info['lower']:.2f}")
    print(f"  当前价格: {bb_info['price']:.2f}")
    print()

    print("=" * 60)
    print("风险提示")
    print("=" * 60)
    print("本信号仅供研究参考，不构成投资建议。")
    print("实盘交易前请充分回测并设置止损。")
    print()

    return {
        "symbol": args.symbol,
        "interval": args.interval,
        "time": str(current_time),
        "price": float(current_price),
        "signal": signal_id,
        "signal_name": signal_name,
        "bollinger": bb_info,
    }


if __name__ == "__main__":
    result = main()
