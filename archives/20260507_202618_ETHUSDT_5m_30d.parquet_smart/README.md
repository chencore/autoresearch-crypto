# ETHUSDT_5m_30d.parquet smart 策略归档

- **归档时间**: 2026-05-07 20:26:18
- **交易对**: ETHUSDT_5m_30d.parquet
- **策略模式**: smart
- **数据量**: 8640 条K线
- **价格范围**: 2062.61 - 2463.23
- **市场状态**: weak_downtrend
- **ADX**: 19.5
- **EMA趋势**: downtrend
- **价格偏离EMA200**: -0.97%
- **年化波动率**: 41.2%
- **选中策略**: hybrid_mm

## 回测指标

| 指标 | 数值 |
|------|------|
| 综合评分 | 0.003010 |
| 总收益率 | 0.30% |
| 年化收益率 | 3.73% |
| 夏普比率 | 0.7472 |
| 最大回撤 | -1.32% |
| 胜率 | 25.0% |
| 交易笔数 | 16 |

## 策略参数

```json
{
  "rsi_period": 14,
  "rsi_low": 20,
  "rsi_high": 65,
  "ma_period": 20,
  "atr_period": 7,
  "atr_multiplier": 1.5,
  "max_hold_bars": 12,
  "enable_short": true,
  "ema_tolerance": 0.0
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
