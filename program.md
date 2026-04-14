# 加密货币量化策略研究

本项目基于 autoresearch 架构，将自主实验循环应用于加密货币量化策略研究。

## 核心思想

给予 AI Agent 一个加密货币策略研发环境，让其自主探索最优策略。你可以在睡觉时启动实验，醒来后查看实验结果和绩效报告。

## 实验设置

### 初始化

1. **创建分支**: `git checkout -b quant/<tag>` (如 `quant/apr14`)
2. **准备数据**: 运行 `uv run python prepare_crypto.py --limit 60` 下载K线数据
3. **验证环境**: 运行 `uv run python train_quant.py` 确保代码正常运行

### 核心文件

- **`train_quant.py`** — 策略模型、训练循环、评估引擎。**这是你修改的唯一文件**。
- **`prepare_crypto.py`** — 数据下载和预处理。**请勿修改**。
- **`data/crypto/`** — K线数据存储目录

### 实验循环

**重要**: 与原版 autoresearch 不同，这里的优化目标是**最大化**综合评分（越高越好），而非最小化 val_bpb。

LOOP FOREVER:

1. 查看 git 状态和当前分支
2. 修改 `train_quant.py` 中的策略模型、超参数或训练设置
3. `git commit`
4. 运行: `uv run python train_quant.py > run.log 2>&1`
5. 检查结果: `grep "^综合评分:\|^夏普比率:" run.log`
6. 记录到 `results.tsv` (不提交到git)
7. 如果评分提升 → 保留提交
8. 如果评分下降 → `git reset --hard`

## 评估指标

### 主指标: 综合评分 (composite_score)

```
综合评分 = 夏普比率 × 0.5 + 总收益率 × 0.3 + 胜率 × 0.1 + (1+回撤) × 0.1
```

### 辅助指标

| 指标 | 描述 | 优化方向 |
|------|------|---------|
| sharpe_ratio | 夏普比率（收益/风险） | 越高越好 |
| total_return | 总收益率 | 越高越好 |
| max_drawdown | 最大回撤 | 越接近0越好 |
| win_rate | 胜率 | 越高越好 |

### 评估规则

- **固定时间预算**: 5分钟训练 + 评估
- **验证集**: 数据的前10%用于验证
- **模拟交易**: 考虑0.1%手续费 + 0.05%滑点

## 可调超参数

在 `train_quant.py` 中直接修改:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| DEPTH | 4 | Transformer层数 |
| ASPECT_RATIO | 32 | 模型维度=depth*ratio |
| HEAD_DIM | 64 | 注意力头维度 |
| DEVICE_BATCH_SIZE | 64 | 批量大小 |
| TOTAL_BATCH_SIZE | 256 | 总批量 |
| LEARNING_RATE | 0.001 | 学习率 |
| WEIGHT_DECAY | 0.0 | 权重衰减 |
| MAX_SEQ_LEN | 256 | 时间窗口长度 |

## 数据说明

### 数据源

使用 Binance public Klines API，无需 API key。数据包括:
- OHLCV 基础数据
- 计算的技术指标: returns, volatility, RSI, MACD, Bollinger Bands, ATR, volume_ratio

### 特征列

- 原始: open, high, low, close, volume
- 指标: returns, volatility, rsi, macd, macd_signal, macd_hist
- 布林带: bb_upper, bb_mid, bb_lower
- 其他: atr, volume_ratio

## 策略说明

### 信号类型

- **卖出 (-1)**: 预测未来价格下跌
- **持有 (0)**: 预测未来价格基本持平
- **买入 (+1)**: 预测未来价格上涨

### 模型架构

使用 Transformer 架构处理时间序列:
- MarketEncoder: 数值特征 → 嵌入
- Transformer Blocks: 因果注意力
- SignalHead: 输出3类信号

## 结果记录

`results.tsv` 格式（tab分隔）:

```
commit    score    sharpe    return    max_dd    status    description
a1b2c3d   0.823    1.45      0.052     -0.031    keep      baseline
b2c3d4e   0.891    1.62      0.068     -0.028    keep      increase depth to 6
```

## 注意事项

- 模型输出是交易信号，不是价格预测
- 策略可能亏损，请谨慎实盘
- 5分钟训练时间有限，不宜用过复杂模型
- 过拟合是常见问题，简单策略往往更稳定
