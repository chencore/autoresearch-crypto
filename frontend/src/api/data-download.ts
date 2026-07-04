import client from './client'

export type DownloadInterval = '1m' | '5m' | '15m' | '1h' | '4h' | '1d'

export interface DownloadStartRequest {
  symbol: string
  interval: DownloadInterval
  days: number
  proxy_url?: string
  force?: boolean
}

export interface DownloadStartResponse {
  task_id: string
  status: string
}

export interface DownloadTaskStatus {
  task_id: string
  symbol: string
  interval: string
  days: number
  status: string
  started_at: string
  completed_at: string | null
  error: string | null
  line_count: number
  last_line: string | null
}

export interface DownloadFile {
  filename: string
  symbol: string
  interval: string
  days: number
  size_bytes: number
  mtime: string
}

export interface DownloadFileListResponse {
  files: DownloadFile[]
  total: number
}

export interface DownloadStopResponse {
  task_id: string
  status: string
}

export interface StartedEvent {
  type: 'started'
  task_id: string
  symbol: string
  interval: string
  days: number
}

export interface ProgressEvent {
  type: 'progress'
  task_id: string
  line: string
  line_number: number
}

export interface CompletedEvent {
  type: 'completed'
  task_id: string
  file_path: string
  line_count: number
}

export interface ErrorEvent {
  type: 'error'
  task_id?: string
  code: string
  message: string
}

export interface StoppedEvent {
  type: 'stopped'
  task_id: string
}

export type DownloadEvent =
  | StartedEvent
  | ProgressEvent
  | CompletedEvent
  | ErrorEvent
  | StoppedEvent

export async function startDownload(
  req: DownloadStartRequest,
): Promise<DownloadStartResponse> {
  return client.post<unknown, DownloadStartResponse>('/data-download/start', req)
}

export async function getDownloadStatus(
  taskId: string,
): Promise<DownloadTaskStatus> {
  return client.get<unknown, DownloadTaskStatus>(
    `/data-download/status/${taskId}`,
  )
}

export async function stopDownload(taskId: string): Promise<DownloadStopResponse> {
  return client.post<unknown, DownloadStopResponse>('/data-download/stop', {
    task_id: taskId,
  })
}

export async function listFiles(): Promise<DownloadFileListResponse> {
  return client.get<unknown, DownloadFileListResponse>('/data-download/files')
}

export function buildDataDownloadWsUrl(taskId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || '/api/v1'
  if (base.startsWith('http://')) {
    return base.replace('http://', 'ws://') + '/ws/data-download/' + taskId
  }
  if (base.startsWith('https://')) {
    return base.replace('https://', 'wss://') + '/ws/data-download/' + taskId
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${base}/ws/data-download/${taskId}`
}
