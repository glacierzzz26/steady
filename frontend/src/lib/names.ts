/**
 * 名称本地化层（功能建议 ③④）——全站统一「中文(英文)」规则，避免各处硬编码。
 * 策略：zh_name(name)；因子：FACTOR_ZH[name](name)；任务：TASK_ZH[task_name]。
 */

/** 策略名「中文(英文)」；缺中文时退回英文名 */
export function fmtName(zh?: string | null, en?: string | null): string {
  const z = zh?.trim()
  const e = en?.trim()
  if (z && e && z !== e) return `${z}(${e})`
  return z || e || '—'
}

/** 因子英文名 → 中文简称（与 factor_definition.description 语义一致，取首段短名） */
export const FACTOR_ZH: Record<string, string> = {
  ma_trend: '均线趋势',
  macd_signal: 'MACD信号',
  roe_quality: 'ROE质量',
  pe_ratio: '市盈率',
  pb_ratio: '市净率',
  debt_risk: '负债风险',
}

/** 因子名「中文(英文)」；未收录的因子退回原英文名 */
export function fmtFactor(name?: string | null): string {
  const n = name?.trim()
  if (!n) return '—'
  const zh = FACTOR_ZH[n]
  return zh ? `${zh}(${n})` : n
}

/** 任务执行名 → 中文标签（运维时间线；与后端 task_run.task_name 对应） */
export const TASK_ZH: Record<string, string> = {
  auto_trade: '自动交易',
  backtest: '回测任务',
  calc_factors: '因子计算',
  consistency_check: '对账校验',
  daily_report: '日报生成',
  data_quality: '数据质量',
  generate_signals: '信号生成',
  morning_brief: '早盘简报',
  nav_snapshot: '净值快照',
}

/** 任务名中文标签：notify:xxx → 「通知·xxx」；alert:xxx → 「告警·xxx」；未收录退回原名 */
export function fmtTask(name?: string | null): string {
  const n = name?.trim()
  if (!n) return '—'
  if (n.startsWith('notify:')) {
    const rest = n.slice('notify:'.length)
    return `通知·${TASK_ZH[rest] ?? rest}`
  }
  if (n.startsWith('alert:')) {
    const rest = n.slice('alert:'.length)
    return `告警·${TASK_ZH[rest] ?? rest}`
  }
  return TASK_ZH[n] ?? n
}
