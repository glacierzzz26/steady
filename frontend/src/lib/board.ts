/**
 * 股票池档位 —— 单一事实源（Issue #13「全A股」档）。
 *
 * 此前 `BOARD_OPTIONS`（Seg 文案）与 `UNIVERSE_MAP`（查询参数）是两份必须手工同步的
 * 平行字面量，新增一档就得记得同时改两处。这里合并成一张表：档位 / 文案 / URL 契约 /
 * 查询条件都从 `BOARDS` 派生，杜绝漂移。
 *
 * **两个维度正交**（本文件最容易搞错处）：
 *   - `universe`  策略选股域：hs300 / zz500，共 800 只（factor_value 恒 800 的来源）
 *   - `scope`     采集域：a_share，沪深两市 5212 只（Issue #13 扩池后）
 * 一只 hs300 股票**同时**是 a_share —— 故二者不会互斥，同时下发即 AND 过滤。
 */
import type { StockListQuery } from '../api'

/** 档位定义。key === Seg 按钮文案 === `?pool=` 取值（「全部」无 pool）。 */
export const BOARDS = [
  { key: '全部', pool: null, q: {} },
  { key: '沪深300', pool: 'hs300', q: { universe: 'hs300' } },
  { key: '中证500', pool: 'zz500', q: { universe: 'zz500' } },
  { key: '全A股', pool: 'a_share', q: { scope: 'a_share' } },
] as const

export type BoardKey = (typeof BOARDS)[number]['key']

/** Seg 组件的 options（文案即 key，故 Seg 的 onChange 值可直接当档位用） */
export const BOARD_OPTIONS: string[] = BOARDS.map((b) => b.key)

/** 档位 → 查询过滤条件（universe 与 scope 不会同时出现） */
export function boardQuery(b: BoardKey): Partial<Pick<StockListQuery, 'universe' | 'scope'>> {
  return BOARDS.find((x) => x.key === b)?.q ?? {}
}

/** `?pool=` → 档位。未知值/缺失一律落「全部」：老书签与手敲错误 URL 都不炸。 */
export function parsePool(pool: string | null): BoardKey {
  return (BOARDS.find((b) => b.pool === pool)?.key ?? '全部') as BoardKey
}

/** 档位 → `?pool=` 取值（null = 无 pool，即「全部」） */
export function poolOf(b: BoardKey): string | null {
  return BOARDS.find((x) => x.key === b)?.pool ?? null
}

/**
 * topbar 指数 chip → 股票池档位（Issue #11-2）。
 *
 * 刻意**不含** `sh000001`（上证指数）：上证指数不是「全A股」，映射过去是错误落点 ——
 * 它落到 `/stocks`（全部档）。
 */
export const INDEX_CODE_TO_POOL: Record<string, string | undefined> = {
  sh000300: 'hs300',
  sh000905: 'zz500',
}
