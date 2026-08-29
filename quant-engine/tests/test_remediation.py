"""自愈 stage2 消费者测试（Issue #4）：repaired → 复检 → 重算 + done/绿卡；仍红 → 流转

覆盖 _process 状态机（迁移 005 契约）：
  repaired + 复检绿 → _recompute 调用 + status='done' + summary['done']
  repaired + 复检绿但重算异常 → 保持 repaired 等下一轮（attempts+1），summary['recompute_failed']
  repaired + 仍红 → attempts+1 回 pending（再走 stage1）
  repaired + 仍红且 attempts≥MAX → failed（红卡升级人工）
飞书卡片走 task_run 去重 + notify_config['remedi'] 开关；本测试不依赖飞书 webhook。
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, DailyPrice, RemediationTask, StockBasic
from app.remediation import MAX_ATTEMPTS, _process

TD = date(2026, 8, 28)
POOL = ["600519", "000001"]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed(session)
    return session


def seed(session, with_bars: bool = True):
    for i, code in enumerate(POOL):
        session.add(StockBasic(code=code, name=f"股{i}", universe="hs300",
                               list_date=date(2020, 1, 1)))
    if with_bars:
        for i, code in enumerate(POOL):
            session.add(DailyPrice(id=i + 1, code=code, trade_date=TD, open=10,
                                   high=10, low=10, close=10, volume=100,
                                   amount=1000.0))
    session.commit()


def _task(db, attempts: int = 0) -> RemediationTask:
    # BigInteger 主键在 sqlite 不自动递增，需显式传 id（与 test_backtest.py 同模式）
    t = RemediationTask(id=1, trade_date=TD, check_name="coverage",
                        status="repaired", attempts=attempts,
                        detail={"missing_codes": [], "repaired_count": 2})
    db.add(t)
    db.commit()
    return t


def _summary() -> dict:
    return {"processed": 1, "done": 0, "requeued": 0,
            "failed": 0, "recompute_failed": 0}


def test_repaired_green_done(db, monkeypatch):
    """复检全绿 → 重算因子/信号 → done"""
    calls = []
    monkeypatch.setattr("app.remediation._recompute",
                        lambda db, td: calls.append((db, td)))
    task = _task(db)
    s = _summary()
    _process(db, task, s)

    assert calls == [(db, TD)]  # 重算按日幂等覆盖
    assert task.status == "done"
    assert task.detail["repaired_count"] == 2
    assert s["done"] == 1


def test_repaired_green_recompute_failure(db, monkeypatch):
    """复检绿但重算异常 → 保持 repaired 等下一轮（attempts+1），不误置 done"""
    def boom(db, td):
        raise RuntimeError("重算失败")

    monkeypatch.setattr("app.remediation._recompute", boom)
    task = _task(db)
    s = _summary()
    _process(db, task, s)

    assert task.status == "repaired"
    assert task.attempts == 1
    assert s["recompute_failed"] == 1
    assert s["done"] == 0


def test_still_red_requeue_pending(db):
    """仍红（attempts+1 < MAX）→ 回 pending 再走 stage1"""
    db.execute(delete(DailyPrice))
    db.commit()
    task = _task(db)  # attempts=0
    s = _summary()
    _process(db, task, s)

    assert task.status == "pending"
    assert task.attempts == 1
    assert s["requeued"] == 1
    assert s["failed"] == 0


def test_still_red_exhaust_attempts_failed(db):
    """仍红且 attempts 达上限 → failed（红卡升级人工）"""
    db.execute(delete(DailyPrice))
    db.commit()
    task = _task(db, attempts=MAX_ATTEMPTS - 1)  # 2+1=3 ≥ 3
    s = _summary()
    _process(db, task, s)

    assert task.status == "failed"
    assert task.attempts == MAX_ATTEMPTS
    assert s["failed"] == 1
    assert s["requeued"] == 0
