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
from app.models.tables import Base, DailyPrice, DailyValuation, FinancialIndicator, \
    StockBasic, TradeCalendar

OPEN = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5),
        date(2026, 8, 6), date(2026, 8, 7)]
CLOSED = date(2026, 8, 8)

# daily_price 种子（本地 + 远端 fake 共用）：(code, trade_date, close, adj_factor, open)
PRICE = [
    ("000001", date(2026, 8, 3), 10.0, 1.0, 9.9),
    ("000001", date(2026, 8, 4), 10.2, 1.0, 10.1),
    ("600000", date(2026, 8, 3), 8.0, 1.0, 7.9),
    ("600519", date(2026, 8, 3), 1500.0, 2.0, 1490.0),
]

# daily_valuation 种子（本地建表 + 远端 fake 共用）：(code, trade_date, pe_ttm, pb)
VAL = [
    ("000001", date(2026, 8, 3), 5.0, 0.80),
    ("000001", date(2026, 8, 4), 5.1, 0.81),
    ("600519", date(2026, 8, 3), 30.0, 8.00),
]

# financial_indicator 种子（本地 + 远端 fake 共用）：
# (code, report_date, announce_date, roe, debt_ratio)
FIN = [
    ("000001", date(2026, 3, 31), date(2026, 4, 25), 11.0, 90.0),
    ("000001", date(2026, 6, 30), date(2026, 7, 20), 12.5, 89.5),
    ("600519", date(2026, 6, 30), date(2026, 7, 15), 30.0, 20.0),
]

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
    for i, (c, d, pe, pb) in enumerate(VAL):
        session.add(DailyValuation(id=i + 1, code=c, trade_date=d, pe_ttm=pe, pb=pb))
    for i, (c, d, close, adj, op) in enumerate(PRICE):
        session.add(DailyPrice(id=i + 1, code=c, trade_date=d, close=close,
                               adj_factor=adj, open=op))
    for i, (c, rd, ad, roe, debt) in enumerate(FIN):
        session.add(FinancialIndicator(id=i + 1, code=c, report_date=rd,
                                       announce_date=ad, roe=roe, debt_ratio=debt))
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


# ---------- daily_valuation ----------

class FakeValuationClient:
    """按 codes/start/end 过滤固定估值行，按 fields 投影；latest_trade_date 返回固定值。"""

    def __init__(self, rows=None, latest=None, fail=None):
        self.rows = [dict(code=c, trade_date=d, pe_ttm=pe, pb=pb)
                     for c, d, pe, pb in (rows if rows is not None else VAL)]
        self.latest = latest
        self.fail = fail
        self.calls = []

    def fetch(self, dataset_id, params=None):
        self.calls.append((dataset_id, dict(params or {})))
        if self.fail is not None:
            raise self.fail
        p = params or {}
        if dataset_id == "latest_trade_date":
            return [{"trade_date": self.latest.isoformat()}] if self.latest else []
        if dataset_id == "dup_keys":
            return []
        want = [x.strip() for x in str(p.get("codes", "")).split(",") if x.strip()]
        start = date.fromisoformat(p["start"]) if p.get("start") else None
        end = date.fromisoformat(p["end"]) if p.get("end") else None
        fields = [c.strip() for c in str(p.get("fields", "")).split(",") if c.strip()]
        out = []
        for r in self.rows:
            td = r["trade_date"]
            if want and r["code"] not in want:
                continue
            if (start and td < start) or (end and td > end):
                continue
            full = {"code": r["code"], "trade_date": td.isoformat(),
                    "pe_ttm": r["pe_ttm"], "pb": r["pb"]}
            out.append({k: full[k] for k in fields})
        return out


@pytest.fixture
def gate_on_val(monkeypatch):
    monkeypatch.setenv("DATAHUB_READ_DATASETS", "daily_valuation")
    monkeypatch.setenv("DATAHUB_BASE_URL", "http://dh:8100")
    monkeypatch.setenv("DATAHUB_TOKEN", "tok")


def test_valuation_rows_local(db):
    rows = data_source.valuation_rows(
        db, ["000001"], date(2026, 8, 3), date(2026, 8, 4))
    assert [(c, td) for c, td, _, _ in rows] == [
        ("000001", date(2026, 8, 3)), ("000001", date(2026, 8, 4))]
    assert float(rows[0][2]) == 5.0 and float(rows[0][3]) == 0.80


def test_valuation_rows_remote(gate_on_val, monkeypatch):
    fake = FakeValuationClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    rows = data_source.valuation_rows(
        _BoomDB(), ["000001"], date(2026, 8, 3), date(2026, 8, 4))
    assert rows == [("000001", date(2026, 8, 3), 5.0, 0.80),
                    ("000001", date(2026, 8, 4), 5.1, 0.81)]
    assert fake.calls[0][0] == "daily_valuation"
    assert fake.calls[0][1]["fields"] == "code,trade_date,pe_ttm,pb"


def test_valuation_rows_empty_codes_short_circuits(db, gate_on_val, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client",
                        lambda: FakeValuationClient(fail=AssertionError("不应调用")))
    assert data_source.valuation_rows(db, [], date(2026, 8, 3), date(2026, 8, 4)) == []


def test_valuation_latest_local(db):
    assert data_source.valuation_latest(db) == date(2026, 8, 4)


def test_valuation_latest_remote(gate_on_val, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client",
                        lambda: FakeValuationClient(latest=date(2026, 8, 4)))
    assert data_source.valuation_latest(_BoomDB()) == date(2026, 8, 4)


def test_valuation_latest_remote_empty(gate_on_val, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakeValuationClient())
    assert data_source.valuation_latest(_BoomDB()) is None


def test_dup_keys_local_counts_groups(db):
    assert data_source.dup_keys(db, "daily_valuation") == 0
    db.add(DailyValuation(id=99, code="000001", trade_date=date(2026, 8, 3),
                          pe_ttm=1.0, pb=1.0))
    db.commit()
    assert data_source.dup_keys(db, "daily_valuation") == 1


def test_dup_keys_remote(gate_on_val, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakeValuationClient())
    assert data_source.dup_keys(_BoomDB(), "daily_valuation") == 0


def test_dup_keys_remote_forwards_window(gate_on_val, monkeypatch):
    # 近窗 start/end 透传到 datahub（避免全表 GROUP BY 超时）
    fake = FakeValuationClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    data_source.dup_keys(_BoomDB(), "daily_valuation",
                         start=date(2026, 9, 1), end=date(2026, 9, 30))
    assert fake.calls == [("dup_keys", {"table": "daily_valuation",
                                        "start": "2026-09-01", "end": "2026-09-30"})]


def test_dup_keys_local_window_filters(db):
    # 本地回退同窗过滤：窗口内外的重复各一，全窗计 2、近窗计 1
    db.add_all([
        DailyValuation(id=98, code="000001", trade_date=date(2026, 8, 3), pe_ttm=1.0, pb=1.0),
        DailyValuation(id=99, code="000001", trade_date=date(2026, 8, 3), pe_ttm=1.0, pb=1.0),
        DailyValuation(id=100, code="000002", trade_date=date(2026, 9, 3), pe_ttm=1.0, pb=1.0),
        DailyValuation(id=101, code="000002", trade_date=date(2026, 9, 3), pe_ttm=1.0, pb=1.0),
    ])
    db.commit()
    assert data_source.dup_keys(db, "daily_valuation") == 2
    assert data_source.dup_keys(db, "daily_valuation",
                                start=date(2026, 9, 1), end=date(2026, 9, 30)) == 1


def test_valuation_fail_loud_without_fallback(gate_on_val, monkeypatch):
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeValuationClient(fail=DatahubError("down")))
    with pytest.raises(DatahubError):
        data_source.valuation_rows(_BoomDB(), ["000001"],
                                   date(2026, 8, 3), date(2026, 8, 4))


def test_valuation_fallback_local_on_error(gate_on_val, monkeypatch, db):
    monkeypatch.setenv("DATAHUB_FALLBACK_LOCAL", "1")
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeValuationClient(fail=DatahubError("down")))
    assert len(data_source.valuation_rows(db, ["000001"],
                                          date(2026, 8, 3), date(2026, 8, 4))) == 2


# ---------- financial_indicator ----------

class FakeFinancialClient:
    """按 codes/as_of 过滤固定财务行，支持 last 窗与 fields 投影；记录调用。"""

    def __init__(self, rows=None, fail=None):
        self.rows = [dict(code=c, report_date=rd, announce_date=ad,
                          roe=roe, debt_ratio=debt)
                     for c, rd, ad, roe, debt in (rows if rows is not None else FIN)]
        self.fail = fail
        self.calls = []

    def fetch(self, dataset_id, params=None):
        self.calls.append((dataset_id, dict(params or {})))
        if self.fail is not None:
            raise self.fail
        p = params or {}
        want = [x.strip() for x in str(p.get("codes", "")).split(",") if x.strip()]
        as_of = date.fromisoformat(p["as_of"]) if p.get("as_of") else None
        last = int(p["last"]) if p.get("last") else None
        fields = [c.strip() for c in str(p.get("fields", "")).split(",") if c.strip()]
        out = [r for r in self.rows
               if (not want or r["code"] in want)
               and (as_of is None or r["announce_date"] <= as_of)]
        out.sort(key=lambda r: (r["code"], r["report_date"]))
        if last:
            # 每 code：announce_date/report_date 降序前 last 行，再升序回填
            per: dict[str, list] = {}
            for r in out:
                per.setdefault(r["code"], []).append(r)
            out = []
            for code in sorted(per):
                top = sorted(per[code],
                             key=lambda r: (r["announce_date"], r["report_date"]),
                             reverse=True)[:last]
                out.extend(sorted(top, key=lambda r: r["report_date"]))
        out = [{k: r[k] for k in fields} for r in out]
        return out


@pytest.fixture
def gate_on_fin(monkeypatch):
    monkeypatch.setenv("DATAHUB_READ_DATASETS", "financial_indicator")
    monkeypatch.setenv("DATAHUB_BASE_URL", "http://dh:8100")
    monkeypatch.setenv("DATAHUB_TOKEN", "tok")


def test_financial_rows_local_as_of(db):
    rows = data_source.financial_rows(db, ["000001"], as_of=date(2026, 5, 1))
    assert [(c, rd, ad) for c, rd, ad, _, _ in rows] == [
        ("000001", date(2026, 3, 31), date(2026, 4, 25))]
    assert float(rows[0][3]) == 11.0 and float(rows[0][4]) == 90.0


def test_financial_rows_remote(gate_on_fin, monkeypatch):
    fake = FakeFinancialClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    rows = data_source.financial_rows(_BoomDB(), ["000001"],
                                      as_of=date(2026, 7, 25))
    assert rows == [("000001", date(2026, 3, 31), date(2026, 4, 25), 11.0, 90.0),
                    ("000001", date(2026, 6, 30), date(2026, 7, 20), 12.5, 89.5)]
    assert fake.calls[0][0] == "financial_indicator"
    assert fake.calls[0][1]["fields"] == "code,report_date,announce_date,roe,debt_ratio"
    assert fake.calls[0][1]["as_of"] == "2026-07-25"


def test_financial_rows_empty_codes_short_circuits(db, gate_on_fin, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client",
                        lambda: FakeFinancialClient(fail=AssertionError("不应调用")))
    assert data_source.financial_rows(db, [], as_of=date(2026, 7, 25)) == []


def test_pool_latest_announce_local(db):
    assert data_source.pool_latest_announce(db) == {
        "000001": date(2026, 7, 20), "600519": date(2026, 7, 15)}


def test_pool_latest_announce_remote(gate_on_fin, monkeypatch):
    fake = FakeFinancialClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    assert data_source.pool_latest_announce(_BoomDB(), ["000001", "600519"]) == {
        "000001": date(2026, 7, 20), "600519": date(2026, 7, 15)}
    assert fake.calls[0][0] == "financial_indicator"
    assert fake.calls[0][1]["last"] == "1"
    assert fake.calls[0][1]["fields"] == "code,announce_date"


def test_financial_fail_loud_without_fallback(gate_on_fin, monkeypatch):
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeFinancialClient(fail=DatahubError("down")))
    with pytest.raises(DatahubError):
        data_source.financial_rows(_BoomDB(), ["000001"], as_of=date(2026, 7, 25))


def test_financial_fallback_local_on_error(gate_on_fin, monkeypatch, db):
    monkeypatch.setenv("DATAHUB_FALLBACK_LOCAL", "1")
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeFinancialClient(fail=DatahubError("down")))
    assert len(data_source.financial_rows(db, ["000001"],
                                          as_of=date(2026, 7, 25))) == 2


# ---------- daily_price ----------

class FakePriceClient:
    """按 codes/start/end/date/last/fields 过滤固定行情行；另管 latest_trade_date/
    market_ready/dup_keys 三小数据集。列序按 fields 投影。"""

    def __init__(self, rows=None, latest=None, ready=True, fail=None):
        self.rows = [dict(code=c, trade_date=d, close=close, adj_factor=adj, open=op)
                     for c, d, close, adj, op in (rows if rows is not None else PRICE)]
        self.latest = latest
        self.ready = ready
        self.fail = fail
        self.calls = []

    def fetch(self, dataset_id, params=None):
        self.calls.append((dataset_id, dict(params or {})))
        if self.fail is not None:
            raise self.fail
        p = params or {}
        if dataset_id == "latest_trade_date":
            b = date.fromisoformat(p["before"]) if p.get("before") else None
            if b is not None:  # 从 rows 派生「非指数、< before 的最大日」
                ds = [r["trade_date"] for r in self.rows
                      if not r["code"].startswith("sh") and r["trade_date"] < b]
                return [{"trade_date": max(ds).isoformat()}] if ds else []
            return [{"trade_date": self.latest.isoformat()}] if self.latest else []
        if dataset_id == "daily_price_days":
            start = date.fromisoformat(p["start"]) if p.get("start") else None
            end = date.fromisoformat(p["end"]) if p.get("end") else None
            excl = str(p.get("exclude_index", "true")).lower() in ("1", "true", "yes", "on")
            ds = sorted({r["trade_date"] for r in self.rows
                         if (not excl or not r["code"].startswith("sh"))
                         and (start is None or r["trade_date"] >= start)
                         and (end is None or r["trade_date"] <= end)})
            return [{"trade_date": d.isoformat()} for d in ds]
        if dataset_id == "market_ready":
            return [{"ready": self.ready, "covered": 3, "total": 3}]
        if dataset_id == "dup_keys":
            return []
        want = [x.strip() for x in str(p.get("codes", "")).split(",") if x.strip()]
        start = date.fromisoformat(p["start"]) if p.get("start") else None
        end = date.fromisoformat(p["end"]) if p.get("end") else None
        exact = date.fromisoformat(p["date"]) if p.get("date") else None
        last = int(p["last"]) if p.get("last") else None
        fields = [c.strip() for c in str(p.get("fields", "")).split(",") if c.strip()]
        out = [r for r in self.rows if not want or r["code"] in want]
        if exact is not None:
            out = [r for r in out if r["trade_date"] == exact]
        else:
            out = [r for r in out
                   if (start is None or r["trade_date"] >= start)
                   and (end is None or r["trade_date"] <= end)]
        out.sort(key=lambda r: (r["code"], r["trade_date"]))
        if last:
            per: dict[str, list] = {}
            for r in out:
                per.setdefault(r["code"], []).append(r)
            out = []
            for code in sorted(per):
                top = sorted(per[code], key=lambda r: r["trade_date"], reverse=True)[:last]
                out.extend(sorted(top, key=lambda r: r["trade_date"]))
        full_cols = {"code", "trade_date", "close", "adj_factor", "open"}
        return [{k: (r[k].isoformat() if k == "trade_date" else r[k])
                 for k in fields if k in full_cols} for r in out]


@pytest.fixture
def gate_on_price(monkeypatch):
    monkeypatch.setenv("DATAHUB_READ_DATASETS", "daily_price")
    monkeypatch.setenv("DATAHUB_BASE_URL", "http://dh:8100")
    monkeypatch.setenv("DATAHUB_TOKEN", "tok")


def test_price_rows_local(db):
    rows = data_source.price_rows(
        db, ["000001"], date(2026, 8, 3), date(2026, 8, 4))
    assert [(c, td) for c, td, _, _ in rows] == [
        ("000001", date(2026, 8, 3)), ("000001", date(2026, 8, 4))]
    assert float(rows[0][2]) == 10.0 and float(rows[0][3]) == 1.0


def test_price_rows_remote(gate_on_price, monkeypatch):
    fake = FakePriceClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    rows = data_source.price_rows(
        _BoomDB(), ["000001"], date(2026, 8, 3), date(2026, 8, 4),
        ("code", "trade_date", "close", "adj_factor"))
    assert rows == [("000001", date(2026, 8, 3), 10.0, 1.0),
                    ("000001", date(2026, 8, 4), 10.2, 1.0)]
    assert fake.calls[0][0] == "daily_price"
    assert fake.calls[0][1]["fields"] == "code,trade_date,close,adj_factor"


def test_price_rows_empty_codes_short_circuits(db, gate_on_price, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client",
                        lambda: FakePriceClient(fail=AssertionError("不应调用")))
    assert data_source.price_rows(db, [], date(2026, 8, 3), date(2026, 8, 4)) == []


def test_price_day_local(db):
    rows = data_source.price_day(db, ["600519", "600000"], date(2026, 8, 3),
                                 ("code", "close"))
    assert sorted((c, float(v)) for c, v in rows) == [("600000", 8.0), ("600519", 1500.0)]


def test_price_day_remote(gate_on_price, monkeypatch):
    fake = FakePriceClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    rows = data_source.price_day(_BoomDB(), ["600519"], date(2026, 8, 3), ("code",))
    assert rows == [("600519",)]
    assert fake.calls[0][1]["date"] == "2026-08-03"


def test_price_latest_local(db):
    row = data_source.price_latest(db, "000001", ("code", "trade_date", "close"))
    assert (row[0], row[1]) == ("000001", date(2026, 8, 4))
    assert float(row[2]) == 10.2


def test_price_latest_remote(gate_on_price, monkeypatch):
    fake = FakePriceClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    row = data_source.price_latest(_BoomDB(), "000001", ("code", "trade_date", "close"))
    assert row == ("000001", date(2026, 8, 4), 10.2)
    assert fake.calls[0][1]["last"] == "1"


def test_latest_trade_date_local_algo(db):
    assert data_source.latest_trade_date(db) == date(2026, 8, 4)


def test_latest_trade_date_remote(gate_on_price, monkeypatch):
    fake = FakePriceClient(latest=date(2026, 8, 4))
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    assert data_source.latest_trade_date(_BoomDB()) == date(2026, 8, 4)
    assert fake.calls[0][0] == "latest_trade_date"
    assert fake.calls[0][1]["exclude_index"] == "true"


def test_latest_trade_date_remote_empty(gate_on_price, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakePriceClient())
    assert data_source.latest_trade_date(_BoomDB()) is None


def test_latest_trade_date_before_local_strict(db):
    # 600000/600519 仅 08-03，000001 有 08-03/08-04；before=08-04（严格 <）→ 08-03
    assert data_source.latest_trade_date(db, before=date(2026, 8, 4)) == date(2026, 8, 3)
    # before=08-03 → 更早无数据 → None
    assert data_source.latest_trade_date(db, before=date(2026, 8, 3)) is None


def test_latest_trade_date_before_remote(gate_on_price, monkeypatch):
    fake = FakePriceClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    assert data_source.latest_trade_date(_BoomDB(), before=date(2026, 8, 4)) == \
        date(2026, 8, 3)
    assert fake.calls[0][0] == "latest_trade_date"
    assert fake.calls[0][1] == {"table": "daily_price", "exclude_index": "true",
                                "before": "2026-08-04"}


def test_price_days_local_distinct_asc(db):
    # 区间 [08-03, 08-04] 内有行情的交易日（默认排指数）→ 08-03, 08-04
    assert data_source.price_days(db, date(2026, 8, 3), date(2026, 8, 4)) == [
        date(2026, 8, 3), date(2026, 8, 4)]


def test_price_days_remote(gate_on_price, monkeypatch):
    fake = FakePriceClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    assert data_source.price_days(_BoomDB(), date(2026, 8, 3), date(2026, 8, 4)) == [
        date(2026, 8, 3), date(2026, 8, 4)]
    assert fake.calls[0][0] == "daily_price_days"
    assert fake.calls[0][1] == {"start": "2026-08-03", "end": "2026-08-04",
                                "exclude_index": "true"}


def test_price_days_fail_loud_without_fallback(gate_on_price, monkeypatch):
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakePriceClient(fail=DatahubError("down")))
    with pytest.raises(DatahubError):
        data_source.price_days(_BoomDB(), date(2026, 8, 3), date(2026, 8, 4))


def test_market_ready_local(db):
    assert data_source.market_ready(db, date(2026, 8, 3)) is True   # 3/3 池覆盖
    assert data_source.market_ready(db, date(2026, 8, 4)) is False  # 1/3


def test_market_ready_remote(gate_on_price, monkeypatch):
    fake = FakePriceClient(ready=True)
    monkeypatch.setattr("app.data_source.get_client", lambda: fake)
    assert data_source.market_ready(_BoomDB(), date(2026, 8, 3)) is True
    assert fake.calls[0][0] == "market_ready"
    assert fake.calls[0][1]["date"] == "2026-08-03"


def test_price_dup_keys_remote(gate_on_price, monkeypatch):
    monkeypatch.setattr("app.data_source.get_client", lambda: FakePriceClient())
    assert data_source.dup_keys(_BoomDB(), "daily_price") == 0


def test_price_fail_loud_without_fallback(gate_on_price, monkeypatch):
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakePriceClient(fail=DatahubError("down")))
    with pytest.raises(DatahubError):
        data_source.price_rows(_BoomDB(), ["000001"], date(2026, 8, 3), date(2026, 8, 4))


def test_price_fallback_local_on_error(gate_on_price, monkeypatch, db):
    monkeypatch.setenv("DATAHUB_FALLBACK_LOCAL", "1")
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakePriceClient(fail=DatahubError("down")))
    assert len(data_source.price_rows(db, ["000001"],
                                      date(2026, 8, 3), date(2026, 8, 4))) == 2

