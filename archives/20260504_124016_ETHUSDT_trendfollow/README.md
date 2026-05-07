# ETHUSDT trendfollow 策略归档

- **归档时间**: 2026-05-04 12:40:16
- **交易对**: ETHUSDT
- **策略模式**: trendfollow
- **数据量**: 2880 条K线
- **价格范围**: 2258.24 - 2461.47

## 回测指标

| 指标 | 数值 |
|------|------|
| 综合评分 | -0.164453 |
| 总收益率 | -16.45% |
| 年化收益率 | -73.55% |
| 夏普比率 | -2.3338 |
| 最大回撤 | -18.14% |
| 胜率 | 29.6% |
| 交易笔数 | 294 |

## 策略参数

```json
{
  "long_ma_period": 200,
  "pull_ma_period": 20,
  "atr_period": 7,
  "atr_multiplier": 3.0,
  "max_hold_bars": 18,
  "entry_zone": 0.001,
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
