import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { marketApi, type IndexQuote, type MarketStatus } from '../api'
import { useApi } from '../hooks/useApi'
import { fmtChg } from '../lib/format'
import { crumbs } from '../mock/data'

/** 行情概览指数（topbar 芯片；与后端 /index/quotes 一致） */
const QUOTE_CODES = ['sh000001', 'sh000300', 'sh000905']

interface NavItem {
  to: string
  icon: string
  label: string
  badge?: string
  p3?: boolean
}

const NAV: Array<{ label: string; items: NavItem[] }> = [
  {
    label: '研究',
    items: [
      { to: '/dashboard', icon: '▣', label: '总览' },
      { to: '/factor', icon: 'ƒ', label: '因子实验室' },
      { to: '/factor-factory', icon: '✚', label: '因子工厂' },
      { to: '/signal', icon: '⇅', label: '策略与信号', badge: '8' },
      { to: '/strategy-factory', icon: '◈', label: '策略工厂' },
    ],
  },
  {
    label: '行情',
    items: [
      { to: '/brief', icon: '☀', label: '早盘简报' },
      { to: '/stocks', icon: '▦', label: '股票池' },
    ],
  },
  {
    label: '交易',
    items: [
      { to: '/trade', icon: '◆', label: '模拟交易' },
      { to: '/backtest', icon: '◱', label: '回测中心' },
      { to: '/live', icon: '⏻', label: '实盘交易', badge: 'P3', p3: true },
    ],
  },
  {
    label: '系统',
    items: [
      { to: '/ops', icon: '⚙', label: '运维监控' },
      { to: '/auth', icon: '⚿', label: '登录与安全', badge: 'P3', p3: true },
      { to: '/settings', icon: '☰', label: '设置' },
    ],
  },
]

const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function weekdayZh(date: string): string {
  return WEEKDAY[new Date(date).getDay()]
}

/** 右上角市场状态 chip：交易日盘中→交易中；交易日休市时段→已收盘/午间；非交易日→休市 */
function marketChip(s?: MarketStatus): { label: string; cls: string } {
  if (!s) return { label: '● 状态加载中', cls: '' }
  if (!s.is_trade_day) {
    const wd = new Date(s.today).getDay()
    return { label: `● 休市 · ${wd === 0 || wd === 6 ? '周末' : '休市日'}`, cls: 'rest' }
  }
  switch (s.market_phase) {
    case 'open':
      return { label: '● 交易中', cls: 'live' }
    case 'pre_open':
      return { label: '● 今日开市 · 未开盘', cls: 'live' }
    case 'lunch_break':
      return { label: '● 午间休市', cls: 'rest' }
    default:
      return { label: '● 今日已收盘', cls: 'rest' }
  }
}

/** 路径 → 面包屑 key */
function crumbKey(pathname: string): string {
  if (pathname.startsWith('/stocks/')) return 'stockd'
  const map: Record<string, string> = {
    '/dashboard': 'dash',
    '/factor': 'factor',
    '/factor-factory': 'facgen',
    '/signal': 'signal',
    '/strategy-factory': 'strgen',
    '/brief': 'brief',
    '/stocks': 'stocks',
    '/trade': 'trade',
    '/backtest': 'backtest',
    '/live': 'live',
    '/ops': 'ops',
    '/auth': 'auth',
    '/settings': 'set',
  }
  return map[pathname] ?? 'dash'
}

export default function MainLayout() {
  const { pathname } = useLocation()
  const [title, sub] = crumbs[crumbKey(pathname)] ?? ['', '']
  const mkt = useApi(() => marketApi.getStatus(), [])
  const quotes = useApi(() => marketApi.getQuotes(QUOTE_CODES), [])
  const chip = marketChip(mkt.data)

  /** 指数芯片：真实行情；无数据/接口异常则不渲染（不做写死 mock） */
  const quoteChips = (quotes.data?.items ?? []).map((q: IndexQuote) => (
    <span className="chip" key={q.code} title={`${q.name} ${q.trade_date} 收盘 ${q.close}`}>
      {q.name}{' '}
      <b className={`num ${q.change_pct >= 0 ? 'up' : 'down'}`}>{fmtChg(q.change_pct)}</b>
    </span>
  ))

  return (
    <div className="app">
      {/* ================= SIDEBAR ================= */}
      <aside className="side">
        <div className="logo">
          <div className="logo-mark">S</div>
          <div>
            <b>Steady Quant</b>
            <span>个人量化研究终端</span>
          </div>
        </div>
        <nav className="nav">
          {NAV.map(group => (
            <div key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items.map(it => (
                <NavLink key={it.to} to={it.to} className={({ isActive }) => (isActive ? 'on' : '')}>
                  <span className="ic">{it.icon}</span>
                  <span className="txt">{it.label}</span>
                  {it.badge && <span className={`badge${it.p3 ? ' p3' : ''}`}>{it.badge}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          <div className="row">
            <span>
              <i className="dot ok" />
              服务正常
            </span>
            <span>v1.6.2</span>
          </div>
          <div className="row">
            <span>数据截至</span>
            <span className="num">{mkt.data?.last_trade_date ? `${mkt.data.last_trade_date.slice(5)} 收盘` : '—'}</span>
          </div>
          <div className="row">
            <span>下一交易日</span>
            <span className="num">
              {mkt.data?.next_trade_date
                ? `${mkt.data.next_trade_date.slice(5)} ${weekdayZh(mkt.data.next_trade_date)}`
                : '—'}
            </span>
          </div>
        </div>
      </aside>

      {/* ================= MAIN ================= */}
      <div className="main">
        <div className="topbar">
          <span className="crumb">{title}</span>
          <span className="sub">{sub}</span>
          <div className="top-right">
            <span className={`chip ${chip.cls}`} title={mkt.error ? '市场状态接口暂不可用' : undefined}>
              {chip.label}
            </span>
            {quoteChips}
            <div className="avatar">主</div>
          </div>
        </div>
        <Outlet />
      </div>
    </div>
  )
}
