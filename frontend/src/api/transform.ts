/**
 * 接入层枚举转换 —— 后端大写/英文 → 前端小写/中文（契约 §2 通用约定）。
 * - 信号 action：后端 `BUY/SELL/HOLD` → 前端 `buy/sell/hold`
 *
 * 注：股票池档位（universe / data_scope）的转换**不在这里** —— 见 `lib/board.ts`。
 * 原 `mapUniverse` 返回 mock 的 `Board='hs'|'zz'|undefined`，新增「全A股」档后该联合类型
 * 无法表达第三档，且任何 `board === 'hs' ? … : '中证500'` 的旧三元都会把新档渲染成
 * 中证500（静默错标），故已删除。
 */
import type { SignalType } from '../mock/data'
import type { SignalAction } from './types'

const ACTION_MAP: Record<SignalAction, SignalType> = { BUY: 'buy', SELL: 'sell', HOLD: 'hold' }

export function mapAction(a?: SignalAction): SignalType | undefined {
  return a ? ACTION_MAP[a] : undefined
}
