# autoresearch-crypto

<div align="right"><a href="README.md">中文</a></div>

Autonomous cryptocurrency quantitative trading strategy research framework. Evolved from the autoresearch paradigm — instead of optimizing LLMs, this framework lets AI agents autonomously discover, evolve, and execute crypto trading strategies.

> **Disclaimer**: This project is for research and educational purposes. Trading cryptocurrencies carries significant risk. Past performance does not guarantee future results. Use at your own risk.

## Overview

autoresearch-crypto is a modular quantitative trading framework designed for:

- **Strategy Research**: Automated search and optimization of trading strategies via evolutionary algorithms
- **Backtesting**: High-fidelity backtesting with realistic fee/slippage modeling
- **Live Trading**: Production-ready integration with Nado DEX and OKX
- **Agent-Driven Evolution**: ATLAS multi-strategy evolution and GEPA reflective evolution engines

## Features

| Feature | Description |
|---------|-------------|
| **11 Built-in Strategies** | Trend, mean-reversion, scalping, grid, hybrid momentum, adaptive, and more |
| **ATLAS Evolution** | Multi-agent competitive evolution across strategy archetypes |
| **GEPA Reflection** | Reflective learning with edge-case detection and strategy-death recovery |
| **Market Regime Detection** | Automatic regime classification (trending / ranging / volatile) |
| **Multi-Timeframe Analysis** | Ensemble signals across 1m, 5m, 15m, 1h, 4h timeframes |
| **Maker/Taker Fee Optimization** | Hybrid execution: POST_ONLY (Maker) for entry/take-profit, IOC (Taker) for stop-loss |
| **Walk-Forward Validation** | Out-of-sample testing with multiple windows |

## Quick Start

### Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager (recommended)
- A single NVIDIA GPU (optional, for strategy search — 8GB+ VRAM recommended)

### Installation

```bash
# 1. Clone the repository
git clone <repo-url> autoresearch-crypto
cd autoresearch-crypto

# 2. Install dependencies
uv sync

# 3. Prepare your environment
cp .env.example .env
# Edit .env and add your API keys / private keys
```

### Download Market Data

```bash
# Download ETH 5-minute data (60 days)
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60
```

### Strategy Search

```bash
# Quick scan (5 minutes)
uv run python search_eth_optimal.py --quick

# Full search (recommended, ~20 minutes)
uv run python search_eth_optimal.py
```

### Backtest

```bash
# Backtest with default strategy
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 60

# Backtest with a specific checkpoint
uv run python backtest_quant.py \
    --symbol ETHUSDT --interval 5m --days 60 \
    --checkpoint checkpoints/hybrid_mm_eth60d.pt
```

### Live Trading (Nado DEX)

```bash
uv run python live_nado_quant.py \
    --ticker ETH \
    --interval 5m \
    --capital 100 \
    --checkpoint checkpoints/hybrid_mm_eth60d.pt
```

## Project Structure

```
autoresearch-crypto/
├── live_nado_quant.py          # Nado DEX live trading
├── live_okx_quant.py           # OKX live trading
├── backtest_quant.py           # Backtesting engine
├── train_quant.py              # Strategy training / search
├── search_eth_optimal.py       # ETH optimal strategy search
├── search_deep.py              # Deep parameter search
├── prepare_crypto.py           # Market data downloader
├── dex/                        # Core framework
│   ├── strategies/             # Strategy implementations (11 classes)
│   ├── indicators.py           # Technical indicators
│   ├── evolution.py            # ATLAS evolution engine
│   ├── reflection.py           # GEPA reflective evolution
│   ├── scoring.py              # Risk-adjusted scoring
│   ├── market_regime.py        # Regime detection
│   ├── config.py               # Centralized constants
│   └── live/common.py          # Shared live trading utilities
├── checkpoints/                # Strategy checkpoints (kept: quant_model.pt)
├── docs/                       # Documentation
│   ├── STRATEGIES.md           # Strategy catalog
│   └── 策略.md                  # Original Chinese strategy notes
└── scripts/                    # Utility scripts
    ├── evolve.py               # Run ATLAS evolution
    ├── evolve_gepa.py          # Run GEPA evolution
    └── train_all.py            # Train all strategy types
```

## Strategies

| Strategy | Type | Best For | Frequency |
|----------|------|----------|-----------|
| **HybridMM** | Mean-reversion + Momentum | Range + Trend | Low (~2/day) |
| TrendStrategy | Bollinger Band Mean-reversion | Range-bound | Very Low |
| ScalpStrategy | High-frequency Scalping | High Volatility | Very High |
| TrendFollowStrategy | Trend Following | Strong Trends | Low |
| AdaptiveHybridStrategy | Self-adapting | Mixed Regimes | Medium |
| GridStrategy | Grid Trading | Sideways | Medium |
| PureActionStrategy | Extremes Reversal | Overbought/Oversold | Medium |

See [docs/STRATEGIES.md](docs/STRATEGIES.md) for full details.

## Evolution Engines

### ATLAS Multi-Strategy Evolution

Competitive evolution where multiple agents (Alpha, Beta, Gamma, Delta) evolve different strategy archetypes simultaneously:

```bash
uv run python scripts/evolve.py --generations 30
```

### GEPA Reflective Evolution

Reflective learning with forced hypothesis logging every 5 rounds:

```bash
uv run python scripts/evolve_gepa.py --cycles 30
```

## Risk Scoring

The framework uses a risk-penalized score instead of raw Sharpe ratio to prevent agents from favoring high-leverage / high-drawdown strategies:

```
Score = Sharpe * (1 - |MaxDD|)^-1 * TradePenalty * EdgeGuard
```

## Configuration

All trading defaults are centralized in [`dex/config.py`](dex/config.py):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `INITIAL_CAPITAL` | 10000.0 | Backtest initial capital |
| `COMMISSION` | 0.02% | DEX Maker fee |
| `SLIPPAGE` | 0.02% | Execution slippage |
| `EVAL_MAX_DRAWDOWN` | 30% | Hard disable threshold |

## Platform Support

| Exchange | Status | File |
|----------|--------|------|
| Nado DEX | Supported | `live_nado_quant.py` |
| OKX | Supported | `live_okx_quant.py` |

## Contributing

Contributions are welcome. Please open an issue or pull request.

## License

MIT — see [LICENSE](LICENSE).
