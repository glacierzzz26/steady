import { SIGNAL_CN, type SignalType } from '../mock/data'

interface Props {
  type: SignalType | 'ok' | 'warn' | 'hold' | 'plan'
  label?: string
}

/** 状态标签（buy/sell/hold 信号 + ok/warn/plan 状态） */
export default function Tag({ type, label }: Props) {
  const text = label ?? (type in SIGNAL_CN ? SIGNAL_CN[type as SignalType] : type)
  return <span className={`tag ${type}`}>{text}</span>
}
