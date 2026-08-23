import { useMemo, useState } from 'react'
import EChart from '../../components/EChart'
import Kpi from '../../components/Kpi'
import Seg from '../../components/Seg'
import Tag from '../../components/Tag'
import { ficBarOpt, hmapOpt } from '../../mock/chartOpt'
import { g, months } from '../../mock/random'

/* ---- 模板库 ---- */
const templates = [
  { zh: '20日动量', en: 'momentum_20', m: '动量 · 估计 IC 0.056 · 中低换手', tag: 'ok', tagText: '推荐' },
  { zh: '60日动量', en: 'momentum_60', m: '动量 · 估计 IC 0.048', tag: 'hold', tagText: '备选' },
  { zh: '20日波动率', en: 'volatility_20', m: '波动 · 反向 · 估计 IC -0.041', tag: 'hold', tagText: '备选' },
  { zh: '换手率', en: 'turnover_rate', m: '流动性 · 反向 · 估计 IC -0.033', tag: 'hold', tagText: '备选' },
  { zh: '毛利率', en: 'gross_margin', m: '质量 · 估计 IC 0.037', tag: 'hold', tagText: '备选' },
  { zh: '营收同比增速', en: 'revenue_yoy', m: '成长 · 估计 IC 0.029', tag: 'hold', tagText: '备选' },
]

const WIZARD = ['选择模板', '公式与参数', '预处理配置', '试算检验', '版本发布']

/* ---- 寻优热力图数据 ---- */
const gwin = ['5', '10', '20', '30', '40', '60', '90']
const ghor = ['持有1周', '持有2周', '持有1月', '持有2月']
const gv = [.031, .044, .058, .056, .049, .041, .033]
const gd: number[][] = []
ghor.forEach((h, i) =>
  gwin.forEach((w, j) => gd.push([j, i, +(gv[j] * (1 - 0.055 * i) + g(-0.004, 0.004)).toFixed(3)])),
)
const gridOption = hmapOpt(gwin, ghor, gd)

/* ---- 试算 RankIC 迷你图 ---- */
const fic = months.map(() => +(g(-0.05, 0.15)).toFixed(3))
const ficOption = ficBarOpt(months, fic)

/* ---- 版本管理行 ---- */
const versions = [
  {
    zh: '均线趋势', en: 'ma_trend', ver: 'v2.0', change: '二值信号 → MA5/MA20 连续偏离度',
    ic: '0.021 → 0.048', status: 'warn', statusText: '检验中', ops: [['编辑', '#A8C0FF'], ['回滚 v1', 'var(--txt2)']],
  },
  {
    zh: '20日动量', en: 'momentum_20', ver: 'v1.0', change: '新建 · 20日动量',
    ic: '— → 0.056', status: 'ok', statusText: '已上线', ops: [['编辑', '#A8C0FF'], ['新版本', 'var(--txt2)'], ['停用', 'var(--warn)']],
  },
  {
    zh: '盈利质量', en: 'roe_quality', ver: 'v1.1', change: 'TTM 化 · 消除季节性跳变',
    ic: '0.041 → 0.047', status: 'ok', statusText: '已上线', ops: [['编辑', '#A8C0FF'], ['新版本', 'var(--txt2)'], ['停用', 'var(--warn)']],
  },
  {
    zh: '负债风险', en: 'debt_risk', ver: 'v1.0', change: '资产负债率 · 分层无单调性',
    ic: '-0.006', status: 'hold', statusText: '拟下线', ops: [['编辑', '#A8C0FF'], ['归档', 'var(--txt3)']],
  },
]

const corrRows = [
  { name: 'roe_quality', rho: '0.31', tag: 'ok', tagText: '低冗余' },
  { name: 'pe_ratio', rho: '-0.18', tag: 'ok', tagText: '低冗余' },
  { name: 'ma_trend（旧）', rho: '0.44', tag: 'warn', tagText: '中度' },
]

const preprocess = [
  '去极值 Winsorize（1% / 99% 分位截断）',
  '横截面秩排名（消除量纲与离群影响）',
  '行业中性化（消除行业系统性偏差）',
  '市值中性化（规避大小盘风格暴露）',
]

export default function FactorFactory() {
  const [step, setStep] = useState(0)
  const [tplIdx, setTplIdx] = useState(0)
  const [win, setWin] = useState(20)
  const [checked, setChecked] = useState<boolean[]>([true, true, false, false])
  const [statusFilter, setStatusFilter] = useState('全部')

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

  return (
    <section className="page">
      {/* 五步向导 */}
      <div className="vstep">
        {WIZARD.map((s, i) => (
          <div key={s} className={`st${i === step ? ' on' : ''}`} onClick={() => setStep(i)}>
            <span className="no">STEP {i + 1}</span>
            {s}
          </div>
        ))}
      </div>

      <div className="grid" style={{ gridTemplateColumns: '270px 1fr 320px', marginBottom: 14 }}>
        {/* 模板库 */}
        <div className="card">
          <h3>
            因子模板库<span className="hint">点击载入编辑器</span>
          </h3>
          {templates.map((t, i) => (
            <div key={t.en} className={`tpl${i === tplIdx ? ' on' : ''}`} onClick={() => setTplIdx(i)}>
              <div>
                {t.zh} ({t.en})
                <div className="m">{t.m}</div>
              </div>
              <Tag type={t.tag as 'ok' | 'hold'} label={t.tagText} />
            </div>
          ))}
          <div className="sec-note">
            模板覆盖常见因子族，公式可直接改写为自定义表达式。ma_trend 二值化改造（→ 连续偏离度）同样走这条流程，改造结果进入版本对比。
          </div>
        </div>

        {/* 编辑器 */}
        <div className="card">
          <h3>
            因子编辑器 · {templates[tplIdx].zh} ({templates[tplIdx].en})
            <span className="hint">草稿 · 未保存 · v1.0</span>
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr .8fr 1fr', gap: 10, marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>中文名称</div>
              <input type="text" defaultValue={templates[tplIdx].zh} style={{ width: '100%' }} />
            </div>
            <div>
              <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>英文标识（唯一 · 创建后不可改）</div>
              <input
                type="text"
                defaultValue={templates[tplIdx].en}
                style={{ width: '100%', color: 'var(--txt2)', fontFamily: 'var(--mono)' }}
              />
            </div>
            <div>
              <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>分类</div>
              <select style={{ width: '100%' }}>
                <option>动量</option>
                <option>趋势</option>
                <option>价值</option>
                <option>质量</option>
                <option>风险</option>
              </select>
            </div>
            <div>
              <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>方向</div>
              <select style={{ width: '100%' }}>
                <option>越大越好</option>
                <option>越小越好</option>
              </select>
            </div>
          </div>
          <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 5 }}>
            因子表达式（前复权日线，仅可引用 T 日及之前数据，防未来函数由引擎强制）
          </div>
          <div className="codebox">
            <span className="cm"># 20日动量：近20日累计涨幅（横截面比较用）</span>
            {'\n'}momentum_20 = close_adj / close_adj.<span className="fn">shift</span>(
            <b style={{ color: '#E9A23B' }}>{win}</b>) - 1
          </div>
          <div style={{ display: 'flex', gap: 16, margin: '12px 0 2px' }}>
            <div style={{ flex: 1.4 }}>
              <div style={{ fontSize: 13, color: 'var(--txt3)' }}>
                回看窗口 <b className="num" style={{ color: '#A8C0FF' }}>{win}</b> 日
              </div>
              <input type="range" min={5} max={120} value={win} onChange={e => setWin(+e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, color: 'var(--txt3)' }}>数据源</div>
              <select style={{ width: '100%' }}>
                <option>daily_price · 前复权</option>
                <option>daily_valuation</option>
                <option>financial_indicator</option>
              </select>
            </div>
          </div>
          <div style={{ fontSize: 13, color: 'var(--txt3)', margin: '10px 0 2px' }}>横截面预处理</div>
          {preprocess.map((p, i) => (
            <div className="crow" key={p} onClick={() => setChecked(prev => prev.map((c, j) => (j === i ? !c : c)))}>
              <button className={`cbox${checked[i] ? ' on' : ''}`}>✓</button>
              {p}
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button className="btn">保存草稿</button>
            <button className="btn">语法校验</button>
            <button className="btn">另存为新版本</button>
            <button className="btn pri">发起试算（约 2 分钟）</button>
          </div>
        </div>

        {/* 试算结果 */}
        <div className="card">
          <h3>
            试算结果<span className="hint">2021-01 ~ 2026-08</span>
          </h3>
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
            <Kpi lb="RankIC 均值" v="0.056" vStyle={{ fontSize: 20 }} d="达标 > 0.03" dClass="up" style={{ border: 0, background: 'var(--panel2)' }} />
            <Kpi lb="ICIR" v="0.61" vStyle={{ fontSize: 20 }} d="达标 > 0.3" dClass="up" style={{ border: 0, background: 'var(--panel2)' }} />
          </div>
          <EChart option={ficOption} height={150} />
          <div style={{ fontSize: 13, color: 'var(--txt3)', margin: '10px 0 4px' }}>
            与现有因子相关性（|ρ|&gt;0.6 建议替换而非叠加）
          </div>
          <table style={{ fontSize: 14 }}>
            <tbody>
              {corrRows.map(r => (
                <tr key={r.name}>
                  <td style={{ color: 'var(--txt2)' }}>{r.name}</td>
                  <td className="r num">{r.rho}</td>
                  <td className="r">
                    <Tag type={r.tag as 'ok' | 'warn'} label={r.tagText} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div
            style={{
              marginTop: 12,
              fontSize: 13,
              color: 'var(--ok)',
              background: 'rgba(47,191,113,.08)',
              border: '1px solid rgba(47,191,113,.25)',
              borderRadius: 8,
              padding: '8px 10px',
              lineHeight: 1.7,
            }}
          >
            检验通过：IC 与 ICIR 均达标，且与现有因子低冗余 → 可进入版本发布
          </div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        {/* 版本管理 */}
        <div className="card">
          <h3>
            因子版本管理
            <span className="hint">每次修改都是新版本 · 可回滚 · 可对比 · {statusSeg}</span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>因子</th>
                <th>版本</th>
                <th>变更内容</th>
                <th className="r">IC 变化</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {versions
                .filter(v => statusFilter === '全部' || v.statusText === statusFilter)
                .map(v => (
                  <tr key={v.en}>
                    <td>
                      <b>{v.zh}</b> <span className="nm-en">{v.en}</span>
                    </td>
                    <td className="num">{v.ver}</td>
                    <td style={{ fontSize: 13.5, color: 'var(--txt2)' }}>{v.change}</td>
                    <td className="r num up">{v.ic}</td>
                    <td>
                      <Tag type={v.status as 'ok' | 'warn' | 'hold'} label={v.statusText} />
                    </td>
                    <td style={{ fontSize: 13.5 }}>
                      {v.ops.map(([label, color], i) => (
                        <a key={label} style={{ color, cursor: 'pointer', marginRight: 8 }}>
                          {label}
                        </a>
                      ))}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          <div className="sec-note">
            状态流转：<b>草稿</b>（可自由编辑）→ <b>试算中</b>（锁定公式，跑 IC/分层）→ <b>检验中</b>（人工复核）→{' '}
            <b style={{ color: 'var(--ok)' }}>已上线（可被策略引用 · 正式使用）</b> → 已停用。规则：①
            新版本须通过 IC/ICIR + 分层单调性双检验，并与旧版本同期对比；② 已上线且被运行中策略引用的因子不可直接停用，需先在策略工厂解除引用；③
            上线后旧版本保留 90 天供回滚。
          </div>
        </div>

        {/* 参数寻优 */}
        <div className="card">
          <h3>
            参数寻优 · momentum 窗口 × 持有期<span className="hint">RankIC 热力图 · 2019-2026</span>
          </h3>
          <EChart option={gridOption} height={250} />
          <div className="sec-note">
            解读：20~40 日窗口均有稳定 IC，说明因子对参数不敏感（好信号）；若只有孤立一格发亮，大概率过拟合，不建议采用该参数。
          </div>
        </div>
      </div>
    </section>
  )
}
