/** 股票 domain：列表 / 详情 / K线 / 财务 / 信号历史（契约 §4.1~4.4 / 4.6） */
import { http } from './http'
import type {
  FinancialListData,
  KLineData,
  SignalHistoryData,
  StockDetail,
  StockListData,
  StockListQuery,
} from './types'

export const stocksApi = {
  getStocks: (params?: StockListQuery) => http.get<StockListData>('/stocks', { ...params }),
  getStockDetail: (code: string) => http.get<StockDetail>(`/stocks/${code}`),
  getKline: (
    code: string,
    params?: { period?: string; adjust?: string; start?: string; end?: string },
  ) => http.get<KLineData>(`/kline/${code}`, { ...params }),
  getFinancial: (code: string, limit = 20) =>
    http.get<FinancialListData>(`/stocks/${code}/financial`, { limit }),
  getSignalHistory: (code: string, limit = 50) =>
    http.get<SignalHistoryData>(`/signals/${code}`, { limit }),
}
