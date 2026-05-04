# ETHUSDT hybrid_mm 策略归档

- **归档时间**: 2026-05-04 12:39:04
- **交易对**: ETHUSDT
- **策略模式**: hybrid_mm
- **数据量**: 2880 条K线
- **价格范围**: 2258.24 - 2461.47

## 回测指标

| 指标 | 数值 |
|------|------|
| 综合评分 | -0.000912 |
| 总收益率 | -0.09% |
| 年化收益率 | -0.67% |
| 夏普比率 | -0.2590 |
| 最大回撤 | -0.40% |
| 胜率 | 40.0% |
| 交易笔数 | 5 |

## 策略参数

```json
{
  "rsi_period": 14,
  "rsi_low": 20,
  "rsi_high": 65,
  "ma_period": 15,
  "atr_period": 7,
  "atr_multiplier": 1.5,
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
