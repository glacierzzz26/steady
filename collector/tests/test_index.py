"""指数行情采集器测试（mock akshare）"""
from datetime import date

import pandas as pd

from app.collectors import index as idx_mod
from app.collectors.index import IndexCollector, build_rows
from tests.helpers import multi_values


def make_index_df():
    return pd.DataFrame({
        "date": ["2026-08-18", "2026-08-19"],
        "open": [4600.0, 4660.0],
        "high": [4650.0, 4674.0],
        "low": [4590.0, 4568.0],
        "close": [4640.0, 4588.0],
        "volume": [24407009000, 24407009001],
    })


def test_build_rows_and_date_filter():
    rows = build_rows("sh000300", make_index_df(),
                      start_date=date(2026, 8, 19))
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "sh000300"
    assert r["trade_date"] == date(2026, 8, 19)
    assert r["close"] == 4588.0
    assert r["adj_factor"] is None


class FakeSession:
    def __init__(self):
        self.executed = []

    def execute(self, stmt):
        self.executed.append(stmt)

    def commit(self):
        pass


def test_save_inserts_index_stock_and_prices():
    from sqlalchemy.dialects.postgresql import dialect as pg_dialect

    db = FakeSession()
    data = build_rows("sh000300", make_index_df())
    IndexCollector(db).save(data)
    # 2 次 execute：stock_basic 伪股票 + daily_price
    assert len(db.executed) == 2
    stock_stmt = db.executed[0]
    stock_row = multi_values(stock_stmt)[0]
    assert stock_row["code"] == "sh000300"
    assert stock_row["market"] == "INDEX"
    assert stock_row["name"] == "沪深300"
    sql = str(db.executed[1].compile(dialect=pg_dialect()))
    assert "ON CONFLICT (code, trade_date) DO UPDATE" in sql


# ---------- 阶段 2：BaoStock 主源 + 同日回退 ----------

def _bs_index_rows(max_date):
    return [{"code": "sh000300", "trade_date": max_date, "open": 4600.0,
             "high": 4650.0, "low": 4590.0, "close": 4640.0,
             "volume": 100, "amount": 1e9, "adj_factor": None}]


def test_fetch_baostock_branch(monkeypatch):
    """BAOSTOCK_SOURCES 含 index 且当日已出 → 直接返回 BaoStock 指数行"""
    today = date.today()
    rows = _bs_index_rows(today)
    monkeypatch.setattr(idx_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(idx_mod.baostock, "get_session", lambda: object())
    monkeypatch.setattr(idx_mod.baostock, "index_rows",
                        lambda sess, symbol, s, e: rows)
    got = IndexCollector(None).fetch("sh000300")
    assert got == rows


def test_fetch_baostock_same_day_fallback(monkeypatch):
    """同日回退：BaoStock 当日未出（max < end）→ 降级 Tushare → AkShare"""
    from datetime import timedelta

    today = date.today()
    monkeypatch.setattr(idx_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(idx_mod.baostock, "get_session", lambda: object())
    monkeypatch.setattr(idx_mod.baostock, "index_rows",
                        lambda sess, symbol, s, e: _bs_index_rows(today - timedelta(days=1)))
    monkeypatch.setattr(idx_mod.tushare, "make_pro", lambda db: None)
    monkeypatch.setattr(idx_mod.ak, "stock_zh_index_daily", lambda symbol: make_index_df())
    rows = IndexCollector(None).fetch("sh000300")
    assert rows[0]["code"] == "sh000300"
    assert rows[0]["trade_date"] == date(2026, 8, 18)  # 落到 AkShare
