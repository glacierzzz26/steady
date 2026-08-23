import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { crumbs } from '../mock/data'

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
            <span className="num">08-21 收盘</span>
          </div>
          <div className="row">
            <span>下一交易日</span>
            <span className="num">08-24 周一</span>
          </div>
        </div>
      </aside>

      {/* ================= MAIN ================= */}
      <div className="main">
        <div className="topbar">
          <span className="crumb">{title}</span>
          <span className="sub">{sub}</span>
          <div className="top-right">
            <span className="chip rest">● 休市 · 周末</span>
            <span className="chip">
              沪深300 <b className="num up">+0.32%</b>
            </span>
            <span className="chip">
              中证500 <b className="num down">-0.18%</b>
            </span>
            <div className="avatar">主</div>
          </div>
        </div>
        <Outlet />
      </div>
    </div>
  )
}
