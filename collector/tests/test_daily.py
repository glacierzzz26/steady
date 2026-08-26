"""日行情采集器测试（mock akshare）"""
from datetime import date

import pandas as pd
import pytest

from app.collectors import daily as daily_mod
from app.collectors.daily import (DailyCollector, build_rows, fetch_pair,
                                  normalize_sina, sina_symbol)
from tests.helpers import multi_values


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """混合批量因子在多日期窗口间有 61s 限频错峰——测试里换成 no-op"""
    monkeypatch.setattr(daily_mod.time, "sleep", lambda s: None)


def make_hist(closes=(10.0, 10.5, 11.0), start="2026-08-01"):
    dates = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({
        "日期": [d.strftime("%Y-%m-%d") for d in dates],
        "开盘": closes,
        "最高": [c + 0.5 for c in closes],
        "最低": [c - 0.5 for c in closes],
        "收盘": closes,
        "成交量": [10000, 12000, 13000],
        "成交额": [1e8, 1.2e8, 1.3e8],
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


def _clear_factor_cache():
    daily_mod._FACTOR_CACHE.clear()


def test_baostock_hybrid_factor_from_tushare(monkeypatch):
    """混合模式（§2.2）：OHLCV 走 BaoStock，adj_factor 被 Tushare 覆盖——
    BaoStock 派生因子全池 51% 有分段阶跃（平安/天齐缺陷），不能作因子唯一来源。
    因子按交易日批量（factor_map_by_date 1 次调用全市场），不逐股拉取。"""
    _clear_factor_cache()
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())
    # BaoStock 原始行情 + 其派生的 hfq（hfq/raw = 6.26，应被 Tushare 覆盖）
    raw = make_hist(closes=(10.0, 10.5, 11.0))
    hfq = make_hist(closes=(62.6, 65.7, 68.9))
    monkeypatch.setattr(daily_mod.baostock, "daily_pairs",
                        lambda sess, code, s, e: (raw, hfq))
    monkeypatch.setattr(daily_mod.tushare, "make_pro", lambda db: object())
    by_date_calls = []

    def fake_by_date(pro, trade_date):
        by_date_calls.append(trade_date)
        # 按交易日返回全市场因子（ts_code 键控）
        return {"600519.SH": 8.8825, "000001.SZ": 150.7263}

    monkeypatch.setattr(daily_mod.tushare, "factor_map_by_date", fake_by_date)

    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert len(rows) == 3
    # adj_factor 来自 Tushare，而非 BaoStock 派生的 6.26
    assert [r["adj_factor"] for r in rows] == [8.8825, 8.8825, 8.8825]
    # OHLCV 仍来自 BaoStock
    assert [r["close"] for r in rows] == [10.0, 10.5, 11.0]
    assert rows[0]["trade_date"] == date(2026, 8, 1)
    # 按窗口内交易日批量拉取（3 个交易日 = 3 次调用，非逐股）
    assert by_date_calls == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_baostock_hybrid_factor_cache_reuse(monkeypatch):
    """因子缓存跨股票复用：同一交易日的第二次 fetch 不重复调 Tushare
    （每日同步 5000 只股票同窗口 → 全市场仅 1 次 adj_factor 调用）"""
    _clear_factor_cache()
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())
    monkeypatch.setattr(daily_mod.baostock, "daily_pairs",
                        lambda sess, code, s, e: (make_hist(closes=(10.0, 10.5, 11.0)),
                                                  make_hist(closes=(62.6, 65.7, 68.9))))
    monkeypatch.setattr(daily_mod.tushare, "make_pro", lambda db: object())
    by_date_calls = []

    def fake_by_date(pro, trade_date):
        by_date_calls.append(trade_date)
        return {"600519.SH": 8.8825, "000001.SZ": 150.7263}

    monkeypatch.setattr(daily_mod.tushare, "factor_map_by_date", fake_by_date)

    DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    # 第二次 fetch 全部命中缓存：仍是 3 次批量调用（每个交易日 1 次）
    assert by_date_calls == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_baostock_hybrid_no_tushare_falls_through(monkeypatch):
    """混合模式缺 Tushare 因子源 → 降级 Tushare 全路径（保因子连续性，不落
    BaoStock 派生因子）"""
    _clear_factor_cache()
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())
    monkeypatch.setattr(daily_mod.baostock, "daily_pairs",
                        lambda sess, code, s, e: (make_hist(), make_hist()))
    # 无 Tushare token：make_pro → None → BaoStock 分支抛错 → 降级路径
    monkeypatch.setattr(daily_mod.tushare, "make_pro", lambda db: None)
    calls = []

    def fake_hist(symbol, period, start_date, end_date, adjust):
        calls.append(adjust)
        return make_hist()

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", fake_hist)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert calls == ["", "hfq"]  # 一路降到 AkShare
    assert len(rows) == 3


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
