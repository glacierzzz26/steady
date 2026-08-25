"""G10 试算/寻优消费者测试（2.3b）：参数化重算 + 检验 + 热力图 + DB 队列落库

合成数据纪律：趋势因子信号（近期上行=1 / 走平=0）与前向收益严格同序 →
短窗口 Rank IC ≈ +1；大窗口把信号抹平均值恒常 → IC 无定义（None）。
两者差异证明 params 真正流入计算（参数化重算生效）。
"""
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.factor_trial import (claim_trial, optimize_result,
                              resolve_base_factor, run_trial_and_save,
                              trial_result)
from app.factor_service import parametrized_params
from app.models.tables import (Base, DailyPrice, DailyValuation,
                               FinancialIndicator, FactorTrial, StockBasic)

# 恒常信号 → numpy corr 除零（预期内，ic_series 正常产出 NaN）
pytestmark = pytest.mark.filterwarnings(
    "ignore:invalid value encountered in divide:RuntimeWarning")

CODES_UP = [f"60{i:04d}" for i in range(6)]      # 近期上行（价格每日 +1）
CODES_FLAT = [f"60{i:04d}" for i in range(6, 10)]  # 走平（价格恒 10）
CODES = CODES_UP + CODES_FLAT
DAYS = [date(2026, 8, 3 + i) for i in range(8)]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed(session)
    return session


def seed(session):
    for i, code in enumerate(CODES_UP):
        session.add(StockBasic(code=code, name=f"上行{i}", market="SH",
                               universe="hs300"))
        for t, d in enumerate(DAYS):
            session.add(DailyPrice(id=1000 + i * 100 + t, code=code,
                                   trade_date=d, close=10.0 + t, adj_factor=1.0))
    for i, code in enumerate(CODES_FLAT):
        session.add(StockBasic(code=code, name=f"走平{i}", market="SH",
                               universe="hs300"))
        for t, d in enumerate(DAYS):
            session.add(DailyPrice(id=1000 + (6 + i) * 100 + t, code=code,
                                   trade_date=d, close=10.0, adj_factor=1.0))
    # pe_ratio（连续，desc）：低 PE 的 code = 上行组 → 与高前向收益同序。
    # 估值逐日 seed → as-of 连续值 → 5 分层均有样本，monotonic 可定义。
    for i, code in enumerate(CODES):
        for t, d in enumerate(DAYS):
            session.add(DailyValuation(id=2000 + i * 100 + t, code=code,
                                       trade_date=d, pe_ttm=15 + i, pb=2.0))
        session.add(FinancialIndicator(id=3000 + i, code=code,
                                       report_date=DAYS[0], announce_date=DAYS[0],
                                       roe=0.1 + i * 0.01, debt_ratio=0.4 + i * 0.02))
    session.commit()


# ---------- 变体解析与参数规约 ----------

def test_resolve_base_factor():
    assert resolve_base_factor("ma_trend") == "ma_trend"
    assert resolve_base_factor("ma_trend_ma10") == "ma_trend"
    assert resolve_base_factor("ma_trend_v2") == "ma_trend"
    assert resolve_base_factor("pe_ratio_fast") == "pe_ratio"
    with pytest.raises(ValueError):
        resolve_base_factor("momentum_20")


def test_parametrized_params_contract():
    # 缺省补经典值
    assert parametrized_params("ma_trend", None) == {"short": 5, "long": 20}
    assert parametrized_params("macd_signal", {}) == {"fast": 12, "slow": 26, "signal": 9}
    # window 简写 → short（long 保持 20）
    assert parametrized_params("ma_trend", {"window": 10}) == {"short": 10, "long": 20}
    # 显式 short/long 覆盖
    assert parametrized_params("ma_trend", {"short": 8, "long": 30}) == \
        {"short": 8, "long": 30}
    # value 因子无计算参数
    assert parametrized_params("pe_ratio", {"window": 3}) == {}


# ---------- 参数化重算 + 检验（与 G9 同口径） ----------

def test_trial_ma_trend_small_window_ic_positive(db):
    """短窗口信号与前向收益同序 → ic_mean ≈ +1，结果形状齐备

    ma_trend 为 0/1 二值因子：5 分层坍缩为 2 组 → monotonic 合法地为 None
    （与真实 G9 行为一致，非 bug）；分层单调性用连续因子单独测。
    """
    r = trial_result(db, "ma_trend_ma10", {"short": 2, "long": 3}, DAYS[0], DAYS[-1])
    assert r["factor"] == "ma_trend_ma10"
    assert r["params"] == {"short": 2, "long": 3}
    assert r["ic_mean"] is not None and r["ic_mean"] > 0.9
    assert all(-1 <= p["ic"] <= 1 for p in r["ic_series"] if p["ic"] is not None)
    assert len(r["quantiles"]) == 5
    assert r["monotonic"] is None or 0 <= r["monotonic"] <= 1  # 二值因子合法 None
    assert len(r["ic_decay"]) == 5  # H∈{1,5,10,20,60}


def test_trial_params_actually_flow_into_computation(db):
    """参数真正流入计算：short>long 使上升股信号翻 0 → 全恒常 → IC 无定义（None）"""
    small = trial_result(db, "ma_trend", {"short": 2, "long": 3}, DAYS[0], DAYS[-1])
    degenerate = trial_result(db, "ma_trend", {"short": 7, "long": 3}, DAYS[0], DAYS[-1])
    assert small["ic_mean"] is not None and small["ic_mean"] > 0.9
    assert degenerate["ic_mean"] is None  # 全恒常信号 → IC 无定义


def test_trial_value_factor_continuous_monotonic(db):
    """pe_ratio（连续 desc，无计算参数）：低 PE 组恒跑赢 → 正 IC + monotonic=1.0"""
    r = trial_result(db, "pe_ratio", None, DAYS[0], DAYS[-1])
    assert r["factor"] == "pe_ratio"
    assert r["params"] == {}  # 无计算参数
    assert r["ic_mean"] is not None and r["ic_mean"] > 0.5
    assert r["monotonic"] == 1.0  # Q1(低 PE) 恒 > Q5(高 PE) → 胜率 1.0
    assert len(r["quantiles"]) == 5


def test_trial_unknown_factor(db):
    with pytest.raises(ValueError):
        trial_result(db, "momentum_20", None, DAYS[0], DAYS[-1])


# ---------- 寻优热力图 ----------

def test_optimize_heatmap_shape_and_grid(db):
    """参数轴 × 持有期热力图：short=2 行有 IC、short=7(>long=3) 行恒 None → 2×2"""
    r = optimize_result(db, "ma_trend",
                        {"short": [2, 7], "long": [3], "horizon": [1, 5]},
                        DAYS[0], DAYS[-1])
    hm = r["heatmap"]
    assert hm["param"] == "short"
    assert hm["param_values"] == [2, 7]
    assert hm["horizons"] == [1, 5]
    assert len(hm["grid"]) == 2 and all(len(row) == 2 for row in hm["grid"])
    assert hm["grid"][0][0] is not None  # short=2,long=3 → 有 IC
    assert hm["grid"][1][0] is None      # short=7>long → 恒常信号无 IC


def test_optimize_no_compute_param_axis(db):
    """无计算参数轴（value 因子）→ 仅持有期单行热力图"""
    r = optimize_result(db, "pe_ratio", {"horizon": [1, 5]}, DAYS[0], DAYS[-1])
    hm = r["heatmap"]
    assert hm["param"] == "base"
    assert len(hm["grid"]) == 1 and len(hm["grid"][0]) == 2


# ---------- DB 队列落库 ----------

def test_claim_trial_claims_exactly_one(db):
    """领取必须单行锁定（回归 2.3b e2e 坑）：批量 update 会把所有 pending 一次
    置 running，RETURNING 只取回第一行，其余被静默挂起永不消费。

    并发下两个消费者同时跑，各自 SELECT 到同一 id 时，后到者 UPDATE 影响 0 行
    RETURNING 为空 → 返回 None 即可安全退出（不会重复领取同一行）。
    """
    t1 = FactorTrial(id=9101, factor_name="ma_trend",
                     params={"start": "2026-08-01", "end": "2026-08-21",
                             "params": {"short": 2, "long": 3}})
    t2 = FactorTrial(id=9102, factor_name="pe_ratio",
                     params={"start": "2026-08-01", "end": "2026-08-21",
                             "params": {}})
    db.add_all([t1, t2])
    db.commit()

    claimed = claim_trial(db)
    assert claimed is not None and claimed.id in (9101, 9102)

    # 只有被领取的那一行 running，另一行必须仍是 pending
    rows = {r.id: r.status for r in
            db.query(FactorTrial).filter(FactorTrial.id.in_([9101, 9102])).all()}
    assert list(rows.values()).count("running") == 1
    assert list(rows.values()).count("pending") == 1

    # 领取未领取的那一行
    claimed2 = claim_trial(db)
    assert claimed2 is not None and claimed2.id != claimed.id
    # 队列清空后再领取 → None
    assert claim_trial(db) is None


def test_run_trial_done_and_failed(db):
    tr = FactorTrial(id=9001, factor_name="ma_trend",
                     params={"start": str(DAYS[0]), "end": str(DAYS[-1]),
                             "params": {"short": 2, "long": 3}})
    db.add(tr)
    db.commit()
    run_trial_and_save(db, tr)
    assert tr.status == "done"
    assert tr.error is None
    assert tr.finished_at is not None
    assert tr.result["ic_mean"] > 0.9
    json.dumps(tr.result)  # result 必须 JSON 可序列化（Go 端展开）

    bad = FactorTrial(id=9002, factor_name="momentum_20",
                      params={"start": str(DAYS[0]), "end": str(DAYS[-1]),
                              "params": {}})
    db.add(bad)
    db.commit()
    run_trial_and_save(db, bad)
    assert bad.status == "failed"
    assert "未知因子" in bad.error
