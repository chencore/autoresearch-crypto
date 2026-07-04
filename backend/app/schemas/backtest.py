from pydantic import BaseModel


class SymbolInfo(BaseModel):
    symbol: str
    interval: str
    days: int
    file: str


class SymbolListResponse(BaseModel):
    symbols: list[SymbolInfo]
    total: int


class BacktestRequest(BaseModel):
    symbol: str
    interval: str
    days: int
    strategy: str
    start: str | None = None
    end: str | None = None


class EquityPoint(BaseModel):
    step: int
    timestamp: int
    equity: float


class Trade(BaseModel):
    type: str
    step: int
    timestamp: int
    price: float
    pnl: float | None = None


class Metrics(BaseModel):
    total_return: float
    annualized_return: float
    annualized_vol: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float


class BacktestMeta(BaseModel):
    strategy: str
    symbol: str
    interval: str
    bars: int


class BacktestResponse(BaseModel):
    equity_curve: list[EquityPoint]
    trades: list[Trade]
    metrics: Metrics
    meta: BacktestMeta
