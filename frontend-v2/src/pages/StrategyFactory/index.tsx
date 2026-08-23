import { useState } from 'react'
import EChart from '../../components/EChart'
import Tag from '../../components/Tag'
import { hmapOpt, lineOpt } from '../../mock/chartOpt'
import { btSeries, g, yrs } from '../../mock/random'

/* ---- 因子池定义：[中文, 英文, 默认权重, 附加标签] ---- */
const FACTOR_POOL = [
  { zh: '20日动量', en: 'momentum_20', w: 25, badge: '新', badgeType: 'ok' },
  { zh: '趋势偏离', en: 'ma_dev v2.0', w: 20 },
  { zh: '盈利质量', en: 'roe_quality', w: 20 },
  { zh: '市盈率', en: 'pe_ratio', w: 15 },
  { zh: '市净率', en: 'pb_ratio', w: 10 },
  { zh: 'MACD信号', en: 'macd_signal', w: 10 },
]

/* ---- 寻优热力图：top_n × buy_buffer ---- */
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

/* ---- A/B 对比图 ---- */
const abOption = lineOpt(
  {
    dates: yrs,
    series: [
      { name: 'mf_v2 候选', data: btSeries(31, 0.093, 0.02, true), w: 2 },
      { name: 'multi_factor v1', data: btSeries(31, 0.051, 0.02, true) },
      { name: '沪深300', data: btSeries(31, 0.004, 0.018, false) },
    ],
  },
  ['#6C5CE7', '#4C7DFF', '#8B93A7'],
  false,
)

const abRows = [
  { label: '年化收益', v1: '+5.1%', v2: '+9.3%', v2Up: true },
  { label: '最大回撤', v1: '-21.4%', v2: '-18.9%', v2Up: true },
  { label: '夏普比率', v1: '0.42', v2: '0.71', v2Up: true },
  { label: '年换手', v1: '8.6x', v2: '6.2x', v2Up: true },
  { label: '样本外超额(23-26)', v1: '+1.2%', v2: '+4.6%', v2Up: true },
]

const statusRows = [
  {
    zh: '多因子轮动', en: 'multi_factor', ver: 'v1.0', status: 'ok', statusText: '运行中',
    desc: '08-01 上线 · 当前唯一生产策略', bt: '#11',
    ops: [['编辑', '#A8C0FF'], ['复制', 'var(--txt2)'], ['暂停', 'var(--warn)']],
  },
  {
    zh: '多因子轮动 v2', en: 'mf_v2', ver: 'v0.9', status: 'warn', statusText: '样本外验证',
    desc: '草稿可编辑 · 验证通过后一键替换运行中策略', bt: '#12',
    ops: [['编辑', '#A8C0FF'], ['对比', 'var(--txt2)'], ['发布上线', 'var(--ok)']],
  },
  {
    zh: '配对交易', en: 'pair_trading', ver: '—', status: 'plan', statusText: '规划中',
    desc: '骨架未实现 · 轮动骨架验证稳定后再评估', bt: '—',
    ops: [['编辑', 'var(--txt3)']] as Array<[string, string]>,
  },
]

export default function StrategyFactory() {
  const [factorOn, setFactorOn] = useState<boolean[]>(FACTOR_POOL.map(() => true))
  const [weights, setWeights] = useState<number[]>(FACTOR_POOL.map(f => f.w))
  const [topN, setTopN] = useState(20)
  const [buyBuf, setBuyBuf] = useState(15)
  const [sellBuf, setSellBuf] = useState(30)
  const [maxPos, setMaxPos] = useState(20)
  const [stopLoss, setStopLoss] = useState(15)
  const [ddFuse, setDdFuse] = useState(10)

  const sum = weights.reduce((a, b) => a + b, 0)

  const toggle = (i: number) => setFactorOn(prev => prev.map((c, j) => (j === i ? !c : c)))
  const setW = (i: number, v: number) => setWeights(prev => prev.map((w, j) => (j === i ? v : w)))

  return (
    <section className="page">
      {/* 三策略卡 */}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', marginBottom: 14 }}>
        <div className="card">
          <h3>
            多因子轮动 (multi_factor) <Tag type="ok" label="运行中" />
          </h3>
          <table style={{ fontSize: 14 }}>
            <tbody>
              <tr><td style={{ color: 'var(--txt2)' }}>版本</td><td className="r num">v1.0 · 08-01 上线</td></tr>
              <tr><td style={{ color: 'var(--txt2)' }}>因子</td><td className="r num">6 因子 · 40/30/20/10</td></tr>
              <tr><td style={{ color: 'var(--txt2)' }}>近30日超额</td><td className="r num down">-1.8%</td></tr>
              <tr><td style={{ color: 'var(--txt2)' }}>回测依据</td><td className="r num">#11 · T+1 假设</td></tr>
            </tbody>
          </table>
        </div>
        <div className="card" style={{ borderColor: 'rgba(76,125,255,.35)' }}>
          <h3>
            多因子轮动 v2 (mf_v2) <Tag type="warn" label="草稿" />
          </h3>
          <table style={{ fontSize: 14 }}>
            <tbody>
              <tr><td style={{ color: 'var(--txt2)' }}>变更</td><td className="r num">+momentum_20 · 趋势连续化</td></tr>
              <tr><td style={{ color: 'var(--txt2)' }}>回测对比</td><td className="r num up">年化 +5.1% → +9.3%</td></tr>
              <tr><td style={{ color: 'var(--txt2)' }}>状态</td><td className="r"><Tag type="warn" label="样本外验证中" /></td></tr>
            </tbody>
          </table>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button className="btn">查看差异</button>
            <button className="btn pri">发布上线</button>
          </div>
        </div>
        <div className="card planned">
          <h3>
            配对交易 (pair_trading) <Tag type="plan" label="规划中" />
          </h3>
          <div style={{ fontSize: 14, color: 'var(--txt2)', lineHeight: 1.8 }}>
            配对交易骨架（协整 + 价差回归）。当前系统仅支持轮动骨架；骨架扩展前需先验证：现有日线级数据深度是否满足协整检验的统计要求。
          </div>
        </div>
      </div>

      {/* 策略状态管理 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>
          策略状态管理
          <span className="hint">同一时间仅一个策略可「运行中」· 状态决定是否参与每日信号生成</span>
        </h3>
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
            {statusRows.map(r => (
              <tr key={r.en}>
                <td>
                  <b>{r.zh}</b> <span className="nm-en">{r.en}</span>
                </td>
                <td className="num">{r.ver}</td>
                <td><Tag type={r.status as 'ok' | 'warn' | 'plan'} label={r.statusText} /></td>
                <td style={{ color: 'var(--txt2)', fontSize: 13.5 }}>{r.desc}</td>
                <td className="r num">{r.bt}</td>
                <td style={{ fontSize: 13.5 }}>
                  {r.ops.map(([label, color]) => (
                    <a
                      key={label}
                      style={{
                        color,
                        cursor: color === 'var(--txt3)' ? 'not-allowed' : 'pointer',
                        marginRight: 8,
                      }}
                    >
                      {label}
                    </a>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="sec-note">
          状态流转：<b>草稿</b>（可编辑参数与因子池）→ <b>回测验证</b> → <b>样本外验证</b>（walk-forward 通过）→{' '}
          <b style={{ color: 'var(--ok)' }}>运行中（正式使用 · 每日生成信号驱动模拟盘）</b> → 已暂停 / 已归档。切换运行中策略需二次确认，新旧策略平滑衔接：旧持仓按卖出缓冲规则自然退出，不强制清仓。
        </div>
      </div>

      {/* 构建器 + 寻优 */}
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            策略构建器 · 多因子轮动 v2 (mf_v2)<span className="hint">骨架固定：轮动 + 缓冲带</span>
          </h3>
          <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>因子池（勾选参与 · 滑杆配权）</div>
          <div>
            {FACTOR_POOL.map((f, i) => (
              <div className="wrow" key={f.en}>
                <span className="wn">
                  <button className={`cbox${factorOn[i] ? ' on' : ''}`} onClick={() => toggle(i)}>✓</button>
                  {f.zh} <span className="nm-en">{f.en}</span>
                  {f.badge && <Tag type="ok" label={f.badge} />}
                </span>
                <input type="range" min={0} max={40} value={weights[i]} onChange={e => setW(i, +e.target.value)} />
                <span className="wv">{weights[i]}%</span>
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

          <div style={{ fontSize: 13, color: 'var(--txt3)', margin: '14px 0 4px' }}>风控参数</div>
          <div className="wrow">
            <span className="wn">单票仓位上限</span>
            <input type="range" min={5} max={40} value={maxPos} onChange={e => setMaxPos(+e.target.value)} />
            <span className="wv">{maxPos}%</span>
          </div>
          <div className="wrow">
            <span className="wn">个股止损线</span>
            <input type="range" min={5} max={30} value={stopLoss} onChange={e => setStopLoss(+e.target.value)} />
            <span className="wv">-{stopLoss}%</span>
          </div>
          <div className="wrow">
            <span className="wn">组合回撤熔断</span>
            <input type="range" min={5} max={25} value={ddFuse} onChange={e => setDdFuse(+e.target.value)} />
            <span className="wv">-{ddFuse}%</span>
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button className="btn">存为草稿</button>
            <button className="btn pri">发起 A/B 回测</button>
          </div>
        </div>

        <div className="card">
          <h3>
            参数寻优 · top_n × buy_buffer<span className="hint">年化收益热力图 · T+1 假设 · 2019-2026</span>
          </h3>
          <EChart option={grid2Option} height={270} />
          <div className="sec-note">
            解读：收益在 top_n 15~20、缓冲 10~15 一带形成平台而非尖峰 — 参数不敏感，策略稳健；尖峰参数通常意味着过拟合。寻优仅在训练段进行，2023
            之后留作样本外验证。
          </div>
        </div>
      </div>

      {/* A/B 对比 */}
      <div className="card">
        <h3>
          A/B 对比 · v1 现行 vs v2 候选<span className="hint">同一区间 · 同一 T+1 成交假设 · 同一股票池</span>
        </h3>
        <div className="grid" style={{ gridTemplateColumns: '2fr 1fr' }}>
          <EChart option={abOption} height={260} />
          <table style={{ fontSize: 14.5 }}>
            <thead>
              <tr>
                <th>指标</th>
                <th className="r">v1 现行</th>
                <th className="r">v2 候选</th>
              </tr>
            </thead>
            <tbody>
              {abRows.map(r => (
                <tr key={r.label}>
                  <td style={{ color: 'var(--txt2)' }}>{r.label}</td>
                  <td className="r num">{r.v1}</td>
                  <td className={`r num${r.v2Up ? ' up' : ''}`}>{r.v2}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="sec-note">
          警示：v2 的提升主要来自「趋势因子连续化 + 动量替换」，方向符合因子检验结论，但仍需 walk-forward
          滚动验证后才能发布 — A/B 对比页面全部基于同一假设，杜绝「用乐观假设对比保守假设」。
        </div>
      </div>
    </section>
  )
}
