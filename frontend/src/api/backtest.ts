// 与 backend/app/schemas/backtest.py 对齐
import client from './client'

export interface SymbolInfo {
  symbol: string
  interval: string
  days: number
  file: string
}

export interface SymbolListResponse {
  symbols: SymbolInfo[]
  total: number
}

export interface BacktestRequest {
  symbol: string
  interval: string
  days: number
  strategy: string
  start?: string
  end?: string
}

export interface EquityPoint {
  step: number
  timestamp: number
  equity: number
}

export interface Trade {
  type: string
  step: number
  timestamp: number
  price: number
  pnl: number | null
}

export interface Metrics {
  total_return: number
  annualized_return: number
  annualized_vol: number
  sharpe_ratio: number
  max_drawdown: number
  win_rate: number
}

export interface BacktestMeta {
  strategy: string
  symbol: string
  interval: string
  bars: number
}

export interface BacktestResponse {
  equity_curve: EquityPoint[]
  trades: Trade[]
  metrics: Metrics
  meta: BacktestMeta
}

export async function fetchSymbolList(): Promise<SymbolListResponse> {
  return client.get<unknown, SymbolListResponse>('/backtest/symbols')
}

export async function runBacktest(req: BacktestRequest): Promise<BacktestResponse> {
  return client.post<unknown, BacktestResponse>('/backtest/run', req)
}
