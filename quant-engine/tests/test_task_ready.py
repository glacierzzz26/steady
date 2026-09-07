"""Issue #9-2：generate_signals 数据就绪预检 + 失败 detail 现场

回归：coverage 不足日 calc_factors 跳过 → 19:30 generate_signals 硬跑抛
RuntimeError「无因子数据」误记 failed 且 detail 恒 {}。修复后：
  - factor_ready：因子表无当日行 → 记 skipped 的预判条件
  - signal_failure_snapshot：失败时快照当日因子行数 / active 策略
  - failed_detail：错误类型+消息 + traceback + 交易日，不再空 {}
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, FactorValue, Strategy
from app.tasks import factor_ready, failed_detail, signal_failure_snapshot

TD = date(2026, 9, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return session


def test_factor_ready_false_when_no_rows(db):
    assert factor_ready(db, TD) is False


def test_factor_ready_true_when_rows_exist(db):
    db.add(FactorValue(id=1, code="600519", factor_name="ma_trend",
                       trade_date=TD, value=0.5))
    db.commit()
    assert factor_ready(db, TD) is True


def test_signal_failure_snapshot_counts_and_active(db):
    db.add(FactorValue(id=1, code="600519", factor_name="ma_trend",
                       trade_date=TD, value=0.5))
    db.add(Strategy(id=1, name="m1", status="active"))
    db.commit()
    snap = signal_failure_snapshot(db, TD)
    assert snap["factor_value_rows"] == 1
    assert snap["active_strategy"] == "m1"


def test_signal_failure_snapshot_no_active(db):
    db.add(FactorValue(id=1, code="600519", factor_name="ma_trend",
                       trade_date=TD, value=0.5))
    db.commit()
    assert signal_failure_snapshot(db, TD)["active_strategy"] is None


def test_failed_detail_carries_context():
    try:
        raise RuntimeError("无因子数据，请先运行因子计算")
    except RuntimeError as e:
        d = failed_detail(TD, e, {"factor_value_rows": 0,
                                  "active_strategy": "m1"})
    assert d["trade_date"] == "2026-09-04"
    assert "RuntimeError" in d["error"]
    assert "无因子数据" in d["error"]
    assert "无因子数据" in d["traceback"]
    assert d["factor_value_rows"] == 0
    assert d["active_strategy"] == "m1"


def test_failed_detail_without_td():
    try:
        raise ValueError("boom")
    except ValueError as e:
        d = failed_detail(None, e)
    assert d["trade_date"] is None
    assert "ValueError" in d["error"]
