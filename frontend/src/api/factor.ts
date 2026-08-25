/**
 * 因子研究 domain（2.3 G9 FactorLab + 2.3b G10 FactorFactory）：
 *  - GET /factors —— 因子定义池（含 version/status/params，构建器共用）
 *  - GET /factors/:name/stats —— 单因子检验统计（IC 时序/ICIR/衰减/分层/单调性），一次性给齐
 *  - GET /factors/stats/correlation —— 6 因子相关矩阵（区间均值）
 *  - G10 生命周期：POST/PUT/DELETE /factors、POST /factors/:name/versions(fork)、POST /factors/:name/switch
 *  - G10 试算/寻优：POST /factors/:name/trial|optimize → {id,status} 入队；GET /factor-trials/:id 轮询结果
 * 数据源：quant-engine 预计算 factor_stat/factor_corr 表（Go 仅读表 + 轻聚合，IC 数学单实现）；
 * 试算/寻优由 quant-engine 消费 factor_trial 队列写 result（设计定稿 §4.2/§4.3/§6.2）。
 */
import { http } from './http'
import type {
  FactorCorrData, FactorDefinition, FactorOptimizeSubmit, FactorsData,
  FactorStatus, FactorStatsData, FactorTrialCreated, FactorTrialDetail,
  FactorTrialsData, FactorTrialSubmit, FactorUpsert,
} from './types'

export interface FactorStatsQuery {
  start?: string // YYYY-MM-DD，缺省近 2 年
  end?: string
  horizon?: number // {1,5,10,20,60}，缺省 5
}

export const factorApi = {
  /** 因子定义池（FactorLab 卡片 + 构建器 + FactorFactory 管理共用） */
  getFactors: () => http.get<FactorsData>('/factors'),
  /** 单因子检验统计 */
  getFactorStats: (name: string, q?: FactorStatsQuery) =>
    http.get<FactorStatsData>(`/factors/${name}/stats`, { ...q }),
  /** 6 因子相关矩阵（缺省近 1 年） */
  getFactorCorr: (q?: FactorStatsQuery) =>
    http.get<FactorCorrData>('/factors/stats/correlation', { ...q }),

  // ---- G10 FactorFactory 生命周期（契约《因子研究闭环》§6.2）----
  /** 新建草稿因子（version v1.0；name 冲突 / params 非对象则拒） */
  createFactor: (body: FactorUpsert) => http.post<FactorDefinition>('/factors', body),
  /** 编辑因子（仅草稿/停用可改；name 不可改） */
  updateFactor: (name: string, body: FactorUpsert) =>
    http.put<FactorDefinition>(`/factors/${name}`, body),
  /** 复制为新草稿版本（ma_trend → ma_trend_v2 → _v3…；version 提升，params 快照随复制） */
  forkFactor: (name: string) => http.post<FactorDefinition>(`/factors/${name}/versions`),
  /** 状态流转（状态机 draft→trial→verified→active→disabled，非法流转拒） */
  switchFactor: (name: string, status: FactorStatus) =>
    http.post<FactorDefinition>(`/factors/${name}/switch`, { status }),
  /** 删除草稿因子（已有试算/评分/检验记录则拒） */
  deleteFactor: (name: string) => http.del<{ deleted: string }>(`/factors/${name}`),

  // ---- G10 试算/寻优任务（DB 队列，quant-engine 消费）----
  /** 提交单组参数试算 → {id, status}（pending 入队） */
  createTrial: (name: string, body: FactorTrialSubmit) =>
    http.post<FactorTrialCreated>(`/factors/${name}/trial`, body),
  /** 提交参数寻优 → {id, status} */
  createOptimize: (name: string, body: FactorOptimizeSubmit) =>
    http.post<FactorTrialCreated>(`/factors/${name}/optimize`, body),
  /** 试算任务详情：pending/running → {status}；failed → {status,error}；done → {status,...result} */
  getTrial: (id: number) => http.get<FactorTrialDetail>(`/factor-trials/${id}`),
  /** 试算任务列表（factor_name 可选过滤；id 倒序，limit 缺省 50） */
  getTrials: (q?: { factor_name?: string; limit?: number }) =>
    http.get<FactorTrialsData>('/factor-trials', { ...q }),
}
