import client from './client'

export type EvolveEngine = 'atlas' | 'gepa'

export interface EvolveStartRequest {
  engine: EvolveEngine
  symbol: string
  interval: string
  days: number
  generations: number
  evolution_interval?: number
}

export interface EvolveStartResponse {
  run_id: string
  status: string
}

export interface EvolveAgentState {
  name: string
  style: string
  score: number
  weight: number
  generation: number
  params: Record<string, unknown>
}

export interface EvolveRunSummary {
  id: string
  engine: string
  symbol: string
  status: string
  current_gen: number
  total_generations: number
  started_at: string
  completed_at: string | null
  error: string | null
}

export interface EvolveRunListResponse {
  runs: EvolveRunSummary[]
  total: number
}

export interface EvolveRunDetail {
  id: string
  engine: string
  symbol: string
  interval: string
  days: number
  generations: number
  status: string
  current_gen: number
  started_at: string
  completed_at: string | null
  error: string | null
  agents: EvolveAgentState[]
  event_count: number
}

export interface EvolveStopResponse {
  run_id: string
  status: string
}

export interface StartedEvent {
  type: 'started'
  run_id: string
  config: Record<string, unknown>
  total: number
}

export interface GenerationEvent {
  type: 'generation'
  run_id: string
  generation: number
  total: number
  agents: EvolveAgentState[]
}

export interface CycleEvent {
  type: 'cycle'
  run_id: string
  cycle: number
  total: number
  agent: string
  hypothesis: string
  score_before: number
  score_after: number
  accepted: boolean
  reflection: string
  agents: EvolveAgentState[]
}

export interface MetaReflectionEvent {
  type: 'meta_reflection'
  run_id: string
  cycle: number
  summary: string
}

export interface BestAgent {
  name: string
  style: string
  score: number
  weight: number
  params: Record<string, unknown>
}

export interface CompletedEvent {
  type: 'completed'
  run_id: string
  best_agent?: BestAgent
  final_weights?: Record<string, number>
  experiment_count?: number
  meta_count?: number
  blind_spots?: string[]
}

export interface StoppedEvent {
  type: 'stopped'
  run_id: string
  generation?: number
  cycle?: number
}

export interface ErrorEvent {
  type: 'error'
  run_id?: string
  code: string
  message: string
}

export type EvolveEvent =
  | StartedEvent
  | GenerationEvent
  | CycleEvent
  | MetaReflectionEvent
  | CompletedEvent
  | StoppedEvent
  | ErrorEvent

export async function startEvolution(
  req: EvolveStartRequest,
): Promise<EvolveStartResponse> {
  return client.post<unknown, EvolveStartResponse>('/evolve/start', req)
}

export async function fetchRunList(): Promise<EvolveRunListResponse> {
  return client.get<unknown, EvolveRunListResponse>('/evolve/runs')
}

export async function fetchRunDetail(runId: string): Promise<EvolveRunDetail> {
  return client.get<unknown, EvolveRunDetail>(`/evolve/runs/${runId}`)
}

export async function stopEvolution(runId: string): Promise<EvolveStopResponse> {
  return client.post<unknown, EvolveStopResponse>('/evolve/stop', {
    run_id: runId,
  })
}

export function buildEvolveWsUrl(runId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || '/api/v1'
  if (base.startsWith('http://')) {
    return base.replace('http://', 'ws://') + '/evolve/' + runId
  }
  if (base.startsWith('https://')) {
    return base.replace('https://', 'wss://') + '/evolve/' + runId
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${base}/evolve/${runId}`
}
