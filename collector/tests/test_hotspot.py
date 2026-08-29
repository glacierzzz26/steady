"""热点采集解析单测（Issue #4）：列名防御 _pick / 安全浮点 _f / 单位换算 / 板块映射"""
from datetime import date

import pandas as pd
import pytest

from app.collectors.hotspot import _f, _fmt_flow, _fmt_yi, _pick


def test_pick_exact_then_substring():
    df = pd.DataFrame(columns=["板块", "涨跌幅", "净流入", "领涨股"])
    assert _pick(df, "板块") == "板块"
    # 候选 2 才精确命中
    assert _pick(df, "涨幅", "涨跌幅") == "涨跌幅"
    # 大小写不敏感
    df2 = pd.DataFrame(columns=["Name", "Close"])
    assert _pick(df2, "name") == "Name"
    # 子串双向包含（候选是列名的子串）
    assert _pick(df2, "nam") == "Name"


def test_pick_missing_returns_none():
    df = pd.DataFrame(columns=["板块"])
    assert _pick(df, "涨跌幅", "主力净流入") is None
    assert _pick(df, *()) is None


def test_f_safe():
    assert _f("12.5") == 12.5
    assert _f(None) is None
    assert _f("--") is None
    assert _f(float("nan")) is None


def test_fmt_units():
    assert _fmt_flow(1.5e8) == "1.50亿"   # 元 → 亿
    assert _fmt_yi(7.82) == "7.82亿"      # 已是亿（同花顺板块概览净流入单位）
    assert _fmt_flow(None) is None
    assert _fmt_yi(None) is None


# ---------- A股指数：东财主源 → 新浪兜底 ----------

def _cn_index_df(rows, source="em"):
    """构造东财/新浪同族结构的指数 DataFrame（列：名称/最新价/涨跌幅）"""
    if source == "em":
        return pd.DataFrame(rows, columns=["代码", "名称", "最新价", "涨跌幅"])
    return pd.DataFrame(rows, columns=["代码", "名称", "最新价", "涨跌额", "涨跌幅", "昨收", "今开"])


def test_cn_indices_em_main_source(monkeypatch):
    """东财主源可用：正常解析 CN_INDEX_NAMES 三个指数"""
    from app.collectors import hotspot as hs

    df = _cn_index_df([
        ("000001", "上证指数", 3952.17, -0.11),
        ("399001", "深证成指", 13953.06, -0.68),
        ("399006", "创业板指", 3424.40, -1.41),
        ("000300", "沪深300", 4609.17, -0.46),   # 不在 CN_INDEX_NAMES → 忽略
    ], "em")
    monkeypatch.setattr(hs.ak, "stock_zh_index_spot_em", lambda **kw: df)
    sentinel = object()
    monkeypatch.setattr(hs.ak, "stock_zh_index_spot_sina", lambda: (_ for _ in ()).throw(AssertionError("不应调用新浪")))
    out = hs._cn_indices_from_em()
    assert [r["name"] for r in out] == ["上证指数", "深证成指", "创业板指"]
    assert out[0]["close"] == 3952.17 and out[0]["change_pct"] == -0.11


def test_cn_indices_em_fail_falls_back_sina(monkeypatch):
    """东财异常（prod 实测 Connection aborted）→ 返回 []，调用方降级新浪"""
    from app.collectors import hotspot as hs

    def _raise(**kw):
        raise ConnectionError("Remote end closed connection")
    monkeypatch.setattr(hs.ak, "stock_zh_index_spot_em", _raise)
    assert hs._cn_indices_from_em() == []

    df = _cn_index_df([
        ("sh000001", "上证指数", 3952.18, 4.5, -0.11, 3956.7, 3950.0),
        ("sz399001", "深证成指", 13953.07, -95.8, -0.68, 14048.9, 13940.0),
        ("sz399006", "创业板指", 3424.40, -48.9, -1.41, 3473.3, 3420.0),
    ], "sina")
    monkeypatch.setattr(hs.ak, "stock_zh_index_spot_sina", lambda: df)
    out = hs._cn_indices_from_sina()
    assert [r["name"] for r in out] == ["上证指数", "深证成指", "创业板指"]
    assert out[0]["code"] == "sh000001"


def test_cn_indices_missing_columns_returns_empty(monkeypatch):
    """缺列（源结构漂移）→ []，不抛异常（增强数据可降级）"""
    from app.collectors import hotspot as hs

    bad = pd.DataFrame(columns=["代码", "名称"])  # 缺最新价/涨跌幅
    monkeypatch.setattr(hs.ak, "stock_zh_index_spot_em", lambda **kw: bad)
    assert hs._cn_indices_from_em() == []

    monkeypatch.setattr(hs.ak, "stock_zh_index_spot_sina", lambda: bad)
    assert hs._cn_indices_from_sina() == []


def test_sectors_from_ths_mapping():
    """同花顺板块概览数据 → 涨幅榜/资金流榜映射（不触发网络）"""
    from app.collectors.hotspot import _sectors_gain_from, _sectors_flow_from

    ths = [
        {"name": "生物制品", "change_pct": 9.12, "leader": "三元基因",
         "net_inflow": "7.82亿", "net_inflow_raw": 7.82},
        {"name": "养殖业", "change_pct": 5.5, "leader": "某股",
         "net_inflow": None, "net_inflow_raw": None},
        {"name": "半导体", "change_pct": 2.1, "leader": "中芯",
         "net_inflow": "3.00亿", "net_inflow_raw": 3.0},
    ]
    gains = _sectors_gain_from(ths)
    assert gains[0]["name"] == "生物制品" and gains[0]["change_pct"] == 9.12
    assert len(gains) == 3

    flows = _sectors_flow_from(ths)
    assert flows[0]["name"] == "生物制品"      # 净流入最高
    assert len(flows) == 2                    # 净流入为 None 的养殖业被过滤
    assert flows[0]["net_inflow"] == "7.82亿"


# ---------- G6：两市成交 ----------

class _FakeRows:
    """sqlalchemy select 结果桩：.all() 返回给定行"""
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """热点用 FakeSession：execute 返回可 .all() 的结果"""
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt):
        return _FakeRows(self.rows)


def test_fetch_turnover_uses_latest_complete_date():
    """两市成交：取 sh/sz 两指数行齐全的最近交易日求和；过渡期缺尾降级"""
    from app.collectors.hotspot import _fetch_turnover

    rows = [
        ("2026-08-25", "sh000001", 5.0e11),
        ("2026-08-25", "sz399106", 6.0e11),
        ("2026-08-24", "sh000001", 4.8e11),   # 缺 sz → 该日不算
    ]
    out = _fetch_turnover(_FakeDB([(date.fromisoformat(d), c, a) for d, c, a in rows]))
    assert out["total"] == 1.1e12
    assert out["sh"] == 5.0e11 and out["sz"] == 6.0e11
    assert out["trade_date"] == "2026-08-25"


def test_fetch_turnover_missing_returns_none():
    """任一行缺失/全缺 → None（增强数据可降级省略）"""
    from app.collectors.hotspot import _fetch_turnover

    assert _fetch_turnover(_FakeDB([])) is None
    assert _fetch_turnover(_FakeDB([(date(2026, 8, 25), "sh000001", 5.0e11)])) is None


def test_fetch_turnover_decimal_converts_to_float():
    """DB numeric 返回 Decimal → 转 float（否则 sections JSON 序列化崩）"""
    from decimal import Decimal

    from app.collectors.hotspot import _fetch_turnover

    rows = [
        ("2026-08-25", "sh000001", Decimal("500000000000.5")),
        ("2026-08-25", "sz399106", Decimal("600000000000.5")),
    ]
    out = _fetch_turnover(_FakeDB([(date.fromisoformat(d), c, a) for d, c, a in rows]))
    assert isinstance(out["total"], float)
    assert isinstance(out["sh"], float) and isinstance(out["sz"], float)
    assert out["total"] == 1.100000000001e12  # 500000000000.5 + 600000000000.5
