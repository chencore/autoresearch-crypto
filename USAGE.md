# ETH 量化交易系统 — 安装与使用说明

## 环境要求

- Python 3.10+
- Windows / Linux / macOS
- 网络连接（Nado DEX 实盘需要）

## 1. 安装

```bash
# 克隆项目
git clone <repo-url> autoresearch-dex
cd autoresearch-dex

# 安装依赖（uv 比 pip 快，推荐）
pip install uv          # 如果没有 uv
uv sync                 # 安装全部依赖

# 或者用 pip
pip install numpy pandas pyarrow torch
pip install nado-protocol python-dotenv
```

## 2. 准备数据

```bash
# 下载 ETH 5分钟 K 线数据（60天）
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60

# 数据保存在 data/crypto/ETHUSDT_5m.parquet
```

## 3. 策略搜索

### 快速扫描（5分钟）
```bash
uv run python search_eth_optimal.py --quick
```

### 完整搜索（20分钟，推荐）
```bash
uv run python search_eth_optimal.py
```

### 深度搜索 Top-3 策略（15分钟）
```bash
uv run python search_deep.py
```

### 输出文件
- `checkpoints/eth_optimal.pt` — 最优策略参数
- `search_results/eth_optimal_report.json` — 可读报告

## 4. 回测

### 使用已有 checkpoint 回测
```bash
# TrendStrategy（布林带均值回归）
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 60

# HybridMM（RSI 均值回归，推荐）
uv run python backtest_quant.py \
    --symbol ETHUSDT --interval 5m --days 60 \
    --checkpoint checkpoints/hybrid_mm_eth60d.pt
```

## 5. 实盘交易（Nado DEX）

### 5.1 配置私钥

创建 `.env` 文件：
```
NADO_PRIVATE_KEY=0x_your_private_key_here
```

### 5.2 启动实盘

```bash
# HybridMM 策略（推荐）
uv run python3 live_nado_quant.py \
    --ticker ETH \
    --interval 5m \
    --capital 100 \
    --checkpoint checkpoints/hybrid_mm_eth60d.pt

# TrendStrategy（原有布林带策略）
uv run python live_nado_quant.py \
    --ticker ETH \
    --interval 5m \
    --capital 100 \
    --checkpoint checkpoints/quant_model.pt

# ScalpStrategy（高频剥头皮）
uv run python live_nado_quant.py \
    --ticker ETH \
    --interval 5m \
    --capital 50 \
    --checkpoint checkpoints/quant_model.pt
```

### 5.3 实盘参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ticker` | ETH | 交易对（ETH, BTC, SOL） |
| `--interval` | 5m | K 线周期（1m/5m/15m/1h） |
| `--capital` | 100 | 每次开仓保证金（USDT） |
| `--leverage` | 1.0 | 杠杆倍数 |
| `--stop-loss` | 策略默认 | 覆盖止损百分比 |
| `--max-hold` | 策略默认 | 覆盖最大持仓 K 线数 |
| `--long-only` | 否 | 只做多，不做空 |
| `--once` | 否 | 运行一次后退出（调试用） |

### 5.4 实盘运行逻辑

每 5 分钟执行一轮：
1. 获取最新 K 线数据
2. 策略生成信号（做多/做空/平仓/持有）
3. 如需换仓 → 挂 Maker 限价单（省手续费）
4. 挂单未成交 → 下一轮 IOC 兜底
5. 持仓中自动挂止盈限价单（Maker）
6. 止损/超时 → IOC 市价单立即退出

### 5.5 日志和状态

- `logs/live_nado_log_YYYYMMDD_HHMMSS.txt` — 运行日志
- `logs/live_nado_state.json` — 交易状态（持仓、入场价、交易记录）
- `logs/live_nado_quant.lock` — 进程锁（防止重复启动）

## 6. 策略说明

### 当前可用策略

| 策略 | checkpoint | 适合市场 | 交易频率 |
|------|-----------|----------|---------|
| **HybridMM** | `hybrid_mm_eth60d.pt` | 震荡+趋势 | 低（~2笔/天） |
| TrendStrategy | `quant_model.pt` | 震荡 | 极低 |
| ScalpStrategy | `quant_model.pt` | 高波动 | 极高 |

### HybridMM 参数（推荐）

```
RSI 周期: 5       RSI 超卖: 28    RSI 超买: 72
EMA 周期: 25      ATR: 12×3.5     最大持仓: 48 根K线
止盈: 3%          止损: 2%
```

特性：低胜率(~38%) + 高盈亏比(3.5:1)，大赚小亏。

## 7. 训练自定义策略

```bash
# 全模式搜索（30分钟+）
uv run python train_quant.py --mode smart

# 特定模式
uv run python train_quant.py --mode trendfollow
uv run python train_quant.py --mode hybrid_mm
uv run python train_quant.py --mode adaptive
```

训练结果保存到 `checkpoints/quant_model.pt`。

## 8. 目录结构

```
autoresearch-dex/
├── live_nado_quant.py      # Nado DEX 实盘交易
├── train_quant.py           # 策略类 + 训练搜索
├── search_eth_optimal.py    # ETH 最优策略搜索
├── search_deep.py           # 深度参数搜索
├── backtest_quant.py        # 回测脚本
├── prepare_crypto.py        # 数据下载
├── exchange/
│   └── nado.py              # Nado 交易所封装
├── checkpoints/
│   ├── quant_model.pt       # 默认策略
│   ├── eth_optimal.pt       # ETH 搜索最优
│   └── hybrid_mm_eth60d.pt  # HybridMM 60天最优
├── data/crypto/             # K 线数据
├── logs/                    # 实盘日志
├── search_results/          # 搜索报告
└── USAGE.md                 # 本文档
```
