import EChart from '../../components/EChart'
import Tag from '../../components/Tag'
import { lineOpt } from '../../mock/chartOpt'
import { dates, navSeries } from '../../mock/random'

const d60 = dates(60)
const liveOption = lineOpt(
  {
    dates: d60,
    series: [
      { name: '模拟盘净值', data: navSeries(60, 100000, 0.006, 0.0012).map(v => Math.round(v)), w: 2 },
      { name: '实盘复演净值(影子)', data: navSeries(60, 100000, 0.006, 0.0007).map(v => Math.round(v)) },
    ],
  },
  ['#4C7DFF', '#E9A23B'],
  false,
)

const modes = [
  { cur: true, nm: '已关闭', ds: '仅模拟盘运行，无任何实盘行为' },
  { cur: false, nm: '影子模式', ds: '信号同步发送券商但不下单，只记录可达成交价，校准滑点模型' },
  { cur: false, nm: '小仓试运行', ds: '≤20% 资金 · 每笔人工确认 · 观察 2-3 个月' },
  { cur: false, nm: '正常运行', ds: '全自动执行 · 风控熔断守护 · 审计全程留痕' },
]

const brokers = [
  { name: 'QMT mini', form: '本地客户端 + Python API', tag: 'ok', tagText: '推荐' },
  { name: '迅投 XT', form: '同 QMT 内核 · 定制更强', tag: 'hold', tagText: '备选' },
  { name: 'easytrader', form: '模拟键鼠 · 稳定性弱', tag: 'warn', tagText: '不推荐' },
]

const riskRules = [
  ['单日亏损 > 3%', '暂停开新仓 · 次日恢复'],
  ['区间回撤 > 8%', '全仓熔断 · 需人工解锁'],
  ['单票 / 单行业', '≤ 20% / ≤ 35%'],
  ['单笔委托 > 总资产 15%', '二次确认'],
  ['连续 3 笔废单/拒单', '停止交易 · 推飞书告警'],
  ['每日 14:55', '持仓集中度检查'],
]

const confirmations = [
  ['切换实盘模式', '2FA + 飞书二次通知'],
  ['手动下单 / 撤单', '弹窗确认 + 审计'],
  ['修改风控参数', '2FA'],
  ['一键停机', '无需确认 · 立即执行'],
]

export default function Live() {
  return (
    <section className="page">
      <div className="banner">
        <b>Phase 3 概念设计 · 本页未实现。</b>
        当前系统不连接任何券商、无真实资金。启用实盘的前置条件：① 因子有效性验证完成 ② 回测 T+1 成交修正 ③
        模拟盘稳定运行 ≥3 个月。下方控件均为设计稿预览，处于锁定状态。
      </div>

      <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 6 }}>实盘模式（单向递进 · 降级需人工确认）</div>
      <div className="mode" style={{ marginBottom: 14 }}>
        {modes.map(m => (
          <div key={m.nm} className={`m${m.cur ? ' cur' : ' off'}`}>
            <Tag type={m.cur ? 'ok' : 'hold'} label={m.cur ? '当前' : '锁定'} />
            <div className="nm">{m.nm}</div>
            <div className="ds">{m.ds}</div>
          </div>
        ))}
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1.1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            券商通道<span className="hint">未配置</span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>通道</th>
                <th>形态</th>
                <th className="r">评估</th>
              </tr>
            </thead>
            <tbody>
              {brokers.map(b => (
                <tr key={b.name}>
                  <td>{b.name}</td>
                  <td style={{ color: 'var(--txt2)', fontSize: 13.5 }}>{b.form}</td>
                  <td className="r">
                    <Tag type={b.tag as 'ok' | 'hold' | 'warn'} label={b.tagText} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="sec-note">
            选型原则：实盘执行层独立进程部署，崩溃不影响研究链路；券商 API 细节在影子模式启用前再对齐，避免现在过度设计。
          </div>
        </div>

        <div className="card">
          <h3>
            风控熔断<span className="hint">实时守护 · 优先级高于策略信号</span>
          </h3>
          <table style={{ fontSize: 14 }}>
            <tbody>
              {riskRules.map(r => (
                <tr key={r[0]}>
                  <td>{r[0]}</td>
                  <td className="r num" style={{ color: 'var(--txt2)' }}>
                    {r[1]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="kill" disabled>
            ⏻ 一键停机（实盘启用后生效）
          </button>
        </div>

        <div className="card">
          <h3>
            实盘操作确认<span className="hint">敏感动作多重防线</span>
          </h3>
          <table style={{ fontSize: 14 }}>
            <thead>
              <tr>
                <th>操作</th>
                <th>确认方式</th>
              </tr>
            </thead>
            <tbody>
              {confirmations.map(c => (
                <tr key={c[0]}>
                  <td>{c[0]}</td>
                  <td style={{ color: c[1].includes('无需确认') ? 'var(--up)' : 'var(--txt2)' }}>{c[1]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="sec-note">设计原则：开仓要慢（多重确认），停机要快（零确认）。宁可错过，不可失控。</div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            实盘 vs 模拟 偏差监控<span className="hint">影子模式核心产出 · 示意数据</span>
          </h3>
          <EChart option={liveOption} height={250} />
        </div>
        <div className="card">
          <h3>
            实盘审计日志<span className="hint">谁 · 何时 · 何地 · 做了什么</span>
          </h3>
          <div className="empty" style={{ padding: '44px 0', lineHeight: 2 }}>
            尚无实盘审计记录
            <br />
            <span style={{ fontSize: 13, color: 'var(--txt3)' }}>
              影子模式启用后，所有敏感操作自动记入：
              <br />
              时间 · 账户 · 动作 · 来源 IP · 结果
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}
