"""data_source 单测：闸门关=本地逐字一致；闸门开=走 datahub（fake client）。

闸门开的用例一律传 `_BoomDB()`（execute 即抛）——证明闸门开时**不查本地库**。
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config, data_source
from app.datahub_client import DatahubError
from app.models.tables import Base, TradeCalendar

OPEN = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5),
        date(2026, 8, 6), date(2026, 8, 7)]
CLOSED = date(2026, 8, 8)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for d in OPEN:
        session.add(TradeCalendar(cal_date=d, is_open=True))
    session.add(TradeCalendar(cal_date=CLOSED, is_open=False))
    session.commit()
    return session


class _BoomDB:
    def execute(self, *a, **k):
        raise AssertionError("闸门开时不应查本地库")


class FakeClient:
    """按 [start, end] 过滤固定日历；记录调用。"""

    def __init__(self, dates, fail=None):
        self.dates = sorted(dates)
        self.fail = fail
        self.calls = []

    def fetch(self, dataset_id, params=None):
        self.calls.append((dataset_id, dict(params or {})))
        if self.fail is not None:
            raise self.fail
        p = params or {}
        start = date.fromisoformat(p["start"]) if p.get("start") else None
        end = date.fromisoformat(p["end"]) if p.get("end") else None
        return [{"cal_date": d.isoformat(), "is_open": True, "exchange": "SSE"}
                for d in self.dates
                if (start is None or d >= start) and (end is None or d <= end)]


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("DATAHUB_READ_DATASETS", "trade_calendar")
    monkeypatch.setenv("DATAHUB_BASE_URL", "http://dh:8100")
    monkeypatch.setenv("DATAHUB_TOKEN", "tok")


# ---------- 闸门判定 ----------

def test_gate_off_by_default():
    assert config.datahub_read_enabled("trade_calendar") is False


def test_gate_requires_dataset_in_whitelist(monkeypatch):
    monkeypatch.setenv("DATAHUB_TOKEN", "tok")
    monkeypatch.setenv("DATAHUB_READ_DATASETS", "other")
    assert config.datahub_read_enabled("trade_calendar") is False


# ---------- 闸门关：本地逐字一致 ----------

def test_cal_dates_local_matches_sql(db):
    assert data_source.cal_dates(date(2026, 8, 3), date(2026, 8, 7), db=db) == OPEN


def test_cal_dates_local_range_excludes_outside(db):
    assert data_source.cal_dates(date(2026, 8, 4), date(2026, 8, 6), db=db) == [
        date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)]


def test_is_open_local(db):
    assert data_source.is_open(date(2026, 8, 3), db=db) is True
    assert data_source.is_open(CLOSED, db=db) is False       # 有记录但休市
    assert data_source.is_open(date(2026, 8, 9), db=db) is False  # 无记录


def test_recent_open_days_local_desc_limit(db):
    assert data_source.recent_open_days(date(2026, 8, 7), 3, db=db) == [
        date(2026, 8, 7), date(2026, 8, 6), date(2026, 8, 5)]


# ---------- 闸门开：走 datahub ----------

def test_cal_dates_remote(gate_on, monkeypatch):
    fake = FakeClient(OPEN)
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    got = data_source.cal_dates(date(2026, 8, 3), date(2026, 8, 7), db=_BoomDB())
    assert got == OPEN
    assert fake.calls[0][0] == "trade_calendar"
    assert fake.calls[0][1]["is_open"] == "true"


def test_is_open_remote(gate_on, monkeypatch):
    fake = FakeClient(OPEN)
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    assert data_source.is_open(date(2026, 8, 3), db=_BoomDB()) is True
    assert data_source.is_open(CLOSED, db=_BoomDB()) is False
    assert data_source.is_open(date(2026, 8, 9), db=_BoomDB()) is False


def test_recent_open_days_remote_widens_window(gate_on, monkeypatch):
    # 60 个连续交易日、limit=50 → 45 天窗口（46 行）不足 → 翻倍到 90 天
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(60)]
    fake = FakeClient(days)
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    got = data_source.recent_open_days(days[-1], 50, db=_BoomDB())
    assert got == sorted(days, reverse=True)[:50]
    assert len(fake.calls) == 2  # 45 → 90 两轮


def test_fail_loud_without_fallback(gate_on, monkeypatch):
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeClient([], fail=DatahubError("down")))
    with pytest.raises(DatahubError):
        data_source.cal_dates(date(2026, 8, 3), date(2026, 8, 7), db=_BoomDB())


def test_fallback_local_on_error(gate_on, monkeypatch, db):
    monkeypatch.setenv("DATAHUB_FALLBACK_LOCAL", "1")
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeClient([], fail=DatahubError("down")))
    assert data_source.cal_dates(date(2026, 8, 3), date(2026, 8, 7), db=db) == OPEN
