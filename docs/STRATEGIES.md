# Strategy Catalog

This document describes all trading strategies available in `autoresearch-dex`.

## Summary Table

| Strategy | Class | Type | Market Condition | Frequency | Risk Level |
|----------|-------|------|------------------|-----------|------------|
| **HybridMM** | `HybridMeanRevMomentumStrategy` | Mean-reversion + Momentum | Range + Trend | Low (~2/day) | Medium |
| **Trend** | `TrendStrategy` | Bollinger Mean-reversion | Range-bound | Very Low | Medium |
| **Scalp** | `ScalpStrategy` | High-frequency Scalping | High Volatility | Very High | High |
| **TrendFollow** | `TrendFollowStrategy` | Trend Following | Strong Trends | Low | Medium |
| **Grid** | `GridStrategy` | Grid Trading | Sideways | Medium | Low |
| **PureAction** | `PureActionStrategy` | Extremes Reversal | Overbought/Oversold | Medium | Medium |
| **Hybrid** | `HybridMomentumStrategy` | Momentum + Mean-reversion | Mixed | Medium | Medium |
| **Adaptive** | `AdaptiveHybridStrategy` | Self-adapting | All regimes | Medium | Medium |
| **Regime** | `RegimeStrategy` | Regime-aware | Trending/Range | Low | Medium |
| **MultiTF** | `MultiTFEnsembleStrategy` | Multi-timeframe Ensemble | All regimes | Medium | Medium |
| **PureActionV2** | `PureActionV2Strategy` | Enhanced Reversal | Extreme moves | Medium | Medium |

---

## HybridMM (Recommended)

**File**: `dex/strategies/hybrid_mm.py`

A hybrid strategy combining RSI mean-reversion with EMA trend alignment.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `rsi_period` | 5 | RSI lookback period |
| `rsi_low` | 28 | RSI oversold threshold |
| `rsi_high` | 72 | RSI overbought threshold |
| `ma_period` | 25 | EMA trend filter period |
| `atr_period` | 12 | ATR lookback for stop sizing |
| `atr_multiplier` | 3.5 | ATR multiplier for stop distance |
| `max_hold_bars` | 48 | Maximum position hold time (bars) |
| `take_profit_pct` | 3% | Take-profit percentage |
| `stop_loss_pct` | 2% | Stop-loss percentage |

### Logic

1. **Long Entry**: RSI < 28 AND price > EMA (trend alignment)
2. **Short Entry**: RSI > 72 AND price < EMA
3. **Exit**: TP/SL hit OR max hold time reached

### Characteristics

- Low win rate (~38%) but high profit factor (~3.5:1)
- Best suited for ETH 5m timeframe
- Requires patience — few trades but large wins

---

## TrendStrategy

**File**: `dex/strategies/trend.py`

Bollinger Band mean-reversion with ADX trend filtering.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window` | 20 | Bollinger Band period |
| `std_dev` | 2.0 | Standard deviation multiplier |
| `atr_multiplier` | 2.5 | ATR stop multiplier |
| `max_hold_bars` | 48 | Maximum hold time |
| `adx_threshold` | 25 | ADX trend strength filter |
| `take_profit_pct` | 5% | Take-profit percentage |
| `stop_loss_pct` | 3% | Stop-loss percentage |

### Logic

1. **Long Entry**: Price touches lower band + ADX > 25 (trend exists)
2. **Short Entry**: Price touches upper band + ADX > 25
3. **Exit**: Mean-reversion to band center OR TP/SL

---

## ScalpStrategy

**File**: `dex/strategies/scalp.py`

High-frequency scalping for volatile markets.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window` | 10 | BB period |
| `std_dev` | 1.2 | Tight BB multiplier |
| `take_profit_pct` | 0.5% | Very tight TP |
| `stop_loss_pct` | 0.3% | Tight SL |
| `max_hold_bars` | 6 | Very short hold |

### Characteristics

- 10-50+ trades per day
- Small wins/losses, relies on volume
- High commission impact — best on low-fee DEX

---

## TrendFollowStrategy

**File**: `dex/strategies/trend_follow.py`

Pure trend following with moving average crossovers.

### Logic

1. **Long Entry**: Fast MA crosses above Slow MA + volume confirmation
2. **Short Entry**: Fast MA crosses below Slow MA
3. **Exit**: Trailing ATR stop OR MA reversal

---

## GridStrategy

**File**: `dex/strategies/grid.py`

Fixed grid trading for sideways markets.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `grid_levels` | 5 | Number of grid layers |
| `grid_spacing` | 1.0% | Distance between grids |
| `max_position` | 3 | Maximum concurrent positions |

### Characteristics

- Profitable in range-bound markets
- Dangerous in trending markets (unbounded risk)
- Requires careful capital allocation per grid

---

## AdaptiveHybridStrategy

**File**: `dex/strategies/adaptive.py`

Self-adapting strategy that switches between mean-reversion and trend-following based on market regime.

### Logic

- Detects regime via volatility and trend strength
- Uses mean-reversion parameters in low-volatility regimes
- Uses trend-following parameters in high-volatility regimes
- Automatically adjusts position sizing

---

## Risk Notes

- All strategies can and will lose money. No strategy is profitable in all market conditions.
- Backtest results do not guarantee live performance.
- Always start with paper trading or small capital.
- Diversify across multiple uncorrelated strategies when possible.
