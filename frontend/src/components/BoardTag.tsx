import type { ReactNode } from 'react'

/** 指数成员标签（由 `universe` 驱动）。样式对齐既有 `.ptag` / `.ptag.zz`。 */
const BOARD_CN: Record<string, string> = { hs300: '沪深300', zz500: '中证500' }
const BOARD_CLS: Record<string, string> = { hs300: '', zz500: ' zz' }

/**
 * 指数成分标签。
 *
 * 两处调用点的空态本就不同（列表返「—」、详情返 null），故用 `fallback` 参数保留各自
 * 既有行为 —— 此前是两段各自内联的三元表达式，已经漂移。
 *
 * **`data_scope` 刻意不在此渲染**：它与 `universe` 正交，同一行可能既属 hs300 又在采集域
 * 内，单个单元格无法同时表达两个维度（按 scope 打标签会在一格里造假）。采集域维度只在
 * 详情页头部以中性 chip 表达（见 StockDetail）。
 */
export default function BoardTag({
  universe,
  fallback = null,
}: {
  universe?: string
  fallback?: ReactNode
}) {
  const cn = universe ? BOARD_CN[universe] : undefined
  if (!cn) return <>{fallback}</>
  return <span className={`ptag${BOARD_CLS[universe!] ?? ''}`}>{cn}</span>
}
