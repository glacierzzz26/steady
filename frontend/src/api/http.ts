/**
 * 纯 fetch 封装 —— 不引 axios。
 * - 统一 baseURL：`/api/v1`（开发走 Vite proxy → 127.0.0.1:8080，生产走 nginx）
 * - 响应信封 `{code, message, data, timestamp}`：code!==0 抛 ApiError
 * - `http.raw`：不解信封，直接返回原始 JSON（供 `/health` 等无信封接口）
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: number, // 后端业务码（40001/40004/50001）；网络错误为 0
    readonly status: number, // HTTP 状态；网络错误为 0
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

interface Envelope<T> {
  code: number
  message: string
  data: T
  timestamp?: string
}

/** 查询串：跳过 null / undefined / 空串（避免 `?x=&y=` 触发后端参数校验） */
function buildQuery(params?: Record<string, unknown>): string {
  if (!params) return ''
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue
    sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  params?: Record<string, unknown>,
): Promise<T> {
  const url = `${API_BASE}${path}${buildQuery(params)}`
  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('网络异常，请稍后重试', 0, 0)
  }

  let json: unknown
  try {
    json = await res.json()
  } catch {
    throw new ApiError(`服务响应异常 (HTTP ${res.status})`, 0, res.status)
  }

  const env = json as Envelope<T>
  if (!res.ok || env.code !== 0) {
    const msg = env?.message || `请求失败 (HTTP ${res.status})`
    const code = env && typeof env.code === 'number' ? env.code : 0
    throw new ApiError(msg, code, res.status)
  }
  return env.data
}

/** 不解信封的原始请求：直接返回 JSON body（供 /health 这类无信封接口） */
async function raw<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const url = `${API_BASE}${path}${buildQuery(params)}`
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new ApiError('网络异常，请稍后重试', 0, 0)
  }
  if (!res.ok) throw new ApiError(`请求失败 (HTTP ${res.status})`, 0, res.status)
  return res.json() as Promise<T>
}

export const http = {
  get: <T>(path: string, params?: Record<string, unknown>) =>
    request<T>('GET', path, undefined, params),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  raw,
}
