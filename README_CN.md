# autoresearch-dex

自主加密货币量化交易策略研究框架。从 autoresearch 范式演化而来——不再优化 LLM，而是让 AI Agent 自主发现、演化和执行加密货币交易策略。

> **免责声明**：本项目仅供研究和教育用途。加密货币交易具有重大风险。过往表现不能保证未来收益。使用风险自负。

## 概述

autoresearch-dex 是一个模块化量化交易框架，专为以下场景设计：

- **策略研究**：通过进化算法自动搜索和优化交易策略
- **回测**：高保真回测，模拟真实手续费和滑点
- **实盘交易**：与 Nado DEX 和 OKX 的生产级集成
- **Agent 驱动演化**：ATLAS 多策略进化和 GEPA 反思式进化引擎

## 功能特性

| 功能 | 说明 |
|---------|-------------|
| **11 种内置策略** | 趋势跟踪、均值回归、剥头皮、网格、混合动量、自适应等 |
| **ATLAS 进化** | 多 Agent 竞争性进化，跨策略原型 |
| **GEPA 反思** | 带边界案例检测和策略枯竭恢复的反思式学习 |
| **市场机制检测** | 自动机制分类（趋势/震荡/高波动） |
| **多时间框架分析** | 跨 1m、5m、15m、1h、4h 时间框架的集合信号 |
| **Maker/Taker 费率优化** | 混合执行：开仓/止盈用 POST_ONLY（Maker），止损用 IOC（Taker） |
| **前进验证** | 多窗口样本外测试 |

## 快速开始

### 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）
- NVIDIA GPU（可选，用于策略搜索——建议 8GB+ 显存）

### 安装

```bash
# 1. 克隆仓库
git clone <repo-url> autoresearch-dex
cd autoresearch-dex

# 2. 安装依赖
uv sync

# 3. 准备环境变量
cp .env.example .env
# 编辑 .env，添加你的 API 密钥 / 私钥
```

### 下载市场数据

```bash
# 下载 ETH 5分钟数据（60天）
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60
```

### 策略搜索

```bash
# 快速扫描（5分钟）
uv run python search_eth_optimal.py --quick

# 完整搜索（推荐，约20分钟）
uv run python search_eth_optimal.py
```

### 回测

```bash
# 使用默认策略回测
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 60

# 使用指定 checkpoint 回测
uv run python backtest_quant.py \
    --symbol ETHUSDT --interval 5m --days 60 \
    --checkpoint checkpoints/hybrid_mm_eth60d.pt
```

### 实盘交易（Nado DEX）

```bash
uv run python live_nado_quant.py \
    --ticker ETH \
    --interval 5m \
    --capital 100 \
    --checkpoint checkpoints/hybrid_mm_eth60d.pt
```

## 项目结构

```
autoresearch-dex/
├── live_nado_quant.py          # Nado DEX 实盘交易
├── live_okx_quant.py           # OKX 实盘交易
├── backtest_quant.py           # 回测引擎
├── train_quant.py              # 策略训练 / 搜索
├── search_eth_optimal.py       # ETH 最优策略搜索
├── search_deep.py              # 深度参数搜索
├── prepare_crypto.py           # 市场数据下载器
├── dex/                        # 核心框架
│   ├── strategies/             # 策略实现（11个类）
│   ├── indicators.py           # 技术指标
│   ├── evolution.py            # ATLAS 进化引擎
│   ├── reflection.py           # GEPA 反思式进化
│   ├── scoring.py              # 风险调整评分
│   ├── market_regime.py        # 机制检测
│   ├── config.py               # 集中式常量配置
│   └── live/common.py          # 实盘交易共享工具
├── checkpoints/                # 策略检查点（保留 quant_model.pt）
├── docs/                       # 文档
│   ├── STRATEGIES.md           # 策略目录
│   └── 策略.md                  # 原始中文策略笔记
└── scripts/                    # 工具脚本
    ├── evolve.py               # 运行 ATLAS 进化
    ├── evolve_gepa.py          # 运行 GEPA 进化
    └── train_all.py            # 训练所有策略类型
```

## 策略一览

| 策略 | 类型 | 适用市场 | 交易频率 | 风险等级 |
|----------|-------|------|------------------|-----------|
| **HybridMM** | 均值回归 + 动量 | 震荡 + 趋势 | 低（约2笔/天） | 中等 |
| **Trend** | 布林带均值回归 | 震荡市 | 极低 | 中等 |
| **Scalp** | 高频剥头皮 | 高波动 | 极高 | 高 |
| **TrendFollow** | 趋势跟踪 | 强趋势 | 低 | 中等 |
| **Grid** | 网格交易 | 横盘 | 中等 | 低 |
| **PureAction** | 极端反转 | 超买/超卖 | 中等 | 中等 |
| **Adaptive** | 自适应 | 所有机制 | 中等 | 中等 |

详见 [docs/STRATEGIES.md](docs/STRATEGIES.md)。

## 进化引擎

### ATLAS 多策略进化

多 Agent（Alpha、Beta、Gamma、Delta）同时进化不同策略原型：

```bash
uv run python scripts/evolve.py --generations 30
```

### GEPA 反思式进化

每 5 轮强制记录假设的反思式学习：

```bash
uv run python scripts/evolve_gepa.py --cycles 30
```

## 风险评分

框架使用风险惩罚评分代替原始夏普比率，防止 Agent 偏好高杠杆/高回撤策略：

```
Score = Sharpe * (1 - |MaxDD|)^-1 * TradePenalty * EdgeGuard
```

## 配置

所有交易默认值集中在 [`dex/config.py`](dex/config.py)：

| 参数 | 默认值 | 说明 |
|-----------|---------|-------------|
| `INITIAL_CAPITAL` | 10000.0 | 回测初始资金 |
| `COMMISSION` | 0.02% | DEX Maker 手续费 |
| `SLIPPAGE` | 0.02% | 执行滑点 |
| `EVAL_MAX_DRAWDOWN` | 30% | 硬性禁用阈值 |

## 平台支持

| 交易所 | 状态 | 文件 |
|----------|--------|------|
| Nado DEX | 已支持 | `live_nado_quant.py` |
| OKX | 已支持 | `live_okx_quant.py` |

## 参与贡献

欢迎贡献！请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解如何参与。

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
