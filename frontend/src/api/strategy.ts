/**
 * 策略 domain（Iteration 4 §4.1/§4.2/§3.3）：
 *  生命周期 CRUD/switch/fork、A/B 对比、构建器因子池（真实 factor_definition）。
 *  create/update 的 factor_weights/params 传对象，http.ts 序列化为 JSON 与后端
 *  json.RawMessage 对齐；switch 走合法状态机路径（draft→backtest→sample→active）。
 */
import { http } from './http'
import type {
  CompareResult,
  FactorsData,
  StrategiesData,
  StrategyInfo,
} from './types'

export interface StrategyInput {
  name?: string
  zh_name?: string
  description?: string
  factor_weights?: Record<string, number> // 因子名 → 权重（和为 1.0 的小数）
  params?: Record<string, unknown> // top_n/buy_buffer/sell_buffer/stop_loss_pct/…
}

export const strategyApi = {
  /** 策略全量（含状态机全部状态） */
  getStrategies: () => http.get<StrategiesData>('/strategies'),
  /** 新建草稿（POST /strategies，name 必填） */
  createStrategy: (req: StrategyInput) => http.post<StrategyInfo>('/strategies', req),
  /** 更新草稿（PUT /strategies/:name，仅 draft 可编辑） */
  updateStrategy: (name: string, req: StrategyInput) =>
    http.put<StrategyInfo>(`/strategies/${name}`, req),
  /** 复制为新草稿版本（POST /strategies/:name/versions，version +1） */
  forkStrategy: (name: string) => http.post<StrategyInfo>(`/strategies/${name}/versions`),
  /** 状态流转（POST /strategies/:name/switch，body {status}） */
  switchStrategy: (name: string, status: string) =>
    http.post<StrategyInfo>(`/strategies/${name}/switch`, { status }),
  /** A/B 对比（GET /strategies/compare；pending 需轮询） */
  compareStrategies: (
    base: string,
    candidate: string,
    start: string,
    end: string,
    fillMode: string,
  ) => http.get<CompareResult>('/strategies/compare', { base, candidate, start, end, fill_mode: fillMode }),
  /** 构建器因子池 */
  getFactors: () => http.get<FactorsData>('/factors'),
}
