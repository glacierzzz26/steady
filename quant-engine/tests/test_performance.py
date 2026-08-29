"""策略效果度量测试（方向① 第一期）：命中率 / 对照 / 月报

合成数据纪律：价格路径严格可手算（涨/跌/除权/平），信号只在已知日，forward 收益与
窗口样本数可精确断言。sqlite 对 BIGINT 主键不自增，需显式 id（同 test_backtest 惯例）。
upsert 为 PG 方言，monkeypatch 成直接 add（capture 也可用于断言落库内容）。
"""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.performance import (
    _fwd_ret,
    _rebuild_holdings,
    build_monthly_report,
    compute_attribution,
    compute_hit_rate,
    compute_nav_overlay,
)
from app.models.tables import (
    Account,
    AccountNav,
    BacktestResult,
    Base,
    DailyPrice,
    FactorValue,
    StockBasic,
    StrategyPerf,
    StrategySignal,
)

# 8 个交易日：周一 08-03 … 周四 08-13（覆盖 5 日窗口，10/20 日窗口无前向数据 → 样本不足）
DAYS = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6),
        date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)]
D0, D5 = DAYS[0], DAYS[5]

_ids = [10000]


def _next_id():
    _ids[0] += 1
    return _ids[0]


def _price_path(kind: str) -> list[tuple[float, float]]:
    """返回 [(close, adj_factor)] × 8 天，价格路径可手算"""
    if kind == "rise":      # 每日 +2%
        return [(10.0 + 0.2 * t, 1.0) for t in range(8)]
    if kind == "fall":      # 每日 -2%
        return [(10.0 - 0.2 * t, 1.0) for t in range(8)]
    if kind == "exdiv":     # d1 除权：close 9.0（名义 -10%），adj 跳 10/9 → 复权连续且缓涨
        return [(10.0, 1.0)] + [(9.0 + 0.05 * (t - 1), 10.0 / 9.0) for t in range(1, 8)]
    if kind == "flat":      # 平走（基准）
        return [(10.0, 1.0) for _ in range(8)]
    raise ValueError(kind)


def _seed_price(session, code: str, kind: str, start_price: float = None,
                adj_override=None):
    """adj_override 为 False → adj_factor 置 None（模拟指数行无复权因子）"""
    for t, (close, adj) in enumerate(_price_path(kind)):
        adj_v = adj_override if adj_override is not None else adj
        session.add(DailyPrice(id=_next_id(), code=code, trade_date=DAYS[t],
                               close=start_price if start_price else close,
                               adj_factor=adj_v))


def _seed_signal(session, code: str, td: date, action: str, score: float = 0.8):
    session.add(StrategySignal(id=_next_id(), strategy_name="multi_factor",
                               code=code, trade_date=td, score=score, action=action))


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def captured(db, monkeypatch):
    """把 PG upsert 换成直接 add + 记录，断言落库内容 / 供后续读取"""
    store = []

    def fake_upsert(session, model, rows, conflict_cols, update_cols):
        store.append(rows)
        for r in rows:
            if "id" not in r:  # sqlite BigInteger 不自增，显式 id
                r = {**r, "id": _next_id()}
            session.add(model(**r))
        session.commit()
        return len(rows)

    monkeypatch.setattr("app.performance.upsert", fake_upsert)
    return store


# ---------- forward 收益（跨除权日） ----------

def test_fwd_ret_across_ex_dividend():
    """除权日 close 名义下跌，但后复权比值连续 → forward 收益为真实收益"""
    series = [(date(2026, 8, 3), 100.0 * 1.0),     # d0
              (date(2026, 8, 4), 90.0 * 10 / 9),   # d1 除权
              (date(2026, 8, 5), 99.0 * 10 / 9)]   # d2
    assert abs(_fwd_ret(series, date(2026, 8, 3), 2) - (99 * 10 / 9 / 100 - 1)) < 1e-9


def test_fwd_ret_insufficient_data():
    series = [(date(2026, 8, 3), 10.0), (date(2026, 8, 4), 11.0)]
    assert _fwd_ret(series, date(2026, 8, 3), 5) is None  # 不足 5 天
    assert _fwd_ret(series, date(2026, 8, 5), 1) is None  # d0 无行情


# ---------- 命中率 ----------

def _seed_hit_rate_base(db):
    """600001 涨 / 600002 跌 / 600003 除权缓涨 / sh000300 平走（基准，adj_factor=NULL 同生产索引行）"""
    for code, kind in [("600001", "rise"), ("600002", "fall"),
                       ("600003", "exdiv")]:
        _seed_price(db, code, kind)
    _seed_price(db, "sh000300", "flat", adj_override=False)
    db.commit()


def test_compute_hit_rate_known(db, captured):
    _seed_hit_rate_base(db)
    _seed_signal(db, "600001", D0, "BUY")   # fwd5 = 11.0/10-1 = +0.10 → hit
    _seed_signal(db, "600002", D0, "BUY")   # fwd5 = 9.0/10-1  = -0.10 → miss
    _seed_signal(db, "600003", D0, "BUY")   # fwd5 = 9.2×(10/9)/10-1 = +0.0222 → hit
    _seed_signal(db, "600002", D0, "SELL")  # SELL 后下跌 → sell_hit
    db.commit()

    detail = compute_hit_rate(db, end_date=D5)

    w5 = detail["windows"]["5"]
    assert w5["samples"] == 3
    assert abs(w5["hit_rate"] - 2 / 3) < 1e-4
    assert abs(w5["relative_hit"] - 2 / 3) < 1e-4   # 基准平走 → 相对命中 = 绝对命中
    assert abs(w5["avg"] - 0.0074) < 1e-4
    assert abs(w5["median"] - 0.0222) < 1e-4
    assert w5["sell_samples"] == 1
    assert w5["sell_hit_rate"] == 1.0               # 卖出的 600002 后 5 日下跌

    # 10/20 日窗口：仅 8 个交易日，前向数据不足 → 样本数如实为 0（hit_rate None）
    for n in ("10", "20"):
        assert detail["windows"][n]["samples"] == 0
        assert detail["windows"][n]["hit_rate"] is None
    assert detail["buy_samples"] == 3

    # 落库一行 strategy_perf（capture 写库）
    row = db.execute(
        __import__("sqlalchemy").select(StrategyPerf)
    ).scalars().first()
    assert row.metric_type == "hit_rate"
    assert row.detail["windows"]["5"]["hit_rate"] == w5["hit_rate"]


def test_compute_hit_rate_dedupe(db, captured):
    """同股票 30 天内重复 BUY 去重（保留首个）；间隔 >30 天不误杀"""
    _seed_hit_rate_base(db)
    _seed_signal(db, "600001", DAYS[0], "BUY")
    _seed_signal(db, "600001", DAYS[1], "BUY")   # 距上次 1 天 → 去重
    _seed_signal(db, "600001", DAYS[2], "BUY")   # 距上次 2 天 → 去重
    _seed_signal(db, "600002", DAYS[0], "BUY")
    _seed_signal(db, "600002", date(2026, 9, 15), "BUY")  # 43 天后 → 保留
    db.commit()

    detail = compute_hit_rate(db, end_date=date(2026, 9, 15))
    assert detail["buy_samples"] == 3          # 600001 去重后 1 个 + 600002 2 个
    assert detail["sell_samples"] == 0


def test_compute_hit_rate_no_signals(db, captured):
    _seed_hit_rate_base(db)
    db.commit()
    detail = compute_hit_rate(db, end_date=D5)
    assert detail["windows"] == {}
    assert "note" in detail


# ---------- 实盘 vs 回测 vs 基准 ----------

def _seed_overlay(db):
    db.add(Account(id=1, name="主账户", status="active"))
    for i, (d, nav, dd) in enumerate([(DAYS[0], 1.00, 0.00),
                                      (DAYS[1], 1.01, 0.00),
                                      (DAYS[2], 0.99, 0.01)]):
        db.add(AccountNav(id=_next_id(), account_id=1, trade_date=d, nav=nav,
                          total_asset=nav * 1_000_000, drawdown=dd))
    db.add(BacktestResult(job_id=1, fill_mode="t1_open", total_return=0.03,
                          nav=[{"date": d.isoformat(), "nav": n, "benchmark": None}
                               for d, n in [(DAYS[0], 1.00), (DAYS[1], 1.02), (DAYS[2], 1.03)]],
                          created_at=datetime(2026, 8, 12, 22, 0)))
    for code, kind in [("sh000300", "flat")]:
        _seed_price(db, code, kind, start_price=3000.0)
    db.commit()


def test_compute_nav_overlay(db, captured):
    _seed_overlay(db)
    detail = compute_nav_overlay(db)

    assert len(detail["series"]) == 3
    s0, s1, s2 = detail["series"]
    assert s0 == {"date": DAYS[0].isoformat(), "live": 1.0, "bt": 1.0, "benchmark": 1.0}
    assert abs(s1["live"] - 1.01) < 1e-9 and abs(s1["bt"] - 1.02) < 1e-9
    assert abs(s2["live"] - 0.99) < 1e-9 and abs(s2["bt"] - 1.03) < 1e-9

    m = detail["metrics"]
    assert abs(m["live_cum_return"] - (-0.01)) < 1e-9
    assert abs(m["bt_cum_return"] - 0.03) < 1e-9
    assert abs(m["drift"] - (-0.04)) < 1e-9
    assert m["live_points"] == 3 and m["bt_points"] == 3
    assert abs(m["live_max_drawdown"] - 0.0198) < 1e-9  # 运行峰值 1.01 → 0.99


# ---------- 月度报告 ----------

def test_build_monthly_report(db):
    # 当月信号：08 月 1 BUY + 1 SELL + 1 HOLD（09 月信号应排除）
    _seed_signal(db, "600001", date(2026, 8, 10), "BUY")
    _seed_signal(db, "600002", date(2026, 8, 10), "SELL")
    _seed_signal(db, "600003", date(2026, 8, 10), "HOLD")
    _seed_signal(db, "600001", date(2026, 9, 2), "BUY")  # 下月 → 不计
    # 月末净值：08-03 nav 1.0 → 08-31 nav 1.05（月收益 +5%，回撤 3%）
    db.add(Account(id=1, name="主账户", status="active"))
    db.add(AccountNav(id=_next_id(), account_id=1, trade_date=date(2026, 8, 3),
                      nav=1.0, total_asset=1_000_000, drawdown=0.0))
    db.add(AccountNav(id=_next_id(), account_id=1, trade_date=date(2026, 8, 31),
                      nav=1.05, total_asset=1_050_000, drawdown=0.03))
    # 基准 8 月平走
    for t in range(8):
        db.add(DailyPrice(id=_next_id(), code="sh000300", trade_date=DAYS[t],
                          close=3000.0, adj_factor=1.0))
    # 命中率预计算行（period_end ≤ 月末）
    db.add(StrategyPerf(id=1, strategy_name="multi_factor", metric_type="hit_rate",
                        period_start=date(2026, 8, 3), period_end=date(2026, 8, 12),
                        detail={"windows": {"5": {"hit_rate": 0.6667, "relative_hit": 0.6667,
                                                  "samples": 3, "avg": 0.0074,
                                                  "median": 0.0222}},
                                "buy_samples": 3, "sell_samples": 1}))
    db.commit()

    content, summary = build_monthly_report(db, 2026, 8)

    assert "2026-08 月度绩效报告" in content
    assert "BUY 1" in content and "SELL 1" in content and "HOLD 1" in content
    assert "+5.00%" in content                      # 月收益
    assert "3.00%" in content                       # 最大回撤
    assert "0.00%" in content or "基准沪深300" in content  # 基准平走
    assert "67%" in content                          # 5 日命中率 2/3
    assert summary["signal"] == {"BUY": 1, "SELL": 1, "HOLD": 1}
    assert abs(summary["nav"]["return"] - 0.05) < 1e-9
    assert abs(summary["benchmark_return"]) < 1e-9


# ---------- 因子贡献归因（第二期） ----------

def _seed_attribution(db):
    """5 只池股（ma_trend normalized 0.1~0.9）+ 基准平走 + 3 个信号日。

    价格路径：600001 fall（-2%/日，池内最低归一 → Q5 组）、600002~600004 flat、
    600005 rise（+2%/日，池内最高归一 → Q1 组）。基准 sh000300 平走（adj NULL→1.0）。
    信号：08-04 BUY 600005 → 08-05 BUY 600004 → 08-06 SELL 600004 → 08-07 SELL 600005。
    """
    for i, (code, kind) in enumerate([("600001", "fall"), ("600002", "flat"),
                                      ("600003", "flat"), ("600004", "flat"),
                                      ("600005", "rise")]):
        db.add(StockBasic(code=code, name=f"股{code}", market="SH", universe="hs300"))
        _seed_price(db, code, kind)
    _seed_price(db, "sh000300", "flat", adj_override=False)
    for td in DAYS[:4]:  # 08-03 ~ 08-06，normalized 每日同值横截面 0.1~0.9
        for i, code in enumerate(["600001", "600002", "600003", "600004", "600005"]):
            db.add(FactorValue(id=_next_id(), code=code, factor_name="ma_trend",
                               trade_date=td, value=0.1 + 0.2 * i,
                               rank=i + 1, normalized=0.1 + 0.2 * i))
    _seed_signal(db, "600005", DAYS[1], "BUY")   # 08-04 持仓 {600005}
    _seed_signal(db, "600004", DAYS[2], "BUY")   # 08-05 持仓 {600005, 600004}
    _seed_signal(db, "600004", DAYS[3], "SELL")  # 08-06 持仓 {600005}
    _seed_signal(db, "600005", DAYS[4], "SELL")  # 08-07 持仓 {}
    db.commit()


def test_holdings_rebuild():
    """BUY 加入 / SELL 移除 / HOLD 不动作，初始空集逐日推进"""
    signals = [
        ("600001", DAYS[1], "BUY"),
        ("600002", DAYS[2], "BUY"),
        ("600001", DAYS[3], "SELL"),
        ("600003", DAYS[3], "HOLD"),   # HOLD 仅维持，绝不新加入（振荡缺陷教训）
    ]
    out = _rebuild_holdings(signals)
    assert out[DAYS[1]] == {"600001"}
    assert out[DAYS[2]] == {"600001", "600002"}
    assert out[DAYS[3]] == {"600002"}
    assert len(out) == 3


def test_compute_attribution(db, captured):
    """手算：组合日收益 / 暴露 / Q1−Q5 因子日收益 / 贡献 / 残差逐日核对"""
    _seed_attribution(db)
    detail = compute_attribution(db)

    assert detail["samples"] == 3                       # 08-07 空仓跳过
    d = {row["date"]: row for row in detail["daily"]}
    r4, r5, r6 = d[DAYS[1].isoformat()], d[DAYS[2].isoformat()], d[DAYS[3].isoformat()]

    # 组合日收益（等权持仓，基准平走 → 超额 = 组合收益）
    assert abs(r4["portfolio_ret"] - 0.02) < 1e-5
    assert abs(r5["portfolio_ret"] - 0.00980392) < 1e-5
    assert abs(r6["portfolio_ret"] - 0.01923077) < 1e-5
    for r in (r4, r5, r6):
        assert abs(r["bench_ret"]) < 1e-9
        assert r["excess"] == r["portfolio_ret"]

    # 暴露 = 持仓均值 − 市场均值（市场 0.5）
    assert abs(r4["exposure"]["ma_trend"] - 0.4) < 1e-4   # 0.9 − 0.5
    assert abs(r5["exposure"]["ma_trend"] - 0.3) < 1e-4   # (0.9+0.7)/2 − 0.5
    assert abs(r6["exposure"]["ma_trend"] - 0.4) < 1e-4

    # 因子日收益 = Q1(600005 rise) − Q5(600001 fall)
    assert abs(r4["factor_ret"]["ma_trend"] - 0.04) < 1e-5
    assert abs(r5["factor_ret"]["ma_trend"] - 0.040016) < 1e-5

    # 贡献 = 暴露 × 因子日收益；残差 = 超额 − Σ 贡献（自洽）
    assert abs(r4["contrib"]["ma_trend"] - 0.016) < 1e-5
    assert abs(r5["contrib"]["ma_trend"] - 0.012005) < 1e-5
    assert abs(r4["residual"] - 0.004) < 1e-5
    assert abs(r5["residual"] - (-0.00220108)) < 1e-5
    for r in (r4, r5, r6):
        assert abs(r["excess"] - r["contrib"]["ma_trend"] - r["residual"]) < 1e-6
        assert r["contrib"]["pe_ratio"] is None          # 无因子数据 → None 而非 0

    # 落库 strategy_perf
    row = db.execute(select(StrategyPerf)).scalars().first()
    assert row.metric_type == "attribution"
    assert row.detail["samples"] == 3
    assert row.detail["factors"][0] == "ma_trend"


def test_attribution_monthly(db, captured):
    """月度聚合 = 各日简单求和，贡献/残差/超额自洽；暴露取日均"""
    _seed_attribution(db)
    detail = compute_attribution(db)
    assert len(detail["monthly"]) == 1
    m = detail["monthly"][0]
    assert m["month"] == "2026-08"
    assert m["days"] == 3
    assert abs(m["portfolio_ret"] - (0.02 + 0.00980392 + 0.01923077)) < 1e-4
    assert m["excess"] == m["portfolio_ret"]              # 基准平走
    expected_c = sum(r["contrib"]["ma_trend"] for r in detail["daily"])
    assert abs(m["contrib"]["ma_trend"] - expected_c) < 1e-9
    expected_r = sum(r["residual"] for r in detail["daily"])
    assert abs(m["residual"] - expected_r) < 1e-9
    assert abs(m["exposure"]["ma_trend"] - (0.4 + 0.3 + 0.4) / 3) < 1e-4
    assert m["contrib"]["pe_ratio"] == 0.0                # 无数据因子聚合为 0


def test_attribution_empty(db, captured):
    """无信号 → 空 detail 不报错"""
    _seed_price(db, "600005", "rise")
    _seed_price(db, "sh000300", "flat", adj_override=False)
    db.commit()
    detail = compute_attribution(db)
    assert "note" in detail
    assert detail["samples"] == 0


def test_attribution_no_factor_data(db, captured):
    """有信号但池内无 normalized → 因子贡献全 None，残差 = 超额"""
    for code in ["600001", "600005"]:
        db.add(StockBasic(code=code, name=f"股{code}", market="SH", universe="hs300"))
        _seed_price(db, code, "rise")
    _seed_price(db, "sh000300", "flat", adj_override=False)
    _seed_signal(db, "600005", DAYS[1], "BUY")
    db.commit()
    detail = compute_attribution(db)
    assert detail["samples"] == 1
    r = detail["daily"][0]
    assert r["contrib"]["ma_trend"] is None
    assert r["residual"] == r["excess"]
