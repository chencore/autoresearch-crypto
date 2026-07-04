import axios from 'axios'

export interface ApiError {
  code: string
  message: string
}

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30_000,
})

client.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const apiError = error.response?.data?.error
    if (apiError && typeof apiError.code === 'string' && typeof apiError.message === 'string') {
      return Promise.reject<ApiError>({ code: apiError.code, message: apiError.message })
    }
    return Promise.reject<ApiError>({ code: 'network', message: error.message || '网络错误' })
  },
)

export default client
