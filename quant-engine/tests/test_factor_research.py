"""因子检验统计测试：纯 pandas 函数用合成数据断言已知值 + 预计算写库行组装

合成数据纪律：因子值与前向收益严格单调对应 → Rank IC 应为 ±1（Spearman 满相关），
分层/相关性应得可手算的已知值。
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.factor_research import (adj_close_frame, factor_correlation,
                                 forward_returns, ic_series, icir,
                                 precompute_factor_stat, quantile_groups,
                                 quantile_returns_by_date)
from app.models.tables import Base, DailyPrice, FactorValue, StockBasic

DATES = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
STOCKS = ["A", "B", "C", "D"]


def _pframe(vals):
    """vals: list of rows（每行 = 一个交易日的横截面值）"""
    return pd.DataFrame(vals, index=DATES, columns=STOCKS)


# ---------- 复权收盘 / 前向收益 ----------

def test_adj_close_frame_pivot():
    d0, d1 = date(2026, 1, 5), date(2026, 1, 6)
    rows = [("A", d0, 100, 2.0), ("B", d0, 50, 1.0),   # adj_close: A=200, B=50
            ("A", d1, 110, 2.0), ("B", d1, 45, 1.0)]
    f = adj_close_frame(rows)
    assert f.loc[d0, "A"] == 200.0
    assert f.loc[d0, "B"] == 50.0
    fwd = forward_returns(f, 1)
    assert np.isclose(fwd.loc[d0, "A"], 110 * 2 / (100 * 2) - 1)  # 0.10
    assert np.isclose(fwd.loc[d0, "B"], 45 / 50 - 1)              # -0.10
    assert pd.isna(fwd.loc[d1, "A"])  # 尾部无前向数据


# ---------- Rank IC / ICIR ----------

def test_ic_series_perfect_positive():
    """因子与未来收益严格同序 → 每日 Rank IC = 1.0"""
    norm = _pframe([[0.1, 0.2, 0.3, 0.4]] * 3)
    fwd = _pframe([[0.01, 0.02, 0.03, 0.04],
                   [0.02, 0.04, 0.06, 0.08],
                   [0.01, 0.03, 0.05, 0.07]])
    ic = ic_series(norm, fwd)
    assert len(ic) == 3
    assert (np.isclose(ic, 1.0)).all()


def test_ic_series_perfect_negative():
    """因子与未来收益严格反序 → Rank IC = -1.0"""
    norm = _pframe([[0.1, 0.2, 0.3, 0.4]] * 3)
    fwd = _pframe([[0.04, 0.03, 0.02, 0.01]] * 3)
    assert (np.isclose(ic_series(norm, fwd), -1.0)).all()


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """参考 Spearman（双侧平均秩 + Pearson = scipy spearmanr 定义，无需 scipy）"""
    return a.rank(method="average").corr(b.rank(method="average"))


def test_ic_series_ties_and_missing():
    """并列归一值（如二进制因子）：与参考 Spearman 定义一致；缺前向收益剔除"""
    norm = _pframe([[0.0, 0.0, 1.0, 1.0]] * 3)  # 并列 0/1
    fwd = _pframe([[0.01, 0.02, 0.03, 0.04]] * 3)
    ic = ic_series(norm, fwd)
    # 并列秩按均值处理 → 相关系数 < 1，但与参考 Spearman 完全一致
    assert np.isclose(ic.mean(), _spearman(norm.iloc[0], fwd.iloc[0]))
    assert ic.mean() > 0.8  # 强正相关（并列拉低但远大于 0）

    # B 缺前向收益 → 该点剔除；剩 A/C/D（C/D 仍并列）→ 仍与参考定义一致
    fwd2 = _pframe([[0.01, np.nan, 0.03, 0.04]] * 3)
    ic2 = ic_series(norm, fwd2)
    assert np.isclose(ic2.mean(),
                      _spearman(norm.iloc[0].drop("B"), fwd2.iloc[0].drop("B")))


def test_ic_series_too_few_samples():
    norm = _pframe([[0.1, 0.2, 0.3, 0.4]] * 3)
    fwd = _pframe([[0.01, np.nan, np.nan, np.nan]] * 3)  # 仅 1 个有效
    assert ic_series(norm, fwd).empty


def test_icir_known_value():
    ic = pd.Series([0.01, 0.03, 0.05, 0.07, 0.09])
    expected = ic.mean() / ic.std(ddof=0)
    assert np.isclose(icir(ic), expected)
    assert icir(pd.Series([0.05] * 5)) is None      # std=0
    assert icir(pd.Series([0.01, 0.02, 0.03])) is None  # 样本 < 5


# ---------- 分层收益 ----------

def test_quantile_groups_extremes():
    s = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    g = quantile_groups(s, 5)
    assert g[s.idxmax()] == 1   # 最优 → 组 1
    assert g[s.idxmin()] == 5   # 最差 → 组 5
    assert set(g.unique()) == {1, 2, 3, 4, 5}


def test_quantile_returns_monotone():
    """因子越优 → 前向收益越高 → 组均收益严格递减（Q1 最高）"""
    stocks = [f"S{i}" for i in range(10)]
    vals = [0.1 + 0.1 * i for i in range(10)]          # 因子值递增
    fwd = [0.001 * (i + 1) for i in range(10)]         # 未来收益同序递增
    norm = pd.DataFrame([vals] * 3, index=DATES, columns=stocks)
    fwd_df = pd.DataFrame([fwd] * 3, index=DATES, columns=stocks)
    qr = quantile_returns_by_date(norm, fwd_df, q=5)
    means = qr.mean()
    assert means[1] == pytest.approx(0.01)             # Q1=最优股 S9 → 0.01
    assert list(means) == sorted(means, reverse=True)  # 严格单调递减


# ---------- 因子相关性 ----------

def test_factor_correlation_known():
    vals = [0.1 + 0.1 * i for i in range(4)]
    f1 = _pframe([vals] * 3)
    f2 = _pframe([vals] * 3)                       # 与 f1 完全一致
    f3 = _pframe([list(reversed(vals))] * 3)       # 与 f1 严格反序
    mat = factor_correlation({"f1": f1, "f2": f2, "f3": f3}, factors=["f1", "f2", "f3"])
    assert mat.loc["f1", "f1"] == 1.0
    assert mat.loc["f1", "f2"] == 1.0
    assert mat.loc["f1", "f3"] == -1.0


# ---------- 预计算写库（行组装与 DB 读取；upsert 桩成收集器） ----------

def test_precompute_factor_stat_rows(db, monkeypatch):
    """precompute：读 factor_value+行情 → 每因子每日 IC/分层行 + per-date 相关矩阵

    ma_trend 归一值与未来收益同序 → ic=1、q1>q5；pe_ratio 反序 → ic=-1；
    两因子严格反相关 → 矩阵 [[1,-1],[-1,1]]。
    """
    captured = {}

    def fake_upsert(session, model, rows, conflict_cols, update_cols):
        captured.setdefault(model.__name__, []).extend(rows)
        return len(rows)

    monkeypatch.setattr("app.factor_research.upsert", fake_upsert)

    # 默认 6 因子（种子仅 2 因子有数据）：空因子因子应被丢弃而非报错
    result = precompute_factor_stat(db)
    assert result["dates"][0] == "2026-08-10"
    assert result["dates"][1] == "2026-08-15"

    stat = captured["FactorStat"]
    assert len(stat) == 6 * 2                       # 6 交易日 × 2 因子
    ma = [r for r in stat if r["factor_name"] == "ma_trend"]
    pe = [r for r in stat if r["factor_name"] == "pe_ratio"]
    assert len(ma) == 6 and len(pe) == 6
    # 末交易日（08-15）无 T+1 前向收益 → ic_1d 为 NULL；其余 5 日应精确
    ma_ic = [r["ic_1d"] for r in ma if r["ic_1d"] is not None]
    pe_ic = [r["ic_1d"] for r in pe if r["ic_1d"] is not None]
    assert len(ma_ic) == 5 and all(np.isclose(v, 1.0) for v in ma_ic)
    assert len(pe_ic) == 5 and all(np.isclose(v, -1.0) for v in pe_ic)
    assert ma[0]["q1"] > ma[0]["q5"]                # 最优组收益高于最差组

    corr = captured["FactorCorr"]
    assert len(corr) == 6                           # 每交易日一个矩阵
    m = corr[0]["matrix"]                           # 已是列表（SQLAlchemy JSON 列序列化）
    assert len(m) == len(m[0]) == 6                 # 恒 6×6（6 因子规范序）
    assert np.isclose(m[0][0], 1.0)                 # ma_trend × ma_trend
    # 规范序 [ma,macd,pe,pb,roe,debt]：ma×pe = -1（严格反相关）
    assert np.isclose(m[0][2], -1.0) and np.isclose(m[2][0], -1.0)
    # 未种数据的 macd/其余 3 因子 → 全 NaN（NULL）
    assert m[0][1] is None and m[1][1] is None


# ---------- 夹具：sqlite 内存库（复用 test_backtest 种子风格） ----------

CODES = ["600001", "600002", "600003", "600004", "600005", "600006"]
PRICE_DAYS = [date(2026, 8, 10 + i) for i in range(6)]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed(session)
    return session


def seed(session):
    """价格：S_i 收盘 = 10 + i*2 + t → 前向收益 S0 最高、S3 最低（与因子同序/反序）

    sqlite 对 BIGINT 主键不自增，需显式 id（同 test_backtest 种子惯例）。
    """
    for i, code in enumerate(CODES):
        session.add(StockBasic(code=code, name=f"测试{i}", market="SH",
                               universe="hs300"))
        for t, d in enumerate(PRICE_DAYS):
            session.add(DailyPrice(id=10000 + i * 100 + t, code=code,
                                   trade_date=d,
                                   close=10 + i * 2 + t, adj_factor=1.0))
            # ma_trend 归一：越高越好 → S0=1.0...S3=0.25（同前向收益序）
            norm_ma = (len(CODES) - i) / len(CODES)
            # pe_ratio 归一：反序 → IC=-1（与 ma_trend 严格反相关）
            norm_pe = (i + 1) / len(CODES)
            session.add(FactorValue(id=20000 + i * 100 + t,
                                    code=code, factor_name="ma_trend",
                                    trade_date=d, value=1.0, rank=i + 1,
                                    normalized=norm_ma))
            session.add(FactorValue(id=30000 + i * 100 + t,
                                    code=code, factor_name="pe_ratio",
                                    trade_date=d, value=1.0,
                                    rank=len(CODES) - i, normalized=norm_pe))
    session.commit()
