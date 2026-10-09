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
from app.models.tables import Base, StockBasic, TradeCalendar

OPEN = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5),
        date(2026, 8, 6), date(2026, 8, 7)]
CLOSED = date(2026, 8, 8)

# stock_basic 种子（同时用于本地建表 + 远端 fake）
STOCKS = [
    {"code": "000001", "name": "平安银行", "market": "SZ", "industry": "银行",
     "list_date": "1991-04-03", "status": "L", "universe": "hs300", "data_scope": "a_share"},
    {"code": "600000", "name": "浦发银行", "market": "SH", "industry": "银行",
     "list_date": "1999-11-10", "status": "L", "universe": "zz500", "data_scope": "a_share"},
    {"code": "600519", "name": "贵州茅台", "market": "SH", "industry": "白酒",
     "list_date": "2001-08-27", "status": "L", "universe": "hs300", "data_scope": "a_share"},
    {"code": "920000", "name": "退市示例", "market": "BJ", "industry": None,
     "list_date": None, "status": "D", "universe": None, "data_scope": None},
    {"code": "300750", "name": "未来上市", "market": "SZ", "industry": "电池",
     "list_date": "2027-01-01", "status": "L", "universe": None, "data_scope": "a_share"},
]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for d in OPEN:
        session.add(TradeCalendar(cal_date=d, is_open=True))
    session.add(TradeCalendar(cal_date=CLOSED, is_open=False))
    for s in STOCKS:
        session.add(StockBasic(
            code=s["code"], name=s["name"], market=s["market"], industry=s["industry"],
            list_date=date.fromisoformat(s["list_date"]) if s["list_date"] else None,
            status=s["status"], universe=s["universe"], data_scope=s["data_scope"]))
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


# ---------- stock_basic ----------

class FakeStockClient:
    """按 codes/market/universe/scope 过滤固定股票行；记录调用。"""

    def __init__(self, rows=None, fail=None):
        self.rows = [dict(r) for r in (rows if rows is not None else STOCKS)]
        self.fail = fail
        self.calls = []

    def fetch(self, dataset_id, params=None):
        self.calls.append((dataset_id, dict(params or {})))
        if self.fail is not None:
            raise self.fail
        p = params or {}

        def in_filter(key, col):
            want = [x.strip() for x in str(p.get(key, "")).split(",") if x.strip()]
            return (lambda r: r[col] in want) if want else (lambda r: True)

        preds = [in_filter("codes", "code"), in_filter("market", "market"),
                 in_filter("universe", "universe"), in_filter("scope", "data_scope")]
        return [dict(r) for r in self.rows if all(f(r) for f in preds)]


@pytest.fixture
def gate_on_stock(monkeypatch):
    monkeypatch.setenv("DATAHUB_READ_DATASETS", "trade_calendar,stock_basic")
    monkeypatch.setenv("DATAHUB_BASE_URL", "http://dh:8100")
    monkeypatch.setenv("DATAHUB_TOKEN", "tok")


def test_pool_codes_local(db):
    assert data_source.pool_codes(db) == ["000001", "600000", "600519"]


def test_pool_codes_remote(gate_on_stock, monkeypatch):
    fake = FakeStockClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    assert data_source.pool_codes(_BoomDB()) == ["000001", "600000", "600519"]
    assert fake.calls[0][0] == "stock_basic"
    assert fake.calls[0][1]["universe"] == "hs300,zz500"


def test_names_by_codes_local(db):
    assert data_source.names_by_codes(db, ["000001", "600519", "999999"]) == {
        "000001": "平安银行", "600519": "贵州茅台"}


def test_names_by_codes_remote(gate_on_stock, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakeStockClient())
    assert data_source.names_by_codes(_BoomDB(), ["000001", "600519", "999999"]) == {
        "000001": "平安银行", "600519": "贵州茅台"}


def test_names_by_codes_empty_short_circuits(db, gate_on_stock, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client",
                        lambda: FakeStockClient(fail=AssertionError("不应调用")))
    assert data_source.names_by_codes(db, []) == {}


def test_industries_by_codes(db):
    assert data_source.industries_by_codes(db, ["000001", "600519", "920000"]) == {
        "000001": "银行", "600519": "白酒"}


def test_a_share_listed_codes_local(db):
    assert sorted(data_source.a_share_listed_codes(db, date(2026, 1, 1))) == [
        "000001", "600000", "600519"]  # 300750 未上市(2027) / 920000 退市+非 a_share


def test_a_share_listed_codes_remote(gate_on_stock, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakeStockClient())
    assert sorted(data_source.a_share_listed_codes(_BoomDB(), date(2026, 1, 1))) == [
        "000001", "600000", "600519"]


def test_a_share_listed_count_local(db):
    assert data_source.a_share_listed_count(db, date(2026, 1, 1)) == 3


def test_a_share_listed_count_remote(gate_on_stock, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakeStockClient())
    assert data_source.a_share_listed_count(_BoomDB(), date(2026, 1, 1)) == 3


def test_pool_code_dates_local(db):
    assert data_source.pool_code_dates(db) == [
        ("000001", date(1991, 4, 3)),
        ("600000", date(1999, 11, 10)),
        ("600519", date(2001, 8, 27)),
    ]


def test_a_share_code_dates_remote(gate_on_stock, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakeStockClient())
    assert sorted(data_source.a_share_code_dates(_BoomDB(), date(2026, 1, 1))) == [
        ("000001", date(1991, 4, 3)),
        ("600000", date(1999, 11, 10)),
        ("600519", date(2001, 8, 27)),
    ]


def test_stock_fail_loud_without_fallback(gate_on_stock, monkeypatch):
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeStockClient(fail=DatahubError("down")))
    with pytest.raises(DatahubError):
        data_source.pool_codes(_BoomDB())


def test_stock_fallback_local_on_error(gate_on_stock, monkeypatch, db):
    monkeypatch.setenv("DATAHUB_FALLBACK_LOCAL", "1")
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeStockClient(fail=DatahubError("down")))
    assert data_source.pool_codes(db) == ["000001", "600000", "600519"]
