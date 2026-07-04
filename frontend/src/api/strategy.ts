// 与 backend/app/schemas/strategy.py 对齐
import client from './client'

export interface ParamDef {
  name: string
  type: string
  default: unknown
  annotation: string | null
  required: boolean
}

export interface StrategySummary {
  name: string
  module: string
  file: string
  description: string
  params_count: number
  live_status: string
}

export interface StrategyDetail {
  name: string
  module: string
  file: string
  description: string
  class_docstring: string
  params: ParamDef[]
  signal_kind: string
  runtime_params: string[]
  live_status: string
}

export interface StrategyListResponse {
  strategies: StrategySummary[]
  total: number
}

export async function fetchStrategyList(): Promise<StrategyListResponse> {
  return client.get<unknown, StrategyListResponse>('/strategy')
}

export async function fetchStrategyDetail(name: string): Promise<StrategyDetail> {
  return client.get<unknown, StrategyDetail>(`/strategy/${encodeURIComponent(name)}`)
}
