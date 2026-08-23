import type { ReactNode } from 'react'

interface Props {
  total: number
  page: number
  size: number
  onChange: (page: number) => void
}

/**
 * 通用分页器 —— 复刻原型 mkPager：
 * 首页 + 当前页前后 2 页 + 末页，省略号折叠。
 */
export default function Pager({ total, page, size, onChange }: Props) {
  const pages = Math.max(1, Math.ceil(total / size))
  const btn = (txt: string, p: number, on = false, dis = false) => (
    <button
      key={txt + p + String(on)}
      className={`pg${on ? ' on' : ''}`}
      disabled={dis}
      onClick={() => !dis && p >= 1 && p <= pages && onChange(p)}
    >
      {txt}
    </button>
  )
  const win = 2
  const lo = Math.max(1, page - win)
  const hi = Math.min(pages, page + win)
  const nodes: ReactNode[] = [btn('‹', page - 1, false, page <= 1)]
  if (lo > 1) {
    nodes.push(btn('1', 1))
    if (lo > 2) nodes.push(<span key="l-ellipsis" style={{ color: 'var(--txt3)' }}>…</span>)
  }
  for (let i = lo; i <= hi; i++) nodes.push(btn(String(i), i, i === page))
  if (hi < pages) {
    if (hi < pages - 1) nodes.push(<span key="r-ellipsis" style={{ color: 'var(--txt3)' }}>…</span>)
    nodes.push(btn(String(pages), pages))
  }
  nodes.push(btn('›', page + 1, false, page >= pages))
  return (
    <div className="pager">
      {nodes}
      <span className="info">
        共 <b className="num">{total}</b> 条 · {size} 条/页 · 第 <b className="num">{page}</b>/{pages} 页
      </span>
    </div>
  )
}
