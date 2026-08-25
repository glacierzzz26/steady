import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { EChartsOption } from 'echarts'
import EChart from '../../components/EChart'
import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import Seg from '../../components/Seg'
import Tag from '../../components/Tag'
import { factorApi } from '../../api'
import type {
  FactorDefinition, FactorHeatmap, FactorStatus, FactorTrialDetail,
  FactorTrialItem, FactorTrialsData,
} from '../../api'
import { ficBarOpt, quintileOpt } from '../../mock/chartOpt'
import { tokens } from '../../theme'

/* 因子中文名（后端 factor_definition 只有 name，zh 为展示层静态映射） */
const FACTOR_ZH: Record<string, string> = {
  ma_trend: '均线趋势', macd_signal: 'MACD信号', pe_ratio: '市盈率',
  pb_ratio: '市净率', roe_quality: '盈利质量', debt_risk: '负债风险',
}

/* 生命周期状态 → 中文/样式（对齐后端状态机 draft/trial/verified/active/disabled） */
const STATUS_ZH: Record<string, { text: string; cls: 'ok' | 'warn' | 'hold' }> = {
  draft: { text: '草稿', cls: 'hold' },
  trial: { text: '试算中', cls: 'warn' },
  verified: { text: '检验中', cls: 'warn' },
  active: { text: '已上线', cls: 'ok' },
  disabled: { text: '已停用', cls: 'hold' },
}
const TRIAL_ZH: Record<string, string> = { pending: '排队中', running: '运行中', done: '已完成', failed: '失败' }

/* 向导 = 页面分区导航（任意新公式模板向导为产品愿景，设计 §4.3，见 sec-note） */
const STEPS = [
  { label: '因子池', anchor: 'ff-pool' },
  { label: '参数配置', anchor: 'ff-config' },
  { label: '试算检验', anchor: 'ff-trial' },
  { label: '版本管理', anchor: 'ff-versions' },
  { label: '参数寻优', anchor: 'ff-optimize' },
]

interface LoadState { loading: boolean; error?: string }

/* ---- 参数契约（引擎权威，quant-engine factor_trial.py 头注释）----
 * ma_trend：short/long（window 简写 → short，long 缺省 20）；macd_signal：fast/slow/signal；
 * value/quality/risk 无计算参数（as-of 取值）。变体因子名 = 基础名 + "_" + 后缀。 */
interface ParamDef { key: string; label: string; min: number; max: number; step: number; def: number }
const PARAM_DEFS: Record<string, ParamDef[]> = {
  ma_trend: [
    { key: 'short', label: '短均线', min: 2, max: 60, step: 1, def: 5 },
    { key: 'long', label: '长均线', min: 3, max: 120, step: 1, def: 20 },
  ],
  macd_signal: [
    { key: 'fast', label: '快线', min: 2, max: 40, step: 1, def: 12 },
    { key: 'slow', label: '慢线', min: 3, max: 60, step: 1, def: 26 },
    { key: 'signal', label: '信号线', min: 2, max: 20, step: 1, def: 9 },
  ],
}
const BASE_FACTORS = Object.keys(PARAM_DEFS)
  .concat(['pe_ratio', 'pb_ratio', 'roe_quality', 'debt_risk'])
  .sort((a, b) => b.length - a.length)

/** 变体因子名 → 基础因子（引擎 resolve_base_factor 同规约：最长前缀匹配） */
function baseOf(name: string): string {
  return BASE_FACTORS.find(b => name === b || name.startsWith(b + '_')) ?? name
}

/** 因子 params 快照 → 试算参数（window 简写 → short；缺省补经典值；无计算参数 → {}） */
function factorParams(base: string, p?: Record<string, unknown> | null): Record<string, number> {
  const defs = PARAM_DEFS[base]
  if (!defs) return {}
  const src = p && typeof p === 'object' ? p : {}
  const out: Record<string, number> = {}
  for (const d of defs) {
    const v = d.key === 'short' && src.window != null ? src.window : src[d.key]
    out[d.key] = typeof v === 'number' && Number.isFinite(v) ? v : d.def
  }
  return out
}

/** 寻优网格默认候选：首参数给 3~4 个候选（默认寻优轴 = 第一个参数，引擎取取值最多者），
 *  其余参数钉住当前值（引擎「其他键取各自网格首值」）；horizon 取 5/10/20 */
function defaultGrid(base: string, p: Record<string, number>): Record<string, string> {
  const defs = PARAM_DEFS[base] ?? []
  const grid: Record<string, string> = {}
  defs.forEach((d, i) => {
    const v = p[d.key] ?? d.def
    grid[d.key] = i === 0
      ? [v - 3, v - 1, v + 1, v + 3].filter(x => x >= d.min && x <= d.max).join(',') || String(v)
      : String(v)
  })
  grid.horizon = '5,10,20'
  return grid
}

/** 逗号分隔候选串 → number[]（忽略空 / 非法段） */
function parseGrid(s: Record<string, string>): Record<string, number[]> {
  const out: Record<string, number[]> = {}
  for (const [k, v] of Object.entries(s)) {
    const arr = v.split(',').map(x => parseFloat(x.trim())).filter(Number.isFinite)
    if (arr.length) out[k] = arr
  }
  return out
}

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${`${d.getMonth() + 1}`.padStart(2, '0')}-${`${d.getDate()}`.padStart(2, '0')}`
}

const f3 = (v?: number | null) => (v == null ? '--' : v.toFixed(3))
const f2 = (v?: number | null) => (v == null ? '--' : v.toFixed(2))

/* 参数寻优热力图 option（null 格 = 无定义，不着色、tooltip 显「无定义」，不做 0 造假） */
function heatmapOption(hm: FactorHeatmap): EChartsOption {
  const x = hm.param_values.map(v => `${v}`)
  const y = hm.horizons.map(h => `${h}日`)
  const data: Array<[number, number, number | null]> = []
  const nums: number[] = []
  hm.grid.forEach((row, i) => row.forEach((ic, j) => {
    data.push([j, i, ic])
    if (ic != null) nums.push(ic)
  }))
  const lo = nums.length ? Math.min(...nums) : 0
  const hi = nums.length ? Math.max(...nums) : 0
  const tip = tokens.tooltip
  return {
    grid: { left: 64, right: 14, top: 26, bottom: 48 },
    tooltip: {
      ...tip,
      formatter: (p: unknown) => {
        const d = (p as { value: [number, number, number | null] }).value
        const v = d[2] == null ? '无定义' : d[2].toFixed(3)
        return `${y[d[1]]} × ${x[d[0]]}：<b>${v}</b>`
      },
    },
    xAxis: { type: 'category', data: x },
    yAxis: { type: 'category', data: y },
    visualMap: {
      min: lo, max: hi, calculable: false, orient: 'horizontal',
      left: 'center', bottom: 0,
      textStyle: { color: '#8B93A7', fontSize: 13 },
      inRange: { color: ['#1B2230', '#4C7DFF', '#A8C0FF'] },
      show: false,
    },
    series: [{
      type: 'heatmap',
      data,
      itemStyle: { borderColor: '#0B0E14', borderWidth: 2 },
      label: {
        show: true, color: '#E6EAF2', fontSize: 13,
        formatter: (p: { value: Array<number | null> }) =>
          (p.value[2] == null ? '—' : p.value[2].toFixed(3).replace(/^0/, '')),
      },
    }],
  } as EChartsOption
}

export default function FactorFactory() {
  const [factors, setFactors] = useState<FactorDefinition[]>()
  const [state, setState] = useState<LoadState>({ loading: true })
  const [active, setActive] = useState('')
  const [statusFilter, setStatusFilter] = useState('全部')
  const [step, setStep] = useState(0)

  /* 参数配置（试算参数 + 区间；寻优网格候选独立于下方卡片） */
  const [params, setParams] = useState<Record<string, number>>({})
  const [optGrid, setOptGrid] = useState<Record<string, string>>({})
  const [start, setStart] = useState(() => {
    const d = new Date(); d.setFullYear(d.getFullYear() - 2); return fmtDate(d)
  })
  const [end, setEnd] = useState(() => fmtDate(new Date()))

  /* 试算/寻优任务（提交 → 轮询 GET /factor-trials/:id） */
  const [trialRes, setTrialRes] = useState<FactorTrialDetail>()
  const [trialId, setTrialId] = useState<number>()
  const [polling, setPolling] = useState(false)
  const [trialErr, setTrialErr] = useState<string>()
  const pollTimer = useRef<number>()

  /* 版本表「最近试算」列（因子 → 最近任务状态 + done 的 ic_mean） */
  const [trialMap, setTrialMap] = useState<Record<string, { status: string; ic?: number | null }>>({})

  const [busy, setBusy] = useState<string>() // 'trial' | 'optimize' | 'sw:name' | 'fk:name' | 'del:name'
  const activeRef = useRef('')

  /* 每因子最近一条试算（列表 id 倒序 → 首次出现即最新） */
  const latestOf = (tr: FactorTrialsData): Record<string, FactorTrialItem> => {
    const latest: Record<string, FactorTrialItem> = {}
    for (const t of tr.items ?? []) if (!latest[t.factor_name]) latest[t.factor_name] = t
    return latest
  }

  const enrichTrialMap = useCallback(async (tr: FactorTrialsData) => {
    const latest = latestOf(tr)
    const map: Record<string, { status: string; ic?: number | null }> = {}
    await Promise.all(Object.entries(latest).map(async ([name, t]) => {
      if (t.status === 'done') {
        try {
          const d = await factorApi.getTrial(t.id)
          map[name] = { status: 'done', ic: d.ic_mean ?? null }
        } catch {
          map[name] = { status: 'done' } /* 详情失败 → 表格只显示状态 */
        }
      }
    }))
    setTrialMap(prev => ({ ...prev, ...map }))
  }, [])

  const refreshTrials = useCallback(async () => {
    try {
      const tr = await factorApi.getTrials({ limit: 50 })
      const latest = latestOf(tr)
      const map: Record<string, { status: string; ic?: number | null }> = {}
      for (const [name, t] of Object.entries(latest)) map[name] = { status: t.status }
      setTrialMap(map)
      void enrichTrialMap(tr)
    } catch {
      /* 试算历史加载失败不拖垮页面 */
    }
  }, [enrichTrialMap])

  const stopPoll = useCallback(() => {
    if (pollTimer.current) { window.clearTimeout(pollTimer.current); pollTimer.current = undefined }
  }, [])

  /* 轮询任务详情：pending/running 每 2.5s 重取；done/failed 落定 */
  const loadTrial = useCallback(async (id: number) => {
    stopPoll()
    setTrialId(id)
    setTrialErr(undefined)
    setPolling(true)
    const tick = async () => {
      try {
        const r = await factorApi.getTrial(id)
        setTrialRes(r)
        if (r.status === 'done' || r.status === 'failed') {
          setPolling(false)
          if (r.status === 'done') void refreshTrials()
          return
        }
        pollTimer.current = window.setTimeout(tick, 2500)
      } catch (e) {
        setPolling(false)
        setTrialErr(e instanceof Error ? e.message : '试算加载失败')
      }
    }
    void tick()
  }, [stopPoll, refreshTrials])

  /* 选中因子：重置参数配置 + 载入该因子最近一次试算 */
  const applyFactor = useCallback(async (name: string, f?: FactorDefinition) => {
    setActive(name)
    activeRef.current = name
    const base = baseOf(name)
    const p = factorParams(base, f?.params)
    setParams(p)
    setOptGrid(defaultGrid(base, p))
    setTrialRes(undefined)
    setTrialId(undefined)
    setTrialErr(undefined)
    stopPoll()
    try {
      const tr = await factorApi.getTrials({ factor_name: name, limit: 1 })
      if (tr.items?.length) await loadTrial(tr.items[0].id)
    } catch {
      /* 最近试算加载失败 → 试算卡空态 */
    }
  }, [loadTrial, stopPoll])

  const load = useCallback(async () => {
    stopPoll()
    setState({ loading: true, error: undefined })
    try {
      const [fl, tr] = await Promise.all([factorApi.getFactors(), factorApi.getTrials({ limit: 50 })])
      setFactors(fl.items)
      const keep = activeRef.current && fl.items.some(f => f.name === activeRef.current)
        ? activeRef.current : (fl.items[0]?.name ?? '')
      setActive(keep)
      setState({ loading: false })
      if (keep) void applyFactor(keep, fl.items.find(f => f.name === keep))
      const latest = latestOf(tr)
      const map: Record<string, { status: string; ic?: number | null }> = {}
      for (const [name, t] of Object.entries(latest)) map[name] = { status: t.status }
      setTrialMap(map)
      void enrichTrialMap(tr)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载失败'
      setState({ loading: false, error: msg })
    }
  }, [applyFactor, enrichTrialMap, stopPoll])

  useEffect(() => { load() }, [load])
  useEffect(() => () => stopPoll(), [stopPoll])

  /* 页面分区导航：滚动高亮当前向导步骤 */
  useEffect(() => {
    const onScroll = () => {
      let cur = 0
      STEPS.forEach((s, i) => {
        const el = document.getElementById(s.anchor)
        if (el && el.getBoundingClientRect().top < 140) cur = i
      })
      setStep(cur)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const jump = (anchor: string) => {
    document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  /* ---- 提交试算 / 寻优 ---- */
  const submitTrial = async () => {
    if (!active || busy) return
    setBusy('trial')
    setTrialErr(undefined)
    try {
      const base = baseOf(active)
      // value/quality/risk 无计算参数 → 省略 params 键（后端显式空对象会被拒）
      const body = PARAM_DEFS[base] ? { params, start, end } : { start, end }
      const r = await factorApi.createTrial(active, body)
      await loadTrial(r.id)
    } catch (e) {
      setTrialErr(e instanceof Error ? e.message : '提交失败')
    } finally {
      setBusy(undefined)
    }
  }

  const submitOptimize = async () => {
    if (!active || busy) return
    const base = baseOf(active)
    const grid = parseGrid({ ...optGrid })
    const paramGrid = PARAM_DEFS[base] ? grid : { horizon: grid.horizon ?? [5, 10, 20] }
    if (Object.keys(paramGrid).length === 0) {
      setTrialErr('寻优网格不能为空（每个参数候选至少一个数字）')
      return
    }
    setBusy('optimize')
    setTrialErr(undefined)
    try {
      const r = await factorApi.createOptimize(active, { param_grid: paramGrid, start, end })
      await loadTrial(r.id)
    } catch (e) {
      setTrialErr(e instanceof Error ? e.message : '提交失败')
    } finally {
      setBusy(undefined)
    }
  }

  /* ---- 版本管理操作（状态流转 / fork / 删除） ---- */
  const opErr = (e: unknown, prefix: string) =>
    setTrialErr(`${prefix}：${e instanceof Error ? e.message : '操作失败'}`)

  const opSwitch = async (name: string, to: FactorStatus) => {
    if (busy) return
    setBusy(`sw:${name}`)
    try { await factorApi.switchFactor(name, to); await load() }
    catch (e) { opErr(e, `${name} 状态流转失败`) }
    finally { setBusy(undefined) }
  }
  const opFork = async (name: string) => {
    if (busy) return
    setBusy(`fk:${name}`)
    try { await factorApi.forkFactor(name); await load() }
    catch (e) { opErr(e, `${name} 新版本失败`) }
    finally { setBusy(undefined) }
  }
  const opDelete = async (name: string) => {
    if (busy) return
    if (!window.confirm(`确认删除草稿因子 ${name}？已有试算/评分/检验记录会被后端拒绝`)) return
    setBusy(`del:${name}`)
    try { await factorApi.deleteFactor(name); await load() }
    catch (e) { opErr(e, `${name} 删除失败`) }
    finally { setBusy(undefined) }
  }

  /* 每状态可用操作（按服务端状态机 factorTransitions 映射） */
  const opsFor = (f: FactorDefinition): Array<{ label: string; color?: string; onClick: () => void }> => {
    const dim = 'var(--txt2)'
    switch (f.status) {
      case 'draft':
        return [
          { label: '提交试算', onClick: () => void opSwitch(f.name, 'trial') },
          { label: '新版本', color: dim, onClick: () => void opFork(f.name) },
          { label: '删除', color: 'var(--warn)', onClick: () => void opDelete(f.name) },
        ]
      case 'trial':
        return [
          { label: '通过', onClick: () => void opSwitch(f.name, 'verified') },
          { label: '回炉', color: dim, onClick: () => void opSwitch(f.name, 'draft') },
        ]
      case 'verified':
        return [
          { label: '上线', onClick: () => void opSwitch(f.name, 'active') },
          { label: '回炉', color: dim, onClick: () => void opSwitch(f.name, 'draft') },
        ]
      case 'active':
        return [
          { label: '停用', color: 'var(--warn)', onClick: () => void opSwitch(f.name, 'disabled') },
          { label: '新版本', color: dim, onClick: () => void opFork(f.name) },
        ]
      default:
        return [
          { label: '恢复草稿', color: dim, onClick: () => void opSwitch(f.name, 'draft') },
          { label: '重新上线', onClick: () => void opSwitch(f.name, 'active') },
        ]
    }
  }

  const statusSeg = useMemo(
    () => (
      <Seg
        options={['全部', '草稿', '试算中', '检验中', '已上线', '已停用']}
        value={statusFilter}
        onChange={setStatusFilter}
      />
    ),
    [statusFilter],
  )

  /* ---- 派生 ---- */
  const activeFactor = factors?.find(f => f.name === active)
  const base = baseOf(active)
  const paramDefs = PARAM_DEFS[base] ?? []
  const hmView = useMemo(() => (trialRes?.heatmap ? heatmapOption(trialRes.heatmap) : undefined), [trialRes])
  const icChart = useMemo(() => {
    const pts = (trialRes?.ic_series ?? []).filter(p => p.ic != null)
    return { has: pts.length > 0, option: ficBarOpt(pts.map(p => p.date), pts.map(p => p.ic as number)) }
  }, [trialRes])
  const qChart = useMemo(() => {
    const q = trialRes?.quantiles ?? []
    return q.length
      ? { has: true, option: quintileOpt(q.map(x => `Q${x.group}`), q.map(x => x.ret)) }
      : { has: false, option: undefined as EChartsOption | undefined }
  }, [trialRes])
  const rangeHint = trialRes?.dates ? `${trialRes.dates.start} ~ ${trialRes.dates.end}` : '近 2 年区间'

  if (state.loading && !factors) {
    return (
      <section className="page">
        <div className="empty">加载中…</div>
      </section>
    )
  }
  if (state.error && !factors) {
    return (
      <section className="page">
        <Notice text={state.error} onRetry={load} retrying={state.loading} />
      </section>
    )
  }
  if (!factors?.length) {
    return (
      <section className="page">
        <div className="empty">暂无因子定义（factor_definition 空）</div>
      </section>
    )
  }

  return (
    <section className="page">
      {/* 分区导航（对齐页面五个区块） */}
      <div className="vstep">
        {STEPS.map((s, i) => (
          <div key={s.label} className={`st${i === step ? ' on' : ''}`} onClick={() => jump(s.anchor)}>
            <span className="no">STEP {i + 1}</span>
            {s.label}
          </div>
        ))}
      </div>

      <div className="grid" style={{ gridTemplateColumns: '270px 1fr 320px', marginBottom: 14 }}>
        {/* 因子池 */}
        <div className="card" id="ff-pool">
          <h3>
            因子定义池<span className="hint">点击载入参数配置</span>
          </h3>
          {factors.map(f => {
            const b = baseOf(f.name)
            const st = STATUS_ZH[f.status ?? ''] ?? { text: f.status ?? '', cls: 'hold' as const }
            return (
              <div
                key={f.name}
                className={`tpl${active === f.name ? ' on' : ''}`}
                onClick={() => void applyFactor(f.name, f)}
              >
                <div>
                  {FACTOR_ZH[b] ?? f.name} <span className="nm-en">{f.name}</span> {f.version}
                  <div className="m">
                    分类 {f.category ?? '--'} · 权重 {(f.weight ?? 0).toFixed(2)}
                  </div>
                </div>
                <Tag type={st.cls} label={st.text} />
              </div>
            )
          })}
          <div className="sec-note">
            factor_definition 全量（6 基础因子 + fork 变体）。任意新公式模板向导为产品愿景（设计 §4.3）。
          </div>
        </div>

        {/* 参数配置 */}
        <div className="card" id="ff-config">
          <h3>
            参数配置 · {FACTOR_ZH[base] ?? active}
            <span className="hint">对已有因子参数化重算 · {active}</span>
          </h3>
          <div style={{ display: 'flex', gap: 14, fontSize: 13, color: 'var(--txt2)', marginBottom: 8, flexWrap: 'wrap' }}>
            <span>分类 {activeFactor?.category ?? '--'}</span>
            <span>版本 {activeFactor?.version ?? '--'}</span>
            <span>状态 <Tag type={STATUS_ZH[activeFactor?.status ?? '']?.cls ?? 'hold'} label={STATUS_ZH[activeFactor?.status ?? '']?.text ?? activeFactor?.status ?? '--'} /></span>
            <span>权重 {(activeFactor?.weight ?? 0).toFixed(2)}</span>
          </div>
          {activeFactor?.formula && (
            <div className="codebox" style={{ fontSize: 13, whiteSpace: 'pre-wrap', marginBottom: 10 }}>
              {activeFactor.formula}
            </div>
          )}
          {paramDefs.length ? (
            <div style={{ marginBottom: 12 }}>
              {paramDefs.map(d => (
                <div key={d.key} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <div style={{ fontSize: 13, color: 'var(--txt3)', width: 86 }}>
                    {d.label}（{d.key}）
                  </div>
                  <input
                    type="number"
                    min={d.min}
                    max={d.max}
                    step={d.step}
                    value={params[d.key] ?? d.def}
                    onChange={e => {
                      const v = +e.target.value
                      if (Number.isFinite(v)) setParams(prev => ({ ...prev, [d.key]: v }))
                    }}
                    style={{ width: 90, fontFamily: 'var(--mono)' }}
                  />
                  <span style={{ fontSize: 12, color: 'var(--txt3)' }}>{d.min} ~ {d.max}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="sec-note" style={{ marginBottom: 12 }}>
              {base} 无计算参数（as-of 取值），试算直接使用因子原定义，参数输入不适用。
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
            <input type="date" value={start} onChange={e => setStart(e.target.value)} style={{ width: 132 }} />
            <span style={{ color: 'var(--txt3)', fontSize: 13 }}>至</span>
            <input type="date" value={end} onChange={e => setEnd(e.target.value)} style={{ width: 132 }} />
            <button className="btn pri" onClick={() => void submitTrial()} disabled={!!busy}>
              {busy === 'trial' ? '提交中…' : '发起试算'}
            </button>
            <button className="btn" onClick={() => void submitOptimize()} disabled={!!busy}>
              发起寻优
            </button>
          </div>
          {trialErr && (
            <div style={{ color: 'var(--warn)', fontSize: 13, marginTop: 4, lineHeight: 1.6 }}>⚠ {trialErr}</div>
          )}
          <div className="sec-note">
            区间上限 5 年；试算与 FactorLab 同口径（winsorize → 百分位 → 方向调整，IC 数学单实现）。
            公式编辑器与任意新公式为产品愿景，当前仅支持对已有因子的参数化重算（设计 §4.3）。
          </div>
        </div>

        {/* 试算结果 */}
        <div className="card" id="ff-trial">
          <h3>
            试算结果 · {FACTOR_ZH[base] ?? active}
            <span className="hint">{rangeHint}</span>
          </h3>
          {polling && (
            <div className="empty">
              {trialRes?.status === 'running'
                ? '引擎计算中…（约 1~2 分钟，请稍候）'
                : '任务排队中…（引擎每 5 分钟消费一次）'}
            </div>
          )}
          {!polling && trialRes?.status === 'failed' && (
            <Notice text={trialRes.error || '试算失败'} />
          )}
          {!polling && trialRes?.status === 'done' && trialRes.heatmap && (
            <div className="empty">最近一次为参数寻优任务，热力图见「参数寻优」卡片</div>
          )}
          {!polling && trialRes?.status === 'done' && trialRes.ic_series && (
            <>
              <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
                <Kpi
                  lb="RankIC 均值"
                  v={f3(trialRes.ic_mean)}
                  vStyle={{ fontSize: 20 }}
                  d={trialRes.ic_mean != null && trialRes.ic_mean > 0.03 ? '达标 > 0.03' : '阈值 0.03'}
                  dClass={trialRes.ic_mean != null && trialRes.ic_mean > 0.03 ? 'up' : undefined}
                  style={{ border: 0, background: 'var(--panel2)' }}
                />
                <Kpi
                  lb="ICIR"
                  v={f2(trialRes.icir)}
                  vStyle={{ fontSize: 20 }}
                  d={trialRes.icir != null && Math.abs(trialRes.icir) > 0.3 ? '达标 > 0.3' : '阈值 0.3'}
                  dClass={trialRes.icir != null && Math.abs(trialRes.icir) > 0.3 ? 'up' : undefined}
                  style={{ border: 0, background: 'var(--panel2)' }}
                />
              </div>
              {icChart.has ? <EChart option={icChart.option} height={140} /> : <div className="empty">无 IC 序列</div>}
              <div style={{ fontSize: 12.5, color: 'var(--txt2)', margin: '10px 0 2px', lineHeight: 1.9 }}>
                单调性（Q1 跑赢 Q5）{' '}
                <b className="num">{trialRes.monotonic == null ? '--' : `${(trialRes.monotonic * 100).toFixed(0)}%`}</b>
                <span style={{ marginLeft: 12 }}>IC 衰减</span>{' '}
                {(trialRes.ic_decay ?? []).map(d => (
                  <span key={d.horizon} className="num" style={{ marginLeft: 6 }}>
                    H{d.horizon}={d.ic == null ? '--' : d.ic.toFixed(2)}
                  </span>
                ))}
              </div>
              {qChart.has ? <EChart option={qChart.option!} height={180} /> : null}
            </>
          )}
          {!polling && !trialRes && (
            <div className="empty">选择因子并发起试算 / 寻优，结果实时轮询展示</div>
          )}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        {/* 版本管理 */}
        <div className="card" id="ff-versions">
          <h3>
            因子版本管理
            <span className="hint">每次修改都是新版本 · 可回滚 · 可对比 · {statusSeg}</span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>因子</th>
                <th>版本</th>
                <th>参数快照</th>
                <th className="r">最近试算</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {factors
                .filter(f => statusFilter === '全部' || (STATUS_ZH[f.status ?? '']?.text ?? f.status) === statusFilter)
                .map(f => {
                  const st = STATUS_ZH[f.status ?? ''] ?? { text: f.status ?? '', cls: 'hold' as const }
                  const tr = trialMap[f.name]
                  const hasParams = f.params && typeof f.params === 'object' && Object.keys(f.params).length
                  return (
                    <tr key={f.name} onClick={() => void applyFactor(f.name, f)} style={{ cursor: 'pointer' }}>
                      <td>
                        <b>{FACTOR_ZH[baseOf(f.name)] ?? f.name}</b> <span className="nm-en">{f.name}</span>
                      </td>
                      <td className="num">{f.version ?? '--'}</td>
                      <td style={{ fontSize: 12.5, color: 'var(--txt2)', fontFamily: 'var(--mono)' }}>
                        {hasParams ? JSON.stringify(f.params) : '—'}
                      </td>
                      <td className="r num">
                        {tr?.status === 'done'
                          ? (tr.ic == null ? '--' : f3(tr.ic))
                          : (tr ? TRIAL_ZH[tr.status] ?? '--' : '—')}
                      </td>
                      <td><Tag type={st.cls} label={st.text} /></td>
                      <td style={{ fontSize: 13.5 }} onClick={e => e.stopPropagation()}>
                        {opsFor(f).map(o => (
                          <a key={o.label} style={{ color: o.color ?? '#A8C0FF', cursor: 'pointer', marginRight: 8 }} onClick={o.onClick}>
                            {o.label}
                          </a>
                        ))}
                      </td>
                    </tr>
                  )
                })}
            </tbody>
          </table>
          <div className="sec-note">
            状态流转：<b>草稿</b>（可自由编辑）→ <b>试算中</b>（锁定公式，跑 IC/分层）→{' '}
            <b>检验中</b>（人工复核）→ <b style={{ color: 'var(--ok)' }}>已上线（可被策略引用 · 正式使用）</b> → 已停用。
            新版本（fork）自动生成 <span className="nm-en">_v2/_v3…</span> 并携带 params 快照；删除仅草稿，已有试算/评分/检验记录则拒。
            上线后旧版本保留 90 天供回滚。
          </div>
        </div>

        {/* 参数寻优 */}
        <div className="card" id="ff-optimize">
          <h3>
            参数寻优 · {FACTOR_ZH[base] ?? active}
            <span className="hint">参数轴 × 持有期 IC 均值 · 区间见上方</span>
          </h3>
          <div style={{ marginBottom: 10 }}>
            {paramDefs.map(d => (
              <div key={d.key} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <div style={{ fontSize: 13, color: 'var(--txt3)', width: 86 }}>
                  {d.label}（{d.key}）
                </div>
                <input
                  type="text"
                  value={optGrid[d.key] ?? ''}
                  onChange={e => setOptGrid(prev => ({ ...prev, [d.key]: e.target.value }))}
                  placeholder="候选值逗号分隔，如 3,5,10"
                  style={{ flex: 1, fontFamily: 'var(--mono)' }}
                />
              </div>
            ))}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <div style={{ fontSize: 13, color: 'var(--txt3)', width: 86 }}>持有期 horizon</div>
              <input
                type="text"
                value={optGrid.horizon ?? ''}
                onChange={e => setOptGrid(prev => ({ ...prev, horizon: e.target.value }))}
                placeholder="候选日数逗号分隔，如 5,10,20"
                style={{ flex: 1, fontFamily: 'var(--mono)' }}
              />
            </div>
            <button className="btn pri" onClick={() => void submitOptimize()} disabled={!!busy}>
              {busy === 'optimize' ? '提交中…' : '发起寻优'}
            </button>
          </div>
          {hmView ? (
            <EChart option={hmView} height={250} />
          ) : polling && trialRes?.status !== 'done' ? (
            <div className="empty">寻优计算中…</div>
          ) : trialRes?.heatmap == null && trialRes?.status === 'done' ? (
            <div className="empty">最近一次为单组试算，无热力图</div>
          ) : (
            <div className="sec-note">
              解读：轴 = 取值最多的计算参数。若参数轴多格均有稳定 IC，说明因子对参数不敏感（好信号）；
              只有孤立一格发亮大概率过拟合，不建议采用该参数。
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
