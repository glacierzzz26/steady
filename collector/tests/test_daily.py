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


def make_601155_glitch():
    """东财 601155 假缩股型：adj 1.5807→0.0762（step−95.2%），close 11.83→157.40
    （gap−92.5%）——step−gap 仅 2.7pp 落在守卫大阶跃容差内，靠交叉验证拦截"""
    raw = pd.DataFrame({
        "日期": ["2026-08-01", "2026-08-02"],
        "开盘": [11.83, 157.40], "最高": [12.0, 161.35],
        "最低": [11.7, 156.56], "收盘": [11.83, 157.40],
        "成交量": [229948, 291937], "成交额": [2.7e8, 4.63e9],
    })
    hfq = pd.DataFrame({"日期": ["2026-08-01", "2026-08-02"],
                        "收盘": [18.699, 12.00]})
    return raw, hfq


def test_akshare_fetch_window_includes_context(monkeypatch):
    """阶段 4 加守：AkShare 拉取窗口扩到 start−7 天（守卫上下文）"""
    calls = []

    def fake_pair(code, start, end):
        calls.append((start, end))
        return (make_hist(start="2026-08-10"),
                make_hist(closes=(62.6, 65.73, 68.86), start="2026-08-10"))

    monkeypatch.setattr(daily_mod, "fetch_pair", fake_pair)
    rows = DailyCollector(None).fetch("600519", "2026-08-10", "2026-08-20")
    assert calls == [("20260803", "20260820")]  # start−7 天
    assert len(rows) == 3  # 上下文行被过滤，window 内 3 行全保留


def test_akshare_guard_reject_falls_to_baostock(monkeypatch):
    """阶段 4 加守：AkShare 主源因子阶跃不符价格（平安型 step+16.7% 反涨）→
    守卫拒收 → 降级 BaoStock 兜底"""
    from datetime import date

    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())

    def fake_hist(symbol, period, start_date, end_date, adjust):
        if adjust == "":
            return pd.DataFrame({
                "日期": ["2026-08-01", "2026-08-02"],
                "开盘": [10.0, 10.5], "最高": [10.5, 11.0], "最低": [9.5, 10.0],
                "收盘": [10.0, 10.5], "成交量": [10000, 12000], "成交额": [1e8, 1.2e8],
            })
        return pd.DataFrame({"日期": ["2026-08-01", "2026-08-02"],
                             "收盘": [20.0, 24.507]})  # adj 2.0 → 2.334

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(daily_mod.baostock, "daily_pairs",
                        lambda sess, code, s, e: (make_hist(), make_hist(closes=(62.6, 65.73, 68.86))))
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    # AkShare 守卫拒收 → 落到 BaoStock（恒因子 6.26）
    assert len(rows) == 3
    assert all(r["adj_factor"] == 6.26 for r in rows)


def test_akshare_split_glitch_cross_check_falls_to_baostock(monkeypatch):
    """阶段 4 加守：东财假缩股（601155 型，守卫放行但 AkShare/BaoStock 因子比值断裂）
    → 交叉验证拒收 → 降级 BaoStock 兜底"""
    from datetime import date

    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist",
                        lambda symbol, period, start_date, end_date, adjust: make_601155_glitch()[0 if adjust == "" else 1])

    def fake_bs_pairs(sess, code, s, e):
        s = s if isinstance(s, date) else date.fromisoformat(str(s))
        if s >= date(2026, 8, 1):
            # 交叉验证段：BaoStock 无该缩股事件（因子恒 ~1.5797）
            raw = pd.DataFrame({
                "日期": ["2026-08-01", "2026-08-02"],
                "开盘": [11.83, 11.99], "最高": [12.0, 12.1], "最低": [11.7, 11.85],
                "收盘": [11.83, 11.99], "成交量": [10000, 12000], "成交额": [1.18e8, 1.44e8],
            })
            hfq = pd.DataFrame({"日期": ["2026-08-01", "2026-08-02"],
                                "收盘": [18.699, 18.94]})
            return raw, hfq
        # _fetch_baostock 兜底段：整窗恒因子 6.26
        return make_hist(), make_hist(closes=(62.6, 65.73, 68.86))

    monkeypatch.setattr(daily_mod.baostock, "daily_pairs", fake_bs_pairs)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    # 交叉验证拒收 → 落到 BaoStock（恒因子 6.26），不落东财假缩股
    assert len(rows) == 3
    assert all(r["adj_factor"] == 6.26 for r in rows)


def test_akshare_legit_split_passes_no_fallback(monkeypatch):
    """合法除权（2:1 送转）：AkShare 因子与价格同步变动 → 守卫放行 + 交叉验证比值
    恒常 → 不兜底，返回 AkShare 数据"""
    from datetime import date

    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())

    def fake_hist(symbol, period, start_date, end_date, adjust):
        if adjust == "":
            return pd.DataFrame({
                "日期": ["2026-08-01", "2026-08-02"],
                "开盘": [10.0, 20.0], "最高": [10.5, 21.0], "最低": [9.5, 19.0],
                "收盘": [10.0, 20.0], "成交量": [10000, 5000], "成交额": [1e8, 1e9],
            })
        return pd.DataFrame({"日期": ["2026-08-01", "2026-08-02"],
                             "收盘": [20.0, 20.0]})  # adj 2.0 → 1.0（2:1 送转）

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", fake_hist)

    def fake_bs_pairs(sess, code, s, e):
        s = s if isinstance(s, date) else date.fromisoformat(str(s))
        if s >= date(2026, 8, 1):
            # BaoStock 确认同一送转（比值恒常）
            raw = pd.DataFrame({
                "日期": ["2026-08-01", "2026-08-02"],
                "开盘": [10.0, 20.0], "最高": [10.5, 21.0], "最低": [9.5, 19.0],
                "收盘": [10.0, 20.0], "成交量": [10000, 5000], "成交额": [1e8, 1e9],
            })
            hfq = pd.DataFrame({"日期": ["2026-08-01", "2026-08-02"], "收盘": [20.0, 20.0]})
            return raw, hfq
        return make_hist(), make_hist(closes=(62.6, 65.73, 68.86))  # 若兜底会变成 6.26

    monkeypatch.setattr(daily_mod.baostock, "daily_pairs", fake_bs_pairs)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    # AkShare 合法除权数据直接返回（因子 2.0 → 1.0，未兜底）
    assert [r["adj_factor"] for r in rows] == [2.0, 1.0]


def test_akshare_no_split_no_cross_check(monkeypatch):
    """无因子阶跃（>7%）→ 不触发交叉验证，BaoStock 完全不触碰"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist",
                        lambda symbol, period, start_date, end_date, adjust:
                        make_hist() if adjust == "" else make_hist(closes=(62.6, 65.73, 68.86)))

    def boom(*a, **k):
        raise AssertionError("不应触碰 BaoStock（无除权日）")

    monkeypatch.setattr(daily_mod.baostock, "get_session", boom)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert len(rows) == 3
    assert all(r["adj_factor"] == 6.26 for r in rows)


def test_akshare_cross_check_skips_when_baostock_down(monkeypatch):
    """潜在除权日但 BaoStock 不可用（封禁/未装）→ 交叉验证跳过，不误伤，接受 AkShare"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist",
                        lambda symbol, period, start_date, end_date, adjust:
                        make_601155_glitch()[0 if adjust == "" else 1])
    monkeypatch.setattr(daily_mod.baostock, "get_session",
                        lambda: (_ for _ in ()).throw(RuntimeError("BaoStock 封禁冷却中")))
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    # BaoStock 不可用 → 交叉验证跳过，AkShare 数据（守卫放行）照常返回
    assert len(rows) == 2
    assert rows[1]["adj_factor"] == 0.0762


def test_akshare_primary_skips_baostock(monkeypatch):
    """阶段 4 主源锁定：AkShare 成功 → BaoStock daily_pairs 不被调用"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    bs_calls = []

    def fake_pairs(*a, **k):
        bs_calls.append(a)
        raise AssertionError("不应走到 BaoStock")

    monkeypatch.setattr(daily_mod.baostock, "daily_pairs", fake_pairs)

    def fake_hist(symbol, period, start_date, end_date, adjust):
        return make_hist()

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", fake_hist)
    rows = DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")
    assert len(rows) == 3
    assert bs_calls == []  # BaoStock 未被触碰


def test_chain_all_fail_raises(monkeypatch):
    """链外无 BaoStock（baostock_enabled=False）且 AkShare 失败 → 抛异常触发重试"""
    import pytest

    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: False)

    def boom(*a, **k):
        raise RuntimeError("AkShare 全挂")

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", boom)
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_daily", boom)
    with pytest.raises(RuntimeError):
        DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")


def test_baostock_pure_factor_from_baostock(monkeypatch):
    """阶段 4 AkShare 主源失败 → BaoStock 兜底：adj_factor 来自 BaoStock 派生因子；
    守卫通过；窗口 start−7 取守卫上下文（此处 08-01 之前无行，恒因子无对）"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())

    def boom(*a, **k):
        raise RuntimeError("AkShare 全挂")

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", boom)
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_daily", boom)
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


def test_akshare_fail_baostock_guard_reject_raises(monkeypatch):
    """阶段 4 守卫拒收（平安型：因子阶跃 16.7% 但 close 反涨）→ BaoStock 兜底数据被
    拒收 → 抛异常触发 base.run 重试，不落 BaoStock 派生因子（不再当场降级 AkShare）"""
    import pytest

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

    def boom(*a, **k):
        raise RuntimeError("AkShare 全挂")

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", boom)
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_daily", boom)
    with pytest.raises(RuntimeError):
        DailyCollector(None).fetch("600519", "2026-08-01", "2026-08-20")


def test_baostock_context_rows_dropped(monkeypatch):
    """守卫上下文行（start−7 内、trade_date < start）不入库（AkShare 失败走 BaoStock 兜底）"""
    monkeypatch.setattr(daily_mod, "baostock_enabled", lambda *a, **k: True)
    monkeypatch.setattr(daily_mod.baostock, "get_session", lambda: object())

    def boom(*a, **k):
        raise RuntimeError("AkShare 全挂")

    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_hist", boom)
    monkeypatch.setattr(daily_mod.ak, "stock_zh_a_daily", boom)
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
