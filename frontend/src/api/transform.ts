/**
 * 接入层枚举转换 —— 后端大写/英文 → 前端小写/中文（契约 §2 通用约定）。
 * - 信号 action：后端 `BUY/SELL/HOLD` → 前端 `buy/sell/hold`
 * - 股票池 board：后端 `universe` `hs300/zz500` → 前端 `hs/zz`
 */
import type { SignalType, Board } from '../mock/data'
import type { SignalAction } from './types'

const ACTION_MAP: Record<SignalAction, SignalType> = { BUY: 'buy', SELL: 'sell', HOLD: 'hold' }

export function mapAction(a?: SignalAction): SignalType | undefined {
  return a ? ACTION_MAP[a] : undefined
}

export function mapUniverse(u?: string): Board | undefined {
  if (u === 'hs300') return 'hs'
  if (u === 'zz500') return 'zz'
  return undefined
}
