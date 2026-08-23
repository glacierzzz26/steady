/**
 * 数值格式化 —— 移植 v1 `frontend/src/utils/format.ts`，
 * 按前端接入约定补齐（契约 §2）：金额单位元、涨跌幅/收益为百分比数值、回测收益为小数比例。
 */

/** 金额：千分位 + 两位小数（元） */
export function fmtMoney(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** 财务字段百分比（15.2 = 15.2%）直接拼 %；≤0 视为缺失（Go 空值序列化为 0） */
export function fmtPct(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v) || v <= 0) return '--'
  return `${v.toFixed(2)}%`
}

/** 量/额：≥1e8 → x.xx亿，≥1e4 → x.xx万；volume 单位手、amount 单位元 */
export function fmtWanYi(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v) || v <= 0) return '--'
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return String(v)
}

/** 带符号涨跌幅（+2.80% / -1.20%），输入已是百分比数值 */
export function fmtChg(v?: number | null, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

/** 回测收益为小数比例（0.382 = 38.2%），×100 显示 */
export function fmtRatioPct(v?: number | null, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return `${(v * 100).toFixed(digits)}%`
}

/** 盈利：带符号数值 + 涨红跌绿 */
export function fmtProfit(v?: number | null): { text: string; color: string } {
  if (v === null || v === undefined || Number.isNaN(v)) return { text: '--', color: 'inherit' }
  return {
    text: `${v >= 0 ? '+' : ''}${v.toFixed(2)}`,
    color: v >= 0 ? '#F0524F' : '#2FBF71',
  }
}
