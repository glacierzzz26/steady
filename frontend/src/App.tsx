import { Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './layouts/MainLayout'
import Dash from './pages/Dash'
import FactorLab from './pages/FactorLab'
import FactorFactory from './pages/FactorFactory'
import Signal from './pages/Signal'
import StrategyFactory from './pages/StrategyFactory'
import Brief from './pages/Brief'
import Stocks from './pages/Stocks'
import StockDetail from './pages/StockDetail'
import Trade from './pages/Trade'
import Backtest from './pages/Backtest'
import Live from './pages/Live'
import Auth from './pages/Auth'
import Ops from './pages/Ops'
import Settings from './pages/Settings'

export default function App() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        {/* 研究区 */}
        <Route path="/dashboard" element={<Dash />} />
        <Route path="/factor" element={<FactorLab />} />
        <Route path="/factor-factory" element={<FactorFactory />} />
        <Route path="/signal" element={<Signal />} />
        <Route path="/strategy-factory" element={<StrategyFactory />} />
        {/* 行情区 */}
        <Route path="/brief" element={<Brief />} />
        <Route path="/stocks" element={<Stocks />} />
        <Route path="/stocks/:code" element={<StockDetail />} />
        {/* 交易区 */}
        <Route path="/trade" element={<Trade />} />
        <Route path="/backtest" element={<Backtest />} />
        <Route path="/live" element={<Live />} />
        {/* 系统区 */}
        <Route path="/ops" element={<Ops />} />
        <Route path="/auth" element={<Auth />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}
