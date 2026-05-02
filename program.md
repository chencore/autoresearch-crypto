# 加密货币量化策略研究 — GEPA 反思式进化

## 核心思想

不是随机调参，而是**带着假设做实验**。Agent 每次实验后强制写反思日志，每5轮元反思归纳盲点、提出新假设。绝不停止——策略枯竭时自动换币种、换时间框架、换思路方向。

## 1. Metric 设计 — 风险惩罚评分

纯夏普会让 Agent 偏好高杠杆/高波动策略。改用**风险调整评分**：

```
Score = Sharpe × (1 - |MaxDD|)⁻¹ × TradePenalty × EdgeGuard
```

| 因子 | 公式 | 作用 |
|------|------|------|
| **RiskAdjust** | `(1 - abs(MaxDD))⁻¹` | 回撤越大分数越低，自然偏好低回撤 |
| **TradePenalty** | `min(1.0, n_trades / min_trades)` | 交易太少 → 惩罚（防过拟合） |
| **EdgeGuard** | `0 if MaxDD < -max_dd OR n_trades < min_trades` | 硬闸门：回撤过大或交易过少直接零分 |

相比之前的纯夏普评分，新 Metric 让 Agent **自然收敛到低回撤、有足够样本量的策略**。

## 2. 边缘案例标记 — 三道防线

每轮评估自动标记三种异常：

| 标记 | 触发条件 | 含义 | 处理 |
|------|---------|------|------|
| `⚠ OVERFIT` | 交易 < min_trades | 样本不足，可能是过拟合 | 冻结该 Agent 权重，不允许推广 |
| `🔥 RISKY` | 回撤 > max_dd | 过度冒险 | 强制回退参数，增加止损倍数 |
| `💤 DEAD` | 连续5轮 score 无变化 | 策略枯竭 | 触发探索模式：换参数空间/换 Agent 类型 |

## 3. 反思日志 — 强制每5轮写入

```json
{
  "cycle": 25,
  "agent": "Delta",
  "hypothesis": "收紧RSI超卖线过滤震荡市假信号",
  "tried": "rsi_low: 30 → 25",
  "score_before": 0.35, "score_after": 0.42,
  "sharpe_before": -0.17, "sharpe_after": 0.08,
  "dd_before": -0.15, "dd_after": -0.06,
  "edge_flags": [],
  "reflection": "RSI收紧减少了假信号，回撤从15%降到6%。但注意高波动期表现不同。下一步：给RSI阈值加volatility条件分支。",
  "meta_note": "本轮3/4 Agent均在震荡假设上改进——市场可能进入低波动期。建议下一轮用更窄的布林带。"
}
```

**关键规则**：如果 Agent 连续3轮写不出有效反思（`reflection` 为空或与上轮相同），标记为 `💤 DEAD`，触发策略枯竭处理。

## 4. 绝不停止 — Karpathy 风格自治循环

```
LOOP FOREVER:
  round += 1

  # --- 正常进化 ---
  FOR each agent IN agents:
    hypothesis = pick_hypothesis(agent, round)
    result = run_experiment(agent, hypothesis)
    write_reflection(result)
    mark_edge_cases(result)

  # --- 每5轮：元反思 ---
  IF round % 5 == 0:
    meta = read_last_5_reflections()
    blind_spots = detect_blind_spots(meta)
    new_hypotheses = generate_hypotheses(blind_spots)
    rebalance_weights(agents)

  # --- 枯竭检测 ---
  FOR each agent IN agents:
    IF agent.flag == DEAD:
      # 选项1：换币种
      agent.data = switch_symbol(current_symbol)
      # 选项2：换时间框架
      agent.interval = switch_interval(current_interval)
      # 选项3：换策略类型
      agent.strategy_cls = pick_different_strategy(agent.strategy_cls)
      # 选项4：换研究方向
      agent.hypothesis_queue = generate_alternative_hypotheses()

  # --- 永远不停止 ---
  GOTO LOOP
```

### 枯竭恢复策略

当 Agent 连续5轮无法改进时，按优先级尝试：

| 优先级 | 操作 | 说明 |
|--------|------|------|
| 1 | **换参数空间** | 扩大/缩小搜索边界，跳出现有局部最优 |
| 2 | **换策略类型** | 从策略池中随机选一个不同类型的策略 |
| 3 | **换时间框架** | 1m ↔ 5m ↔ 15m ↔ 1h，不同周期有不同特性 |
| 4 | **换币种** | ETH ↔ BTC ↔ SOL，不同币种相关性不同 |

## ATLAS 多策略进化

| Agent | 风格 | 策略类 | 优化方向 |
|-------|------|--------|---------|
| Alpha | 趋势跟踪 | TrendStrategy | 降低假突破损耗，提高胜率 |
| Beta  | 均值回归 | PureActionStrategy | 捕捉极端超买超卖 |
| Gamma | 网格交易 | GridStrategy | 优化网格间距和层数 |
| Delta | 事件驱动 | HybridMeanRevMomentumStrategy | 捕捉爆拉/暴跌后的反转 |

## 项目结构

```
autoresearch-dex/
├── dex/
│   ├── strategies/          # 8 个策略类
│   ├── indicators.py        # 共享技术指标
│   ├── evolution.py         # ATLAS 多策略进化引擎
│   ├── reflection.py        # GEPA 反思式进化 + 边缘标记 + 枯竭检测
│   ├── scoring.py            # Risk-adjusted scoring (新增)
│   ├── config.py, data.py
│   └── live/, search/
├── scripts/
│   ├── evolve.py            # ATLAS 进化运行
│   └── evolve_gepa.py       # GEPA 反思式进化运行
├── live_nado_quant.py       # Nado DEX 实盘
└── prepare_crypto.py        # 数据下载
```

## 快速开始

```bash
# 数据准备
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60

# ATLAS 多策略进化（含风险评分 + 边缘标记）
uv run python scripts/evolve.py --generations 30

# GEPA 反思式进化（含强制反思 + 枯竭恢复）
uv run python scripts/evolve_gepa.py --cycles 30

# 实盘交易
uv run python live_nado_quant.py --ticker ETH --interval 5m --capital 100
```

## 注意事项

- 评分优先低回撤：`Sharpe × (1-DD)⁻¹` 让 Agent 天然避险
- 强制反思：连续3轮无有效反思 → 标记枯竭 → 自动切换方向
- 代理可能亏损，请谨慎实盘
- 过拟合是常见问题，边缘标记 `OVERFIT` 会锁住过拟合策略的权重
