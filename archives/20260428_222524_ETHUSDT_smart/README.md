# ETHUSDT smart 策略归档

- **归档时间**: 2026-04-28 22:25:24
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
| 综合评分 | 0.028885 |
| 总收益率 | 2.89% |
| 年化收益率 | 3.53% |
| 夏普比率 | 0.3965 |
| 最大回撤 | -1.87% |
| 胜率 | 51.3% |
| 交易笔数 | 76 |

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
  "enable_short": true,
  "tp_atr_multiplier": 0,
  "volume_threshold": 0,
  "htf_ma_period": 0,
  "use_ema_cross_exit": true,
  "rsi_vol_adjust": true
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
