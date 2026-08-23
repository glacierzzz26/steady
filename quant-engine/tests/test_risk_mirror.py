"""风控规则 Python 镜像测试（Iteration 4 · §3.4/§3.6）

与 backend/internal/service/trading_risk_test.go 共用同一 fixture 数字：
同参同输入 → 同持仓演化 + 同现金（round2 口径）。费率与 Go Broker 同源。

三个用例各验证一条风控规则在引擎每日循环中的镜像：
  1. 止损扫描：持仓亏损 ≥ stop_loss_pct → 信号前强制卖出，计入 risk_actions
  2. 回撤熔断：回撤幅度 ≥ fuse_pct → BUY 全跳、SELL 照常
  3. 行业集中：加仓后行业市值占比超限 → BUY 拒单
"""
import math
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest.engine import BacktestEngine
from app.backtest.portfolio import Position
from app.models.tables import Base, TradeCalendar
from app.strategies.base import Signal

D = date(2026, 8, 20)


def round2(v: float) -> float:
    """与 Go math.Round(v*100)/100 同语义（round2 后现金逐字段可比）"""
    return math.floor(v * 100 + 0.5) / 100


class RiskStrategy:
    """预设信号/价格 + 风控参数，精确控制场景（引擎只依赖 run/top_n/风控参数/取价接口）"""

    def __init__(self, signals, prices, industry=None, **params):
        self.signals = signals   # {date_iso: [Signal, ...]}
        self.prices = prices     # {code: {date_iso: (open, close)}}
        self.industry = industry or {}
        self.top_n = params.get("top_n", 10)
        self.max_position_pct = params.get("max_position_pct", 0.20)
        self.stop_loss_pct = params.get("stop_loss_pct", 0.0)
        self.drawdown_fuse_pct = params.get("drawdown_fuse_pct", 0.0)
        self.industry_limit_pct = params.get("industry_limit_pct", 0.0)

    def run(self, trade_date):
        return self.signals.get(trade_date, [])

    def price_at(self, code, trade_date):
        v = self.prices.get(code, {}).get(trade_date)
        return v[1] if v else None

    def open_at(self, code, trade_date):
        v = self.prices.get(code, {}).get(trade_date)
        return v[0] if v else None

    def prev_close_at(self, code, trade_date):
        """该日期前最近一个有价的交易日 close（涨跌停判断用）"""
        prev = None
        for d in sorted(self.prices.get(code, {})):
            if d < trade_date:
                prev = self.price_at(code, d)
        return prev


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(TradeCalendar(cal_date=D, is_open=True))
    session.commit()
    return session


def make_engine(db, strat, **portfolio_kw):
    eng = BacktestEngine(strat, D.isoformat(), D.isoformat(), db=db,
                         fill_mode="t_close")
    eng.portfolio.cash = portfolio_kw["cash"]
    for code, (qty, avail, cost, cur) in portfolio_kw["positions"].items():
        eng.portfolio.positions[code] = Position(code, qty, avail, cost, cur)
    if "peak" in portfolio_kw:
        eng._peak = portfolio_kw["peak"]  # 镜像 Go 的历史净值峰值
    return eng


# ---------- 止损（与 Go TestRiskStopLossScan 同 fixture） ----------

def test_stop_loss_mirror(db):
    """持仓 600000 2000@12.00，收盘 10.00（-16.7%）→ 信号前强制卖出。
    成交价 9.99、金额 19980、佣金 5、印花税 9.99 → 回款 19965.01。
    现金 89985 → 109950.01（与 Go 一致）。"""
    strat = RiskStrategy(
        {},
        {"600000": {"2026-08-19": (11.0, 10.90), "2026-08-20": (10.0, 10.0)}},
        stop_loss_pct=0.15, top_n=10, max_position_pct=0.20)
    eng = make_engine(db, strat, cash=89985.0,
                      positions={"600000": (2000, 2000, 12.00, 12.00)})
    eng.run()

    assert eng.risk_actions == 1, "止损应触发并计入 risk_actions"
    assert "600000" not in eng.portfolio.positions, "止损后持仓应清仓"
    assert round2(eng.portfolio.cash) == 109950.01, eng.portfolio.cash


# ---------- 回撤熔断（与 Go TestRiskDrawdownFuse 同 fixture） ----------

def test_drawdown_fuse_mirror(db):
    """历史峰值 100000（Go 侧 account_nav），当前总资产 32002 → 回撤 68% ≥ 10% 熔断。
    当日 SELL 000001 照常（回款 1994 → 现金 31994），BUY 600002 全跳。"""
    strat = RiskStrategy(
        {D.isoformat(): [Signal("600002", 95, "BUY"),
                         Signal("000001", 10, "SELL")]},
        {"000001": {"2026-08-19": (20.02, 20.02), "2026-08-20": (20.02, 20.02)}},
        drawdown_fuse_pct=0.10, top_n=10, max_position_pct=0.20)
    eng = make_engine(db, strat, cash=30000.0,
                      positions={"000001": (100, 100, 20.02, 20.02)},
                      peak=100000.0)
    eng.run()

    assert eng._fused is True, "回撤 68% 应触发熔断"
    assert "000001" not in eng.portfolio.positions, "熔断日 SELL 照常清仓"
    assert not any(t["code"] == "600002" for t in eng.portfolio.trades), \
        "熔断日 BUY 应全跳"
    assert round2(eng.portfolio.cash) == 31994.00, eng.portfolio.cash


# ---------- 行业集中（与 Go TestRiskIndustryLimit 同 fixture） ----------

def test_industry_limit_mirror(db):
    """600000 1000@10.01 + 000001 400@20.02 均属「银行」，集中度 18.0%。
    BUY 600000 加仓 900 股后占比 27.0% > 10% → 拒单，持仓不变。"""
    strat = RiskStrategy(
        {D.isoformat(): [Signal("600000", 90, "BUY")]},
        {"600000": {"2026-08-19": (9.50, 9.50), "2026-08-20": (10.0, 10.0)},
         "000001": {"2026-08-19": (19.0, 19.0), "2026-08-20": (20.02, 20.02)}},
        industry={"600000": "银行", "000001": "银行"},
        industry_limit_pct=0.10, top_n=10, max_position_pct=0.20)
    eng = make_engine(db, strat, cash=81972.0,
                      positions={"600000": (1000, 1000, 10.01, 10.01),
                                 "000001": (400, 400, 20.02, 20.02)})
    eng.run()

    assert not any(t["code"] == "600000" and t["action"] == "BUY"
                   for t in eng.portfolio.trades), "行业集中超限应拒单"
    assert eng.portfolio.positions["600000"].quantity == 1000, "拒单后持仓不变"
    assert round2(eng.portfolio.total_value) == 99980.0, eng.portfolio.total_value
