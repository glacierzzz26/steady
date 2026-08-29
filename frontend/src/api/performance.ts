/** 策略效果度量 domain（方向① 第一期：只读 strategy_perf 预计算结果） */
import { http } from './http'

/** 单窗口命中率（5/10/20 交易日 forward 收益） */
export interface PerfWindow {
  hit_rate: number | null
  relative_hit: number | null
  avg: number | null
  median: number | null
  avg_excess: number | null
  sell_hit_rate: number | null
  samples: number
  sell_samples: number
}

export interface HitRateData {
  strategy_name: string
  period_start: string
  period_end: string
  detail: {
    windows: Record<string, PerfWindow>
    buy_samples: number
    sell_samples: number
    period_start?: string
  }
  /** 传 ?window= 时单窗口 */
  window?: PerfWindow
}

export interface OverlayPoint {
  date: string
  live: number | null
  bt: number | null
  benchmark: number | null
}

export interface OverlayMetrics {
  live_cum_return: number | null
  bt_cum_return: number | null
  drift: number | null
  live_max_drawdown: number | null
  bt_max_drawdown: number | null
  live_points: number
  bt_points: number
}

export interface NavOverlayData {
  strategy_name: string
  period_start: string
  period_end: string
  series: OverlayPoint[]
  metrics: OverlayMetrics
}

export const performanceApi = {
  getHitRate: (strategy = 'multi_factor') =>
    http.get<HitRateData>('/performance/hit-rate', { strategy }),
  getNavOverlay: (strategy = 'multi_factor') =>
    http.get<NavOverlayData>('/performance/nav-overlay', { strategy }),
}
