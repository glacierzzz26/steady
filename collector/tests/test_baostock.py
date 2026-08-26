"""BaoStock 适配层测试（mock 协议函数，无网络）

覆盖：代码转 bs 格式 / ResultData 翻页转 DataFrame / daily_pairs 的单位与列对齐 /
后复权缺失报错 / 交易日历过滤 / 会话重连重试（连接级错误码）/ 单例 / bs 缺失退化。
"""
from datetime import date

import pandas as pd
import pytest

import baostock as baostock_mod
from app.sources import baostock


# ---------- 工具 ----------

def _rs(fields, rows, error_code="0", error_msg=""):
    """baostock ResultData 桩：fields + 翻页"""
    class _RS:
        def __init__(self):
            self.fields = fields
            self._rows = list(rows)
            self.error_code = error_code
            self.error_msg = error_msg
            self._i = -1

        def next(self):
            if self._i + 1 < len(self._rows):
                self._i += 1
                return True
            return False

        def get_row_data(self):
            return list(self._rows[self._i])

    return _RS()


class FakeBS:
    """baostock 协议函数桩：登录/登出 + 可编程的日线/交易日历查询"""

    def __init__(self, login_code="0"):
        self.login_code = login_code
        self.login_calls = 0
        self.logout_calls = 0
        self.queries = []          # 记录 (qfunc, args, kwargs)
        self.hist_responses = {}   # adjustflag -> list of _rs 或 callable
        self.trade_cal_resp = None
        self.last_adjustflag = None

    def login(self):
        self.login_calls += 1
        return _rs([], [], error_code=self.login_code)

    def logout(self):
        self.logout_calls += 1

    def query_history_k_data_plus(self, code, fields, **kw):
        self.queries.append((code, fields, kw))
        af = kw.get("adjustflag")
        self.last_adjustflag = af
        resp = self.hist_responses.get(af)
        if callable(resp):
            return resp(kw)
        return resp if resp is not None else _rs([], [])

    def query_trade_dates(self, **kw):
        self.queries.append(("trade_dates", None, kw))
        return self.trade_cal_resp if self.trade_cal_resp is not None else _rs([], [])


@pytest.fixture(autouse=True)
def fake_bs(monkeypatch):
    """每测试隔离：替换 baostock 模块的协议函数，并重置单例"""
    fbs = FakeBS()
    monkeypatch.setattr(baostock_mod, "login", fbs.login)
    monkeypatch.setattr(baostock_mod, "logout", fbs.logout)
    monkeypatch.setattr(baostock_mod, "query_history_k_data_plus", fbs.query_history_k_data_plus)
    monkeypatch.setattr(baostock_mod, "query_trade_dates", fbs.query_trade_dates)
    baostock._reset_session()
    yield fbs
    baostock._reset_session()


# ---------- 代码 / 日期工具 ----------

def test_bs_code():
    assert baostock.bs_code("600519") == "sh.600519"
    assert baostock.bs_code("000001") == "sz.000001"
    assert baostock.bs_code("300750") == "sz.300750"
    assert baostock.bs_code("830001") == "bj.830001"
    assert baostock.bs_code("430047") == "bj.430047"
    assert baostock.bs_code("920001") == "bj.920001"


def test_ymd():
    assert baostock._ymd(date(2026, 8, 26)) == "2026-08-26"
    assert baostock._ymd("2026/08/26") == "2026-08-26"


# ---------- ResultData → DataFrame ----------

def test_rs_to_df_paginates():
    rs = _rs(["date", "close"], [["2026-08-26", "10.5"], ["2026-08-25", "10.0"]])
    df = baostock._rs_to_df(rs)
    assert list(df.columns) == ["date", "close"]
    assert df.iloc[0]["close"] == "10.5"          # 原样字符串（build_rows 负责 float）


def test_rs_to_df_empty():
    assert baostock._rs_to_df(None).empty
    assert baostock._rs_to_df(_rs([], [])).empty


# ---------- daily_pairs：单位/列对齐/后复权缺失 ----------

def test_daily_pairs_units_and_columns(fake_bs):
    fake_bs.hist_responses = {
        "3": _rs(["date", "open", "high", "low", "close", "volume", "amount"],
                 [["2026-08-26", "10.0", "10.5", "9.9", "10.2", "1000000", "10200000"]]),
        "1": _rs(["date", "close"], [["2026-08-26", "68.9"]]),
    }
    sess = baostock.get_session()
    raw, hfq = baostock.daily_pairs(sess, "600519", "2026-08-26", "2026-08-26")
    assert list(raw.columns) == ["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]
    # 成交量 股 → 手（÷100），成交额 元 原样
    assert raw.iloc[0]["成交量"] == 10000.0
    assert float(raw.iloc[0]["成交额"]) == 10200000.0  # 成交额留字符串，build_rows 负责 float
    assert hfq.iloc[0]["收盘"] == "68.9"
    # 请求用 bs 格式代码 + 后复权 adjustflag=1
    assert fake_bs.queries[0][0] == "sh.600519"
    assert fake_bs.queries[0][2]["adjustflag"] == "3"
    assert fake_bs.queries[1][2]["adjustflag"] == "1"


def test_daily_pairs_hfq_empty_raises(fake_bs):
    fake_bs.hist_responses = {
        "3": _rs(["date", "close"], [["2026-08-26", "10.2"]]),
        "1": _rs([], []),  # 后复权缺失
    }
    sess = baostock.get_session()
    with pytest.raises(RuntimeError, match="后复权未返回数据"):
        baostock.daily_pairs(sess, "600519", "2026-08-26", "2026-08-26")


def test_daily_pairs_raw_empty_returns_empty(fake_bs):
    fake_bs.hist_responses = {"3": _rs([], [])}
    sess = baostock.get_session()
    raw, hfq = baostock.daily_pairs(sess, "600519", "2026-08-26", "2026-08-26")
    assert raw.empty and hfq.empty


# ---------- 交易日历 ----------

def test_trade_cal_rows_filters_trading_days(fake_bs):
    fake_bs.trade_cal_resp = _rs(
        ["calendar_date", "is_trading_day"],
        [["2026-08-26", "1"], ["2026-08-27", "0"], ["2026-08-28", "1"]])
    sess = baostock.get_session()
    rows = baostock.trade_cal_rows(sess, "2026-08-26", "2026-08-28")
    assert [r["cal_date"] for r in rows] == [date(2026, 8, 26), date(2026, 8, 28)]
    assert all(r["is_open"] for r in rows)
    assert all(r["exchange"] == "SSE" for r in rows)


# ---------- 会话：连接级错误重连重试 ----------

def test_query_retries_on_conn_error(monkeypatch, fake_bs):
    """首查返回连接级错误码 → 重连重试 → 成功；sleep 只发生一次"""
    sleeps = []
    monkeypatch.setattr(baostock.time, "sleep", lambda s: sleeps.append(s))
    fake_bs.hist_responses["3"] = _rs([], [], error_code="10002002", error_msg="网络连接失败")
    # 第一次 error，第二次成功
    state = {"n": 0}

    def flip(af):
        state["n"] += 1
        if state["n"] == 1:
            return _rs([], [], error_code="10002002", error_msg="网络连接失败")
        return _rs(["date", "close"], [["2026-08-26", "10.2"]])

    fake_bs.hist_responses["3"] = flip
    sess = baostock.BaoStockSession(retries=1, retry_delay=0.5)
    rs = sess.query(fake_bs.query_history_k_data_plus, "sh.600519", "date,close",
                    start_date="2026-08-26", end_date="2026-08-26",
                    frequency="d", adjustflag="3")
    assert rs.error_code == "0"
    assert state["n"] == 2          # 重试一次
    assert sleeps == [0.5]          # 只有一次重试等待
    assert fake_bs.login_calls >= 2  # 连接失效后重新登录


def test_query_non_conn_error_no_retry(monkeypatch, fake_bs):
    """非连接级错误（参数/数据错）直接抛，不重试"""
    sleeps = []
    monkeypatch.setattr(baostock.time, "sleep", lambda s: sleeps.append(s))
    fake_bs.hist_responses["3"] = _rs([], [], error_code="10003001", error_msg="参数错误")
    sess = baostock.BaoStockSession(retries=2, retry_delay=0.5)
    with pytest.raises(RuntimeError, match="参数错误"):
        sess.query(fake_bs.query_history_k_data_plus, "sh.600519", "date,close",
                   start_date="2026-08-26", end_date="2026-08-26",
                   frequency="d", adjustflag="3")
    assert sleeps == []


def test_query_raises_when_bs_none(monkeypatch):
    """baostock 未安装：get_session() 返回 None，查询直接抛"""
    monkeypatch.setattr(baostock, "bs", None)
    assert baostock.get_session() is None


# ---------- 单例 ----------

def test_get_session_singleton():
    s1 = baostock.get_session()
    s2 = baostock.get_session()
    assert s1 is s2
    baostock._reset_session()
    assert baostock.get_session() is not s1
