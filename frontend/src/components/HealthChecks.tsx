import type { HealthCheckItem } from '../api'

/** 数据健康 7 项行列表（G5，/health/checks）：左=检查名 · 中=状态条 · 右=明细值；悬停看完整明细 */
export default function HealthChecks({ items }: { items: HealthCheckItem[] }) {
  if (items.length === 0) return <div className="empty">暂无检查记录</div>
  return (
    <div>
      {items.map(c => {
        const tip = [c.value, c.pct != null ? `${(c.pct * 100).toFixed(1)}%` : ''].filter(Boolean).join(' · ')
        return (
          <div className="health" key={c.name} title={tip}>
            <span style={{ width: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <b style={{ fontWeight: 500 }}>{c.name}</b>
            </span>
            <div className="hbar">
              <i style={{ width: c.ok ? '100%' : '45%', background: c.ok ? 'var(--ok)' : 'var(--down)' }} />
            </div>
            <span
              className="num"
              style={{
                width: 130,
                textAlign: 'right',
                fontSize: 13,
                color: c.ok ? 'var(--txt2)' : 'var(--down)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {c.value || (c.ok ? '正常' : '异常')}
            </span>
          </div>
        )
      })}
    </div>
  )
}
