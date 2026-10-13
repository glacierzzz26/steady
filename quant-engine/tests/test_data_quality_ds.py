"""data_quality Tier3 读点迁移（Phase 4 增量 B）：**开/关值等价**。

同一份种子过两条路：
- 闸门关 → sqlite 本地（`_check_*` 的既有实现）；
- 闸门开 → fake datahub client（`_BoomDB`，证明**不触本地库**）。

断言 `_check_coverage`（含 prev/prev_bar/suspect）、`_check_missing_days`、
`_check_price_anomalies`（含 prev_close 涨跌幅）逐位相同。
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import data_quality as dq
from app.datahub_client import DatahubError
from app.models.tables import (Base, DailyPrice, StockBasic, TradeCalendar)

TD = date(2026, 8, 21)
PREV = date(2026, 8, 20)
# 池内：600519/600001/000002（两日有 bar）、000004（仅 PREV，做 suspect）；非池 300001
POOLFUL = {"600519", "600001", "000002"}
POOL = {"600519", "600001", "000002", "000004"}
STOCKS = [
    ("600519", "hs300"), ("600001", "hs300"), ("000002", "hs300"),
    ("000004", "hs300"), ("300001", ""),
]
# (code, base)：000002 TD 收 20*1.15=23 → 主板 +15% 越限（warn），其余平盘
PRICE_BASE = [("600519", 1800.0), ("600001", 10.0), ("000002", 20.0),
              ("000004", 30.0), ("300001", 5.0), ("sh000300", 4000.0)]


def _price_rows():
    rows = []
    for code, base in PRICE_BASE:
        rows.append(dict(code=code, trade_date=PREV, open=base, high=base * 1.01,
                         low=base * 0.99, close=base, volume=10000,
                         amount=1_000_000.0))
        if code == "000004":
            continue  # 只有 PREV → TD 缺 bar
        close = base * 1.15 if code == "000002" else base
        rows.append(dict(code=code, trade_date=TD, open=base, high=close * 1.01,
                         low=base * 0.99, close=close, volume=10000,
                         amount=1_000_000.0))
    return rows


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for code, universe in STOCKS:
        s.add(StockBasic(code=code, name=code, universe=universe,
                         list_date=date(2020, 1, 1)))
    for d in (PREV, TD):
        s.add(TradeCalendar(cal_date=d, is_open=True))
    for i, r in enumerate(_price_rows(), 1):
        s.add(DailyPrice(id=i, **r))
    s.commit()
    return s


class _BoomDB:
    def execute(self, *a, **k):
        raise AssertionError("闸门开时不应查本地库")


class FakeDQClient:
    """按种子行服务 stock_basic / daily_price / latest_trade_date / daily_price_days /
    trade_calendar；列序按 fields 投影，trade_date 出 ISO 串。"""

    def __init__(self, prices=None, fail=None):
        self.prices = prices if prices is not None else _price_rows()
        self.stocks = [{"code": c, "name": c, "universe": u} for c, u in STOCKS]
        self.fail = fail
        self.calls = []

    def fetch(self, dataset_id, params=None):
        self.calls.append((dataset_id, dict(params or {})))
        if self.fail is not None:
            raise self.fail
        p = params or {}
        if dataset_id == "trade_calendar":
            return [{"cal_date": d.isoformat(), "is_open": True, "exchange": "SSE"}
                    for d in (PREV, TD)]
        if dataset_id == "stock_basic":
            want = {x for x in str(p.get("universe", "")).split(",") if x}
            return [dict(r) for r in self.stocks
                    if not want or r["universe"] in want]
        if dataset_id == "latest_trade_date":
            b = date.fromisoformat(p["before"]) if p.get("before") else None
            ds = [r["trade_date"] for r in self.prices
                  if not r["code"].startswith("sh") and (b is None or r["trade_date"] < b)]
            return [{"trade_date": max(ds).isoformat()}] if ds else []
        if dataset_id == "daily_price_days":
            start = date.fromisoformat(p["start"]) if p.get("start") else None
            end = date.fromisoformat(p["end"]) if p.get("end") else None
            ds = sorted({r["trade_date"] for r in self.prices
                         if not r["code"].startswith("sh")
                         and (start is None or r["trade_date"] >= start)
                         and (end is None or r["trade_date"] <= end)})
            return [{"trade_date": d.isoformat()} for d in ds]
        if dataset_id == "daily_price":
            want = {x for x in str(p.get("codes", "")).split(",") if x}
            exact = date.fromisoformat(p["date"]) if p.get("date") else None
            fields = [c.strip() for c in str(p.get("fields", "")).split(",") if c.strip()]
            out = [r for r in self.prices
                   if (not want or r["code"] in want)
                   and (exact is None or r["trade_date"] == exact)]
            out.sort(key=lambda r: (r["code"], r["trade_date"]))
            return [{k: (r[k].isoformat() if k == "trade_date" else r[k])
                     for k in fields} for r in out]
        raise AssertionError(f"fake 未实现数据集 {dataset_id}")


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("DATAHUB_READ_DATASETS", "trade_calendar,stock_basic,daily_price")
    monkeypatch.setenv("DATAHUB_BASE_URL", "http://dh:8100")
    monkeypatch.setenv("DATAHUB_TOKEN", "tok")


@pytest.fixture
def fake(monkeypatch):
    client = FakeDQClient()
    monkeypatch.setattr("app.data_source.get_client", lambda: client)
    return client


def test_coverage_gate_off_on_equal(db, gate_on, fake):
    off = dq._check_coverage(db, TD)
    on = dq._check_coverage(_BoomDB(), TD)
    assert on == off
    # 健全性：suspect 抓到只缺 TD 的 000004，覆盖率 3/4
    assert off["metrics"]["with_bar"] == 3 and off["metrics"]["pool"] == 4
    assert off["metrics"]["suspect"] == ["000004"]


def test_missing_days_gate_off_on_equal(db, gate_on, fake):
    off = dq._check_missing_days(db, TD)
    on = dq._check_missing_days(_BoomDB(), TD)
    assert on == off
    assert off["level"] == "ok" and off["metrics"]["missing"] == []


def test_price_anomalies_gate_off_on_equal(db, gate_on, fake):
    off = dq._check_price_anomalies(db, TD)
    on = dq._check_price_anomalies(_BoomDB(), TD)
    assert on == off
    # 健全性：000002 +15% 越主板限 → warn；prev_close 推导生效
    assert off["metrics"]["warn_count"] >= 1 and off["metrics"]["fail_count"] == 0


def test_price_days_fail_loud(db, gate_on, monkeypatch):
    monkeypatch.setattr(
        "app.data_source.get_client",
        lambda: FakeDQClient(fail=DatahubError("down")))
    with pytest.raises(DatahubError):
        dq._check_missing_days(_BoomDB(), TD)
