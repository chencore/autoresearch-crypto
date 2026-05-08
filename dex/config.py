"""Centralized configuration constants for the dex trading framework."""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = PROJECT_DIR / "data" / "crypto"
CHECKPOINT_DIR = PROJECT_DIR / "checkpoints"
LOG_DIR = PROJECT_DIR / "logs"
SEARCH_RESULTS_DIR = PROJECT_DIR / "search_results"

# Default checkpoint
DEFAULT_CHECKPOINT = CHECKPOINT_DIR / "quant_model.pt"

# Data files (used by downloader)
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_INTERVAL = "5m"
DEFAULT_START_DAYS = 60

# ---------------------------------------------------------------------------
# Trading defaults
# ---------------------------------------------------------------------------
INITIAL_CAPITAL: float = 10000.0
COMMISSION: float = 0.0002  # 0.02% — DEX Maker fee
SLIPPAGE: float = 0.0002  # 0.02% — execution slippage
MIN_NOTIONAL: float = 100.0  # Minimum order notional (USDT)

# ---------------------------------------------------------------------------
# Time constants (5-minute bars)
# ---------------------------------------------------------------------------
BARS_PER_DAY_5M: int = 288  # 24 * 60 / 5
TRADING_DAYS_PER_YEAR: int = 365
BARS_PER_YEAR: int = BARS_PER_DAY_5M * TRADING_DAYS_PER_YEAR

INTERVAL_SECONDS: dict = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# ---------------------------------------------------------------------------
# Strategy default parameters
# ---------------------------------------------------------------------------
DEFAULT_TREND_PARAMS: dict = {
    "window": 20,
    "std_dev": 2.0,
    "atr_multiplier": 2.5,
    "max_hold_bars": 48,
    "adx_threshold": 25,
    "rsi_threshold": 30,
    "entry_zone": 1.0,
    "take_profit_pct": 0.05,
    "stop_loss_pct": 0.03,
}

DEFAULT_SCALP_PARAMS: dict = {
    "window": 10,
    "std_dev": 1.2,
    "take_profit_pct": 0.005,
    "stop_loss_pct": 0.003,
    "max_hold_bars": 6,
    "rsi_period": 14,
}

DEFAULT_HYBRID_MM_PARAMS: dict = {
    "rsi_period": 5,
    "rsi_low": 28,
    "rsi_high": 72,
    "ma_period": 25,
    "atr_period": 12,
    "atr_multiplier": 3.5,
    "max_hold_bars": 48,
    "take_profit_pct": 0.03,
    "stop_loss_pct": 0.02,
}

DEFAULT_PURE_ACTION_PARAMS: dict = {
    "window": 20,
    "std_dev": 2.0,
    "atr_period": 14,
    "atr_multiplier": 2.0,
    "max_hold_bars": 36,
    "entry_zone": 0.0,
    "enable_short": True,
}

# ---------------------------------------------------------------------------
# Evaluation thresholds
# ---------------------------------------------------------------------------
SEARCH_MIN_TRADES: int = 50
EVAL_MIN_TRADES: int = 10
EVAL_MAX_DRAWDOWN: float = 0.30  # Disable strategy if DD exceeds 30%
EVAL_MIN_EQUITY_RATIO: float = 0.70  # Disable if equity < 70% of initial
EVAL_MIN_RETURN: float = -0.005  # Disable if return <= -0.5%

# ---------------------------------------------------------------------------
# Training / search
# ---------------------------------------------------------------------------
TIME_BUDGET: int = 600  # Default search time budget (s)
WF_N_WINDOWS: int = 3  # Walk-forward windows

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
STATE_FILE: str = "live_nado_state.json"
LOCK_FILE: str = "live_nado_quant.lock"
LOG_FILE_TEMPLATE: str = "live_nado_log_{timestamp}.txt"
