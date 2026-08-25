/**
 * 因子研究 domain（2.3 G9 FactorLab）：
 *  - GET /factors —— 因子定义池（含 version/status/weight，构建器共用）
 *  - GET /factors/:name/stats —— 单因子检验统计（IC 时序/ICIR/衰减/分层/单调性），一次性给齐
 *  - GET /factors/stats/correlation —— 6 因子相关矩阵（区间均值）
 * 数据源：quant-engine 预计算 factor_stat/factor_corr 表（Go 仅读表 + 轻聚合，IC 数学单实现）。
 */
import { http } from './http'
import type { FactorCorrData, FactorsData, FactorStatsData } from './types'

export interface FactorStatsQuery {
  start?: string // YYYY-MM-DD，缺省近 2 年
  end?: string
  horizon?: number // {1,5,10,20,60}，缺省 5
}

export const factorApi = {
  /** 因子定义池（FactorLab 卡片 + 构建器共用） */
  getFactors: () => http.get<FactorsData>('/factors'),
  /** 单因子检验统计 */
  getFactorStats: (name: string, q?: FactorStatsQuery) =>
    http.get<FactorStatsData>(`/factors/${name}/stats`, { ...q }),
  /** 6 因子相关矩阵（缺省近 1 年） */
  getFactorCorr: (q?: FactorStatsQuery) =>
    http.get<FactorCorrData>('/factors/stats/correlation', { ...q }),
}
