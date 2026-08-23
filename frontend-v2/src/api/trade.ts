/** 模拟交易 domain：账户 / 净值 / 持仓 / 委托 / 成交（契约 §4.8） */
import { http } from './http'
import type {
  AccountData,
  AccountNavData,
  OrdersData,
  PositionsData,
  TradesData,
} from './types'

export const tradeApi = {
  getAccount: () => http.get<AccountData>('/account'),
  getAccountNav: (params?: { start?: string; end?: string }) =>
    http.get<AccountNavData>('/account/nav', { ...params }),
  getPositions: () => http.get<PositionsData>('/positions'),
  getOrders: (params?: { status?: string; page?: number; page_size?: number }) =>
    http.get<OrdersData>('/orders', { ...params }),
  getTrades: (params?: { page?: number; page_size?: number }) =>
    http.get<TradesData>('/trades', { ...params }),
}
