import { useMemo, useState } from 'react'
import EChart from '../../components/EChart'
import { corrOpt, decayOpt, genCorr, icBarOpt, lineOpt } from '../../mock/chartOpt'
import { g, layerSeries, months } from '../../mock/random'
import { weightDefs } from '../../mock/data'

/* ---- 因子卡数据 ---- */
const factors = [
  { zh: '均线趋势', en: 'ma_trend', status: 'warn', statusText: '存疑', ic: '0.021', icClass: '', meta: 'ICIR 0.18 · 二值信号', w: '80%' },
  { zh: 'MACD信号', en: 'macd_signal', status: 'warn', statusText: '存疑', ic: '0.018', icClass: '', meta: 'ICIR 0.15 · 二值信号', w: '80%' },
  { zh: '市盈率', en: 'pe_ratio', status: 'ok', statusText: '有效', ic: '-0.034', icClass: 'down', meta: 'ICIR 0.41 · 反向', w: '60%' },
  { zh: '市净率', en: 'pb_ratio', status: 'ok', statusText: '有效', ic: '-0.029', icClass: 'down', meta: 'ICIR 0.35 · 反向', w: '60%' },
  { zh: '盈利质量', en: 'roe_quality', status: 'ok', statusText: '有效', ic: '0.047', icClass: '', meta: 'ICIR 0.52 · 换手低', w: '80%' },
  { zh: '负债风险', en: 'debt_risk', status: 'hold', statusText: '无效', ic: '-0.006', icClass: '', meta: 'ICIR 0.07 · 建议剔除', w: '40%' },
]

/* ---- mock 图表数据（模块级计算，保证稳定） ---- */
const icBars = months.map(() => +g(-0.08, 0.14).toFixed(3))
const cumIc = icBars.reduce<number[]>((acc, v) => [...acc, +(acc.length ? acc[acc.length - 1] + v : v).toFixed(3)], [])
const icOption = icBarOpt(months, icBars, cumIc)
const decayOption = decayOpt()
const layerOption = lineOpt(
  {
    dates: months,
    series: [
      { name: 'Q1(最高)', data: layerSeries(92, 0.124), w: 2 },
      { name: 'Q2', data: layerSeries(92, 0.071) },
      { name: 'Q3', data: layerSeries(92, 0.042) },
      { name: 'Q4', data: layerSeries(92, 0.018) },
      { name: 'Q5(最低)', data: layerSeries(92, -0.023) },
    ],
  },
  ['#F0524F', '#E9863F', '#E9C23B', '#5BBA6D', '#2FBF71'],
  false,
)
const corrFacs = ['ma', 'macd', 'pe', 'pb', 'roe', 'debt']
const corrOption = corrOpt(corrFacs, genCorr(corrFacs))

const layerLegend = [
  { c: '#F0524F', t: 'Q1 年化 +12.4%' },
  { c: '#E9863F', t: 'Q2 年化 +7.1%' },
  { c: '#E9C23B', t: 'Q3 年化 +4.2%' },
  { c: '#5BBA6D', t: 'Q4 年化 +1.8%' },
  { c: '#2FBF71', t: 'Q5 年化 -2.3%' },
]

export default function FactorLab() {
  const [weights, setWeights] = useState<number[]>(() => weightDefs.map(w => w[2]))
  const sum = weights.reduce((a, b) => a + b, 0)

  const setWeight = (i: number, v: number) =>
    setWeights(prev => prev.map((w, j) => (j === i ? v : w)))

  const factorCards = useMemo(
    () =>
      factors.map(f => (
        <div className="fac" key={f.en}>
          <div className="name" style={{ fontSize: 14 }}>
            {f.zh} ({f.en}) <span className={`tag ${f.status}`}>{f.statusText}</span>
          </div>
          <div className={`ic20${f.icClass ? ' ' + f.icClass : ''}`}>{f.ic}</div>
          <div className="meta">{f.meta}</div>
          <div className="wbar">
            <i style={{ width: f.w }} />
          </div>
        </div>
      )),
    [],
  )

  return (
    <section className="page">
      <div className="grid fac-grid" style={{ marginBottom: 14 }}>
        {factorCards}
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            RankIC 时序 · roe_quality<span className="hint">月度 · 2019-01 ~ 2026-08</span>
          </h3>
          <EChart option={icOption} height={260} />
        </div>
        <div className="card">
          <h3>
            IC 衰减曲线<span className="hint">预测窗口 1~60 日</span>
          </h3>
          <EChart option={decayOption} height={260} />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <h3>
          分层回测 · roe_quality<span className="hint">Q1-Q5 等权组合 · 20日换仓 · 2019-2026</span>
        </h3>
        <EChart option={layerOption} height={280} />
        <div className="legend" style={{ marginTop: 8 }}>
          {layerLegend.map(l => (
            <span className="li" key={l.t}>
              <span className="sw" style={{ background: l.c }} />
              {l.t}
            </span>
          ))}
          <span className="li" style={{ marginLeft: 'auto', color: 'var(--ok)' }}>
            单调性 ✓ Q1&gt;Q2&gt;Q3&gt;Q4&gt;Q5
          </span>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            因子相关性矩阵<span className="hint">Spearman · 周度</span>
          </h3>
          <EChart option={corrOption} height={250} />
        </div>
        <div className="card">
          <h3>
            权重实验台<span className="hint">拖动滑杆模拟重新配权 · 实时预览组合分</span>
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
