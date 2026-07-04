import client from './client'

export interface ExchangeInfo {
  name: string
  status: string
  pid: number | null
  script: string
  state_file: string
  log_file: string
}

export interface ExchangeListResponse {
  exchanges: ExchangeInfo[]
  total: number
}

export interface StartRequest {
  exchange: string
  symbol: string
  mode: string
  capital: number
  leverage: number
}

export interface StartResponse {
  exchange: string
  pid: number
  status: string
}

export interface StopRequest {
  exchange: string
}

export interface StopResponse {
  exchange: string
  status: string
}

export type LiveState = Record<string, unknown> | null

export interface LiveStatus {
  exchange: string
  running: boolean
  state: LiveState
  updated_at: string | null
}

export interface LogResponse {
  exchange: string
  lines: string[]
  total_lines: number
}

export async function fetchExchanges(): Promise<ExchangeListResponse> {
  return client.get<unknown, ExchangeListResponse>('/live/exchanges')
}

export async function startLive(req: StartRequest): Promise<StartResponse> {
  return client.post<unknown, StartResponse>('/live/start', req)
}

export async function stopLive(req: StopRequest): Promise<StopResponse> {
  return client.post<unknown, StopResponse>('/live/stop', req)
}

export async function fetchLiveStatus(exchange: string): Promise<LiveStatus> {
  return client.get<unknown, LiveStatus>(`/live/status/${exchange}`)
}

export async function fetchLiveLogs(exchange: string, tail = 200): Promise<LogResponse> {
  return client.get<unknown, LogResponse>(`/live/logs/${exchange}?tail=${tail}`)
}
