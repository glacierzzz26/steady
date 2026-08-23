import { useEffect, useMemo, useRef, useState } from 'react'
import EChart from '../../components/EChart'
import Notice from '../../components/Notice'
import Tag from '../../components/Tag'
import { strategyApi, type CompareResult, type CompareSide, type StrategyInfo } from '../../api'
import { useApi } from '../../hooks/useApi'
import { hmapOpt, lineOpt } from '../../mock/chartOpt'
import { g } from '../../mock/random'

/* ---- 寻优热力图：top_n × buy_buffer（保持 mock + 标注，Iteration 4 不落地）---- */
const tns = ['5', '10', '15', '20', '25', '30', '40']
const bbs = ['5', '10', '15', '20', '25']
const gd2: number[][] = []
bbs.forEach((b, i) =>
  tns.forEach((t, j) => {
    const d1 = Math.abs(+t - 17.5)
    const d2 = Math.abs(+b - 12.5)
    gd2.push([j, i, +Math.max(0.015, 0.098 - 0.0026 * d1 - 0.002 * d2 + g(-0.004, 0.004)).toFixed(3)])
  }),
)
const grid2Option = hmapOpt(tns, bbs, gd2)

/* ---- 状态机元数据（契约 §4.2） ---- */
const STATUS_META: Record<string, ['ok' | 'hold' | 'warn' | 'plan', string]> = {
  draft: ['warn', '草稿'],
  backtest: ['hold', '回测验证'],
  sample: ['hold', '样本外'],
  active: ['ok', '运行中'],
  paused: ['plan', '已暂停'],
  archived: ['plan', '已归档'],
}
const STATUS_DESC: Record<string, string> = {
  draft: '草稿可编辑 · 保存后推进回测验证',
  backtest: '回测验证中 · 通过后进入样本外',
  sample: '样本外验证中 · 通过后可发布上线',
  active: '当前唯一生产策略 · 每日生成信号驱动模拟盘',
  paused: '已暂停 · 不参与每日信号生成',
  archived: '已归档冻结',
}
/** 合法下一步（单 active 不变量由后端强制） */
const NEXT: Record<string, [string, string] | null> = {
  draft: ['backtest', '推进回测'],
  backtest: ['sample', '样本外验证'],
  sample: ['active', '发布上线'],
  active: ['paused', '暂停'],
  paused: ['archived', '归档'],
  archived: null,
}

const fmtBtPct = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`
const catZh = (c?: string) => (c === 'trend' ? '趋势' : c === 'value' ? '价值' : c === 'quality' ? '质量' : c === 'risk' ? '风险' : c ?? '')
const catTag = (c?: string): 'ok' | 'hold' | 'warn' | 'plan' =>
  c === 'risk' ? 'warn' : c === 'value' ? 'hold' : 'ok'

/** 因子启用数（factor_weights 里权重>0 的因子；缺省按因子池） */
function factorCount(s: StrategyInfo, poolLen: number): number {
  const fw = s.factor_weights ?? {}
  const keys = Object.keys(fw).filter(k => fw[k] > 0)
  return keys.length > 0 ? keys.length : poolLen
}

const today = () => new Date().toISOString().slice(0, 10)

export default function StrategyFactory() {
  const strategies = useApi(() => strategyApi.getStrategies(), [])
  const factors = useApi(() => strategyApi.getFactors(), [])
  const itemList = strategies.data?.items ?? []

  // 构建器因子池（GET /factors 真实 factor_definition）
  const factorPool = useMemo(
    () => (factors.data?.items ?? []).map(f => ({
      name: f.name, zh: f.description || f.name, cat: f.category, weight: f.weight,
    })),
    [factors.data],
  )

  // ---- 构建器编辑目标：'' 未选 → 自动首个 draft；'__new__' 新建草稿；否则策略名 ----
  const [editing, setEditing] = useState<string>('')
  const [newName, setNewName] = useState('')
  const bootRef = useRef(false)

  const [factorOn, setFactorOn] = useState<boolean[]>([])
  const [weights, setWeights] = useState<number[]>([]) // 百分比（保存时归一为 1.0 小数）
  const [topN, setTopN] = useState(20)
  const [buyBuf, setBuyBuf] = useState(15)
  const [sellBuf, setSellBuf] = useState(30)
  const [maxPos, setMaxPos] = useState(20) // %
  const [stopLoss, setStopLoss] = useState(0) // %（0 = 关闭，与引擎缺省一致）
  const [ddFuse, setDdFuse] = useState(0) // %（0 = 关闭）
  const [industryLimit, setIndustryLimit] = useState(0) // %（0 = 关闭）
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  const editingInfo = editing === '__new__' ? null : itemList.find(x => x.name === editing) ?? null
  const canSave = editing === '__new__' || (editingInfo?.status ?? '') === 'draft'

  const resetDefaults = () => {
    setFactorOn(factorPool.map(f => f.weight > 0))
    setWeights(factorPool.map(f => Math.round(f.weight * 100)))
    setTopN(20); setBuyBuf(15); setSellBuf(30)
    setMaxPos(20); setStopLoss(0); setDdFuse(0); setIndustryLimit(0)
    setNewName('')
  }

  const loadBuilder = (name: string | null) => {
    setEditing(name ?? '__new__')
    setMsg(null)
    if (name && name !== '__new__') {
      const s = itemList.find(x => x.name === name)
      if (!s) { resetDefaults(); return }
      const fw = s.factor_weights ?? {}
      const p = s.params ?? {}
      const hasW = Object.keys(fw).length > 0
      setFactorOn(factorPool.map(f => (hasW ? (fw[f.name] ?? 0) > 0 : f.weight > 0)))
      setWeights(factorPool.map(f => Math.round((hasW ? (fw[f.name] ?? 0) : f.weight) * 100)))
      setTopN((p.top_n as number) ?? 20)
      setBuyBuf((p.buy_buffer as number) ?? 15)
      setSellBuf((p.sell_buffer as number) ?? 30)
      setMaxPos(Math.round(((p.max_position_pct as number) ?? 0.2) * 100))
      setStopLoss(Math.round(((p.stop_loss_pct as number) ?? 0) * 100))
      setDdFuse(Math.round(((p.drawdown_fuse_pct as number) ?? 0) * 100))
      setIndustryLimit(Math.round(((p.industry_limit_pct as number) ?? 0) * 100))
    } else {
      resetDefaults()
    }
  }

  // 首次就绪：自动选中首个草稿（否则首个非归档）
  useEffect(() => {
    if (bootRef.current) return
    const items = strategies.data?.items
    if (!items || factorPool.length === 0) return
    bootRef.current = true
    const def =
      items.find(s => s.status === 'draft') ??
      items.find(s => s.status && s.status !== 'archived') ??
      items[0]
    loadBuilder(def ? def.name : '__new__')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies.data, factorPool])

  // A/B 默认 base=active、candidate=首个草稿/候选（strategies 就绪后一次）
  const [abCfg, setAbCfg] = useState<{ base: string; candidate: string; start: string; end: string; fillMode: string }>({
    base: '', candidate: '', start: '2019-01-01', end: today(), fillMode: 't1_open',
  })
  useEffect(() => {
    if (!strategies.data) return
    const items = strategies.data.items
    const active = items.find(s => s.status === 'active')
    const cand = items.find(s => s.status === 'draft') ??
      items.find(s => s.status && s.status !== 'archived' && s.status !== 'active')
    setAbCfg(c => ({
      ...c,
      base: c.base || active?.name || items[0]?.name || '',
      candidate: c.candidate || cand?.name || active?.name || '',
    }))
  }, [strategies.data])

  // ---- A/B 轮询（pending → 每 3s 重查，最多 10 分钟） ----
  const [ab, setAb] = useState<{ phase: 'idle' | 'pending' | 'done' | 'failed'; result?: CompareResult; error?: string }>({ phase: 'idle' })
  const abortRef = useRef(false)
  useEffect(() => () => { abortRef.current = true }, [])
  const runCompare = async () => {
    if (!abCfg.base || !abCfg.candidate) {
      setAb({ phase: 'failed', error: '请先选择 base 与 candidate' }); return
    }
    setAb({ phase: 'pending' })
    abortRef.current = false
    const { base, candidate, start, end, fillMode } = abCfg
    try {
      for (let i = 0; i < 200; i++) {
        if (abortRef.current) return
        const res = await strategyApi.compareStrategies(base, candidate, start, end, fillMode)
        if (!res.status) { setAb({ phase: 'done', result: res }); return }
        if (res.status === 'failed') { setAb({ phase: 'failed', error: '任务失败，请到回测页查看具体原因' }); return }
        if (abortRef.current) return
        await new Promise(r => setTimeout(r, 3000))
      }
      setAb({ phase: 'failed', error: '轮询超时（10 分钟），请稍后重试' })
    } catch (e) {
      setAb({ phase: 'failed', error: e instanceof Error ? e.message : '对比请求失败' })
    }
  }

  // ---- 存为草稿（新建 POST / 已有 draft PUT） ----
  const saveDraft = async () => {
    const sum = weights.reduce((a, b) => a + b, 0)
    if (sum === 0) { setMsg({ ok: false, text: '至少保留一个因子权重' }); return }
    if (editing === '__new__' && !newName.trim()) {
      setMsg({ ok: false, text: '新建草稿需填写策略名称（英文唯一标识）' }); return
    }
    const factor_weights: Record<string, number> = {}
    factorPool.forEach((f, i) => {
      if (!factorOn[i]) return
      const v = Math.round((weights[i] / sum) * 10000) / 10000
      if (v > 0) factor_weights[f.name] = v
    })
    const params = {
      top_n: topN, buy_buffer: buyBuf, sell_buffer: sellBuf,
      max_position_pct: maxPos / 100, stop_loss_pct: stopLoss / 100,
      drawdown_fuse_pct: ddFuse / 100, industry_limit_pct: industryLimit / 100,
    }
    setSaving(true)
    try {
      let savedName = editing
      if (editing === '__new__') {
        const st = await strategyApi.createStrategy({
          name: newName.trim(), zh_name: newName.trim(), description: '策略构建器新建',
          factor_weights, params,
        })
        savedName = st.name
        setEditing(st.name)
      } else {
        await strategyApi.updateStrategy(editing, { factor_weights, params })
      }
      setMsg({ ok: true, text: `已存为草稿 ${savedName}（权重合计 ${sum}%）` })
      strategies.reload()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  const fork = async (name: string) => {
    try {
      await strategyApi.forkStrategy(name)
      setMsg({ ok: true, text: `已复制 ${name} 为新草稿（version +1）` })
      strategies.reload()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '复制失败' })
    }
  }
  const advance = async (name: string, next: string) => {
    if (next === 'active' &&
      !window.confirm(`确认将 ${name} 发布上线？当前 active 策略将自动降级为已暂停，旧持仓按卖出缓冲自然退出，不强制清仓。`)) return
    try {
      await strategyApi.switchStrategy(name, next)
      setMsg({ ok: true, text: `${name} → ${STATUS_META[next][1]}` })
      strategies.reload()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '状态流转失败' })
    }
  }

  const sum = weights.reduce((a, b) => a + b, 0)
  const active = itemList.find(s => s.status === 'active')
  const cand = itemList.find(s => s.status && s.status !== 'active' && s.status !== 'archived')

  // ---- A/B 图与指标 ----
  const abOption = useMemo(() => {
    const r = ab.result
    if (ab.phase !== 'done' || !r || !r.base?.nav?.length || !r.candidate) return null
    const b = r.base
    const dates = b.nav.map(p => p.date)
    const cMap = new Map(r.candidate.nav.map(p => [p.date, p.nav]))
    const bmMap = new Map((r.benchmark?.nav ?? []).map(p => [p.date, p.nav]))
    return lineOpt(
      {
        dates,
        series: [
          { name: `${b.strategy_name}`, data: b.nav.map(p => p.nav), w: 2 },
          { name: `${r.candidate.strategy_name}`, data: dates.map(d => cMap.get(d) ?? null) },
          { name: '沪深300', data: dates.map(d => bmMap.get(d) ?? null) },
        ],
      },
      ['#6C5CE7', '#4C7DFF', '#8B93A7'],
      false,
    )
  }, [ab])

  const abRows = useMemo(() => {
    const r = ab.result
    if (ab.phase !== 'done' || !r || !r.base || !r.candidate) return [] as { label: string; v1: string; v2: string; better: 'up' | 'down' | '' }[]
    const b = r.base, c = r.candidate
    const rows: {
      label: string
      get: (s: CompareSide) => string
      cmp?: (s: CompareSide) => number // 无 cmp = 中性指标（如成交笔数），不高亮
      lowerBetter?: boolean
    }[] = [
      { label: '总收益', get: s => fmtBtPct(s.total_return), cmp: s => s.total_return },
      { label: '年化收益', get: s => fmtBtPct(s.annualized_return), cmp: s => s.annualized_return },
      { label: '最大回撤', get: s => fmtBtPct(s.max_drawdown), cmp: s => s.max_drawdown },
      { label: '夏普比率', get: s => (s.sharpe ? s.sharpe.toFixed(2) : '--'), cmp: s => s.sharpe },
      { label: '年换手', get: s => `${s.turnover.toFixed(2)}×/年`, cmp: s => s.turnover, lowerBetter: true },
      { label: '成本占比', get: s => fmtBtPct(s.cost), cmp: s => s.cost, lowerBetter: true },
      { label: '成交笔数', get: s => `${s.trades} 笔` },
    ]
    return rows.map(rw => {
      const better = rw.cmp
        ? rw.lowerBetter ? rw.cmp(b) > rw.cmp(c) : rw.cmp(c) > rw.cmp(b)
        : false
      return { label: rw.label, v1: rw.get(b), v2: rw.get(c), better: better ? ('up' as const) : ('' as const) }
    })
  }, [ab])

  if (factors.error) return (
    <section className="page"><div className="card"><Notice text={factors.error} onRetry={factors.reload} retrying={factors.loading} /></div></section>
  )
  if (strategies.error && !strategies.data) return (
    <section className="page"><div className="card"><Notice text={strategies.error} onRetry={strategies.reload} retrying={strategies.loading} /></div></section>
  )

  return (
    <section className="page">
      {/* 三策略卡 */}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', marginBottom: 14 }}>
        {active ? (
          <div className="card" style={{ borderColor: 'rgba(47,191,113,.35)' }}>
            <h3>
              {active.zh_name || active.name} ({active.name}) <Tag type="ok" label="运行中" />
            </h3>
            <table style={{ fontSize: 14 }}>
              <tbody>
                <tr><td style={{ color: 'var(--txt2)' }}>版本</td><td className="r num">{active.version || '—'}</td></tr>
                <tr><td style={{ color: 'var(--txt2)' }}>因子</td><td className="r num">{factorCount(active, factorPool.length)} 因子参与</td></tr>
                <tr><td style={{ color: 'var(--txt2)' }}>轮动参数</td><td className="r num">top_n {(active.params?.top_n as number) ?? 20} · 单票 {(active.params?.max_position_pct as number ?? 0.2) * 100}%</td></tr>
                <tr><td style={{ color: 'var(--txt2)' }}>回测依据</td><td className="r num">{active.latest_backtest_id ? `#${active.latest_backtest_id}` : '—'}</td></tr>
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card">
            <h3>运行中策略 <Tag type="plan" label="无" /></h3>
            <div className="empty" style={{ padding: '24px 0', fontSize: 13.5 }}>
              暂无 active 策略。请在构建器新建草稿并沿状态机推进到运行中。
            </div>
          </div>
        )}

        {cand ? (
          <div className="card" style={{ borderColor: 'rgba(76,125,255,.35)' }}>
            <h3>
              {cand.zh_name || cand.name} ({cand.name}){' '}
              <Tag type={(STATUS_META[cand.status ?? ''] ?? ['hold', cand.status ?? ''])[0]} label={(STATUS_META[cand.status ?? ''] ?? ['hold', ''])[1]} />
            </h3>
            <table style={{ fontSize: 14 }}>
              <tbody>
                <tr><td style={{ color: 'var(--txt2)' }}>版本</td><td className="r num">{cand.version || '—'}</td></tr>
                <tr><td style={{ color: 'var(--txt2)' }}>因子</td><td className="r num">{factorCount(cand, factorPool.length)} 因子参与</td></tr>
                <tr><td style={{ color: 'var(--txt2)' }}>风控</td><td className="r num">止损 {(cand.params?.stop_loss_pct as number ?? 0) * 100}% · 熔断 {(cand.params?.drawdown_fuse_pct as number ?? 0) * 100}%</td></tr>
                <tr><td style={{ color: 'var(--txt2)' }}>最近回测</td><td className="r num">{cand.latest_backtest_id ? `#${cand.latest_backtest_id}` : '—'}</td></tr>
              </tbody>
            </table>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button className="btn" onClick={() => loadBuilder(cand.name)}>载入构建器</button>
              {cand.status === 'sample' && (
                <button className="btn pri" onClick={() => advance(cand.name, 'active')}>发布上线</button>
              )}
            </div>
          </div>
        ) : (
          <div className="card planned">
            <h3>无候选策略</h3>
            <div style={{ fontSize: 14, color: 'var(--txt2)', lineHeight: 1.8 }}>
              当前没有草稿/验证中的候选。用右侧构建器新建草稿，或对运行中策略「复制」出一条候选版本迭代。
            </div>
          </div>
        )}

        <div className="card planned">
          <h3>新建策略草稿 <Tag type="plan" label="新" /></h3>
          <div style={{ fontSize: 14, color: 'var(--txt2)', lineHeight: 1.8 }}>
            轮动 + 缓冲带骨架固定。新建进入草稿状态，可配因子权重与风控参数；验证通过后沿状态机发布上线（单 active 不变量）。
          </div>
          <div style={{ marginTop: 12 }}>
            <button className="btn pri" onClick={() => loadBuilder('__new__')}>开始新建</button>
          </div>
        </div>
      </div>

      {/* 策略状态管理 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>
          策略状态管理
          <span className="hint">同一时间仅一个策略可「运行中」· 状态决定是否参与每日信号生成</span>
        </h3>
        {strategies.loading && !strategies.data ? (
          <div className="empty">策略列表加载中…</div>
        ) : itemList.length === 0 ? (
          <div className="empty">暂无策略</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>策略</th>
                <th>版本</th>
                <th>状态</th>
                <th>说明</th>
                <th className="r">最近回测</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {itemList.map(s => {
                const meta = STATUS_META[s.status ?? ''] ?? ['hold', s.status ?? '—']
                const next = s.status ? NEXT[s.status] : null
                return (
                  <tr key={s.name}>
                    <td>
                      <b>{s.zh_name || s.name}</b> <span className="nm-en">{s.name}</span>
                    </td>
                    <td className="num">{s.version || '—'}</td>
                    <td><Tag type={meta[0]} label={meta[1]} /></td>
                    <td style={{ color: 'var(--txt2)', fontSize: 13.5 }}>{STATUS_DESC[s.status ?? ''] ?? ''}</td>
                    <td className="r num">{s.latest_backtest_id ? `#${s.latest_backtest_id}` : '—'}</td>
                    <td style={{ fontSize: 13.5 }}>
                      <a style={{ color: '#A8C0FF', cursor: 'pointer', marginRight: 10 }} onClick={() => loadBuilder(s.name)}>
                        {s.status === 'draft' ? '编辑' : '查看'}
                      </a>
                      <a style={{ color: 'var(--txt2)', cursor: 'pointer', marginRight: 10 }} onClick={() => fork(s.name)}>复制</a>
                      {next && (
                        <a
                          style={{ color: next[0] === 'active' ? 'var(--ok)' : 'var(--warn)', cursor: 'pointer', marginRight: 10 }}
                          onClick={() => advance(s.name, next[0])}
                        >
                          {next[1]}
                        </a>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {msg && (
          <div style={{ marginTop: 10, fontSize: 13, color: msg.ok ? 'var(--ok)' : 'var(--down)' }}>{msg.text}</div>
        )}
        <div className="sec-note">
          状态流转：<b>草稿</b>（可编辑参数与因子池）→ <b>回测验证</b> → <b>样本外验证</b>（walk-forward 通过）→{' '}
          <b style={{ color: 'var(--ok)' }}>运行中（正式使用 · 每日生成信号驱动模拟盘）</b> → 已暂停 / 已归档。切换运行中策略需二次确认，新旧策略平滑衔接：旧持仓按卖出缓冲规则自然退出，不强制清仓。
        </div>
      </div>

      {/* 构建器 + 寻优 */}
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 14 }}>
        <div className="card builder">
          <h3>
            策略构建器 · {editing === '__new__' ? '新建草稿' : `${editingInfo?.zh_name || editingInfo?.name || ''} (${editing})`}
            <span className="hint">骨架固定：轮动 + 缓冲带 · 仅草稿可保存</span>
          </h3>
          {editing === '__new__' && (
            <div className="wrow" style={{ marginBottom: 10 }}>
              <span className="wn">策略名称（英文唯一标识）</span>
              <input
                style={{ flex: 1, minWidth: 0 }}
                placeholder="如 mf_v2"
                value={newName}
                onChange={e => setNewName(e.target.value.trim())}
              />
            </div>
          )}

          <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>因子池（勾选参与 · 滑杆配权 · 权重合计保存时归一为 1.0）</div>
          <div>
            {factorPool.map((f, i) => (
              <div className="wrow" key={f.name}>
                <span className="wn">
                  <button className={`cbox${factorOn[i] ? ' on' : ''}`} onClick={() => setFactorOn(prev => prev.map((c, j) => (j === i ? !c : c)))}>✓</button>
                  {f.zh} <span className="nm-en">{f.name}</span>
                  {f.cat && <Tag type={catTag(f.cat)} label={catZh(f.cat)} />}
                </span>
                <input type="range" min={0} max={40} value={weights[i] ?? 0} onChange={e => setWeights(prev => prev.map((w, j) => (j === i ? +e.target.value : w)))} />
                <span className="wv">{weights[i] ?? 0}%</span>
              </div>
            ))}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 14, color: 'var(--txt2)' }}>
              权重合计 <b className="num" style={{ color: sum === 100 ? '#A8C0FF' : 'var(--up)' }}>{sum}%</b>
            </div>
          </div>

          <div style={{ fontSize: 13, color: 'var(--txt3)', margin: '14px 0 4px' }}>轮动参数</div>
          <div className="wrow">
            <span className="wn">目标持仓 top_n</span>
            <input type="range" min={5} max={40} value={topN} onChange={e => setTopN(+e.target.value)} />
            <span className="wv">{topN} 只</span>
          </div>
          <div className="wrow">
            <span className="wn">买入缓冲</span>
            <input type="range" min={5} max={30} value={buyBuf} onChange={e => setBuyBuf(+e.target.value)} />
            <span className="wv">前 {buyBuf} 名</span>
          </div>
          <div className="wrow">
            <span className="wn">卖出缓冲</span>
            <input type="range" min={20} max={60} value={sellBuf} onChange={e => setSellBuf(+e.target.value)} />
            <span className="wv">&gt;{sellBuf} 名</span>
          </div>

          <div style={{ fontSize: 13, color: 'var(--txt3)', margin: '14px 0 4px' }}>风控参数（0 = 关闭，引擎缺省）</div>
          <div className="wrow">
            <span className="wn">单票仓位上限</span>
            <input type="range" min={5} max={40} value={maxPos} onChange={e => setMaxPos(+e.target.value)} />
            <span className="wv">{maxPos}%</span>
          </div>
          <div className="wrow">
            <span className="wn">个股止损线</span>
            <input type="range" min={0} max={30} value={stopLoss} onChange={e => setStopLoss(+e.target.value)} />
            <span className="wv">{stopLoss === 0 ? '关闭' : `-${stopLoss}%`}</span>
          </div>
          <div className="wrow">
            <span className="wn">组合回撤熔断</span>
            <input type="range" min={0} max={25} value={ddFuse} onChange={e => setDdFuse(+e.target.value)} />
            <span className="wv">{ddFuse === 0 ? '关闭' : `-${ddFuse}%`}</span>
          </div>
          <div className="wrow">
            <span className="wn">行业集中度上限</span>
            <input type="range" min={0} max={50} value={industryLimit} onChange={e => setIndustryLimit(+e.target.value)} />
            <span className="wv">{industryLimit === 0 ? '关闭' : `≤${industryLimit}%`}</span>
          </div>

          {msg && (
            <div style={{ marginTop: 10, fontSize: 13, color: msg.ok ? 'var(--ok)' : 'var(--down)' }}>{msg.text}</div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button className="btn" onClick={saveDraft} disabled={saving || !canSave} title={canSave ? '' : '仅草稿可保存（先对非草稿做「复制」或新建）'}>
              {saving ? '保存中…' : '存为草稿'}
            </button>
            <button
              className="btn pri"
              disabled={editing === '__new__'}
              onClick={() => {
                if (editing === '__new__') { setMsg({ ok: false, text: '请先存为草稿，再发起 A/B 对比' }); return }
                setAbCfg(c => ({ ...c, candidate: editing }))
                runCompare()
              }}
              title={editing === '__new__' ? '请先存为草稿' : ''}
            >
              发起 A/B 回测
            </button>
          </div>
        </div>

        <div className="card">
          <h3>
            参数寻优 · top_n × buy_buffer<span className="hint">年化收益热力图 · T+1 假设 · 2019-2026（骨架/寻优不在 Iteration 4 范围，此图为示意）</span>
          </h3>
          <EChart option={grid2Option} height={270} />
          <div className="sec-note">
            解读：收益在 top_n 15~20、缓冲 10~15 一带形成平台而非尖峰 — 参数不敏感，策略稳健；尖峰参数通常意味着过拟合。寻优仅在训练段进行，2023 之后留作样本外验证。
          </div>
        </div>
      </div>

      {/* A/B 对比 */}
      <div className="card">
        <h3>
          A/B 对比
          <span className="hint">同一区间 · 同一成交假设 · 同一股票池 · 幂等复用已跑任务（回测页可见）</span>
        </h3>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, fontSize: 14, color: 'var(--txt2)', flexWrap: 'wrap' }}>
          <span>基准</span>
          <select style={{ minWidth: 140 }} value={abCfg.base} onChange={e => setAbCfg(c => ({ ...c, base: e.target.value }))}>
            {itemList.filter(s => s.status !== 'archived').map(s => (
              <option key={s.name} value={s.name}>{s.name}{s.status === 'active' ? '（现行）' : ''}</option>
            ))}
          </select>
          <span>候选</span>
          <select style={{ minWidth: 140 }} value={abCfg.candidate} onChange={e => setAbCfg(c => ({ ...c, candidate: e.target.value }))}>
            {itemList.filter(s => s.status !== 'archived').map(s => (
              <option key={s.name} value={s.name}>{s.name}</option>
            ))}
          </select>
          <input type="date" value={abCfg.start} onChange={e => setAbCfg(c => ({ ...c, start: e.target.value }))} />
          <span>~</span>
          <input type="date" value={abCfg.end} onChange={e => setAbCfg(c => ({ ...c, end: e.target.value }))} />
          <span>假设</span>
          <div className="seg" style={{ display: 'flex' }}>
            <button
              style={{ ...(abCfg.fillMode === 't1_open' ? { background: 'rgba(76,125,255,.18)', color: '#A8C0FF' } : {}) }}
              onClick={() => setAbCfg(c => ({ ...c, fillMode: 't1_open' }))}
            >T+1开盘</button>
            <button
              style={{ ...(abCfg.fillMode === 't_close' ? { background: 'rgba(76,125,255,.18)', color: '#A8C0FF' } : {}) }}
              onClick={() => setAbCfg(c => ({ ...c, fillMode: 't_close' }))}
            >T日收盘</button>
          </div>
          <button className="btn pri" onClick={runCompare} disabled={ab.phase === 'pending'}>
            {ab.phase === 'pending' ? '任务运行中…' : '发起对比'}
          </button>
        </div>

        {ab.phase === 'idle' && <div className="empty" style={{ padding: '36px 0' }}>选择两侧策略发起对比（首次需等待回测完成，任务在回测页可见）</div>}
        {ab.phase === 'pending' && (
          <div className="empty" style={{ padding: '36px 0' }}>
            A/B 任务运行中，每 3 秒自动轮询…（新建任务约 1~3 分钟；已跑过的区间秒回）
          </div>
        )}
        {ab.phase === 'failed' && <Notice text={ab.error ?? '对比失败'} onRetry={runCompare} retrying={false} />}

        {ab.phase === 'done' && ab.result && abOption && (
          <div className="grid" style={{ gridTemplateColumns: '2fr 1fr' }}>
            <EChart option={abOption} height={260} />
            <table style={{ fontSize: 14.5 }}>
              <thead>
                <tr>
                  <th>指标</th>
                  <th className="r">{ab.result.base?.strategy_name ?? ''}</th>
                  <th className="r">{ab.result.candidate?.strategy_name ?? ''}</th>
                </tr>
              </thead>
              <tbody>
                {abRows.map(r => (
                  <tr key={r.label}>
                    <td style={{ color: 'var(--txt2)' }}>{r.label}</td>
                    <td className="r num">{r.v1}</td>
                    <td className={`r num${r.better ? ' up' : ''}`}>{r.v2}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="sec-note">
          警示：A/B 基于同一区间、同一成交假设与同一股票池，杜绝「用乐观假设对比保守假设」；候选提升需经 walk-forward 滚动验证后才能发布。基准为沪深300（sh000300）。
        </div>
      </div>
    </section>
  )
}
