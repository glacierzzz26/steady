/** 行情 domain（指数基准 + 市场状态；Dash 净值对比 / 右上角开市chip） */
import { http } from './http'
import type { IndexNavData, IndexQuotesData, MarketStatus } from './types'

export const marketApi = {
  getIndexNav: (code: string, params?: { start?: string; end?: string }) =>
    http.get<IndexNavData>(`/index/nav/${code}`, { ...params }),
  /** 市场状态（trade_calendar 判定今日是否交易日 + 盘中阶段） */
  getStatus: () => http.get<MarketStatus>('/market/status'),
  /** 指数行情概览（topbar 上证/沪深300/中证500 三枚芯片） */
  getQuotes: (codes: string[]) =>
    http.get<IndexQuotesData>(`/index/quotes?codes=${codes.join(',')}`),
}
