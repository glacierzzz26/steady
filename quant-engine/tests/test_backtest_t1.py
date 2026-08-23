"""回测可信度校准测试（Iteration 3 · G8 T+1 成交假设）

- t1_open 语义：信号 T 日生成、T+1 开盘成交（无未来函数）；T 日信号只入队不成交
- 边界场景：涨停买不进 / 跌停卖不出 / 现金不足 100 股递减 / T+1 卖出限制 / 停牌跳过 / 末日军不改
- 可复现性：t1_open 同参两次结果一致；t_close 与 t1_open 成交时点对比（偏差来源）
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest.engine import BacktestEngine
from app.models.tables import Base, TradeCalendar
from app.strategies.base import Signal

D1, D2, D3, D4 = (date(2026, 8, 10 + i) for i in range(4))
DAYS = [D1, D2, D3, D4]


class ScriptedStrategy:
    """预设信号 + 价格表，精确控制回测场景（引擎只依赖 run/top_n/max_position_pct 与取价接口）"""

    def __init__(self, signals, prices, top_n=2, max_position_pct=1.0):
        self.signals = signals   # {date_iso: [Signal, ...]}
        self.prices = prices     # {code: {date_iso: (open, close)}}
        self.top_n = top_n
        self.max_position_pct = max_position_pct

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
    for d in DAYS:
        session.add(TradeCalendar(cal_date=d, is_open=True))
    session.commit()
    return session


def run_t1(db, signals, prices, **kw):
    strat = ScriptedStrategy(signals, prices, **kw)
    engine = BacktestEngine(strat, D1.isoformat(), D4.isoformat(), db=db,
                            fill_mode="t1_open")
    engine.run()
    return engine


# ---- t1_open 核心语义 ----


def test_t1_open_fill_at_next_open(db):
    """T 日 BUY 信号 → T+1 开盘价成交；首日无 pending 不成交"""
    prices = {"600001": {
        D1.isoformat(): (10, 11), D2.isoformat(): (12, 13),
        D3.isoformat(): (14, 15), D4.isoformat(): (16, 17)}}
    engine = run_t1(db, {D1.isoformat(): [Signal("600001", 80, "BUY")]}, prices)

    buys = [t for t in engine.portfolio.trades if t["action"] == "BUY"]
    assert len(buys) == 1
    assert buys[0]["date"] == D2.isoformat()     # T+1 成交
    assert buys[0]["price"] == 12.0              # T+1 开盘价
    assert buys[0]["qty"] % 100 == 0 and buys[0]["qty"] > 0
    # 首日（D1）无成交：T 日信号只入队
    assert all(t["date"] != D1.isoformat() for t in engine.portfolio.trades)


def test_t1_open_last_day_signal_not_filled(db):
    """末日军只入队、窗口外不成交"""
    prices = {"600001": {d.isoformat(): (10, 11) for d in DAYS}}
    engine = run_t1(db, {D4.isoformat(): [Signal("600001", 80, "BUY")]}, prices)
    assert engine.portfolio.trades == []


def test_t1_sell_defers_after_buy(db):
    """T+1 卖出限制：买入当日冻结，SELL 顺延至次一交易日开盘"""
    prices = {"600001": {
        D1.isoformat(): (10, 10), D2.isoformat(): (10.5, 11),
        D3.isoformat(): (11.5, 12), D4.isoformat(): (12.5, 13)}}
    signals = {
        D1.isoformat(): [Signal("600001", 80, "BUY")],    # 入队 → D2 开盘买入
        D2.isoformat(): [Signal("600001", 80, "SELL")],   # D2 买入当日冻结，入队 → D3 开盘卖出
    }
    engine = run_t1(db, signals, prices)
    sells = [t for t in engine.portfolio.trades if t["action"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["date"] == D3.isoformat()    # 非 D2：当日买入不可卖
    assert sells[0]["price"] == 11.5             # D3 开盘价


# ---- 边界场景 ----


def test_limit_up_block_buy_t1_open(db):
    """T+1 开盘触涨停（>= 前收×1.10）→ BUY 跳过"""
    prices = {"600001": {
        D1.isoformat(): (10, 10), D2.isoformat(): (11, 11),   # 11 = 10×1.1 涨停线
        D3.isoformat(): (11, 11), D4.isoformat(): (11, 11)}}
    engine = run_t1(db, {D1.isoformat(): [Signal("600001", 80, "BUY")]}, prices)
    assert engine.portfolio.trades == []


def test_limit_down_block_sell_t1_open(db):
    """T+1 开盘触跌停（<= 前收×0.90）→ SELL 跳过，持仓保留"""
    prices = {"600001": {
        D1.isoformat(): (10, 10), D2.isoformat(): (10, 12),       # D2 买入，收盘 12
        D3.isoformat(): (10.8, 10.8), D4.isoformat(): (10.8, 10.8)}}  # 12×0.9 跌停线
    signals = {
        D1.isoformat(): [Signal("600001", 80, "BUY")],
        D2.isoformat(): [Signal("600001", 80, "SELL")],
    }
    engine = run_t1(db, signals, prices)
    assert [t for t in engine.portfolio.trades if t["action"] == "SELL"] == []
    assert "600001" in engine.portfolio.positions


def test_cash_insufficient_decrement(db):
    """现金不足：200 股超预算 → 按 100 股递减至 100 成交"""
    prices = {"600001": {d.isoformat(): (500, 500) for d in DAYS}}
    engine = run_t1(db, {D1.isoformat(): [Signal("600001", 80, "BUY")]}, prices,
                    top_n=1, max_position_pct=1.0)
    buys = [t for t in engine.portfolio.trades if t["action"] == "BUY"]
    assert len(buys) == 1
    assert buys[0]["qty"] == 100


def test_halt_no_open_skip_fill(db):
    """T+1 无 open（停牌/数据缺失）→ 跳过成交"""
    prices = {"600001": {
        D1.isoformat(): (10, 11), D2.isoformat(): (None, 12),
        D3.isoformat(): (14, 15), D4.isoformat(): (16, 17)}}
    engine = run_t1(db, {D1.isoformat(): [Signal("600001", 80, "BUY")]}, prices)
    assert engine.portfolio.trades == []


def test_empty_signals_report(db):
    """空信号区间：报告不崩，净值恒为初始资金"""
    prices = {"600001": {d.isoformat(): (10, 11) for d in DAYS}}
    strat = ScriptedStrategy({}, prices)
    eng = BacktestEngine(strat, D1.isoformat(), D4.isoformat(), db=db,
                         fill_mode="t1_open")
    report = eng.run()
    assert eng.portfolio.trades == []
    assert report["trades"] == 0
    assert report["trading_days"] == len(DAYS)
    assert report["portfolio"]["total_return"] == 0.0


# ---- 可复现性 / 偏差来源 / 报告字段 ----


def test_t1_open_reproducible(db):
    """t1_open 同参两次独立回测 → 净值序列与成交逐字段一致"""
    prices = {"600001": {d.isoformat(): (10 + i, 11 + i) for i, d in enumerate(DAYS)}}
    signals = {
        D1.isoformat(): [Signal("600001", 80, "BUY")],
        D2.isoformat(): [Signal("600001", 80, "SELL")],
    }

    def run():
        strat = ScriptedStrategy(signals, prices)
        eng = BacktestEngine(strat, D1.isoformat(), D4.isoformat(), db=db,
                             fill_mode="t1_open")
        eng.run()
        return eng.daily_returns, eng.portfolio.trades

    r1, t1 = run()
    r2, t2 = run()
    assert r1 == r2
    assert t1 == t2


def test_t1_open_vs_t_close_fill_point(db):
    """偏差来源：t_close 当日收盘成交 vs t1_open 次一交易日开盘成交，成交点不同"""
    prices = {"600001": {
        D1.isoformat(): (10, 11), D2.isoformat(): (12, 13),
        D3.isoformat(): (14, 15), D4.isoformat(): (16, 17)}}
    signals = {D1.isoformat(): [Signal("600001", 80, "BUY")]}

    def trades_of(mode):
        strat = ScriptedStrategy(signals, prices)
        eng = BacktestEngine(strat, D1.isoformat(), D4.isoformat(), db=db,
                             fill_mode=mode)
        eng.run()
        return eng.portfolio.trades

    tc, t1 = trades_of("t_close"), trades_of("t1_open")
    assert tc[0]["date"] == D1.isoformat() and tc[0]["price"] == 11.0
    assert t1[0]["date"] == D2.isoformat() and t1[0]["price"] == 12.0


def test_report_carries_fill_mode(db):
    """报告带 fill_mode 字段（落库/前端展示依据）"""
    prices = {"600001": {d.isoformat(): (10, 11) for d in DAYS}}
    strat = ScriptedStrategy({D1.isoformat(): [Signal("600001", 80, "BUY")]}, prices)
    eng = BacktestEngine(strat, D1.isoformat(), D4.isoformat(), db=db,
                         fill_mode="t1_open")
    report = eng.run()
    assert report["fill_mode"] == "t1_open"
