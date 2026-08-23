/** 设置页 domain：数据源 / 通知 / LLM 配置（契约 §4.12） */
import { http } from './http'
import type {
  FeishuConfig,
  LLMConfig,
  LLMConfigUpdate,
  NotifyConfigData,
  NotifyEvent,
  TushareConfig,
} from './types'

export const settingsApi = {
  // Tushare
  getTushareConfig: () => http.get<TushareConfig>('/config/tushare'),
  updateTushareConfig: (token: string) => http.put<{ updated: boolean }>('/config/tushare', { token }),
  /** token 为空 = 用已存 token 测试 */
  testTushare: (token: string) => http.post<{ ok: boolean }>('/config/tushare/test', { token }),
  // 通知
  getNotifyConfig: () => http.get<NotifyConfigData>('/notify/config'),
  updateNotifyEvent: (eventKey: string, req: Partial<NotifyEvent>) =>
    http.put<{ event_key: string; updated: boolean }>(`/notify/config/${eventKey}`, req),
  updateFeishuConfig: (req: Partial<FeishuConfig>) =>
    http.put<{ updated: boolean }>('/notify/config/feishu', req),
  sendNotifyTest: () => http.post<{ sent: boolean }>('/notify/test'),
  // LLM
  getLLMConfig: () => http.get<LLMConfig>('/config/llm'),
  updateLLMConfig: (req: LLMConfigUpdate) => http.put<{ updated: boolean }>('/config/llm', req),
  testLLM: () => http.post<{ ok: boolean }>('/config/llm/test'),
}
