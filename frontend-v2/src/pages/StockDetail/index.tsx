import { useMemo } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import EChart from '../../components/EChart'
import Notice from '../../components/Notice'
import Tag from '../../components/Tag'
import { mapAction, mapUniverse, stocksApi, tradeApi } from '../../api'
import { useApi } from '../../hooks/useApi'
import { klineOpt, radarOpt } from '../../mock/chartOpt'
import { fmtChg, fmtPct } from '../../lib/format'

const G3_HINT = '待 G3 后端补齐因子得分'

export default function StockDetail() {
  const { code = '000792' } = useParams()
  const navigate = useNavigate()

  const detail = useApi(() => stocksApi.getStockDetail(code), [code])
  const kline = useApi(() => stocksApi.getKline(code, { period: 'day', adjust: 'qfq' }), [code])
  const fin = useApi(() => stocksApi.getFinancial(code, 10), [code])
  const sighist = useApi(() => stocksApi.getSignalHistory(code, 10), [code])
  // 持仓命中判定（辅助；失败静默 → 视为未持仓）
  const positions = useApi(() => tradeApi.getPositions(), [])

  const { klineOption, chg, lastClose, lastDate } = useMemo(() => {
    const items = kline.data?.items ?? []
    const kd = items.map(i => i.date)
    const d = items.map(i => [i.open, i.close, i.low, i.high]) // candlestick [open, close, low, high]
    const v = items.map(i => i.volume)
    const n = items.length
    const lastClose = n ? items[n - 1].close : null
    const prevClose = n >= 2 ? items[n - 2].close : null
    const chg = lastClose && prevClose ? ((lastClose - prevClose) / prevClose) * 100 : null
    return {
      klineOption: klineOpt(kd, d, v),
      chg,
      lastClose,
      lastDate: n ? items[n - 1].date : null,
    }
  }, [kline.data])

  const fs = detail.data?.factor_score
  const hasRadar =
    !!fs && [fs.trend, fs.value, fs.quality, fs.risk].every(v => v !== undefined && v !== null)
  const isHeld = (positions.data?.items ?? []).some(p => p.code === code)
  const board = detail.data ? mapUniverse(detail.data.universe) : undefined

  if (detail.loading && !detail.data) {
    return (
      <section className="page">
        <div className="empty">加载中…</div>
      </section>
    )
  }
  if (detail.error) {
    return (
      <section className="page">
        <Notice text={detail.error} onRetry={detail.reload} retrying={detail.loading} />
      </section>
    )
  }
  if (!detail.data) {
    return (
      <section className="page">
        <div className="empty">未找到股票 {code}</div>
      </section>
    )
  }
  const stock = detail.data

  return (
    <section className="page">
      {/* 头部 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <button className="btn" onClick={() => navigate('/stocks')}>
          ← 返回股票池
        </button>
        <span style={{ fontSize: 18, fontWeight: 600 }}>
          {stock.name}{' '}
          <span className="num" style={{ color: 'var(--txt3)', fontSize: 15 }}>
            {stock.code}
          </span>
        </span>
        {board && <span className={`ptag${board === 'zz' ? ' zz' : ''}`}>{board === 'hs' ? '沪深300' : '中证500'}</span>}
        {isHeld && <Tag type="buy" label="当前持仓" />}
        <span className="num" style={{ marginLeft: 'auto', fontSize: 16 }}>
          {lastClose !== null ? lastClose.toFixed(2) : '--'}{' '}
          <b className={chg !== null && chg >= 0 ? 'up' : 'down'}>{fmtChg(chg)}</b>{' '}
          <span style={{ color: 'var(--txt3)', fontSize: 14 }}>{lastDate ?? ''} 收盘</span>
        </span>
      </div>

      {/* K线 + 因子得分 */}
      <div className="grid" style={{ gridTemplateColumns: '2fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            日K · 前复权
            <span className="hint">
              <span className="seg">
                <button className="on">日线</button>
                <button>周线</button>
                <button>月线</button>
              </span>
            </span>
          </h3>
          {kline.error ? (
            <Notice text={kline.error} onRetry={kline.reload} retrying={kline.loading} />
          ) : kline.loading && !kline.data ? (
            <div className="empty">K线加载中…</div>
          ) : (
            <EChart option={klineOption} height={320} />
          )}
        </div>
        <div className="card">
          <h3>
            因子得分
            <span className="hint">{hasRadar && fs ? (stock.valuation?.trade_date ?? '') : '待 G3'}</span>
          </h3>
          {hasRadar && fs ? (
            <EChart option={radarOpt([fs.trend!, fs.value!, fs.quality!, fs.risk!])} height={230} />
          ) : (
            <div className="empty" style={{ padding: '52px 0' }}>
              因子雷达待 G3 后端补齐
            </div>
          )}
          <table style={{ fontSize: 14 }}>
            <tbody>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>综合分</td>
                <td className="r num">
                  {fs?.score !== undefined ? (
                    <b>{fs.score.toFixed(1)}</b>
                  ) : (
                    <span className="muted" title={G3_HINT}>
                      —
                    </span>
                  )}{' '}
                  / 100
                </td>
              </tr>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>横截面排名</td>
                <td className="r num">
                  {fs?.rank !== undefined ? (
                    fs.rank
                  ) : (
                    <span className="muted" title={G3_HINT}>
                      —
                    </span>
                  )}
                </td>
              </tr>
              <tr>
                <td style={{ color: 'var(--txt2)' }}>今日信号</td>
                <td className="r">
                  {fs?.signal ? <Tag type={mapAction(fs.signal)!} /> : <span className="muted">—</span>}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* 财务 + 信号历史 */}
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            财务指标<span className="hint">按公告日对齐 · 防未来函数</span>
          </h3>
          {fin.error ? (
            <Notice text={fin.error} onRetry={fin.reload} retrying={fin.loading} />
          ) : fin.loading && !fin.data ? (
            <div className="empty">财务加载中…</div>
          ) : (fin.data?.items ?? []).length === 0 ? (
            <div className="empty">暂无财务数据</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>报告期</th>
                  <th className="r">ROE</th>
                  <th className="r">净利同比</th>
                  <th className="r">毛利率</th>
                  <th className="r">资产负债率</th>
                </tr>
              </thead>
              <tbody>
                {(fin.data?.items ?? []).map(f => (
                  <tr key={f.report_date}>
                    <td className="num">{f.report_date}</td>
                    <td className="r num">{fmtPct(f.roe)}</td>
                    <td className={`r num ${f.profit_growth >= 0 ? 'up' : 'down'}`}>{fmtPct(f.profit_growth)}</td>
                    <td className="r num">{fmtPct(f.gross_margin)}</td>
                    <td className="r num">{fmtPct(f.debt_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h3>
            信号历史<span className="hint">近 10 个交易日</span>
          </h3>
          {sighist.error ? (
            <Notice text={sighist.error} onRetry={sighist.reload} retrying={sighist.loading} />
          ) : sighist.loading && !sighist.data ? (
            <div className="empty">信号加载中…</div>
          ) : (sighist.data?.items ?? []).length === 0 ? (
            <div className="empty">暂无信号记录</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>动作</th>
                  <th className="r">综合分</th>
                  <th className="r">排名</th>
                </tr>
              </thead>
              <tbody>
                {(sighist.data?.items ?? []).map((h, i) => (
                  <tr key={i} title={h.reason}>
                    <td className="num">{h.trade_date}</td>
                    <td>
                      <Tag type={mapAction(h.action)!} />
                    </td>
                    <td className="r num">{h.score.toFixed(1)}</td>
                    <td className="r num">
                      {h.rank !== undefined ? (
                        h.rank
                      ) : (
                        <span className="muted" title={G3_HINT}>
                          —
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  )
}
