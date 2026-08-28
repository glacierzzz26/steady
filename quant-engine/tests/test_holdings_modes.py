"""振荡修复 backtest 对照的三种持仓模式测试（replay.py holdings_mode）

- running（默认）：信号运行集（BUY 加 / SELL 删），引擎不介入持仓
- reconstruct：实盘 _reconstruct_holdings 语义——持仓 = 上一信号日 {BUY, HOLD}，
  含把「未持有等回调 HOLD」误计为持仓的缺陷（振荡根因）
- portfolio：修复方向——引擎从真实组合同步持仓，成交失败不产生幽灵持仓
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest.engine import BacktestEngine
from app.backtest.replay import ReplayStrategy, reconstruct_holdings
from app.models.tables import (Base, DailyPrice, DailyValuation,
                               FinancialIndicator, StockBasic, TradeCalendar)
from app.strategies.base import Signal

WEIGHTS = {"ma_trend": 0.20, "macd_signal": 0.20, "pe_ratio": 0.15,
           "pb_ratio": 0.15, "roe_quality": 0.20, "debt_risk": 0.10}
CATEGORIES = {"ma_trend": "trend", "macd_signal": "trend", "pe_ratio": "value",
              "pb_ratio": "value", "roe_quality": "quality", "debt_risk": "risk"}
CODES = ["600001", "600002", "600003"]
DAYS = [date(2026, 8, 10 + i) for i in range(8)]
WARMUP = [date(2026, 7, 1) + timedelta(days=i) for i in range(30)]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for d in DAYS:
        session.add(TradeCalendar(cal_date=d, is_open=True))
    for ci, code in enumerate(CODES):
        for i, d in enumerate(WARMUP):
            session.add(DailyPrice(id=5000 + ci * 100 + i, code=code,
                                   trade_date=d, close=10 + ci + i * 0.01,
                                   adj_factor=1.0))
        for i, d in enumerate(DAYS):
            session.add(DailyPrice(id=2000 + ci * 100 + i, code=code,
                                   trade_date=d,
                                   close=10 + ci + i * 0.1, adj_factor=1.0))
            session.add(DailyValuation(id=3000 + ci * 100 + i, code=code,
                                       trade_date=d,
                                       pe_ttm=10 + ci * 5, pb=1 + ci))
        session.add(FinancialIndicator(id=4000 + ci, code=code,
                                       report_date=date(2026, 6, 30),
                                       announce_date=date(2026, 8, 12),
                                       roe=15 + ci, debt_ratio=40 + ci))
        session.add(StockBasic(code=code, name=f"测试{ci}", market="SH",
                               universe="hs300"))
    session.commit()
    return session


def make_strat(db, mode, **kw):
    params = {"top_n": 2, "buy_buffer": 1, "sell_buffer": 2,
              "holdings_mode": mode}
    params.update(kw)
    return ReplayStrategy(db, params, WEIGHTS, CATEGORIES)


# ---- reconstruct_holdings 纯函数 ----

def test_reconstruct_pure():
    """持仓 = 上一信号日 {BUY, HOLD}；SELL/缺失不计入"""
    assert reconstruct_holdings({}) == set()
    assert reconstruct_holdings({"A": "BUY", "B": "HOLD", "C": "SELL"}) \
        == {"A", "B"}
    assert reconstruct_holdings({"A": "SELL"}) == set()


def test_reconstruct_mode_derives_from_prev_signals(db):
    """reconstruct：T 日持仓 = T-1 信号 {BUY, HOLD} 集合"""
    strat = make_strat(db, "reconstruct")
    strat.preload("2026-08-10", "2026-08-17")
    assert strat.holdings == set()          # 首日空集
    d1 = strat.run("2026-08-10")
    prev = {s.code for s in d1 if s.action in ("BUY", "HOLD")}
    strat.run("2026-08-11")
    assert strat.holdings == prev           # 次日 holdings 由前日信号重建


# ---- portfolio：引擎从真实组合同步，成交失败无幽灵持仓 ----

class HoldingsProbe:
    """带 holdings_mode=portfolio 的探针策略：run 时记录引擎同步后的 holdings"""

    def __init__(self, signals, prices, top_n=2, max_position_pct=1.0):
        self.signals = signals
        self.prices = prices
        self.top_n = top_n
        self.max_position_pct = max_position_pct
        self.holdings_mode = "portfolio"
        self.holdings = set()
        self.seen = []

    def run(self, trade_date):
        self.seen.append((trade_date, set(self.holdings)))
        return self.signals.get(trade_date, [])

    def price_at(self, code, trade_date):
        v = self.prices.get(code, {}).get(trade_date)
        return v[1] if v else None

    def open_at(self, code, trade_date):
        v = self.prices.get(code, {}).get(trade_date)
        return v[0] if v else None

    def prev_close_at(self, code, trade_date):
        prev = None
        for d in sorted(self.prices.get(code, {})):
            if d < trade_date:
                prev = self.price_at(code, d)
        return prev


def test_portfolio_mode_syncs_real_positions(db):
    """portfolio：T 日成交的 BUY → T+1 持仓含该股（真实持仓闭环）"""
    D1, D2, D3, D4 = (d.isoformat() for d in DAYS[:4])
    prices = {"600001": {
        D1: (10, 11), D2: (12, 13), D3: (14, 15), D4: (16, 17)}}
    probe = HoldingsProbe(
        {D1: [Signal("600001", 80, "BUY")]}, prices)
    BacktestEngine(probe, DAYS[0].isoformat(), DAYS[3].isoformat(),
                   db=db, fill_mode="t_close").run()
    seen = dict(probe.seen)
    assert "600001" not in seen[D1]        # 首日信号前无持仓
    assert "600001" in seen[D2]            # 次日 D1 已成交 → 持仓含 600001


def test_portfolio_mode_no_phantom_on_failed_fill(db):
    """portfolio：BUY 因涨停未成交 → 次日持仓不含该股（无幽灵持仓）"""
    D1, D2, D3, D4 = (d.isoformat() for d in DAYS[:4])
    # D2 开盘 11 = D1 收盘 10 × 1.1 → 触涨停，BUY 在 D2 开盘无法成交
    prices = {"600001": {
        D1: (10, 10), D2: (11, 11), D3: (11, 11), D4: (11, 11)}}
    probe = HoldingsProbe(
        {D1: [Signal("600001", 80, "BUY")]}, prices)
    BacktestEngine(probe, DAYS[0].isoformat(), DAYS[3].isoformat(),
                   db=db, fill_mode="t1_open").run()
    seen = dict(probe.seen)
    assert all("600001" not in s for _, s in probe.seen)


def test_running_mode_engine_does_not_sync(db):
    """running（默认）：引擎不覆盖策略持仓——持仓由策略自行维护"""
    strat = make_strat(db, "running")
    strat.preload("2026-08-10", "2026-08-17")
    strat.run("2026-08-10")
    snap = set(strat.holdings)             # 策略信号运行集
    BacktestEngine(strat, "2026-08-11", "2026-08-17", db=db).run()
    # 引擎不触碰 holdings（非 portfolio 模式无同步）；持仓仍按信号演化
    assert strat.holdings_mode == "running"
