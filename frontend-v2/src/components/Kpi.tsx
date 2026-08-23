import type { CSSProperties, ReactNode } from 'react'

interface Props {
  lb: string
  v: ReactNode
  d?: ReactNode
  vClass?: string
  dClass?: string
  vStyle?: CSSProperties
  style?: CSSProperties
}

/** 统计卡（原型 .kpi 结构） */
export default function Kpi({ lb, v, d, vClass, dClass, vStyle, style }: Props) {
  return (
    <div className="kpi" style={style}>
      <div className="lb">{lb}</div>
      <div className={`v${vClass ? ' ' + vClass : ''}`} style={vStyle}>
        {v}
      </div>
      {d !== undefined && <div className={`d${dClass ? ' ' + dClass : ''}`}>{d}</div>}
    </div>
  )
}
