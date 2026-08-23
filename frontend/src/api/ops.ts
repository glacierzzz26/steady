/** 运维 domain：任务时间线 + 服务状态（契约 §4.12；/health 无信封走 raw） */
import { http } from './http'
import type { HealthData, TaskRunsData } from './types'

export const opsApi = {
  getTaskRuns: (limit = 20) => http.get<TaskRunsData>('/tasks/runs', { limit }),
  /** /health 无信封，必须走 raw */
  getHealth: () => http.raw<HealthData>('/health'),
}
