from __future__ import annotations

import pandas as pd

from dex.config import COMMISSION, DATA_DIR, INITIAL_CAPITAL, SLIPPAGE
from dex.strategies.base import StrategyEvaluator

from app.schemas.backtest import (
    BacktestMeta,
    BacktestRequest,
    BacktestResponse,
    EquityPoint,
    Metrics,
    Trade,
)
from app.services.strategy_registry import discover_strategies


class BacktestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _find_strategy_class(name: str):
    for cls in discover_strategies():
        if cls.__name__ == name:
            return cls
    raise BacktestError("not_found", f"strategy not found: {name}")


def _load_data(req: BacktestRequest) -> pd.DataFrame:
    filepath = DATA_DIR / f"{req.symbol}_{req.interval}_{req.days}d.parquet"
    if not filepath.exists():
        raise BacktestError("data_not_found", f"data file not found: {filepath.name}")
    df = pd.read_parquet(filepath)
    df["datetime"] = pd.to_datetime(df["datetime"])
    if req.start:
        df = df[df["datetime"] >= pd.Timestamp(req.start)].reset_index(drop=True)
    if req.end:
        df = df[df["datetime"] <= pd.Timestamp(req.end)].reset_index(drop=True)
    if len(df) == 0:
        raise BacktestError(
            "no_data_in_range",
            f"no data in range {req.start} ~ {req.end}",
        )
    return df


def run_backtest(req: BacktestRequest) -> BacktestResponse:
    try:
        cls = _find_strategy_class(req.strategy)
        df = _load_data(req)
        strategy = cls()
        signals = strategy.generate_signals(df)
        prices = df["close"].values
        evaluator = StrategyEvaluator(INITIAL_CAPITAL, COMMISSION, SLIPPAGE)
        equity, trades = evaluator.simulate(signals, prices, df)
        if len(equity) == 0:
            raise BacktestError("backtest_failed", "equity curve is empty")
        metrics_dict = evaluator.compute_metrics(equity, trades)

        ts_values = df["timestamp"].values
        close_values = df["close"].values
        equity_curve = [
            EquityPoint(
                step=int(i),
                timestamp=int(ts_values[i]),
                equity=float(equity[i]),
            )
            for i in range(len(equity))
        ]
        trades_out = [
            Trade(
                type=str(t["type"]),
                step=int(t["step"]),
                timestamp=int(ts_values[t["step"]]),
                price=float(close_values[t["step"]]),
                pnl=float(t["pnl"]) if "pnl" in t else None,
            )
            for t in trades
        ]
        metrics = Metrics(**{k: float(v) for k, v in metrics_dict.items()})
        meta = BacktestMeta(
            strategy=req.strategy,
            symbol=req.symbol,
            interval=req.interval,
            bars=int(len(df)),
        )
        return BacktestResponse(
            equity_curve=equity_curve,
            trades=trades_out,
            metrics=metrics,
            meta=meta,
        )
    except BacktestError:
        raise
    except Exception as e:
        raise BacktestError("backtest_failed", str(e)) from e
