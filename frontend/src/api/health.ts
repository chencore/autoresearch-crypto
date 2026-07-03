export interface HealthResponse {
  status: string
  version: string
}

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch('/health')
  return res.json() as Promise<HealthResponse>
}
