# ETHUSDT smart 策略归档

- **归档时间**: 2026-04-28 20:19:19
- **交易对**: ETHUSDT
- **策略模式**: smart
- **数据量**: 17280 条K线
- **价格范围**: 1842.45 - 2461.47
- **市场状态**: strong_downtrend
- **ADX**: 32.3
- **EMA趋势**: downtrend
- **价格偏离EMA200**: -0.88%
- **年化波动率**: 42.4%
- **选中策略**: hybrid_mm

## 回测指标

| 指标 | 数值 |
|------|------|
| 综合评分 | 0.028651 |
| 总收益率 | 2.87% |
| 年化收益率 | 3.50% |
| 夏普比率 | 0.4855 |
| 最大回撤 | -2.48% |
| 胜率 | 53.1% |
| 交易笔数 | 81 |

## 策略参数

```json
{
  "rsi_period": 14,
  "rsi_low": 20,
  "rsi_high": 65,
  "ma_period": 10,
  "atr_period": 14,
  "atr_multiplier": 2.5,
  "max_hold_bars": 24,
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
