"""回测引擎主循环：按交易日历逐日执行，记录净值/回撤/基准对比"""
import logging
from typing import List, Optional

import pandas as pd
from sqlalchemy import select

from app.backtest.broker import Broker
from app.backtest.portfolio import Portfolio
from app.models.tables import DailyPrice, TradeCalendar
from app.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)


class BacktestEngine:
    """回测引擎：逐日跑策略 → 按信号成交（含 T+1/涨跌停/手续费）→ 净值曲线

    fill_mode 成交假设（Iteration 3 · G8）：
    - t_close：信号 T 日收盘生成、T 日收盘价成交（乐观假设，含未来函数）
    - t1_open：信号 T 日收盘生成、T+1 开盘价成交（保守假设，无未来函数）
    t1_open 下 T 日信号暂存 pending_signals，T+1 日循环先执行昨日 pending
    （以当日开盘价），再生成新信号入队。

    strategy 若实现 preload(start, end)（如 ReplayStrategy），引擎自动
    在 run() 前调用；价格取价优先走 strategy.price_at/open_at（复用预加载数据）。
    """

    def __init__(self, strategy: Strategy, start_date: str, end_date: str,
                 db=None, fill_mode: str = "t_close"):
        if db is None:
            from app.db import get_session
            db = get_session()
        self.db = db
        self.strategy = strategy
        self.start_date = start_date
        self.end_date = end_date
        self.fill_mode = fill_mode
        self.portfolio = Portfolio(initial_cash=100000)
        self.broker = Broker()
        self.daily_returns = []
        self.pending_signals: list = []  # t1_open：T 日信号待 T+1 执行
        self._benchmark_cache: Optional[dict] = None
        # Iteration 4 风控（镜像 Go execStrategy；策略未定义时缺省 0 = 规则关闭）
        self.stop_loss_pct = getattr(strategy, "stop_loss_pct", 0.0)
        self.drawdown_fuse_pct = getattr(strategy, "drawdown_fuse_pct", 0.0)
        self.industry_limit_pct = getattr(strategy, "industry_limit_pct", 0.0)
        self._peak = 0.0       # 历史最高 total_value（熔断基准，峰值回撤引擎内维护）
        self._fused = False    # 当日回撤熔断（BUY 全跳）
        self.risk_actions = 0  # 止损强制卖出笔数（与 Go RiskActions 同口径）

    def _get_trading_dates(self) -> List[str]:
        """从 trade_calendar 取 [start, end] 区间的交易日"""
        rows = self.db.execute(
            select(TradeCalendar.cal_date)
            .where(TradeCalendar.cal_date >= self.start_date,
                   TradeCalendar.cal_date <= self.end_date,
                   TradeCalendar.is_open.is_(True))
            .order_by(TradeCalendar.cal_date)
        ).scalars().all()
        return [d.isoformat() for d in rows]

    def _get_price(self, code: str, date: str) -> Optional[float]:
        """当日真实收盘价（不复权）；优先用策略预加载数据，回退查库"""
        if hasattr(self.strategy, "price_at"):
            return self.strategy.price_at(code, date)
        close = self.db.execute(
            select(DailyPrice.close).where(
                DailyPrice.code == code, DailyPrice.trade_date == date)
        ).scalar()
        return float(close) if close is not None else None

    def _get_fill_price(self, code: str, date: str) -> Optional[float]:
        """成交价：t_close=当日收盘；t1_open=当日开盘（无 open 视为停牌/数据缺失）"""
        if self.fill_mode == "t1_open":
            if hasattr(self.strategy, "open_at"):
                return self.strategy.open_at(code, date)
            open_ = self.db.execute(
                select(DailyPrice.open).where(
                    DailyPrice.code == code, DailyPrice.trade_date == date)
            ).scalar()
            return float(open_) if open_ is not None else None
        return self._get_price(code, date)

    def _get_prev_close(self, code: str, date: str) -> Optional[float]:
        """前一交易日收盘价，用于涨跌停判断"""
        if hasattr(self.strategy, "prev_close_at"):
            return self.strategy.prev_close_at(code, date)
        rows = self.db.execute(
            select(DailyPrice.close)
            .where(DailyPrice.code == code, DailyPrice.trade_date < date)
            .order_by(DailyPrice.trade_date.desc())
            .limit(1)
        ).scalar()
        return float(rows) if rows is not None else None

    def _get_benchmark_nav(self, date: str) -> Optional[float]:
        """沪深300 指数当日收盘（用于超额收益对比），缓存全量"""
        if self._benchmark_cache is None:
            rows = self.db.execute(
                select(DailyPrice.trade_date, DailyPrice.close)
                .where(DailyPrice.code == "sh000300")
            ).all()
            self._benchmark_cache = {
                d.isoformat(): float(c) for d, c in rows if c is not None}
        return self._benchmark_cache.get(date)

    def _calc_quantity(self, price: float) -> int:
        """等权 + 单股仓位上限，向下取整到 100 股整数倍"""
        budget = min(self.portfolio.total_value / self.strategy.top_n,
                     self.portfolio.total_value * self.strategy.max_position_pct)
        qty = int(budget / price // 100) * 100
        return qty

    def _unfreeze_t1(self, date: str):
        """T+1：上一交易日买入的份额当日可用（简化实现：全部解冻）"""
        for pos in self.portfolio.positions.values():
            pos.available_qty = pos.quantity

    def _execute_pending(self, date: str):
        """t1_open：执行上一交易日收盘生成的信号（以今日开盘价成交）"""
        pending, self.pending_signals = self.pending_signals, []
        for signal in pending:
            self._process_signal(signal, date)

    def _mark_to_market(self, date: str):
        """持仓按当日收盘 mark-to-market（停牌无当日价保留旧价）。

        镜像 Go ExecuteDay 第 2 步：止损 profit_rate / 熔断 total_value /
        行业集中市值占比均基于当日收盘口径。
        """
        prices = {}
        for code in self.portfolio.positions:
            price = self._get_price(code, date)
            if price is not None:
                prices[code] = price
        self.portfolio.mark_to_market(prices)

    def _stop_loss_scan(self, date: str):
        """止损扫描（策略信号前）：持仓亏损 >= stop_loss_pct → 当日可卖量强制卖出。

        镜像 Go stopLossScan：profit_rate = (现价-成本)/成本 <= -stop_loss_pct 触发，
        卖当日可卖量，停牌/跌停跳过，计入 risk_actions。
        """
        if self.stop_loss_pct <= 0:
            return
        for code in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[code]
            if pos.available_qty <= 0:
                continue
            if pos.cost_price <= 0:
                continue
            profit_rate = (pos.current_price - pos.cost_price) / pos.cost_price
            if profit_rate > -self.stop_loss_pct:
                continue
            price = self._get_price(code, date)  # 当日收盘
            if price is None:  # 停牌：保留持仓，次日再扫
                continue
            prev_close = self._get_prev_close(code, date)
            if not self.broker._check_price_limit(code, price, prev_close, "SELL"):
                continue  # 跌停无法成交，保留持仓
            ok = self.broker.execute_sell(
                self.portfolio, code, price, pos.available_qty,
                prev_close=prev_close)
            if ok:
                self.portfolio.trades.append(
                    {"date": date, "code": code, "action": "SELL",
                     "price": round(price, 2), "qty": pos.available_qty})
                self.risk_actions += 1

    def _drawdown_fused(self) -> bool:
        """回撤熔断：回撤幅度 = (历史峰值 − 当前总资产)/历史峰值 >= fuse_pct。

        镜像 Go drawdownFused（回撤按幅度计：当前资产低于峰值才触发）。峰值取
        已记录净值的 MAX total_value（即本引擎 _peak，每日记净值后更新）；
        峰值缺失（首日）不熔断。
        """
        if self.drawdown_fuse_pct <= 0 or self._peak <= 0:
            return False
        total = self.portfolio.total_value
        return (self._peak - total) / self._peak >= self.drawdown_fuse_pct

    def _industry_over_limit(self, code: str, exec_price: float, qty: int) -> bool:
        """行业集中度（BUY 撮合前）：加仓后该行业市值占比 > limit_pct → 拒单。

        镜像 Go industryOverLimit：同行业持仓市值（含目标股票已有仓）+ 本次买入
        毛额，除以组合总资产；目标行业缺失/未知 → 不检查。
        """
        if self.industry_limit_pct <= 0 or qty <= 0:
            return False
        industry = getattr(self.strategy, "industry", {}).get(code)
        if not industry:
            return False
        industry_mv = 0.0
        for c, pos in self.portfolio.positions.items():
            if c == code:
                industry_mv += pos.market_value
            elif getattr(self.strategy, "industry", {}).get(c) == industry:
                industry_mv += pos.market_value
        after = (industry_mv + exec_price * qty) / self.portfolio.total_value
        return after > self.industry_limit_pct

    def run(self) -> dict:
        """执行回测并返回报告；策略实现 preload 时先预加载"""
        preload = getattr(self.strategy, "preload", None)
        if callable(preload):
            try:
                preload(self.start_date, self.end_date)
            except TypeError:
                preload()
        dates = self._get_trading_dates()
        logger.info("回测区间 %s ~ %s，共 %s 个交易日（fill_mode=%s）",
                    self.start_date, self.end_date, len(dates), self.fill_mode)
        for date in dates:
            # 每日循环（镜像 Go ExecuteDay）：解冻 → 收盘 mark → 止损 → 熔断 →
            # 信号（BUY 熔断日跳过 + 行业集中检查）→ 记净值
            self._unfreeze_t1(date)            # T+1：解冻上一交易日买入
            self._mark_to_market(date)         # 持仓按当日收盘 mark
            self._stop_loss_scan(date)         # 止损扫描（策略信号前）
            self._fused = self._drawdown_fused()  # 回撤熔断判断
            if self.fill_mode == "t1_open":
                # 先执行昨日（T-1 收盘生成的）信号：今日开盘价成交，无未来函数
                self._execute_pending(date)
            signals = self.strategy.run(date)
            if self.fill_mode == "t1_open":
                # T 日信号仅暂存，T+1 开盘执行；末日在窗口外，天然不成交
                self.pending_signals = [
                    s for s in signals if s.action in ("BUY", "SELL")]
            else:
                for signal in signals:
                    self._process_signal(signal, date)
            # 记录每日净值（含基准对比），并维护历史峰值
            nav = self.portfolio.total_value
            benchmark = self._get_benchmark_nav(date)
            self.daily_returns.append({"date": date, "nav": nav,
                                       "benchmark": benchmark})
            if nav > self._peak:
                self._peak = nav
        return self._generate_report()

    def _process_signal(self, signal: Signal, date: str):
        if signal.action == "BUY":
            if self._fused:  # 回撤熔断日：BUY 全跳（SELL/止损照常）
                return
            price = self._get_fill_price(signal.code, date)
            if price is None:  # 停牌/数据缺失：跳过
                return
            qty = self._calc_quantity(price)
            if qty <= 0:
                return
            # 行业集中度（BUY 撮合前）：加仓后该行业市值占比超限 → 拒单
            if self._industry_over_limit(signal.code, price * (1 + Broker.SLIPPAGE), qty):
                return
            # 涨停检查 + 100股整手 + 资金校验（Broker 内完成）；
            # 滑点+佣金可能超出预算（top_n 全量买入时现金趋近预算），
            # 与 Go 模拟盘一致按 100 股递减重试，减到 0 放弃
            filled = 0
            while qty > 0:
                try:
                    ok = self.broker.execute_buy(
                        self.portfolio, signal.code, price, qty,
                        prev_close=self._get_prev_close(signal.code, date),
                        trade_date=date)
                except ValueError:
                    ok = False
                if ok:
                    filled = qty
                    break
                qty -= 100
            if filled > 0:
                self.portfolio.trades.append(
                    {"date": date, "code": signal.code, "action": "BUY",
                     "price": round(price, 2), "qty": filled})
        elif signal.action == "SELL":
            price = self._get_fill_price(signal.code, date)
            pos = self.portfolio.positions.get(signal.code)
            if price is None or not pos or pos.available_qty <= 0:
                # 停牌/无持仓/T+1 当日买入不可卖：跳过
                return
            ok = self.broker.execute_sell(
                self.portfolio, signal.code, price, pos.available_qty,
                prev_close=self._get_prev_close(signal.code, date))
            if ok:
                self.portfolio.trades.append(
                    {"date": date, "code": signal.code, "action": "SELL",
                     "price": round(price, 2), "qty": pos.available_qty})

    def _generate_report(self) -> dict:
        """总收益、年化、最大回撤、Sharpe、基准对比"""
        if not self.daily_returns:
            return {"error": "无回测数据"}

        def metrics(values: list[float]) -> dict:
            s = pd.Series(values)
            rets = s.pct_change().dropna()
            total = s.iloc[-1] / s.iloc[0] - 1
            days = len(s)
            return {
                "total_return": round(float(total), 4),
                "annualized_return": round(float((1 + total) ** (252 / days) - 1), 4),
                "max_drawdown": round(float((s / s.cummax() - 1).min()), 4),
                "sharpe": round(float(rets.mean() / rets.std() * (252 ** 0.5)), 4)
                if len(rets) > 1 and rets.std() > 0 else None,
            }

        navs = [r["nav"] for r in self.daily_returns]
        report = {"start": self.daily_returns[0]["date"],
                  "end": self.daily_returns[-1]["date"],
                  "trading_days": len(navs),
                  "fill_mode": self.fill_mode,
                  "final_value": round(self.portfolio.total_value, 2),
                  "trades": len(self.portfolio.trades),
                  "positions": len(self.portfolio.positions),
                  "portfolio": metrics(navs)}
        # 风险指标（§3.5）：turnover=年化单边换手，cost=年化交易成本占比
        # 平均总资产 = 区间每日净值均值；252/交易日数 年化
        if navs:
            avg_asset = sum(navs) / len(navs)
            annual = 252 / len(navs)
            report["turnover"] = round(
                self.portfolio.buy_amount / avg_asset * annual, 2)
            report["cost"] = round(
                self.portfolio.total_cost / avg_asset * annual, 4)
        bench = [r["benchmark"] for r in self.daily_returns
                 if r["benchmark"] is not None]
        if bench:
            bm = metrics(bench)
            report["benchmark"] = bm
            report["excess_return"] = round(
                report["portfolio"]["total_return"] - bm["total_return"], 4)
        # 净值序列（date/nav/benchmark，benchmark 缺失日 None）——落库/前端画图用
        report["nav_series"] = [
            {"date": r["date"], "nav": r["nav"], "benchmark": r["benchmark"]}
            for r in self.daily_returns
        ]
        return report
