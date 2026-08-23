import { useMemo } from 'react'
import EChart from '../../components/EChart'
import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import Tag from '../../components/Tag'
import { tradeApi, type OrderStatus } from '../../api'
import { useApi } from '../../hooks/useApi'
import { lineOpt } from '../../mock/chartOpt'
import { fmtMoney } from '../../lib/format'

const rules = [
  ['成交价', '收盘价 ± 0.1% 滑点'],
  ['交易制度', 'T+1 · 整手 100 股'],
  ['佣金', '万 2.5 · 最低 ¥5'],
  ['印花税', '万 5 · 仅卖出'],
  ['涨跌停', '主板10% / 创科20%'],
]

const ORDER_STATUS: Record<OrderStatus, ['ok' | 'warn' | 'hold', string]> = {
  PENDING: ['hold', '待成交'],
  FILLED: ['ok', '已成'],
  REJECTED: ['warn', '已拒'],
  CANCELLED: ['hold', '已撤'],
}

const G4_HINT = '待 G4 后端补齐证券名称'

export default function Trade() {
  const account = useApi(() => tradeApi.getAccount(), [])
  const nav = useApi(() => tradeApi.getAccountNav(), [])
  const positions = useApi(() => tradeApi.getPositions(), [])
  const orders = useApi(() => tradeApi.getOrders({ page: 1, page_size: 20 }), [])
  const trades = useApi(() => tradeApi.getTrades({ page: 1, page_size: 100 }), [])

  const navOption = useMemo(() => {
    const items = nav.data?.items ?? []
    return lineOpt(
      {
        dates: items.map(i => i.trade_date),
        series: [{ name: '模拟账户', data: items.map(i => i.total_asset) }],
      },
      ['#6C5CE7'],
      true,
    )
  }, [nav.data])

  const tradeRows = trades.data?.items ?? []
  const commission = tradeRows.reduce((s, t) => s + t.commission, 0)
  const tax = tradeRows.reduce((s, t) => s + t.tax, 0)
  const buyCount = tradeRows.filter(t => t.direction === 'BUY').length
  const sellCount = tradeRows.filter(t => t.direction === 'SELL').length

  const total = account.data?.total_asset
  const cash = account.data?.cash
  const mv = account.data?.market_value
  const profit = account.data?.profit
  const init = account.data?.initial_cash
  const posCount = positions.data?.items.length

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: 'repeat(5,1fr)', marginBottom: 14 }}>
        <Kpi
          lb="总资产"
          v={total !== undefined ? fmtMoney(total) : '—'}
          d={profit !== undefined && init !== undefined ? `${profit >= 0 ? '+' : ''}${fmtMoney(profit)} / 初始 ${fmtMoney(init)}` : '—'}
          dClass={profit !== undefined && profit >= 0 ? 'up' : 'down'}
        />
        <Kpi
          lb="可用资金"
          v={cash !== undefined ? fmtMoney(cash) : '—'}
          d={cash !== undefined && total ? `${((cash / total) * 100).toFixed(1)}%` : '—'}
        />
        <Kpi
          lb="持仓市值"
          v={mv !== undefined ? fmtMoney(mv) : '—'}
          d={mv !== undefined && total ? `${posCount ?? 0} 只 · ${((mv / total) * 100).toFixed(1)}%` : '—'}
        />
        <Kpi
          lb="累计手续费"
          v={trades.data ? fmtMoney(commission + tax) : '—'}
          d={trades.data ? `佣金 ¥${commission.toFixed(2)} + 税 ¥${tax.toFixed(2)}` : '—'}
        />
        <Kpi lb="成交笔数" v={trades.data?.total ?? '—'} d={trades.data ? `买 ${buyCount} / 卖 ${sellCount}` : '—'} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 2fr', marginBottom: 14 }}>
        <div className="card">
          <h3>模拟盘规则</h3>
          <table style={{ fontSize: 14 }}>
            <tbody>
              {rules.map(r => (
                <tr key={r[0]}>
                  <td style={{ color: 'var(--txt2)' }}>{r[0]}</td>
                  <td className="r num">{r[1]}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
            当前按 T 日收盘价撮合，存在乐观偏差；切换 T+1 成交假设见回测中心
          </div>
        </div>
        <div className="card">
          <h3>
            账户净值<span className="hint">模拟盘启动以来</span>
          </h3>
          {nav.error ? (
            <Notice text={nav.error} onRetry={nav.reload} retrying={nav.loading} />
          ) : nav.loading && !nav.data ? (
            <div className="empty">净值加载中…</div>
          ) : (
            <EChart option={navOption} height={240} />
          )}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            持仓明细<span className="hint">含 T+1 冻结</span>
          </h3>
          {positions.error ? (
            <Notice text={positions.error} onRetry={positions.reload} retrying={positions.loading} />
          ) : positions.loading && !positions.data ? (
            <div className="empty">持仓加载中…</div>
          ) : (positions.data?.items ?? []).length === 0 ? (
            <div className="empty">暂无持仓</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>证券</th>
                  <th className="r">持仓</th>
                  <th className="r">可卖</th>
                  <th className="r">成本价</th>
                  <th className="r">现价</th>
                  <th className="r">市值</th>
                  <th className="r">浮动盈亏</th>
                </tr>
              </thead>
              <tbody>
                {(positions.data?.items ?? []).map(p => (
                  <tr key={p.code}>
                    <td>
                      {p.name} <span className="num" style={{ color: 'var(--txt3)' }}>{p.code}</span>
                    </td>
                    <td className="r num">{p.quantity}</td>
                    <td className="r num" style={{ color: p.available_qty < p.quantity ? 'var(--txt3)' : undefined }}>
                      {p.available_qty}
                    </td>
                    <td className="r num">{fmtMoney(p.cost_price)}</td>
                    <td className="r num">{fmtMoney(p.current_price)}</td>
                    <td className="r num">{fmtMoney(p.market_value)}</td>
                    <td className={`r num ${p.profit >= 0 ? 'up' : 'down'}`}>
                      {fmtMoney(p.profit)}
                      {p.available_qty < p.quantity && (
                        <span style={{ color: 'var(--txt3)', fontSize: 12 }}> (冻结)</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h3>委托记录</h3>
          {orders.error ? (
            <Notice text={orders.error} onRetry={orders.reload} retrying={orders.loading} />
          ) : orders.loading && !orders.data ? (
            <div className="empty">委托加载中…</div>
          ) : (orders.data?.items ?? []).length === 0 ? (
            <div className="empty">暂无委托记录</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>方向</th>
                  <th>证券</th>
                  <th className="r">委托</th>
                  <th className="r">成交</th>
                  <th className="r">成交价</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {(orders.data?.items ?? []).map(o => {
                  const [tagType, tagText] = ORDER_STATUS[o.status] ?? ['hold', o.status]
                  return (
                    <tr key={o.order_id}>
                      <td className="num">{o.created_at}</td>
                      <td>
                        <Tag type={o.direction === 'BUY' ? 'buy' : 'sell'} label={o.direction === 'BUY' ? '买' : '卖'} />
                      </td>
                      <td title={o.name === undefined ? G4_HINT : undefined}>{o.name ?? o.code}</td>
                      <td className="r num">{o.quantity}</td>
                      <td className="r num">{o.filled_qty}</td>
                      <td className="r num">{o.avg_fill_price > 0 ? fmtMoney(o.avg_fill_price) : '—'}</td>
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
            成交明细见 KPI「累计手续费 / 成交笔数」汇总；委托状态映射 PENDING/FILLED/REJECTED/CANCELLED
          </div>
        </div>
      </div>
    </section>
  )
}
