import { useMemo } from 'react'
import EChart from '../../components/EChart'
import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import Tag from '../../components/Tag'
import { mapAction, marketApi, signalsApi, strategyApi, tradeApi, type IndexNavData } from '../../api'
import { useApi } from '../../hooks/useApi'
import { lineOpt } from '../../mock/chartOpt'
import { steps } from '../../mock/data'
import { fmtMoney, fmtRatioPct } from '../../lib/format'
import { fmtName } from '../../lib/names'

const G1_HINT = '待 G1 后端补齐因子分项/行情'
const G5_HINT = '待 G5 后端补齐数据健康检查'

export default function Dash() {
  const account = useApi(() => tradeApi.getAccount(), [])
  const nav = useApi(() => tradeApi.getAccountNav(), [])
  const positions = useApi(() => tradeApi.getPositions(), [])
  const buySig = useApi(() => signalsApi.getSignals({ action: 'BUY', page_size: 1 }), [])
  const sellSig = useApi(() => signalsApi.getSignals({ action: 'SELL', page_size: 1 }), [])
  const sigList = useApi(() => signalsApi.getSignals({ page_size: 8 }), [])
  const strategies = useApi(() => strategyApi.getStrategies(), [])
  // name → zh_name 映射（信号流头只带英文 strategy，展示补中文）
  const zhBy = new Map((strategies.data?.items ?? []).map(s => [s.name, s.zh_name]))

  const navItems = nav.data?.items ?? []
  const start = navItems.length ? navItems[0].trade_date : undefined
  const end = navItems.length ? navItems[navItems.length - 1].trade_date : undefined
  // 指数净值对齐账户净值窗口（start/end 就绪后才请求）：沪深300 + 上证指数（功能建议 ①）
  const idx = useApi<IndexNavData | null>(
    () =>
      start && end
        ? marketApi.getIndexNav('000300', { start, end })
        : Promise.resolve(null),
    [start, end],
  )
  const idxSz = useApi<IndexNavData | null>(
    () =>
      start && end
        ? marketApi.getIndexNav('000001', { start, end })
        : Promise.resolve(null),
    [start, end],
  )

  const navOption = useMemo(() => {
    const idxItems = idx.data?.items ?? []
    const szItems = idxSz.data?.items ?? []
    const dates = [
      ...new Set([
        ...navItems.map(i => i.trade_date),
        ...idxItems.map(i => i.trade_date),
        ...szItems.map(i => i.trade_date),
      ]),
    ].sort()
    const acctMap = new Map(navItems.map(i => [i.trade_date, i.nav]))
    const idxMap = new Map(idxItems.map(i => [i.trade_date, i.nav]))
    const szMap = new Map(szItems.map(i => [i.trade_date, i.nav]))
    return lineOpt(
      {
        dates,
        series: [
          { name: '策略净值', data: dates.map(d => acctMap.get(d) ?? null) },
          { name: '沪深300', data: dates.map(d => idxMap.get(d) ?? null) },
          { name: '上证指数', data: dates.map(d => szMap.get(d) ?? null) },
        ],
      },
      ['#4C7DFF', '#8B93A7', '#F0B45A'],
      true,
    )
  }, [nav.data, idx.data, idxSz.data])

  // 超额收益（年化近似）：两端点年化差（252 交易日）
  const excess = useMemo(() => {
    const a = navItems
    const i = idx.data?.items ?? []
    if (a.length < 2 || i.length < 2) return null
    const ann = (items: { nav: number }[]) => Math.pow(items[items.length - 1].nav / items[0].nav, 252 / items.length) - 1
    return ann(a) - ann(i)
  }, [nav.data, idx.data])

  const total = account.data?.total_asset
  const lastNav = navItems[navItems.length - 1]
  const todayProfit = lastNav ? lastNav.daily_return * lastNav.total_asset : null
  const posRows = positions.data?.items ?? []
  const frozen = posRows.reduce((s, p) => s + (p.quantity - p.available_qty), 0)
  const sigDate = sigList.data?.trade_date

  return (
    <section className="page">
      <div style={{ fontSize: 12, color: 'var(--txt3)', marginBottom: 6 }}>
        每日流水线 · 静态示意（真实任务状态见运维页）
      </div>
      <div className="pipe">
        {steps.map(s => (
          <div key={s[1]} className="step done">
            <div className="t">{s[0]}</div>
            <div className="n">{s[1]}</div>
            <div className="s">✓ {s[2]}</div>
          </div>
        ))}
      </div>

      <div className="grid kpi-grid" style={{ marginBottom: 14 }}>
        <Kpi
          lb="总资产"
          v={total !== undefined ? fmtMoney(total) : '—'}
          d={account.data ? `${fmtRatioPct(account.data.profit_rate)} 累计` : '—'}
          dClass={account.data && account.data.profit_rate >= 0 ? 'up' : 'down'}
        />
        <Kpi
          lb="今日盈亏"
          v={todayProfit !== null ? fmtMoney(todayProfit) : '—'}
          d={lastNav ? fmtRatioPct(lastNav.daily_return) : '—'}
          vClass={todayProfit !== null && todayProfit < 0 ? 'down' : undefined}
          dClass={lastNav && lastNav.daily_return < 0 ? 'down' : undefined}
        />
        <Kpi
          lb="超额收益(年化)"
          v={excess !== null ? fmtRatioPct(excess) : '—'}
          d="vs 沪深300"
          vClass={excess !== null && excess < 0 ? 'down' : undefined}
        />
        <Kpi
          lb="最大回撤"
          v={account.data ? fmtRatioPct(account.data.max_drawdown) : '—'}
          d={start && end ? `${start} ~ ${end}` : '—'}
        />
        <Kpi lb="夏普比率" v="—" d="待后端直供" vClass="muted" />
        <Kpi
          lb="今日信号"
          v={
            buySig.data?.total !== undefined && sellSig.data?.total !== undefined ? (
              <>
                {buySig.data.total}{' '}
                <span style={{ fontSize: 14, color: 'var(--txt3)' }}>买</span> / {sellSig.data.total}{' '}
                <span style={{ fontSize: 14, color: 'var(--txt3)' }}>卖</span>
              </>
            ) : (
              '—'
            )
          }
          d={sigDate ? `${sigDate} 已生成` : '加载中…'}
        />
      </div>

      <div className="grid" style={{ gridTemplateColumns: '2fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            策略净值 vs 指数
            <span className="hint">
              <span className="seg">
                <button className="on">MAX</button>
              </span>
            </span>
          </h3>
          {nav.error ? (
            <Notice text={nav.error} onRetry={nav.reload} retrying={nav.loading} />
          ) : nav.loading && !nav.data ? (
            <div className="empty">净值加载中…</div>
          ) : (
            <EChart option={navOption} height={300} />
          )}
        </div>
        <div className="card">
          <h3>
            持仓快照
            <span className="hint">
              {posRows.length} 只 · 满仓率{' '}
              {total ? `${(((account.data?.market_value ?? 0) / total) * 100).toFixed(1)}%` : '—'}
            </span>
          </h3>
          {positions.error ? (
            <Notice text={positions.error} onRetry={positions.reload} retrying={positions.loading} />
          ) : positions.loading && !positions.data ? (
            <div className="empty">持仓加载中…</div>
          ) : posRows.length === 0 ? (
            <div className="empty">当前空仓</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>证券</th>
                  <th className="r">数量</th>
                  <th className="r">成本</th>
                  <th className="r">现价</th>
                  <th className="r">盈亏</th>
                </tr>
              </thead>
              <tbody>
                {posRows.map(p => (
                  <tr key={p.code}>
                    <td>
                      {p.name}
                      <div style={{ color: 'var(--txt3)', fontSize: 13 }} className="num">
                        {p.code}
                      </div>
                    </td>
                    <td className="r num">{p.quantity}</td>
                    <td className="r num">{fmtMoney(p.cost_price)}</td>
                    <td className="r num">{fmtMoney(p.current_price)}</td>
                    <td className={`r num ${p.profit_rate >= 0 ? 'up' : 'down'}`}>
                      {fmtRatioPct(p.profit_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, color: 'var(--txt2)', padding: '3px 0' }}>
              <span>可用资金</span>
              <b className="num">{fmtMoney(account.data?.cash)}</b>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, color: 'var(--txt2)', padding: '3px 0' }}>
              <span>持仓市值</span>
              <b className="num">{fmtMoney(account.data?.market_value)}</b>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, color: 'var(--txt2)', padding: '3px 0' }}>
              <span>T+1 冻结</span>
              <b className="num">{frozen} 股</b>
            </div>
          </div>
          {posRows.length === 0 && (
            <div
              style={{
                marginTop: 12,
                fontSize: 13,
                color: 'var(--warn)',
                background: 'rgba(233,162,59,.07)',
                border: '1px solid rgba(233,162,59,.2)',
                borderRadius: 8,
                padding: '8px 10px',
              }}
            >
              当前空仓：等待买入缓冲区内出现新信号
            </div>
          )}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            今日信号流
            <span className="hint">
              {sigList.data ? `${fmtName(zhBy.get(sigList.data.strategy), sigList.data.strategy)} · ${sigDate}` : '加载中…'}
            </span>
          </h3>
          {sigList.error ? (
            <Notice text={sigList.error} onRetry={sigList.reload} retrying={sigList.loading} />
          ) : sigList.loading && !sigList.data ? (
            <div className="empty">信号加载中…</div>
          ) : (sigList.data?.items ?? []).length === 0 ? (
            <div className="empty">当日暂无信号</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>动作</th>
                  <th>证券</th>
                  <th className="r">综合分</th>
                  <th className="r">排名</th>
                  <th>理由</th>
                </tr>
              </thead>
              <tbody>
                {(sigList.data?.items ?? []).map((s, i) => (
                  <tr key={s.code + i}>
                    <td>
                      <Tag type={mapAction(s.action)!} />
                    </td>
                    <td>
                      {s.name} <span className="num" style={{ color: 'var(--txt3)' }}>{s.code}</span>
                    </td>
                    <td className="r num">{s.score.toFixed(1)}</td>
                    <td className="r num" title={s.rank === undefined ? G1_HINT : undefined}>
                      {s.rank !== undefined ? s.rank : <span className="muted">—</span>}
                    </td>
                    <td style={{ color: 'var(--txt2)', fontSize: 13.5 }}>{s.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h3>
            数据健康检查
            <span className="hint">{G5_HINT}</span>
          </h3>
          <div className="empty" style={{ padding: '52px 0' }}>
            数据健康检查接口待 G5 后端补齐（每日 7 项检查已产出，见运维页任务记录）
          </div>
        </div>
      </div>
    </section>
  )
}
