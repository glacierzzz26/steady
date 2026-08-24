import { useMemo, useState } from 'react'
import EChart from '../../components/EChart'
import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import Tag from '../../components/Tag'
import { backtestApi, strategyApi, type BacktestJobItem } from '../../api'
import { fmtName } from '../../lib/names'
import { useApi } from '../../hooks/useApi'
import { lineOpt, type LineSeriesDef } from '../../mock/chartOpt'

const G8_DONE_HINT = 'T+1 开盘为保守假设（无未来函数），已支持（G8）'
/** 年化单边换手（倍数/年，后端已年化） */
const fmtTurnover = (v?: number) =>
  v === undefined || v === null || Number.isNaN(v) ? '--' : `${v.toFixed(2)}×/年`
/** 年化交易成本占比（小数比例 → %） */
const fmtCost = (v?: number) =>
  v === undefined || v === null || Number.isNaN(v) ? '--' : `${(v * 100).toFixed(2)}%/年`

/** 回测收益为小数比例（0.382 = 38.2%）→ 带符号百分比 */
function fmtBtPct(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`
}
const signCls = (v?: number | null) => (v !== undefined && v !== null && v < 0 ? 'down' : 'up')
/** fill_mode 后端小写 → 前端展示文案 */
const fillModeLabel = (v?: string) => (v === 't1_open' ? 'T+1开盘' : v === 't_close' ? 'T日收盘' : '--')

const BT_STATUS: Record<string, ['ok' | 'hold' | 'warn', string]> = {
  pending: ['hold', '排队中'],
  running: ['hold', '运行中'],
  done: ['ok', '完成'],
  failed: ['warn', '失败'],
}

// ---- 历史任务横向对比（Iteration 4 遗留项） ----
const MAX_CMP = 5
const NAV_COLORS = ['#4C7DFF', '#2FBF71', '#F0B45A', '#C792EA', '#E86F9B']
const BENCH_COLOR = '#8B93A7'

interface CmpMetric {
  label: string
  raw: (j: BacktestJobItem) => number | null
  fmt: (v: number | null) => string
  best?: 'max' // best:'max'=越大越好（最大回撤为负，取最大即最接近 0），该行最佳列高亮
}

const CMP_METRICS: CmpMetric[] = [
  { label: '总收益', raw: j => j.total_return ?? null, fmt: fmtBtPct, best: 'max' },
  { label: '年化', raw: j => j.annualized_return ?? null, fmt: fmtBtPct, best: 'max' },
  { label: '最大回撤', raw: j => j.max_drawdown ?? null, fmt: fmtBtPct, best: 'max' },
  { label: '夏普', raw: j => j.sharpe ?? null, fmt: v => (v == null ? '--' : v.toFixed(2)), best: 'max' },
  { label: '超额', raw: j => j.excess_return ?? null, fmt: fmtBtPct, best: 'max' },
  { label: '年换手', raw: j => j.turnover ?? null, fmt: v => fmtTurnover(v ?? undefined) },
  { label: '成本占比', raw: j => j.cost ?? null, fmt: v => fmtCost(v ?? undefined) },
  { label: '交易数', raw: j => j.trades ?? null, fmt: v => (v == null ? '--' : String(v)) },
]

export default function Backtest() {
  const [strategy, setStrategy] = useState('')
  const [start, setStart] = useState('2019-01-01')
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10))
  const [topN, setTopN] = useState(20)
  const [fillMode, setFillMode] = useState('T+1开盘')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const strategies = useApi(() => strategyApi.getStrategies(), [])
  const list = useApi(() => backtestApi.getBacktests(20), [])
  const detail = useApi<BacktestJobItem | null>(
    () =>
      selectedId !== null ? backtestApi.getBacktest(selectedId) : Promise.resolve(null),
    [selectedId, list.data],
  )

  const strategyOpts = strategies.data?.items ?? []
  const btRows = list.data?.items ?? []
  // name → zh_name 映射（回测历史表只存英文 strategy_name，展示时补中文）
  const zhBy = new Map(strategyOpts.map(s => [s.name, s.zh_name]))

  // ---- 历史任务横向对比：勾选已完成任务（数据全部来自列表缓存，不新增请求） ----
  const [cmpSel, setCmpSel] = useState<Set<number>>(new Set())
  const [selNote, setSelNote] = useState<string | null>(null)
  const toggleSel = (id: number) => {
    setSelNote(null)
    if (cmpSel.has(id)) {
      const next = new Set(cmpSel)
      next.delete(id)
      setCmpSel(next)
    } else if (cmpSel.size >= MAX_CMP) {
      setSelNote(`最多对比 ${MAX_CMP} 条`)
    } else {
      const next = new Set(cmpSel)
      next.add(id)
      setCmpSel(next)
    }
  }
  const selJobs = btRows.filter(j => cmpSel.has(j.id) && j.status === 'done')
  const mixedAssumption = new Set(selJobs.map(j => j.fill_mode).filter(Boolean)).size > 1

  // 净值叠加：每条归一化（nav/nav[0]），对齐全部选中任务日期的并集；基准取第一条含基准点的任务
  const cmpNavOption = useMemo(() => {
    if (selJobs.length < 2) return null
    const dates = [...new Set(selJobs.flatMap(j => (j.nav ?? []).map(p => p.date)))].sort()
    const series: LineSeriesDef[] = []
    const colors: string[] = []
    selJobs.forEach((j, i) => {
      const pts = j.nav ?? []
      const anchor = pts.find(p => p.nav > 0)?.nav
      if (!anchor) return
      const map = new Map(pts.filter(p => p.nav > 0).map(p => [p.date, p.nav / anchor]))
      series.push({
        name: `#${j.id} ${fmtName(zhBy.get(j.strategy_name), j.strategy_name)}`,
        data: dates.map(d => map.get(d) ?? null),
      })
      colors.push(NAV_COLORS[i % NAV_COLORS.length])
    })
    const benchJob = selJobs.find(j => (j.nav ?? []).some(p => p.benchmark != null && p.benchmark > 0))
    if (benchJob) {
      const bpts = (benchJob.nav ?? []).filter(p => p.benchmark != null && p.benchmark > 0)
      const anchor = bpts[0].benchmark!
      const bmap = new Map(bpts.map(p => [p.date, p.benchmark! / anchor]))
      series.push({ name: '沪深300 基准', data: dates.map(d => bmap.get(d) ?? null), w: 1.4 })
      colors.push(BENCH_COLOR)
    }
    if (!series.length) return null
    return lineOpt({ dates, series }, colors, false)
  }, [selJobs, zhBy])

  // 指标行最佳列（best:'max'），返回最佳列下标；无有效值返回 -1
  const bestIdxOf = (m: CmpMetric) => {
    if (!m.best) return -1
    let best = -1
    let bestV = -Infinity
    selJobs.forEach((j, i) => {
      const v = m.raw(j)
      if (v === null || Number.isNaN(v)) return
      if (v > bestV) {
        bestV = v
        best = i
      }
    })
    return best
  }

  const navOption = useMemo(() => {
    const n = detail.data?.nav ?? []
    return lineOpt(
      {
        dates: n.map(p => p.date),
        series: [
          { name: '策略净值', data: n.map(p => p.nav), w: 2 },
          { name: '沪深300', data: n.map(p => p.benchmark ?? null) },
        ],
      },
      ['#4C7DFF', '#8B93A7'],
      true,
    )
  }, [detail.data])

  const submit = async () => {
    setSubmitting(true)
    setMsg(null)
    try {
      const res = await backtestApi.submitBacktest({
        start_date: start,
        end_date: end,
        top_n: topN,
        fill_mode: fillMode === 'T+1开盘' ? 't1_open' : 't_close',
        strategy_name: strategy, // Iteration 4：指定策略回测（空=后端解析 active）
      })
      setMsg({ ok: true, text: `回测任务已提交 job #${res.job_id}（${res.status}）` })
      setSelectedId(res.job_id)
      list.reload()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '提交失败' })
    } finally {
      setSubmitting(false)
    }
  }

  const pick = (id: number) => {
    setSelectedId(id)
    setMsg(null)
  }

  const d = detail.data
  const dStatus = d ? (BT_STATUS[d.status] ?? ['hold', d.status]) : null

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: '340px 1fr', marginBottom: 14 }}>
        {/* 发起回测表单 */}
        <div className="card">
          <h3>发起回测</h3>
          <div style={{ fontSize: 14, color: 'var(--txt2)', marginBottom: 6 }}>策略</div>
          <select
            style={{ width: '100%' }}
            value={strategy}
            onChange={e => setStrategy(e.target.value)}
          >
            <option value="">请选择策略</option>
            {strategyOpts.map(s => (
              <option key={s.name} value={s.name}>
                {fmtName(s.zh_name, s.name)} · {s.status === 'active' ? '运行中' : s.status}
              </option>
            ))}
          </select>
          <div style={{ fontSize: 14, color: 'var(--txt2)', margin: '12px 0 6px' }}>回测区间</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input type="date" value={start} onChange={e => setStart(e.target.value)} style={{ width: '50%' }} />
            <input type="date" value={end} onChange={e => setEnd(e.target.value)} style={{ width: '50%' }} />
          </div>
          <div style={{ fontSize: 14, color: 'var(--txt2)', margin: '12px 0 6px' }}>
            持仓数 top_n <b className="num" style={{ color: '#A8C0FF', float: 'right' }}>{topN}</b>
          </div>
          <input type="range" min={5} max={50} value={topN} onChange={e => setTopN(+e.target.value)} />
          <div style={{ fontSize: 14, color: 'var(--txt2)', margin: '12px 0 6px' }}>成交时点假设</div>
          <div className="seg" style={{ width: '100%', display: 'flex' }}>
            <button
              style={{ flex: 1, ...(fillMode === 'T日收盘' ? { background: 'rgba(76,125,255,.18)', color: '#A8C0FF' } : {}) }}
              onClick={() => setFillMode('T日收盘')}
            >
              T日收盘
            </button>
            <button
              style={{ flex: 1, ...(fillMode === 'T+1开盘' ? { background: 'rgba(76,125,255,.18)', color: '#A8C0FF' } : {}) }}
              onClick={() => setFillMode('T+1开盘')}
            >
              T+1开盘
            </button>
          </div>
          <div style={{ fontSize: 13, color: 'var(--txt3)', margin: '8px 0 14px' }}>
            {fillMode === 'T+1开盘'
              ? `${G8_DONE_HINT}；结果自动附 T vs T+1 年化偏差`
              : 'T 日收盘为乐观假设（含未来函数），仅用于偏差对比'}
          </div>
          <button className="btn pri" style={{ width: '100%', justifyContent: 'center' }} onClick={submit} disabled={submitting || !strategy}>
            {submitting ? '提交中…' : '提交回测任务'}
          </button>
          {msg && (
            <div
              style={{
                marginTop: 10,
                fontSize: 13,
                color: msg.ok ? 'var(--ok)' : 'var(--down)',
                border: `1px solid ${msg.ok ? 'rgba(47,191,113,.25)' : 'rgba(240,82,79,.25)'}`,
                borderRadius: 8,
                padding: '8px 10px',
                background: msg.ok ? 'rgba(47,191,113,.06)' : 'rgba(240,82,79,.06)',
              }}
            >
              {msg.text}
            </div>
          )}
        </div>

        {/* 回测历史 */}
        <div className="card">
          <h3>
            回测历史
            <span className="hint">近 20 条 · 点击行查看净值 · 勾选 ≥2 条已完成任务横向对比</span>
          </h3>
          {list.error ? (
            <Notice text={list.error} onRetry={list.reload} retrying={list.loading} />
          ) : list.loading && !list.data ? (
            <div className="empty">回测列表加载中…</div>
          ) : btRows.length === 0 ? (
            <div className="empty">暂无回测任务</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>对比</th>
                  <th>ID</th>
                  <th>策略</th>
                  <th className="r">区间</th>
                  <th className="r">top_n</th>
                  <th className="r">假设</th>
                  <th className="r">总收益</th>
                  <th className="r">年化</th>
                  <th className="r">最大回撤</th>
                  <th className="r">夏普</th>
                  <th className="r">超额</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {btRows.map(j => {
                  const [tagType, tagText] = BT_STATUS[j.status] ?? ['hold', j.status]
                  const sel = j.id === selectedId
                  return (
                    <tr
                      key={j.id}
                      onClick={() => pick(j.id)}
                      title={j.error || '点击加载净值'}
                      style={{ cursor: 'pointer', ...(sel ? { background: 'rgba(76,125,255,.08)' } : {}) }}
                    >
                      <td onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={cmpSel.has(j.id)}
                          disabled={j.status !== 'done'}
                          title={j.status !== 'done' ? '仅已完成任务可对比' : undefined}
                          onChange={() => toggleSel(j.id)}
                        />
                      </td>
                      <td className="num">#{j.id}</td>
                      <td>{fmtName(zhBy.get(j.strategy_name), j.strategy_name)}</td>
                      <td className="r num">{`${j.start_date}~${j.end_date}`}</td>
                      <td className="r num">{j.top_n}</td>
                      <td className="r num">{fillModeLabel(j.fill_mode)}</td>
                      <td className={`r num ${signCls(j.total_return)}`}>{fmtBtPct(j.total_return)}</td>
                      <td className={`r num ${signCls(j.annualized_return)}`}>{fmtBtPct(j.annualized_return)}</td>
                      <td className="r num">{fmtBtPct(j.max_drawdown)}</td>
                      <td className="r num">{j.sharpe ? j.sharpe.toFixed(2) : '--'}</td>
                      <td className={`r num ${signCls(j.excess_return)}`}>{fmtBtPct(j.excess_return)}</td>
                      <td>
                        <Tag type={tagType} label={tagText} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
          <div style={{ marginTop: 10, fontSize: 13, color: 'var(--txt3)' }}>
            fill_mode / T+1 偏差已点亮（G8）；年换手 / 成本占比已点亮（Iteration 4 §3.5）
          </div>
        </div>
      </div>

      {/* 历史任务横向对比 */}
      <div className="card">
        <h3>
          历史任务横向对比
          <span className="hint">
            已选 {selJobs.length} 条 · 勾选 ≥2 条已完成任务并排对比
            {selNote && <b style={{ color: 'var(--warn)', marginLeft: 8 }}>{selNote}</b>}
          </span>
          {selJobs.length > 0 && (
            <button
              className="act"
              style={{ float: 'right' }}
              onClick={() => {
                setCmpSel(new Set())
                setSelNote(null)
              }}
            >
              清除选择
            </button>
          )}
        </h3>
        {mixedAssumption && selJobs.length >= 2 && (
          <div style={{ fontSize: 13, color: 'var(--warn)', marginBottom: 10 }}>
            ⚠️ 勾选混合了 T日收盘(t_close) 与 T+1开盘(t1_open) 假设，口径不同，对比仅供参考
          </div>
        )}
        {selJobs.length < 2 ? (
          <div className="empty" style={{ padding: '28px 0' }}>
            {selJobs.length === 0
              ? '在左侧历史列表勾选已完成任务，开启横向对比'
              : '再勾选 1 条已完成任务即可对比'}
          </div>
        ) : (
          <>
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  {selJobs.map(j => (
                    <th key={j.id} className="r">
                      <div className="num">#{j.id}</div>
                      <div>{fmtName(zhBy.get(j.strategy_name), j.strategy_name)}</div>
                      <div style={{ fontSize: 12, color: 'var(--txt3)', fontWeight: 400 }}>
                        {fillModeLabel(j.fill_mode)} · top_n {j.top_n}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {CMP_METRICS.map(m => {
                  const bi = bestIdxOf(m)
                  return (
                    <tr key={m.label}>
                      <td style={{ color: 'var(--txt2)' }}>{m.label}</td>
                      {selJobs.map((j, i) => {
                        const v = m.raw(j)
                        const isBest = m.best && v !== null && !Number.isNaN(v) && i === bi
                        const sign = !isBest && v !== null && !Number.isNaN(v) ? signCls(v) : ''
                        return (
                          <td
                            key={j.id}
                            className={`r num ${sign}`}
                            style={isBest ? { color: 'var(--ok)', fontWeight: 600 } : undefined}
                          >
                            {m.fmt(v)}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
                <tr>
                  <td style={{ color: 'var(--txt2)' }}>区间</td>
                  {selJobs.map(j => (
                    <td key={j.id} className="r num">
                      {j.start_date}~{j.end_date}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
            {cmpNavOption && (
              <>
                <h4 style={{ fontSize: 14, margin: '14px 0 6px', color: 'var(--txt2)' }}>
                  净值对比（归一化到 1.0）
                </h4>
                <EChart option={cmpNavOption} height={280} />
              </>
            )}
          </>
        )}
      </div>

      {/* 回测详情 */}
      <div className="card">
        <h3>
          {d
            ? `回测 #${d.id} · ${fmtName(zhBy.get(d.strategy_name), d.strategy_name)} · ${d.start_date}~${d.end_date} · top_n ${d.top_n} · ${fillModeLabel(d.fill_mode)}`
            : '回测详情'}
          <span className="hint">
            {dStatus ? (
              <Tag type={dStatus[0]} label={dStatus[1]} />
            ) : (
              '点击历史行查看'
            )}
          </span>
        </h3>
        {!d ? (
          <div className="empty" style={{ padding: '44px 0' }}>
            从上方历史列表选择一条回测查看净值与指标
          </div>
        ) : detail.error ? (
          <Notice text={detail.error} onRetry={detail.reload} retrying={detail.loading} />
        ) : d.status !== 'done' ? (
          <div className="empty" style={{ padding: '44px 0' }}>
            {d.status === 'failed' ? `任务失败：${d.error || '未知错误'}` : '任务运行中，完成后展示净值曲线'}
          </div>
        ) : (
          <>
            <EChart option={navOption} height={280} />
            <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginTop: 14 }}>
              <Kpi lb="总收益" v={fmtBtPct(d.total_return)} vClass={signCls(d.total_return)} d={`基准 ${fmtBtPct(d.benchmark_return)}`} style={{ border: 0, background: 'var(--panel2)' }} />
              <Kpi lb="年化收益" v={fmtBtPct(d.annualized_return)} vClass={signCls(d.annualized_return)} d={`${d.trading_days} 个交易日`} style={{ border: 0, background: 'var(--panel2)' }} />
              <Kpi lb="最大回撤" v={fmtBtPct(d.max_drawdown)} d="全程" style={{ border: 0, background: 'var(--panel2)' }} />
              <Kpi lb="夏普" v={d.sharpe ? d.sharpe.toFixed(2) : '--'} d={`最终资产 ¥${d.final_value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`} style={{ border: 0, background: 'var(--panel2)' }} />
              <Kpi lb="超额收益" v={fmtBtPct(d.excess_return)} vClass={signCls(d.excess_return)} d={`${d.trades} 笔交易 · ${d.positions} 只持仓`} style={{ border: 0, background: 'var(--panel2)' }} />
              <Kpi lb="T+1偏差" v={fmtBtPct(d.t1_deviation)} vClass={signCls(d.t1_deviation)} d="年化（t1_open−t_close）" style={{ border: 0, background: 'var(--panel2)' }} />
              <Kpi lb="年换手" v={fmtTurnover(d.turnover)} d="单边 ×/年（§3.5）" style={{ border: 0, background: 'var(--panel2)' }} />
              <Kpi lb="成本占比" v={fmtCost(d.cost)} d="年化成本/平均总资产" style={{ border: 0, background: 'var(--panel2)' }} />
            </div>
          </>
        )}
      </div>
    </section>
  )
}
