/** 运维 domain：任务时间线 + 服务状态 / 数据资产 / 数据健康 / 告警（契约 §4.12；/health 无信封走 raw） */
import { http } from './http'
import type {
  DataAssetData,
  HealthChecksData,
  HealthData,
  HealthServicesData,
  TaskRunsData,
} from './types'

export const opsApi = {
  /** 任务台账（后端上限 100 条，超出会静默回落 20 → 固定传满；告警记录由此过滤） */
  getTaskRuns: (limit = 100) => http.get<TaskRunsData>('/tasks/runs', { limit }),
  /** 服务探活：6 容器实时状态（ok/down/unknown + 详情） */
  getServices: () => http.get<HealthServicesData>('/health/services'),
  /** 数据资产：21 张表精确行数 */
  getDataAssets: () => http.get<DataAssetData>('/health/data-assets'),
  /** 数据健康 7 项（最近一次 data_quality 检查结果） */
  getHealthChecks: () => http.get<HealthChecksData>('/health/checks'),
  /** /health 无信封，必须走 raw */
  getHealth: () => http.raw<HealthData>('/health'),
}
