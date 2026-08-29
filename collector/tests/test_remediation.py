"""自愈 stage1 消费者测试（Issue #4）：pending → 补齐 → repaired / source_blocked / failed

覆盖 _process 状态机（迁移 005 契约）：
  逐只补齐成功 → status='repaired' + detail.repaired_count
  源被限（BaoStock 封禁/黑名单冷却）→ 立即中止整批 + status='source_blocked'，不重试
  瞬时错误 → attempts+1 回 pending（下轮重试剩余）
  瞬时错误且 attempts≥MAX → failed（stage2 红卡升级人工）
DailyCollector 以 monkeypatch 假类注入（真实拉取在采集器测试覆盖，不在此重放网络）。
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, RemediationTask
from app.remediation import MAX_ATTEMPTS, _process

TD = date(2026, 8, 28)
CODES = ["600519", "000001", "000002"]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return session


def _task(db, attempts: int = 0) -> RemediationTask:
    # BigInteger 主键在 sqlite 不自动递增，需显式传 id（与 test_backtest.py 同模式）
    t = RemediationTask(id=1, trade_date=TD, check_name="coverage",
                        status="pending", attempts=attempts,
                        detail={"missing_codes": CODES})
    db.add(t)
    db.commit()
    return t


def _summary() -> dict:
    return {"processed": 1, "repaired": 0, "source_blocked": 0,
            "failed": 0, "requeued": 0}


def _patch_collector(monkeypatch, factory):
    """把 app.collectors.daily.DailyCollector 换成假类（_process 内延迟导入即命中）"""
    monkeypatch.setattr("app.collectors.daily.DailyCollector", factory)


class OkCollector:
    def __init__(self, db):
        self.calls = []

    def fetch(self, code, start, end):
        self.calls.append(code)
        return [{"code": code}]  # 非空 → save

    def save(self, rows):
        return len(rows)  # 自愈按 save 实际入库条数计 repaired_count


def test_all_repaired(db, monkeypatch):
    """全部补齐成功 → repaired + repaired_count"""
    _patch_collector(monkeypatch, OkCollector)
    task = _task(db)
    s = _summary()
    _process(db, task, s)

    assert task.status == "repaired"
    assert task.detail["repaired_count"] == len(CODES)
    assert s["repaired"] == 1


def test_primary_empty_baostock_second_chance(db, monkeypatch):
    """主源链返回空（停牌日东财/新浪无行）→ 补试 BaoStock 单源才成功"""
    calls = []

    class EmptyPrimary:
        def __init__(self, db):
            self.db = db

        def fetch(self, code, start, end):
            calls.append(("primary", code))
            return []  # 空返回不抛异常

        def _fetch_baostock(self, code, start, end):
            calls.append(("baostock", code))
            return [{"code": code}]

        def save(self, rows):
            return len(rows)

    _patch_collector(monkeypatch, EmptyPrimary)
    task = _task(db)
    s = _summary()
    _process(db, task, s)

    assert task.status == "repaired"
    assert task.detail["repaired_count"] == len(CODES)
    # 每只代码都先主源后 BaoStock 各试一次
    assert calls == [(t, c) for c in CODES for t in ("primary", "baostock")]


def test_source_blocked_aborts_batch(db, monkeypatch):
    """源被限（BaoStock 封禁）→ 立即中止整批，不逐只反复轰"""
    class BlockedCollector(OkCollector):
        def fetch(self, code, start, end):
            self.calls.append(code)
            raise RuntimeError("BaoStock 封禁冷却中（900s 后重试）")

    _patch_collector(monkeypatch, BlockedCollector)
    task = _task(db)
    s = _summary()
    _process(db, task, s)

    assert task.status == "source_blocked"
    assert s["source_blocked"] == 1
    # 只试了第一只就中止
    assert len(task.detail.get("failed_codes", [])) == 0
    assert s["requeued"] == 0 and s["failed"] == 0


def test_transient_requeue(db, monkeypatch):
    """瞬时错误 → attempts+1 回 pending（下轮重试剩余）"""
    class FlakyCollector(OkCollector):
        def fetch(self, code, start, end):
            self.calls.append(code)
            raise TimeoutError("connect timed out")

    _patch_collector(monkeypatch, FlakyCollector)
    task = _task(db)  # attempts=0
    s = _summary()
    _process(db, task, s)

    assert task.status == "pending"
    assert task.attempts == 1
    assert set(task.detail["failed_codes"]) == set(CODES)
    assert task.detail["repaired_count"] == 0
    assert s["requeued"] == 1
    assert s["failed"] == 0


def test_transient_exhaust_attempts_failed(db, monkeypatch):
    """瞬时错误且 attempts 达上限 → failed（stage2 红卡升级人工）"""
    class FlakyCollector(OkCollector):
        def fetch(self, code, start, end):
            raise TimeoutError("connect timed out")

    _patch_collector(monkeypatch, FlakyCollector)
    task = _task(db, attempts=MAX_ATTEMPTS - 1)  # 2+1=3 ≥ 3
    s = _summary()
    _process(db, task, s)

    assert task.status == "failed"
    assert task.attempts == MAX_ATTEMPTS
    assert s["failed"] == 1
    assert s["requeued"] == 0


def test_empty_missing_codes_defensive(db, monkeypatch):
    """无缺失清单（防御分支）→ 直接转 repaired 交 stage2 复检定夺"""
    _patch_collector(monkeypatch, OkCollector)
    task = _task(db)
    task.detail = {"missing_codes": []}
    db.add(task)
    db.commit()
    s = _summary()
    _process(db, task, s)

    assert task.status == "repaired"
    assert s["repaired"] == 1
