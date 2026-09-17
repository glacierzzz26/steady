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


# ---------- market_ready：策略就绪闸门（Issue #13 R6）----------

def test_market_ready_reads_universe_only(db):
    """market_ready 只认 universe 池；data_scope 扩到全量不影响它就绪判定（R6）。

    扩池采集 5212 只但策略域仍 800：某日只有 3/3 池股有 bar → ready True，
    即便库里另有大量 data_scope='a_share' 的非池股无 bar。
    """
    from app.models.tables import DailyPrice, StockBasic
    from app.tasks import market_ready

    for i, code in enumerate(["600519", "000001", "000002"]):
        db.add(StockBasic(code=code, name=f"池{i}", market="SH",
                          universe="hs300", data_scope="a_share"))
    # 非池股：采集范围内但 universe=NULL，当日无 bar
    for i, code in enumerate(["002415", "688111", "300750"]):
        db.add(StockBasic(code=code, name=f"非池{i}", market="SZ",
                          data_scope="a_share"))
    pid = 0
    for code in ["600519", "000001", "000002"]:
        pid += 1
        db.add(DailyPrice(id=pid, code=code, trade_date=TD, open=10, high=10,
                          low=10, close=10, volume=100, amount=1000.0))
    db.commit()
    assert market_ready(db, TD) is True   # 3/3 池股有 bar


def test_market_ready_threshold_is_90pct(db):
    """阈值 0.9：10 只池股仅 8 只有 bar → False（锁死阈值不被误改）"""
    from app.models.tables import DailyPrice, StockBasic
    from app.tasks import market_ready

    codes = [f"6000{i:02d}" for i in range(10)]
    for code in codes:
        db.add(StockBasic(code=code, name=code, market="SH", universe="hs300"))
    pid = 0
    for code in codes[:8]:          # 8/10 = 80% < 90%
        pid += 1
        db.add(DailyPrice(id=pid, code=code, trade_date=TD, open=10, high=10,
                          low=10, close=10, volume=100, amount=1000.0))
    db.commit()
    assert market_ready(db, TD) is False


def test_market_ready_9_of_10_passes(db):
    """9/10 = 90% ≥ 0.9 → True（边界）"""
    from app.models.tables import DailyPrice, StockBasic
    from app.tasks import market_ready

    codes = [f"6001{i:02d}" for i in range(10)]
    for code in codes:
        db.add(StockBasic(code=code, name=code, market="SH", universe="hs300"))
    pid = 100
    for code in codes[:9]:
        pid += 1
        db.add(DailyPrice(id=pid, code=code, trade_date=TD, open=10, high=10,
                          low=10, close=10, volume=100, amount=1000.0))
    db.commit()
    assert market_ready(db, TD) is True
