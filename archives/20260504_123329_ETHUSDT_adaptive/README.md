# ETHUSDT adaptive 策略归档

- **归档时间**: 2026-05-04 12:33:29
- **交易对**: ETHUSDT
- **策略模式**: adaptive
- **数据量**: 2880 条K线
- **价格范围**: 2258.24 - 2461.47

## 回测指标

| 指标 | 数值 |
|------|------|
| 综合评分 | -0.158587 |
| 总收益率 | -15.86% |
| 年化收益率 | -72.91% |
| 夏普比率 | -1.8998 |
| 最大回撤 | -18.52% |
| 胜率 | 33.3% |
| 交易笔数 | 222 |

## 策略参数

```json
{
  "rsi_period": 14,
  "rsi_low": 25,
  "rsi_high": 65,
  "ma_period": 10,
  "trend_long_ma": 100,
  "trend_pull_ma": 20,
  "adx_threshold": 20,
  "adx_period": 14,
  "atr_period": 14,
  "atr_multiplier": 2.5,
  "max_hold_bars": 12,
  "enable_short": true
}
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `quant_model.pt` | PyTorch 模型/参数文件 |
| `params.json` | 策略参数 JSON |
| `metrics.json` | 回测指标 JSON |
| `equity.csv` | 权益曲线（每行一个时间步） |
| `trades.csv` | 交易记录（每笔交易的类型、步数、盈亏） |
| `README.md` | 本说明文档 |
