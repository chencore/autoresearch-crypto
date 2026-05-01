# 加密货币量化策略研究 — GEPA 反思式进化

## 核心思想

不是随机调参，而是**带着假设做实验**。Agent 每次实验后写反思日志，每5轮元反思归纳盲点、提出新假设。

## ATLAS 多策略进化（超级投资者模型）

同时维护 4 个独立的分支策略：

| Agent | 风格 | 策略类 | 优化方向 |
|-------|------|--------|---------|
| Alpha | 趋势跟踪 | TrendStrategy | 降低假突破损耗，提高胜率 |
| Beta  | 均值回归 | PureActionStrategy | 捕捉极端超买超卖 |
| Gamma | 网格交易 | GridStrategy | 优化网格间距和层数 |
| Delta | 事件驱动 | HybridMeanRevMomentumStrategy | 捕捉爆拉/暴跌后的反转 |

### 进化循环

```
每 5 轮迭代：
  1. 对比 4 个 Agent 的近期夏普
  2. 最差 Agent → 学习最优 Agent 的"基因"（参数交叉）
  3. 最优 Agent → 加密探索（更大胆的参数突变）
  4. 达尔文选择：软最大化权重分配
```

### 自适应策略组合

结果是 4 个 Agent 的动态加权组合，在不同市场风格下自动调整投票权重。

## GEPA 反思式进化（科学方法）

### 实验循环

```
假设 → 实验 → 反思 → 元分析 → 新假设
```

### 实验日志结构

每次实验记录：
```json
{
  "agent": "Delta",
  "hypothesis": "收紧RSI超卖线过滤震荡市假信号",
  "tried": "rsi_low: 30 → 25",
  "score_before": 0.35, "score_after": 0.42,
  "sharpe_before": -0.17, "sharpe_after": 0.08,
  "reflection": "RSI收紧确实减少了假信号，但注意高波动期表现不同。下一步：给RSI阈值加volatility条件分支。"
}
```

### 元反思（每5轮）

读取最近 5 条反思日志 → 归纳盲点 → 提出新假设 + 验证实验。

### 假设模板（领域知识注入）

| # | 假设 | 目标 Agent |
|---|------|-----------|
| 1 | 低波动时收紧入场阈值减少假信号 | Beta |
| 2 | 高波动时放宽止损避免噪音震出 | Alpha |
| 3 | 强趋势市用 ADX 过滤逆势交易 | Alpha |
| 4 | RSI 超卖线根据波动率动态调整 | Delta |
| 5 | 均值回归在震荡市缩短持仓更有效 | Beta |
| 6 | 网格间距用 ATR 自适应而非固定百分比 | Gamma |
| 7 | 多因子共振过滤单一指标假信号 | Alpha |
| 8 | 不同市场状态使用不同参数集 | Beta |

## 项目结构

```
autoresearch-dex/
├── dex/                          # 核心包
│   ├── strategies/               # 8 个策略类
│   │   ├── trend.py, scalp.py, pure_action.py
│   │   ├── hybrid.py, trend_follow.py
│   │   ├── hybrid_mm.py, adaptive.py, grid.py
│   │   └── base.py               # BaseStrategy + StrategyEvaluator
│   ├── indicators.py             # 共享技术指标
│   ├── evolution.py              # ATLAS 多策略进化引擎
│   ├── reflection.py             # GEPA 反思式进化引擎
│   ├── config.py                 # 集中配置
│   ├── data.py, live/
│   └── search/
├── scripts/
│   ├── evolve.py                 # ATLAS 进化运行
│   └── evolve_gepa.py            # GEPA 反思式进化运行
├── live_nado_quant.py            # Nado DEX 实盘
├── train_quant.py                # 原始策略（向后兼容）
└── prepare_crypto.py             # 数据下载
```

## 快速开始

```bash
# 数据准备
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60

# ATLAS 多策略进化
uv run python scripts/evolve.py --generations 20

# GEPA 反思式进化
uv run python scripts/evolve_gepa.py --cycles 20

# 实盘交易
uv run python live_nado_quant.py --ticker ETH --interval 5m --capital 100
```

## 进化输出

- `reflection_logs/exp_*.json` — 每次实验的完整日志和反思
- `search_results/evolution_result.json` — ATLAS 进化结果
- `search_results/gepa_result.json` — GEPA 进化结果

## 注意事项

- 进化评分使用相对市场基准（跑赢买持即得分）
- 策略可能亏损，请谨慎实盘
- GEPA 实验日志可被 LLM 读取做更深层分析
- 过拟合是常见问题，交叉验证优于单次高分
