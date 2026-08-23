/** 指数基准 domain（契约 §4.9，Dash 净值对比用） */
import { http } from './http'
import type { IndexNavData } from './types'

export const marketApi = {
  getIndexNav: (code: string, params?: { start?: string; end?: string }) =>
    http.get<IndexNavData>(`/index/nav/${code}`, { ...params }),
}
