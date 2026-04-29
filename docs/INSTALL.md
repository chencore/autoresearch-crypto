# 加密货币量化策略研究工具

基于 autoresearch 架构的加密货币量化策略自动研究工具。让 AI Agent 在 5 分钟固定时间预算内自主探索最优交易策略。

## 环境要求

- Python 3.10+
- NVIDIA GPU (建议 8GB+ VRAM)
- 网络访问（需要代理访问加密货币交易所 API）

## 安装步骤

### 1. 克隆仓库

```bash
cd d:/10_skill
git clone <repository_url> autoresearch
cd autoresearch
```

### 2. 配置代理

工具通过代理访问 OKX 交易所 API。编辑 `prepare_crypto.py` 中的代理地址：

```python
# 第 24 行
PROXY = {"http": "http://127.0.0.1:50830", "https": "http://127.0.0.1:50830"}
```

将 `127.0.0.1:50830` 替换为你的代理地址。

**验证代理是否可用**：
```bash
python -c "import requests; r = requests.get('https://www.okx.com', proxies={'http': 'http://127.0.0.1:50830'}, timeout=10); print(r.status_code)"
```

### 3. 安装依赖

**重要**：项目使用 `uv` 管理依赖，必须通过 `uv run` 运行脚本：

```bash
cd d:/10_skill/autoresearch
uv sync
```

**所有脚本必须使用 `uv run` 运行**，否则会报 `ModuleNotFoundError`：

```bash
# 正确方式
uv run python prepare_crypto.py --limit 60

# 错误方式（会缺少模块）
python prepare_crypto.py --limit 60
```

### 4. 下载数据

下载 60 天 BTC + ETH 历史数据：
```bash
uv run python prepare_crypto.py --limit 60
```

查看数据：
```bash
ls -la d:/10_skill/autoresearch/data/crypto/
```

### 5. 验证安装

运行测试训练：
```bash
uv run python train_quant.py
```

成功输出：
```
============================================================
加密货币量化策略训练
============================================================
找到 1 个数据文件
  BTCUSDT_5m.parquet
加载数据...
数据集大小: 9600 个样本
...
综合评分:     0.143222
夏普比率:     -0.0594
总收益率:     -3.65%
...
```

## 使用方法

### 数据下载

```bash
# 下载 BTC 数据（1天）
uv run python prepare_crypto.py --symbol BTCUSDT --limit 1

# 下载 BTC + ETH 数据（7天）
uv run python prepare_crypto.py --symbol BTCUSDT --limit 7
uv run python prepare_crypto.py --symbol ETHUSDT --limit 7

# 下载多个交易对
uv run python prepare_crypto.py --limit 30  # 默认下载 BTCUSDT + ETHUSDT

# 查看帮助
uv run python prepare_crypto.py --help
```

**参数说明**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--symbol` | BTCUSDT,ETHUSDT | 交易对 |
| `--interval` | 5m | K线周期: 1m, 5m, 15m, 1h, 4h, 1d |
| `--limit` | 60 | 下载天数 |
| `--force` | - | 强制重新下载，即使文件已存在 |

### 训练策略

```bash
# 运行训练
uv run python train_quant.py

# 重定向输出到日志
uv run python train_quant.py > run.log 2>&1

# 提取结果
grep "^综合评分:" run.log
grep "^夏普比率:" run.log
```

### 配置代理

如果代理地址变更，修改 `prepare_crypto.py` 第 24 行：

```python
PROXY = {"http": "http://你的代理地址", "https": "http://你的代理地址"}
```

例如：
```python
PROXY = {"http": "http://192.168.1.100:7890", "https": "http://192.168.1.100:7890"}
```

## 项目结构

```
autoresearch/
├── prepare_crypto.py   # 数据下载脚本（下载K线、计算技术指标）
├── train_quant.py       # 策略训练脚本（模型、训练、评估）
├── program.md           # AI Agent 实验指令
├── CLAUDE.md           # Claude Code 指导文件
├── pyproject.toml       # 依赖配置
└── docs/
    └── INSTALL.md      # 本文档
```

## 核心文件说明

### prepare_crypto.py

数据准备脚本：
- 从 OKX 交易所下载 K线数据
- 计算技术指标（RSI、MACD、布林带、ATR 等）
- 保存为 Parquet 格式

**技术指标**：
| 指标 | 说明 |
|------|------|
| returns | 收益率 |
| volatility | 波动率 |
| rsi | RSI 相对强弱指数 |
| macd | MACD |
| macd_signal | MACD 信号线 |
| macd_hist | MACD 柱状图 |
| bb_upper/mid/lower | 布林带 |
| atr | ATR 平均真实范围 |
| volume_ratio | 成交量比率 |

### train_quant.py

策略训练脚本：
- MarketEncoder：数值特征 → 嵌入向量
- StrategyTransformer：Transformer 策略网络
- SignalHead：输出 3 类信号（卖出/持有/买入）
- StrategyEvaluator：绩效评估（夏普比率、最大回撤、胜率等）

**可调超参数**（文件内直接修改）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DEPTH` | 4 | Transformer 层数 |
| `ASPECT_RATIO` | 32 | 模型维度系数 |
| `HEAD_DIM` | 64 | 注意力头维度 |
| `DEVICE_BATCH_SIZE` | 64 | 批量大小 |
| `TOTAL_BATCH_SIZE` | 256 | 总批量 |
| `LEARNING_RATE` | 0.001 | 学习率 |
| `MAX_SEQ_LEN` | 256 | 时间窗口长度 |
| `WARMUP_RATIO` | 0.1 | 预热比例 |
| `WARMDOWN_RATIO` | 0.3 | 冷却比例 |

## 输出说明

训练完成后输出：

```
============================================================
结果
============================================================
综合评分:     0.143222
夏普比率:     -0.0594
总收益率:     -3.65%
年化收益率:   -97.57%
年化波动率:   1642.89%
最大回撤:     -6.78%
胜率:         50.0%
训练步数:     600
总耗时:       301.9s
```

**指标说明**：
| 指标 | 说明 | 优化方向 |
|------|------|---------|
| 综合评分 | 多指标加权综合 | 越高越好 |
| 夏普比率 | 收益/风险比 | 越高越好 |
| 总收益率 | 总收益百分比 | 越高越好 |
| 最大回撤 | 最大资产回落 | 越接近 0 越好 |
| 胜率 | 盈利交易占比 | 越高越好 |

## 常见问题

### Q: GPU 内存不足怎么办？

A: 减小 `DEVICE_BATCH_SIZE`（默认 64）在 `train_quant.py` 中。

### Q: 如何使用其他交易所？

A: 当前仅支持 OKX。如需其他交易所，修改 `prepare_crypto.py` 中的 `EXCHANGES` 配置。

### Q: 训练 loss 爆炸怎么办？

A: 检查 `WARMUP_RATIO` 和 `WARMDOWN_RATIO` 设置，或降低 `LEARNING_RATE`。

## 下一步

1. 下载更长时间的数据（60-90 天）
2. 调优超参数
3. 添加更多技术指标
4. 尝试不同的模型架构
