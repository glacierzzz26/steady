/** 信号 domain：信号列表 + 策略定义（契约 §4.5 / §4.7） */
import { http } from './http'
import type { SignalQuery, SignalsData, StrategiesData } from './types'

export const signalsApi = {
  getSignals: (params?: SignalQuery) => http.get<SignalsData>('/signals', { ...params }),
  getStrategies: () => http.get<StrategiesData>('/strategies'),
}
