"""实盘侧持仓重建修复测试：multi_factor._reconstruct_holdings 从 position 表真实持仓重建

修复前：从 strategy_signal 上一信号日 action ∈ {BUY, HOLD} 重建，
HOLD 把「未持有等回调」误计为持仓 → 隔日 mass-HOLD/mass-SELL 振荡
（见 策略振荡-backtest对照 归档）。
修复后：position 表主账户 quantity>0 的真实持仓，与 Go ExecuteDay ledger.positions 同口径。
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.tables import Account, Base, Position, StrategySignal
from app.strategies.multi_factor import MultiFactorStrategy


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_strategy(db):
    return MultiFactorStrategy({"db": db})


def test_holdings_from_position_table(db):
    """持仓 = 主账户 position 表 quantity>0 的真实持仓（quantity=0 不计）"""
    db.add(Account(id=1, name="主账户", cash=100000))
    db.add(Position(id=1, account_id=1, code="600001", quantity=200, available_qty=200))
    db.add(Position(id=2, account_id=1, code="600002", quantity=0, available_qty=0))
    db.commit()
    strat = make_strategy(db)
    assert strat._reconstruct_holdings(date(2026, 8, 28)) == {"600001"}


def test_holdings_ignore_historical_signals(db):
    """不再从 strategy_signal 重建：旧 BUY/HOLD 信号不影响持仓"""
    db.add(Account(id=1, name="主账户", cash=100000))
    db.add(StrategySignal(id=1, strategy_name="multi_factor", code="600003",
                          trade_date=date(2026, 8, 27), action="HOLD",
                          score=60))
    db.commit()
    strat = make_strategy(db)
    assert strat._reconstruct_holdings(date(2026, 8, 28)) == set()


def test_holdings_primary_account_min_id(db):
    """主账户 = id 最小者（Go GetPrimary 同口径）；仅主账户持仓计入"""
    db.add(Account(id=2, name="次账户", cash=50000))
    db.add(Account(id=1, name="主账户", cash=100000))
    db.add(Position(id=3, account_id=2, code="600004", quantity=300, available_qty=300))
    db.add(Position(id=4, account_id=1, code="600005", quantity=100, available_qty=100))
    db.commit()
    strat = make_strategy(db)
    assert strat._reconstruct_holdings(date(2026, 8, 28)) == {"600005"}


def test_holdings_no_account(db):
    """无账户记录 → 空持仓"""
    strat = make_strategy(db)
    assert strat._reconstruct_holdings(date(2026, 8, 28)) == set()
