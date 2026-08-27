import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import Tag from '../../components/Tag'
import { briefApi, type MorningBriefData } from '../../api'
import { useApi } from '../../hooks/useApi'
import { fmtMoney, fmtRatioPct, fmtWanYi } from '../../lib/format'

const G6_HINT = '该能力未产出（G6，采集走免费 AkShare）'

/** 百分比数值（0.22 = 0.22%）→ 带符号字符串 */
function fmtPctNum(v?: number | null): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}
const cls = (v?: number | null) => (v !== undefined && v !== null && v < 0 ? 'down' : 'up')

/** 占位 KPI：未产出显示「—」，已产出显示值（两市成交等 G6 项点亮用） */
function NoticeKpi({ lb, v, d }: { lb: string; v?: string; d?: string }) {
  return v !== undefined && v !== '' ? (
    <Kpi lb={lb} v={v} d={d} />
  ) : (
    <Kpi lb={lb} v="—" vClass="muted" d={G6_HINT} />
  )
}

function G6Cell({ v }: { v?: number | null }) {
  return (
    <td className={`r num ${cls(v)}`} title={v === undefined ? G6_HINT : undefined}>
      {fmtPctNum(v)}
    </td>
  )
}

export default function Brief() {
  const brief = useApi(() => briefApi.getMorningBrief(), [])
  // 仅当简报存在后拉取 AI 解读
  const interp = useApi<unknown>(
    () =>
      brief.data
        ? briefApi.interpretBrief(brief.data.brief_date)
        : Promise.resolve(null),
    [brief.data?.brief_date],
  )

  const s = brief.data?.sections
  const market = s?.market
  const indices = market?.indices ?? []
  const usIndices = indices.filter(i => i.code.startsWith('.')) // 美股前夜（code 以 . 开头）
  const sectors = market?.sectors_gain ?? []
  const hotStocks = market?.hot_stocks ?? []
  const turnover = market?.turnover
  const y = s?.yesterday
  const today = s?.today
  const sigCounts = y?.signal.counts ?? {}
  const positions = today?.positions ?? []
  const posMv = positions.reduce((sum, p) => sum + (p.market_value ?? 0), 0)
  const healthFail = (y?.data_health.fail ?? 0) > 0
  const healthWarn = (y?.data_health.warn ?? 0) > 0
  const failedTasks = (y?.tasks ?? []).filter(t => t.status === 'failed')

  const maxAbsPct = Math.max(1, ...sectors.map(sr => Math.abs(sr.change_pct ?? 0)))
  const ndx = usIndices.find(i => i.name.includes('纳斯达克'))?.change_pct

  if (brief.loading && !brief.data) {
    return (
      <section className="page">
        <div className="empty">简报加载中…</div>
      </section>
    )
  }
  if (brief.error) {
    return (
      <section className="page">
        <Notice text={brief.error} onRetry={brief.reload} retrying={brief.loading} />
      </section>
    )
  }
  if (!brief.data) {
    return (
      <section className="page">
        <div className="empty">暂无早盘简报（后端 40004 → 空态）</div>
      </section>
    )
  }
  const briefDateFull = brief.data.brief_date

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: 'repeat(5,1fr)', marginBottom: 14 }}>
        <Kpi
          lb="简报日期"
          v={briefDateFull.slice(5)}
          vStyle={{ fontSize: 19 }}
          d={`${s?.trade_date ?? ''} 回顾 · ${s?.is_open_today ? '今日开市' : '今日休市'}`}
        />
        <Kpi
          lb="隔夜纳指"
          v={ndx !== undefined ? fmtPctNum(ndx) : '—'}
          vClass={ndx !== undefined && ndx !== null && ndx < 0 ? 'down' : 'up'}
          d={ndx !== undefined ? '前夜收盘' : G6_HINT}
        />
        <NoticeKpi lb="A50 期指" />
        <NoticeKpi lb="北向资金(昨日)" />
        <NoticeKpi
          lb="两市成交(昨日)"
          v={turnover ? fmtWanYi(turnover.total) : undefined}
          d={turnover ? `沪 ${fmtWanYi(turnover.sh)} / 深 ${fmtWanYi(turnover.sz)}` : undefined}
        />
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1.1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            隔夜外盘<span className="hint">美股前夜收盘</span>
          </h3>
          {usIndices.length === 0 ? (
            <div className="empty">{G6_HINT}</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>指数</th>
                  <th className="r">收盘</th>
                  <th className="r">涨跌幅</th>
                </tr>
              </thead>
              <tbody>
                {usIndices.map(i => (
                  <tr key={i.code}>
                    <td>{i.name}</td>
                    <td className="r num">{i.close !== null ? i.close.toFixed(2) : '--'}</td>
                    <G6Cell v={i.change_pct} />
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h3>
            昨日热点板块<span className="hint">涨跌幅前 {sectors.length || 6}</span>
          </h3>
          {sectors.length === 0 ? (
            <div className="empty">{G6_HINT}</div>
          ) : (
            sectors.map(sr => (
              <div className="sbar" key={sr.name}>
                <span className="nm" title={sr.leader ? `领涨 ${sr.leader}` : undefined}>
                  {sr.name}
                </span>
                <div className="bar">
                  <i
                    style={{
                      width: `${Math.max(4, (Math.abs(sr.change_pct ?? 0) / maxAbsPct) * 100)}%`,
                      background: (sr.change_pct ?? 0) < 0 ? 'var(--down)' : undefined,
                    }}
                  />
                </div>
                <b className={`pc ${cls(sr.change_pct)}`}>{fmtPctNum(sr.change_pct)}</b>
              </div>
            ))
          )}
        </div>

        <div className="card">
          <h3>
            昨日活跃个股<span className="hint">涨停池 / 人气榜 TOP</span>
          </h3>
          {hotStocks.length === 0 ? (
            <div className="empty">{G6_HINT}</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>排名</th>
                  <th>证券</th>
                  <th className="r">涨跌幅</th>
                  <th>备注</th>
                </tr>
              </thead>
              <tbody>
                {hotStocks.map(h => (
                  <tr key={h.code}>
                    <td className="num" style={{ color: 'var(--txt3)' }}>{h.rank}</td>
                    <td>
                      {h.name} <span className="num" style={{ color: 'var(--txt3)', fontSize: 13 }}>{h.code}</span>
                    </td>
                    <G6Cell v={h.change_pct} />
                    <td style={{ color: 'var(--txt3)', fontSize: 13 }}>
                      {h.board_days !== undefined && h.board_days > 1
                        ? `${h.board_days} 连板`
                        : h.industry ?? ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="sec-note">
            {hotStocks.length > 0
              ? `今日持仓/观察名单与活跃股重合检查见早报摘要；成交额未采集（${G6_HINT}）`
              : G6_HINT}
          </div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            昨日回顾<span className="hint">模拟盘 · {s?.trade_date ?? ''}</span>
          </h3>
          <table style={{ fontSize: 14 }}>
            <tbody>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>账户净值</td>
                <td className="r num">
                  {y?.nav.total_asset !== null && y?.nav.total_asset !== undefined ? (
                    <>
                      {fmtMoney(y.nav.total_asset)}{' '}
                      {y.nav.daily_return !== null && y.nav.daily_return !== undefined && (
                        <span className={y.nav.daily_return < 0 ? 'down' : 'up'}>
                          ({fmtRatioPct(y.nav.daily_return)})
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>信号执行</td>
                <td className="r num">
                  {y?.signal ? (
                    <>
                      {sigCounts.BUY ?? 0} 买 / {sigCounts.SELL ?? 0} 卖 / 共 {y.signal.total} 条
                      {y.signal.top_buys.length > 0 && (
                        <div style={{ fontSize: 12.5, color: 'var(--txt3)' }}>
                          重点买入 {y.signal.top_buys.slice(0, 3).join('、')}…
                        </div>
                      )}
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>被拒委托</td>
                <td className="r num">
                  <span className="muted" title={G6_HINT}>该能力未产出</span>
                </td>
              </tr>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>当前持仓</td>
                <td className="r num">
                  {positions.length} 只
                  {posMv > 0 && <span className="num" style={{ color: 'var(--txt3)' }}> · 市值 {fmtMoney(posMv)}</span>}
                </td>
              </tr>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>数据健康</td>
                <td className="r">
                  <Tag
                    type={healthFail ? 'warn' : 'ok'}
                    label={
                      y?.data_health
                        ? `${y.data_health.fail + y.data_health.warn} 项异常 / ${y.data_health.overall}`
                        : '—'
                    }
                  />
                </td>
              </tr>
            </tbody>
          </table>
          {(failedTasks.length > 0 || y?.trade.message) && (
            <div className="sec-note">
              {failedTasks.length > 0 && `${failedTasks.map(t => t.task_name).join('、')} 执行失败；`}
              {y?.trade.message ?? ''}
            </div>
          )}
        </div>

        <div className="card">
          <h3>
            今日计划<span className="hint">交易日历 · {briefDateFull}</span>
          </h3>
          <div className="tl">
            {(today?.checklist ?? []).map(p => (
              <div className="tl-item" key={p.time}>
                <div className="t">{p.time}</div>
                <div className="n">{p.task}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <h3>
          AI 解读
          <span className="hint">deepseek-chat · 基于 BriefReader 白名单只读数据</span>
        </h3>
        {interp.error ? (
          <Notice text={interp.error} onRetry={interp.reload} retrying={interp.loading} />
        ) : interp.loading && !interp.data ? (
          <div className="empty">AI 解读生成中…</div>
        ) : interp.data ? (
          <div style={{ fontSize: 14.5, color: 'var(--txt2)', lineHeight: 2, whiteSpace: 'pre-wrap' }}>
            {(interp.data as { interpretation: string }).interpretation}
          </div>
        ) : (
          <div className="empty">暂无解读</div>
        )}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="chip" style={{ fontSize: 13 }}>情绪：以解读内容为准</span>
          <span className="chip" style={{ fontSize: 13 }}>数据源：早报 sections 白名单</span>
          <button className="btn" style={{ marginLeft: 'auto' }} onClick={interp.reload} disabled={interp.loading}>
            {interp.loading ? '生成中…' : '重新生成'}
          </button>
        </div>
      </div>
    </section>
  )
}
