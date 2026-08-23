import { useState } from 'react'
import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import Pager from '../../components/Pager'
import Seg from '../../components/Seg'
import Tag from '../../components/Tag'
import { mapAction, signalsApi, type SignalAction, type StrategyInfo } from '../../api'
import { useApi } from '../../hooks/useApi'

const SIG_PG = 10
const G1_HINT = '待 G1 后端补齐因子分项/行情'

const FILTER_OPTIONS = ['全部', '买入', '卖出', '持有']
const ACTION_MAP: Record<string, SignalAction | undefined> = {
  全部: undefined,
  买入: 'BUY',
  卖出: 'SELL',
  持有: 'HOLD',
}

// strategy.params → [中文名, 格式化]（未收录键原样展示）
const PARAM_RENDER: Record<string, [string, (v: unknown) => string]> = {
  top_n: ['目标持仓', v => `${Number(v)} 只`],
  max_position_pct: ['单票上限', v => `${(Number(v) * 100).toFixed(0)}%`],
}

function ParamRow({ k, v }: { k: string; v: unknown }) {
  const [label, fmt] = PARAM_RENDER[k] ?? [k, (x: unknown) => String(x)]
  return (
    <tr>
      <td style={{ color: 'var(--txt2)' }}>{label}</td>
      <td className="r num">{fmt(v)}</td>
    </tr>
  )
}

/** G1 缺口列：缺失时显示 — 并带 title 提示 */
function G1Cell({ v, cls }: { v?: number | null; cls?: string }) {
  return v === undefined || v === null || Number.isNaN(v) ? (
    <td className={`r num muted ${cls ?? ''}`} title={G1_HINT}>
      —
    </td>
  ) : (
    <td className={`r num ${cls ?? ''}`}>{v}</td>
  )
}

function StrategyCard({ strat }: { strat?: StrategyInfo }) {
  if (!strat) return <div className="card"><div className="empty">暂无策略配置</div></div>
  const weights = Object.entries(strat.factor_weights ?? {})
  return (
    <div className="card">
      <h3>策略 {strat.name}</h3>
      <table style={{ fontSize: 14 }}>
        <tbody>
          <tr>
            <td style={{ color: 'var(--txt2)' }}>状态</td>
            <td className="r">
              <Tag type={strat.status === 'active' ? 'ok' : 'hold'} label={strat.status === 'active' ? '运行中' : strat.status} />
            </td>
          </tr>
          {strat.description && (
            <tr>
              <td style={{ color: 'var(--txt2)' }}>描述</td>
              <td className="r" style={{ color: 'var(--txt2)', fontSize: 12.5 }}>
                {strat.description}
              </td>
            </tr>
          )}
          {Object.entries(strat.params ?? {}).map(([k, v]) => (
            <ParamRow key={k} k={k} v={v} />
          ))}
        </tbody>
      </table>
      {weights.length > 0 && (
        <>
          <div style={{ fontSize: 14, color: 'var(--txt2)', margin: '10px 0 4px' }}>因子权重</div>
          <table style={{ fontSize: 14 }}>
            <tbody>
              {weights.map(([name, w]) => (
                <tr key={name}>
                  <td className="num" style={{ color: 'var(--txt2)', fontSize: 13 }}>
                    {name}
                  </td>
                  <td className="r num">{(Number(w) * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

export default function Signal() {
  const [mode, setMode] = useState('全部')
  const [page, setPage] = useState(1)
  const action = ACTION_MAP[mode]

  const strategies = useApi(() => signalsApi.getStrategies(), [])
  const list = useApi(
    () => signalsApi.getSignals({ action, page, page_size: SIG_PG }),
    [action, page],
  )
  // KPI 计数（page_size=1 只取 total）
  const buy = useApi(() => signalsApi.getSignals({ action: 'BUY', page_size: 1 }), [])
  const sell = useApi(() => signalsApi.getSignals({ action: 'SELL', page_size: 1 }), [])
  const hold = useApi(() => signalsApi.getSignals({ action: 'HOLD', page_size: 1 }), [])

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 14 }}>
        <StrategyCard strat={strategies.data?.items?.[0]} />
        <Kpi lb="今日买入信号" v={buy.data?.total ?? '—'} vClass="up" d="当日 BUY" style={{ border: 0 }} />
        <Kpi lb="今日卖出信号" v={sell.data?.total ?? '—'} vClass="down" d="当日 SELL" style={{ border: 0 }} />
        <Kpi
          lb="持有 / 观察池"
          v={
            hold.data?.total !== undefined ? (
              <>
                {hold.data.total}{' '}
                <span style={{ fontSize: 15, color: 'var(--txt3)' }}>/ {hold.data.total}</span>
              </>
            ) : (
              '—'
            )
          }
          d={hold.data ? '当日 HOLD' : '加载中…'}
          style={{ border: 0 }}
        />
      </div>

      <div className="card">
        <h3>
          信号明细
          <span className="hint">
            {list.data?.trade_date ? (
              <span style={{ color: 'var(--txt3)' }}>
                交易日期 {list.data.trade_date} · {list.data.strategy}
              </span>
            ) : (
              <span style={{ color: 'var(--txt3)' }}>暂无信号</span>
            )}
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', marginLeft: 8 }}>
              <Seg
                options={FILTER_OPTIONS}
                value={mode}
                onChange={v => {
                  setMode(v)
                  setPage(1)
                }}
              />
            </span>
          </span>
        </h3>
        {list.error ? (
          <Notice text={list.error} onRetry={list.reload} retrying={list.loading} />
        ) : (
          <table>
            <thead>
              <tr>
                <th>动作</th>
                <th>排名</th>
                <th>代码</th>
                <th>名称</th>
                <th className="r">综合分</th>
                <th className="r">趋势</th>
                <th className="r">价值</th>
                <th className="r">质量</th>
                <th className="r">风险</th>
                <th className="r">PE(TTM)</th>
                <th className="r">20日涨幅</th>
              </tr>
            </thead>
            <tbody>
              {list.loading && !list.data ? (
                <tr>
                  <td colSpan={11}>
                    <div className="empty">加载中…</div>
                  </td>
                </tr>
              ) : (
                (list.data?.items ?? []).map((s, i) => (
                  <tr key={s.code + i} title={s.reason}>
                    <td>
                      <Tag type={mapAction(s.action)!} />
                    </td>
                    <G1Cell v={s.rank} />
                    <td className="num">{s.code}</td>
                    <td>{s.name}</td>
                    <td className="r num">
                      <b>{s.score.toFixed(1)}</b>
                    </td>
                    <G1Cell v={s.trend} />
                    <G1Cell v={s.value} />
                    <G1Cell v={s.quality} />
                    <G1Cell v={s.risk} />
                    <G1Cell v={s.pe} />
                    <G1Cell
                      v={s.chg20}
                      cls={s.chg20 !== undefined && s.chg20 >= 0 ? 'up' : 'down'}
                    />
                  </tr>
                ))
              )}
              {list.data && list.data.items.length === 0 && (
                <tr>
                  <td colSpan={11}>
                    <div className="empty">无匹配信号</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
        {list.data && <Pager total={list.data.total} page={page} size={SIG_PG} onChange={p => setPage(p)} />}
      </div>
    </section>
  )
}
