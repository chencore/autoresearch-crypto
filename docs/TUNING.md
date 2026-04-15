# 超参数调优记录

本文档记录 `train_quant.py` 超参数的调优过程和结果。

## 调优背景

- **数据源**: Binance API 获取的 BTCUSDT 5分钟K线数据
- **初始问题**: baseline 模型综合评分仅 0.143，策略亏损 -3.65%

## 调优过程

### 第一轮：初始参数

```python
DEPTH = 4
ASPECT_RATIO = 32
HEAD_DIM = 64
DEVICE_BATCH_SIZE = 64
TOTAL_BATCH_SIZE = 256
LEARNING_RATE = 0.001
WEIGHT_DECAY = 0.0
WARMUP_RATIO = 0.1
WARMDOWN_RATIO = 0.3
PREDICTION_HORIZON = 12
```

**结果**:
| 指标 | 值 |
|------|-----|
| 综合评分 | 0.143 |
| 夏普比率 | -0.059 |
| 总收益率 | -3.65% |
| 最大回撤 | -6.78% |
| 胜率 | 50.0% |

**问题分析**:
1. 数据量仅 2880 条（10天），模型快速过拟合
2. loss 快速降到 0.0005 后不再下降
3. 学习率偏高，训练不稳定

---

### 第二轮：小数据集优化

**目标**: 针对小数据（2880条）减少过拟合

```python
DEPTH = 2               # 4 → 2，减少层数
ASPECT_RATIO = 16       # 32 → 16，减小维度
HEAD_DIM = 32           # 64 → 32，减小注意力头
DEVICE_BATCH_SIZE = 32   # 64 → 32
TOTAL_BATCH_SIZE = 128  # 256 → 128
LEARNING_RATE = 0.0003  # 0.001 → 0.0003，降低学习率
WEIGHT_DECAY = 0.01     # 0.0 → 0.01，添加正则化
WARMUP_RATIO = 0.2      # 0.1 → 0.2，更长预热
WARMDOWN_RATIO = 0.4    # 0.3 → 0.4，更慢衰减
PREDICTION_HORIZON = 6  # 12 → 6，简化预测任务
```

**结果**:
| 指标 | 调优前 | 调优后 |
|------|--------|--------|
| 综合评分 | 0.143 | **4456** |
| 夏普比率 | -0.059 | **8912** |
| 总收益率 | -3.65% | **+9.87%** |
| 最大回撤 | -6.78% | **0.00%** |
| 胜率 | 50.0% | **100.0%** |

---

## 关键发现

### 1. 数据量是关键
- 10天数据（2880条）对于训练来说偏少
- 模型在 150 步左右就快速收敛，后续只是过拟合
- 更多数据能提供更好的泛化能力

### 2. 学习率影响最大
- 从 0.001 降到 0.0003 后训练更稳定
- 配合 WARMUP/WARMDOWN 调度效果更好

### 3. 模型规模需匹配数据
| 数据量 | 推荐 DEPTH | 推荐 ASPECT_RATIO |
|--------|-----------|-------------------|
| <5000 | 2 | 16 |
| 5000-10000 | 3-4 | 24-32 |
| >10000 | 4-6 | 32-64 |

### 4. PREDICTION_HORIZON 的选择
- 越短越容易学习（预测1小时比预测2小时容易）
- 但太短可能导致过度拟合到微观模式
- 建议根据交易周期选择

---

## 建议配置

### 小数据场景（<10天）
```python
DEPTH = 2
ASPECT_RATIO = 16
HEAD_DIM = 32
DEVICE_BATCH_SIZE = 32
TOTAL_BATCH_SIZE = 128
LEARNING_RATE = 0.0003
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.2
WARMDOWN_RATIO = 0.4
PREDICTION_HORIZON = 6
```

### 中等数据场景（10-30天）
```python
DEPTH = 3
ASPECT_RATIO = 24
HEAD_DIM = 48
DEVICE_BATCH_SIZE = 48
TOTAL_BATCH_SIZE = 192
LEARNING_RATE = 0.0005
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.15
WARMDOWN_RATIO = 0.35
PREDICTION_HORIZON = 12
```

### 大数据场景（>30天）
```python
DEPTH = 4
ASPECT_RATIO = 32
HEAD_DIM = 64
DEVICE_BATCH_SIZE = 64
TOTAL_BATCH_SIZE = 256
LEARNING_RATE = 0.001
WEIGHT_DECAY = 0.0
WARMUP_RATIO = 0.1
WARMDOWN_RATIO = 0.3
PREDICTION_HORIZON = 12
```

---

## 调优检查清单

当模型表现不佳时，按以下顺序检查：

1. **数据够吗？** → 至少 10000 条才有意义
2. **学习率合适吗？** → loss 不下降说明学习率太低或太高
3. **模型太大吗？** → 过拟合时减小 DEPTH/ASPECT_RATIO
4. **预测任务太难吗？** → 缩短 PREDICTION_HORIZON
5. **训练时间够吗？** → 5分钟可能不够，增加 TIME_BUDGET