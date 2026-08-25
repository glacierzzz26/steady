import { useCallback, useEffect, useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import EChart from '../../components/EChart'
import Notice from '../../components/Notice'
import { factorApi } from '../../api'
import type { FactorCorrData, FactorDefinition, FactorStatsData } from '../../api'
import { corrOpt, decayOpt, icBarOpt, quintileOpt } from '../../mock/chartOpt'
import { weightDefs } from '../../mock/data'

/* 因子中文名（后端 factor_definition 只有 name，zh 为展示层静态映射） */
const FACTOR_ZH: Record<string, string> = {
  ma_trend: '均线趋势', macd_signal: 'MACD信号', pe_ratio: '市盈率',
  pb_ratio: '市净率', roe_quality: '盈利质量', debt_risk: '负债风险',
}

/* 相关矩阵热力图短标签（规范序 6 因子） */
const CORR_SHORT: Record<string, string> = {
  ma_trend: 'ma', macd_signal: 'macd', pe_ratio: 'pe',
  pb_ratio: 'pb', roe_quality: 'roe', debt_risk: 'debt',
}

interface LoadState { loading: boolean; error?: string }

/* 卡片状态：ICIR 阈值 有效≥0.3 / 存疑≥0.1 / 无效；ic_mean 为负 → 反向（下挫色） */
function cardView(s?: FactorStatsData): {
  cls: string; text: string; meta: string; ic: string; down: boolean
} {
  if (!s || s.ic_mean == null) return { cls: 'hold', text: '无数据', meta: '暂无预计算', ic: '--', down: false }
  const a = s.icir == null ? 0 : Math.abs(s.icir)
  const cls = s.icir == null ? 'hold' : a >= 0.3 ? 'ok' : a >= 0.1 ? 'warn' : 'hold'
  const text = s.icir == null ? '样本不足' : a >= 0.3 ? '有效' : a >= 0.1 ? '存疑' : '无效'
  const dir = s.ic_mean < 0 ? '反向' : '正向'
  const meta = s.icir == null ? '无有效样本' : `ICIR ${s.icir.toFixed(2)} · ${dir}`
  return { cls, text, meta, ic: s.ic_mean.toFixed(3), down: s.ic_mean < 0 }
}

export default function FactorLab() {
  const [factors, setFactors] = useState<FactorDefinition[]>()
  const [statsMap, setStatsMap] = useState<Record<string, FactorStatsData>>({})
  const [corr, setCorr] = useState<FactorCorrData>()
  const [active, setActive] = useState('')
  const [state, setState] = useState<LoadState>({ loading: true })
  const [weights, setWeights] = useState<number[]>(() => weightDefs.map(w => w[2]))

  const load = useCallback(async () => {
    setState({ loading: true, error: undefined })
    try {
      const [fl, cr] = await Promise.all([
        factorApi.getFactors(),
        factorApi.getFactorCorr(),
      ])
      const names = (fl.items ?? []).map(f => f.name)
      // 每因子一份 stats（一次性给齐 FactorLab 页），失败单因子不拖垮整页
      const st: Record<string, FactorStatsData> = {}
      await Promise.all(
        names.map(async n => {
          try {
            st[n] = await factorApi.getFactorStats(n)
          } catch {
            /* 单因子加载失败 → 卡片显示无数据，其余照常 */
          }
        }),
      )
      setFactors(fl.items)
      setStatsMap(st)
      setCorr(cr)
      setActive(prev => (names.includes(prev) ? prev : names[0] ?? ''))
      setState({ loading: false })
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载失败'
      setState({ loading: false, error: msg })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const activeStats = statsMap[active]
  const sum = weights.reduce((a, b) => a + b, 0)
  const setWeight = (i: number, v: number) =>
    setWeights(prev => prev.map((w, j) => (j === i ? v : w)))

  /* RankIC 时序（选中因子，日度 IC + 累计） */
  const icChart = useMemo(() => {
    const pts = (activeStats?.ic_series ?? []).filter(p => p.ic != null)
    const dates = pts.map(p => p.date)
    const bars = pts.map(p => p.ic as number)
    const cum = bars.reduce<number[]>((acc, v) => [...acc, +(acc.length ? acc[acc.length - 1] + v : v)], [])
    return { has: dates.length > 0, option: icBarOpt(dates, bars, cum) }
  }, [activeStats])

  /* IC 衰减：全部因子叠加（横轴预计算档位） */
  const decayChart = useMemo(() => {
    const first = Object.values(statsMap).find(s => (s.ic_decay?.length ?? 0) > 0)
    if (!first) return { has: false, option: undefined as EChartsOption | undefined }
    const dates = first.ic_decay.map(d => `${d.horizon}日`)
    const series = (factors ?? [])
      .map(f => ({ name: f.name, data: statsMap[f.name]?.ic_decay.map(d => d.ic) ?? [] }))
      .filter(s => s.data.length > 0)
    return { has: series.length > 0, option: decayOpt(dates, series) }
  }, [statsMap, factors])

  /* 分层收益（选中因子 Q1..Q5 组均前向收益，单调递减为佳） */
  const quintileChart = useMemo(() => {
    const q = activeStats?.quantiles ?? []
    if (!q.length) return { has: false, option: undefined as EChartsOption | undefined }
    return { has: true, option: quintileOpt(q.map(x => `Q${x.group}`), q.map(x => x.ret)) }
  }, [activeStats])

  /* 相关矩阵（6×6 区间均值；null 格显示为中性 0） */
  const corrOption = useMemo(() => {
    if (!corr || !corr.factors?.length) return undefined
    const labels = corr.factors.map(f => CORR_SHORT[f] ?? f)
    return corrOpt(labels, corr.matrix.map(row => row.map(v => (v == null ? 0 : v))))
  }, [corr])

  const rangeHint = activeStats ? `${activeStats.range.start} ~ ${activeStats.range.end}` : '暂无数据'
  const monotonic = activeStats?.monotonic

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
  if (!factors || !factors.length) {
    return (
      <section className="page">
        <div className="empty">暂无因子定义（factor_definition 空）</div>
      </section>
    )
  }

  return (
    <section className="page">
      <div className="grid fac-grid" style={{ marginBottom: 14 }}>
        {factors.map(f => {
          const v = cardView(statsMap[f.name])
          return (
            <div
              className={`fac${active === f.name ? ' sel' : ''}`}
              key={f.name}
              onClick={() => setActive(f.name)}
              title={`${FACTOR_ZH[f.name] ?? f.name} · ${v.text} · 点击查看检验明细`}
            >
              <div className="name" style={{ fontSize: 14 }}>
                {FACTOR_ZH[f.name] ?? f.name} <span className={`tag ${v.cls}`}>{v.text}</span>
              </div>
              <div className={`ic20${v.down ? ' down' : ''}`}>{v.ic}</div>
              <div className="meta">{v.meta}</div>
              <div className="wbar">
                <i style={{ width: `${((f.weight ?? 0) * 100).toFixed(0)}%` }} />
              </div>
            </div>
          )
        })}
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            RankIC 时序 · {active}
            <span className="hint">日度 · {rangeHint}</span>
          </h3>
          {icChart.has ? (
            <EChart option={icChart.option} height={260} />
          ) : (
            <div className="empty">暂无预计算数据</div>
          )}
        </div>
        <div className="card">
          <h3>
            IC 衰减曲线
            <span className="hint">预测窗口 1~60 日 · 区间均值</span>
          </h3>
          {decayChart.has ? (
            <EChart option={decayChart.option!} height={260} />
          ) : (
            <div className="empty">暂无预计算数据</div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <h3>
          分层收益 · {active}
          <span className="hint">H=5 日前向 · 组均收益（Q1=因子最优组）</span>
        </h3>
        {quintileChart.has ? (
          <EChart option={quintileChart.option!} height={280} />
        ) : (
          <div className="empty">暂无预计算数据</div>
        )}
        <div className="legend" style={{ marginTop: 8 }}>
          {['#F0524F', '#E9863F', '#E9C23B', '#5BBA6D', '#2FBF71'].map((c, i) => (
            <span className="li" key={i}>
              <span className="sw" style={{ background: c }} />
              Q{i + 1}
            </span>
          ))}
          <span className="li" style={{ marginLeft: 'auto', color: 'var(--ok)' }}>
            单调性 · Q1 跑赢 Q5 {monotonic == null ? '--' : `${(monotonic * 100).toFixed(0)}%`}
          </span>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            因子相关性矩阵
            <span className="hint">Spearman · 区间均值</span>
          </h3>
          {corrOption ? (
            <EChart option={corrOption} height={250} />
          ) : (
            <div className="empty">暂无预计算数据</div>
          )}
        </div>
        <div className="card">
          <h3>
            权重实验台
            <span className="hint">拖动滑杆模拟重新配权 · 实时预览组合分</span>
          </h3>
          <div>
            {weightDefs.map((w, i) => (
              <div className="wrow" key={w[0]}>
                <span className="wn">{w[1]}</span>
                <input
                  type="range"
                  min={0}
                  max={40}
                  value={weights[i]}
                  onChange={e => setWeight(i, +e.target.value)}
                />
                <span className="wv">{weights[i]}%</span>
              </div>
            ))}
          </div>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 14,
              paddingTop: 12,
              borderTop: '1px solid var(--line)',
            }}
          >
            <span style={{ fontSize: 14, color: 'var(--txt2)' }}>
              权重合计 <b className="num" style={{ color: sum === 100 ? '#A8C0FF' : 'var(--up)' }}>{sum}%</b>
            </span>
            <span style={{ display: 'flex', gap: 8 }}>
              <button className="btn" onClick={() => setWeights(weightDefs.map(w => w[2]))}>
                重置
              </button>
              <button className="btn pri">以此权重发起回测</button>
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}
