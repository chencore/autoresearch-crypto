# 超参数调优指南

本文档详细介绍 `train_quant.py` 中各超参数的作用及调优建议。

## 超参数速查表

| 参数 | 默认值 | 可调范围 | 影响 |
|------|--------|----------|------|
| DEPTH | 4 | 2-8 | 模型层数，越多越复杂 |
| ASPECT_RATIO | 32 | 16-64 | 模型维度 = DEPTH × ASPECT_RATIO |
| HEAD_DIM | 64 | 32-128 | 注意力头维度 |
| DEVICE_BATCH_SIZE | 64 | 16-128 | GPU 批量大小 |
| TOTAL_BATCH_SIZE | 256 | 64-512 | 总批量大小 |
| LEARNING_RATE | 0.001 | 0.0001-0.01 | 学习率 |
| MAX_SEQ_LEN | 256 | 128-512 | 时间窗口长度 |
| WARMUP_RATIO | 0.1 | 0.05-0.2 | 预热阶段比例 |
| WARMDOWN_RATIO | 0.3 | 0.2-0.5 | 冷却阶段比例 |

---

## 模型架构参数

### DEPTH（Transformer 层数）

**作用**：控制模型深度，即有多少层 Transformer 块。

**调优建议**：
| 值 | 适用场景 | 风险 |
|----|----------|------|
| 2-3 | 数据少（<5000样本）、训练时间短 | 欠拟合 |
| 4-6 | 默认选择，数据量充足 | 平衡 |
| 6-8 | 数据多（>10000样本）、需要复杂模式 | 过拟合 |

**经验**：
- 5分钟训练预算下，DEPTH=4 是较好的起点
- 每增加一层，训练时间约增加 15-20%

---

### ASPECT_RATIO（维度系数）

**作用**：决定模型隐藏层维度。计算公式：`n_embd = DEPTH × ASPECT_RATIO`

| DEPTH | ASPECT_RATIO | n_embd | 参数量 |
|-------|--------------|--------|--------|
| 4 | 16 | 64 | 最小 |
| 4 | 32 | 128 | 中等 |
| 4 | 64 | 256 | 最大 |

**调优建议**：
- 数据少 → 用小 ASPECT_RATIO（16-24）
- 数据多 → 用大 ASPECT_RATIO（32-64）
- 与 DEPTH 配合调整：深度增加时减小 ASPECT_RATIO，反之亦然

---

### HEAD_DIM（注意力头维度）

**作用**：每个注意力头的维度，影响注意力计算的细腻度。

**调优建议**：
- 默认 64 适用于大多数场景
- 减小到 32 可加快训练速度，但可能损失精度
- 增加需谨慎，计算量增长较快

---

## 训练参数

### DEVICE_BATCH_SIZE（GPU 批量大小）

**作用**：每次在单个 GPU 上处理的样本数。

**调优建议**：
| 值 | VRAM 需求 | 适用场景 |
|----|-----------|----------|
| 32 | ~4GB | 显存有限（8GB 以下） |
| 64 | ~8GB | 默认，8GB 显卡 |
| 128 | ~16GB | 高端显卡，16GB+ |

**注意**：如果显存不足会报 OOM 错误，需减小此值。

---

### TOTAL_BATCH_SIZE（总批量大小）

**作用**：累计多个设备的批量大小，影响梯度更新稳定性。

**计算公式**：`gradient_accumulation_steps = TOTAL_BATCH_SIZE // DEVICE_BATCH_SIZE`

**调优建议**：
- 越大越稳定，但每个 epoch 时间更长
- 最小值不应低于 64
- 与 LEARNING_RATE 配合：批量大时学习率可适当提高

---

### LEARNING_RATE（学习率）

**作用**：控制权重更新的步长。

**调优建议**：
| 值 | 效果 | 适用场景 |
|----|------|----------|
| 0.0001-0.0005 | 收敛慢但稳定 | 训练不稳定时 |
| 0.001 | 默认平衡 | 一般情况 |
| 0.002-0.005 | 收敛快 | 数据充足、需快速迭代 |

**警告**：学习率过大会导致 loss 爆炸（变为 nan 或 inf），此时应降低学习率。

---

### MAX_SEQ_LEN（时间窗口长度）

**作用**：每个样本包含多少根 K线，决定模型能看到多长远的历史。

**计算**：MAX_SEQ_LEN × K线周期 = 模型视野
- 256 × 5分钟 = 21.3 小时
- 256 × 15分钟 = 64 小时
- 512 × 5分钟 = 42.7 小时

**调优建议**：
| 值 | 适用场景 |
|----|----------|
| 128-192 | 短期波动交易 |
| 256 | 默认，平衡选择 |
| 384-512 | 趋势跟踪，需要更远视野 |

**注意**：值越大显存消耗越多。

---

## 学习率调度参数

### WARMUP_RATIO（预热比例）

**作用**：训练开始时学习率从 0 逐渐增加到目标值的阶段比例。

**实际计算**：
```
warmup_steps = total_steps × 0.1
# 例如 total_steps=600，则前 60 步为预热阶段
```

**调优建议**：
- 0.05-0.1 是稳定的选择
- 过小（<0.05）可能导致早期训练不稳定
- 过大（>0.2）会浪费训练时间

---

### WARMDOWN_RATIO（冷却比例）

**作用**：训练后期学习率从目标值逐渐降到 0 的阶段比例。

**实际计算**：
```
warmdown_start = warmup_steps + constant_steps
warmdown_steps = total_steps - warmdown_start
```

**调优建议**：
| 值 | 效果 |
|----|------|
| 0.2-0.3 | 默认，快速收敛场景 |
| 0.4-0.5 | 更精细调整，适合长训练 |

**注意**：此参数对 5 分钟预算影响不大，因为总步数较少。

---

## 综合调优策略

### 按优先级排序

1. **首先调整**：LEARNING_RATE（影响最大）
2. **其次调整**：DEPTH + ASPECT_RATIO（模型容量）
3. **最后调整**：BATCH_SIZE（影响稳定性）

### 快速迭代流程

```
1. baseline: DEPTH=4, ASPECT_RATIO=32, LR=0.001
   ↓
2. 如果 loss 不收敛 → 降低 LR 到 0.0005
   ↓
3. 如果过拟合 → 减小 DEPTH 或 ASPECT_RATIO
   ↓
4. 如果欠拟合 → 增加 DEPTH 或 ASPECT_RATIO
   ↓
5. 最终微调 BATCH_SIZE
```

### 不同目标的调优方向

**目标：快速 baseline**
```
DEPTH = 4
ASPECT_RATIO = 24
LEARNING_RATE = 0.002
MAX_SEQ_LEN = 256
```

**目标：稳定保守**
```
DEPTH = 3
ASPECT_RATIO = 32
LEARNING_RATE = 0.0005
WARMUP_RATIO = 0.15
WARMDOWN_RATIO = 0.4
```

**目标：高收益（高风险）**
```
DEPTH = 6
ASPECT_RATIO = 48
LEARNING_RATE = 0.001
MAX_SEQ_LEN = 384
```

---

## 常见问题处理

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| loss = nan | 学习率过高 | 降低 LR 到 0.0005 或 0.0001 |
| loss 不下降 | 学习率过低或模型容量不足 | 提高 LR 或增加 DEPTH |
| 训练很慢 | 模型太大或 BATCH_SIZE 过大 | 减小 DEPTH 或 BATCH_SIZE |
| 过拟合严重 | 模型太复杂 | 减小 DEPTH/ASPECT_RATIO，增加正则 |
| OOM 错误 | 显存不足 | 减小 DEVICE_BATCH_SIZE 或 MAX_SEQ_LEN |

---

## 参数修改位置

所有参数集中在 `train_quant.py` 文件顶部：

```python
# ===== 超参数 =====
DEPTH = 4
ASPECT_RATIO = 32
HEAD_DIM = 64
DEVICE_BATCH_SIZE = 64
TOTAL_BATCH_SIZE = 256
LEARNING_RATE = 0.001
MAX_SEQ_LEN = 256
WARMUP_RATIO = 0.1
WARMDOWN_RATIO = 0.3
```

修改后直接运行即可生效：
```bash
uv run python train_quant.py
```