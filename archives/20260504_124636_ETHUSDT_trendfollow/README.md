# ETHUSDT trendfollow 策略归档

- **归档时间**: 2026-05-04 12:46:36
- **交易对**: ETHUSDT
- **策略模式**: trendfollow
- **数据量**: 17280 条K线
- **价格范围**: 1842.45 - 2461.47

## 回测指标

| 指标 | 数值 |
|------|------|
| 综合评分 | -0.396337 |
| 总收益率 | -39.63% |
| 年化收益率 | -45.96% |
| 夏普比率 | -0.9186 |
| 最大回撤 | -45.26% |
| 胜率 | 31.2% |
| 交易笔数 | 924 |

## 策略参数

```json
{
  "long_ma_period": 50,
  "pull_ma_period": 20,
  "atr_period": 7,
  "atr_multiplier": 3.0,
  "max_hold_bars": 24,
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
