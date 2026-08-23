/** 回测 domain：列表 / 详情 / 发起（契约 §4.10） */
import { http } from './http'
import type { BacktestJobItem, BacktestsData, BacktestSubmit } from './types'

export const backtestApi = {
  getBacktests: (limit = 20) => http.get<BacktestsData>('/backtests', { limit }),
  getBacktest: (id: number) => http.get<BacktestJobItem>(`/backtests/${id}`),
  submitBacktest: (req: BacktestSubmit) =>
    http.post<{ job_id: number; status: string }>('/backtests', req),
}
