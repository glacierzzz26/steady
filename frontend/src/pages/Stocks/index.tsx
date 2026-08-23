import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import Pager from '../../components/Pager'
import Seg from '../../components/Seg'
import Tag from '../../components/Tag'
import { mapAction, mapUniverse, stocksApi, type StockListQuery, type StockPoolItem } from '../../api'
import { useApi } from '../../hooks/useApi'
import { fmtChg, fmtPct, fmtWanYi } from '../../lib/format'

const POOL_PG = 12

const BOARD_OPTIONS = ['全部', '沪深300', '中证500']
const UNIVERSE_MAP: Record<string, StockListQuery['universe'] | undefined> = {
  全部: undefined,
  沪深300: 'hs300',
  中证500: 'zz500',
}

// 后端排序白名单：code/name/list_date/market/industry（rank/chg/amt 待 G2 加入）
const SORT_OPTIONS = [
  ['code', '按代码'],
  ['name', '按名称'],
  ['list_date', '按上市日期'],
] as const

const SORT_TITLE: Record<string, string> = { code: '待 G2', name: '待 G2', list_date: '待 G2' }
// G2 字段未产出时列头的 title 提示
const G2_HINT = '待 G2 后端补齐评分/行情'

/** G2 缺口单元格：缺失时显示 — 并带 title 提示 */
function G2Cell({ v, hint = G2_HINT }: { v?: number | null; hint?: string }) {
  return v === undefined || v === null || Number.isNaN(v) ? (
    <td className="r num muted" title={hint}>
      —
    </td>
  ) : (
    <td className="r num">{v}</td>
  )
}

function BoardTag({ item }: { item: StockPoolItem }) {
  const board = mapUniverse(item.universe)
  if (!board) return <span className="num muted">—</span>
  return <span className={`ptag${board === 'zz' ? ' zz' : ''}`}>{board === 'hs' ? '沪深300' : '中证500'}</span>
}

export default function Stocks() {
  const navigate = useNavigate()
  const [mode, setMode] = useState('全部')
  const [qInput, setQInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [sort, setSort] = useState('code')
  const [page, setPage] = useState(1)

  const universe = UNIVERSE_MAP[mode]
  const listParams: StockListQuery = {
    page,
    page_size: POOL_PG,
    keyword: keyword || undefined,
    universe,
    sort: sort as StockListQuery['sort'],
    order: 'asc',
  }

  // —— 列表 + KPI 总数（page_size=1 只取 total）——
  const list = useApi(() => stocksApi.getStocks(listParams), [page, universe, keyword, sort])
  const total = useApi(() => stocksApi.getStocks({ page: 1, page_size: 1 }), [])
  const totalHs = useApi(() => stocksApi.getStocks({ page: 1, page_size: 1, universe: 'hs300' }), [])
  const totalZz = useApi(() => stocksApi.getStocks({ page: 1, page_size: 1, universe: 'zz500' }), [])

  const submitSearch = () => {
    setKeyword(qInput.trim())
    setPage(1)
  }

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 14 }}>
        <Kpi lb="股票池总数" v={total.data?.total ?? '—'} d={total.loading ? '加载中…' : 'hs300 + zz500'} />
        <Kpi lb="沪深300" v={totalHs.data?.total ?? '—'} d="大盘核心" />
        <Kpi lb="中证500" v={totalZz.data?.total ?? '—'} d="中盘成长" />
        <Kpi lb="今日有效评分" v="待 G2" d="评分/排名/信号待后端补齐" vClass="muted" />
      </div>

      <div className="card">
        <h3>
          股票池
          <span className="hint">
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <input
                type="text"
                placeholder="搜索代码 / 名称（回车）"
                style={{ width: 170, padding: '5px 10px' }}
                value={qInput}
                onChange={e => setQInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && submitSearch()}
              />
              <Seg
                options={BOARD_OPTIONS}
                value={mode}
                onChange={v => {
                  setMode(v)
                  setPage(1)
                }}
              />
              <select
                style={{ padding: '5px 8px' }}
                value={sort}
                onChange={e => {
                  setSort(e.target.value)
                  setPage(1)
                }}
              >
                {SORT_OPTIONS.map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
              <span style={{ color: 'var(--txt3)', fontSize: 13 }}>🖱 点击行查看个股详情</span>
              <span style={{ color: 'var(--txt3)', fontSize: 12 }}>综合排名/涨跌/成交额排序待 G2</span>
            </span>
          </span>
        </h3>
        {list.error ? (
          <Notice text={list.error} onRetry={list.reload} retrying={list.loading} />
        ) : (
          <table>
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>指数</th>
                <th className="r">最新价</th>
                <th className="r">涨跌幅</th>
                <th className="r">成交额</th>
                <th className="r">PE(TTM)</th>
                <th className="r">PB</th>
                <th className="r">ROE</th>
                <th className="r">综合分</th>
                <th className="r">排名</th>
                <th>今日信号</th>
              </tr>
            </thead>
            <tbody>
              {list.loading && !list.data ? (
                <tr>
                  <td colSpan={12}>
                    <div className="empty">加载中…</div>
                  </td>
                </tr>
              ) : (
                (list.data?.items ?? []).map(s => {
                  const sig = mapAction(s.signal)
                  return (
                    <tr key={s.code} className="clickable" onClick={() => navigate(`/stocks/${s.code}`)}>
                      <td className="num">{s.code}</td>
                      <td>
                        <b>{s.name}</b>
                      </td>
                      <td>
                        <BoardTag item={s} />
                      </td>
                      <td className="r num" title={s.price === undefined ? G2_HINT : undefined}>
                        {s.price === undefined ? '—' : s.price.toFixed(2)}
                      </td>
                      <td
                        className={`r num ${s.chg !== undefined && s.chg >= 0 ? 'up' : 'down'}`}
                        title={s.chg === undefined ? G2_HINT : undefined}
                      >
                        {fmtChg(s.chg)}
                      </td>
                      <td className="r num" title={s.amount === undefined ? G2_HINT : undefined}>
                        {fmtWanYi(s.amount)}
                      </td>
                      <G2Cell v={s.pe} />
                      <G2Cell v={s.pb} />
                      <G2Cell v={s.roe && s.roe > 0 ? s.roe : undefined} />
                      <G2Cell v={s.score} />
                      <G2Cell v={s.rank} />
                      <td title={s.signal === undefined ? G2_HINT : undefined}>
                        {sig ? <Tag type={sig} /> : <span className="num muted">—</span>}
                      </td>
                    </tr>
                  )
                })
              )}
              {list.data && list.data.items.length === 0 && (
                <tr>
                  <td colSpan={12}>
                    <div className="empty">无匹配股票</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
        {list.data && (
          <Pager total={list.data.total} page={page} size={POOL_PG} onChange={p => setPage(p)} />
        )}
      </div>
    </section>
  )
}
