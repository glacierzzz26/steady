"""风险指标口径测试（Iteration 4 · §3.5 turnover/cost）

已知成交序列手算对照：
  D1 买入 600000 1000 股 @10.00（成交 10.01，佣金 5，滑点 10）
  D2 卖出 600000 1000 股 @11.00（成交 10.989，佣金 5，印花税 5.4945，滑点 11）

turnover = 累计买入成交金额 / 平均总资产 × (252/交易日数)
cost     = 累计(佣金+印花税+滑点) / 平均总资产 × (252/交易日数)

手算：buy_amount=10010，total_cost=36.4945，avg=(99985+100963.5055)/2，
      turnover≈12.55，cost≈0.0458（与设计 §3.5 口径一致，DECIMAL(8,2)/(8,4)）。
"""
import math
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest.engine import BacktestEngine
from app.models.tables import Base, TradeCalendar
from app.strategies.base import Signal

D1 = date(2026, 8, 20)
D2 = date(2026, 8, 21)


class TurnoverStrategy:
    """预设 BUY→SELL 两日信号 + 价格，顶格权重让 _calc_quantity 恰好 1000 股"""

    top_n = 10
    max_position_pct = 1.0

    def __init__(self):
        self.prices = {
            "600000": {D1.isoformat(): (9.50, 10.00),
                       D2.isoformat(): (10.50, 11.00)}}
        self.signals = {
            D1.isoformat(): [Signal("600000", 99, "BUY")],
            D2.isoformat(): [Signal("600000", 10, "SELL")],
        }

    def run(self, trade_date):
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


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for d in (D1, D2):
        session.add(TradeCalendar(cal_date=d, is_open=True))
    session.commit()
    return session


def test_turnover_cost_hand_calc(db):
    eng = BacktestEngine(TurnoverStrategy(), D1.isoformat(), D2.isoformat(),
                         db=db, fill_mode="t_close")
    report = eng.run()

    # 成交口径逐字段校验（与 Go Broker 同费率）
    assert eng.portfolio.buy_amount == pytest.approx(10010, abs=1e-6), \
        eng.portfolio.buy_amount
    assert eng.portfolio.total_cost == pytest.approx(36.4945, abs=1e-6), \
        eng.portfolio.total_cost
    assert eng.portfolio.cash == pytest.approx(100963.5055, abs=1e-6), \
        eng.portfolio.cash

    # 报告风险指标（round 到 2/4 位小数）
    assert report["turnover"] == pytest.approx(12.55, abs=0.01), report
    assert report["cost"] == pytest.approx(0.0458, abs=0.0001), report
