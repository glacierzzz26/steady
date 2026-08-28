"""日行情采集器测试（mock akshare）"""
from datetime import date

import pandas as pd

from app.collectors import daily as daily_mod
from app.collectors.daily import (DailyCollector, build_rows, fetch_pair,
                                  normalize_sina, sina_symbol)
from tests.helpers import multi_values


def make_hist(closes=(10.0, 10.5, 11.0), start="2026-08-01"):
    dates = pd.date_range(start, periods=len(closes), freq="D")
    n = len(closes)
    return pd.DataFrame({
        "日期": [d.strftime("%Y-%m-%d") for d in dates],
        "开盘": closes,
        "最高": [c + 0.5 for c in closes],
        "最低": [c - 0.5 for c in closes],
        "收盘": closes,
        "成交量": [10000 + 2000 * i for i in range(n)],
        "成交额": [1e8 * (1 + 0.1 * i) for i in range(n)],
    })


def test_build_rows_adj_factor_and_prev_close():
    raw = make_hist()
    hfq = make_hist(closes=(62.6, 65.7, 68.9))
    rows = build_rows("600519", raw, hfq)
    assert len(rows) == 3
    r0, r1 = rows[0], rows[1]
    # 复权因子 = 后复权收盘 / 不复权收盘
    assert r0["adj_factor"] == 6.26
    # prev_close 由序列内前一行收盘推出
    assert r1["prev_close"] == 10.0
    assert r0["prev_close"] is None
    # 日期转成 date 对象
    assert r0["trade_date"] == date(2026, 8, 1)


def test_build_rows_empty_raw():
    assert build_rows("600519", pd.DataFrame(), pd.DataFrame()) == []


def test_build_rows_missing_hfq():
    """后复权缺失时 adj_factor 为 None，但行情照常入库"""
    rows = build_rows("600519", make_hist(), pd.DataFrame())
    assert len(rows) == 3
    assert all(r["adj_factor"] is None for r in rows)


class FakeSession:
    def __init__(self):
        self.executed = []

    def execute(self, stmt):
        self.executed.append(stmt)

    def commit(self):
        pass


def test_fetch_calls_both_adjusts(monkeypatch):
    calls = []

    def fake_hist(symbol, period, start_date, end_date, adjust):
        calls.append(adjust)
        assert symbol == "600519"
        return make_hist()

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", fake_hist)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert calls == ["", "hfq"]
    assert len(rows) == 3


def test_baostock_pure_factor_from_baostock(monkeypatch):
    """阶段 3 纯 BaoStock：adj_factor 直接来自 BaoStock 派生因子；守卫通过；
    窗口 start−7 取守卫上下文（此处 08-01 之前无行，恒因子无对）"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())
    bs_calls = []

    def fake_pairs(sess, code, s, e):
        bs_calls.append((s, e))
        return (make_hist(closes=(10.0, 10.5, 11.0)),
                make_hist(closes=(62.6, 65.73, 68.86)))  # 恒因子 6.26

    monkeypatch.setattr(daily_mod.baostock, "daily_pairs", fake_pairs)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert len(rows) == 3
    # 因子来自 BaoStock 派生（hfq/raw = 6.26）
    assert [r["adj_factor"] for r in rows] == [6.26, 6.26, 6.26]
    assert [r["close"] for r in rows] == [10.0, 10.5, 11.0]
    # 上下文窗口 = start − 7 天
    assert bs_calls == [(date(2026, 7, 25), "2026-08-20")]


def test_baostock_guard_reject_falls_to_akshare(monkeypatch):
    """阶段 3 守卫拒收（平安型：因子阶跃 16.7% 但 close 反涨）→ 整段降级 AkShare，
    不落 BaoStock 派生因子"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())
    raw = pd.DataFrame({
        "日期": ["2026-08-01", "2026-08-02"],
        "开盘": [10.0, 10.5], "最高": [10.5, 11.0],
        "最低": [9.5, 10.0], "收盘": [10.0, 10.5],
        "成交量": [10000, 12000], "成交额": [1e8, 1.2e8],
    })
    hfq = pd.DataFrame({"日期": ["2026-08-01", "2026-08-02"],
                        "收盘": [20.0, 24.507]})  # 因子 2.0 → 2.334（+16.7%）
    monkeypatch.setattr(daily_mod.baostock, "daily_pairs",
                        lambda sess, code, s, e: (raw, hfq))
    calls = []

    def fake_hist(symbol, period, start_date, end_date, adjust):
        calls.append(adjust)
        if adjust == "hfq":
            return make_hist(closes=(62.6, 65.73, 68.86))  # 恒因子 6.26
        return make_hist()

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", fake_hist)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert calls == ["", "hfq"]  # 一路降到 AkShare
    assert len(rows) == 3
    assert [r["adj_factor"] for r in rows] == [6.26, 6.26, 6.26]


def test_baostock_context_rows_dropped(monkeypatch):
    """守卫上下文行（start−7 内、trade_date < start）不入库"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())
    # 5 行：07-28..08-01（恒因子 6.26，无变化对）
    raw = make_hist(closes=(10.0, 10.5, 11.0, 11.5, 12.0), start="2026-07-28")
    hfq = make_hist(closes=(62.6, 65.73, 68.86, 71.99, 75.12), start="2026-07-28")
    monkeypatch.setattr(daily_mod.baostock, "daily_pairs",
                        lambda sess, code, s, e: (raw, hfq))
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert len(rows) == 1
    assert rows[0]["trade_date"] == date(2026, 8, 1)


def test_sina_symbol():
    assert sina_symbol("600519") == "sh600519"
    assert sina_symbol("000001") == "sz000001"
    assert sina_symbol("300750") == "sz300750"
    assert sina_symbol("830001") == "bj830001"


def test_normalize_sina_volume_to_lots():
    df = pd.DataFrame({"date": ["2026-08-19"], "open": [10], "high": [11],
                       "low": [9], "close": [10.5], "volume": [3754751.0],
                       "amount": [4.8e9]})
    out = normalize_sina(df)
    assert out["成交量"].iloc[0] == 37548  # 股 → 手（/100）
    assert "date" not in out.columns
    assert "日期" in out.columns


def test_fetch_pair_fallback_to_sina(monkeypatch):
    """东财失败 → 自动降级新浪源，且带市场前缀"""
    from requests.exceptions import ConnectionError

    sina_calls = []

    def fake_hist(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("em down")

    def fake_daily(symbol, start_date, end_date, adjust):
        sina_calls.append((symbol, adjust))
        return pd.DataFrame({"date": ["2026-08-19"], "open": [10], "high": [11],
                             "low": [9], "close": [10.5], "volume": [100.0],
                             "amount": [1e8]})

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_daily", fake_daily)
    raw, hfq = fetch_pair("600519", "20260801", "20260819")
    assert sina_calls == [("sh600519", ""), ("sh600519", "hfq")]
    assert not raw.empty


def test_save_upserts_clean_rows(monkeypatch):
    """save：质量校验（volume=0 丢弃）+ UPSERT 冲突列 (code, trade_date)"""
    from sqlalchemy.dialects.postgresql import dialect as pg_dialect

    from app.collectors.daily import DailyCollector

    db = FakeSession()
    data = [
        {"code": "600519", "trade_date": date(2026, 8, 1), "open": 10,
         "high": 11, "low": 9, "close": 10.5, "volume": 100,
         "amount": 1e8, "adj_factor": 1.0, "prev_close": None},
        {"code": "600519", "trade_date": date(2026, 8, 2), "open": 10,
         "high": 11, "low": 9, "close": 10.5, "volume": 0,
         "amount": 0, "adj_factor": 1.0, "prev_close": 10.5},
    ]
    DailyCollector(db).save(data)
    assert len(db.executed) == 1
    stmt = db.executed[0]
    values = multi_values(stmt)
    assert len(values) == 1  # volume=0 的行被丢弃
    assert values[0]["trade_date"] == date(2026, 8, 1)
    # 冲突判定列编译进 SQL
    sql = str(stmt.compile(dialect=pg_dialect()))
    assert "ON CONFLICT (code, trade_date) DO UPDATE" in sql
