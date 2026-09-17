"""数据质量检查（Issue #6）：每日采集完成后体检数据，结果落 task_run 台账并推送飞书

7 项检查：
1. coverage        行情覆盖：股票池（hs300+zz500）当日 bar 覆盖率
2. missing_days    缺失交易日：最近 N 个开市日中全池无 bar 的开市日
3. duplicates      重复数据：daily_price/valuation/financial/factor 四表唯一键冲突
4. price_anomalies 价格异常：bar 合法性 + 按板块涨跌幅越界
5. valuation       估值新鲜度：daily_valuation 最新日 vs 行情最新日
6. financial       财务新鲜度：近 270 天内有公告的股票占比（新股豁免 90 天）
7. benchmark       指数基准：sh000300 最新 bar 是否同步

聚合：任一 fail → 整体 fail（红卡）；仅 warn → warn（蓝卡）；全 ok → ok（绿卡）。
check_data_quality 返回 detail JSONB（task_run.detail 记录 + 通知卡片渲染共用）：
{
  trade_date, overall, checks_total, ok, warn, fail,
  results: [{name, level, message}],   # 卡片逐行渲染
  check_details: {name: metrics},      # 台账结构化明细（LLM-ready）
  message: 摘要
}
"""
import logging
import os
from datetime import date, timedelta

from sqlalchemy import func, select

from app.models.tables import (DailyPrice, DailyValuation, FactorValue,
                               FinancialIndicator, StockBasic, TradeCalendar)

logger = logging.getLogger("data_quality")

# ---- 阈值 ----
COVERAGE_MIN = 0.90           # 行情覆盖下限（与 market_ready 一致）
COVERAGE_DRIFT_WARN = 0.02    # 采集范围分母漂移 >2% → warn（Issue #13 R2）
MISSING_DAY_WINDOW = 10       # 缺失交易日：回溯最近 N 个开市日
VALUATION_WARN_LAG = 1        # 估值落后 ≥1 天 warn
VALUATION_FAIL_LAG = 3        # 落后 ≥3 天 fail
FINANCIAL_FRESH_DAYS = 270    # 财务：近 270 天应有公告（覆盖最近 2 个披露期）
FINANCIAL_NEW_LIST_DAYS = 90  # 新股豁免：上市不足 90 天无财报属正常
FINANCIAL_COVERAGE_MIN = 0.90  # 财务覆盖下限
LIMIT_HARD = 30.5             # 日涨跌幅 > 30.5% 必错（A股上限 30%，仅北交所）
BOARD_LIMIT_TOL = 0.5         # 板块涨跌幅容差（百分点）


def _collect_scope() -> str:
    """采集范围闸门（Issue #13）：读 `COLLECT_SCOPE` env。

    'pool'（默认）→ coverage 分母 = 800 池（现状，逐字不变）；
    'a_share'      → 分母 = 全量（data_scope='a_share'），且启用三重过滤。
    **必须与 collector 侧同批翻** —— 只翻一边会造成 coverage 假绿/假红中间态。
    """
    return ("a_share" if os.getenv("COLLECT_SCOPE", "pool").strip().lower() == "a_share"
            else "pool")


def _board_limit(code: str) -> float:
    """板块日涨跌幅上限（%）：创业/科创 20%、北交 30%、其余主板 10%"""
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "4")):
        return 30.0
    return 10.0


def _market_latest(db) -> date | None:
    """最新有行情的交易日（跳过指数伪股票 sh*）"""
    return db.execute(
        select(func.max(DailyPrice.trade_date))
        .where(DailyPrice.code.not_like("sh%"))
    ).scalar()


# ---------- 单项检查 ----------

def _coverage_pool(db, td: date) -> list[str]:
    """coverage 分母（采集范围内的代码），按闸门分档（Issue #13）。

    - pool（默认）：`universe IN ('hs300','zz500')` —— **与旧实现逐字等价**；
    - a_share：`data_scope='a_share'`，再叠**三重过滤**，每条都对应一个静默故障：
        · `status='L'`     —— 退市股不再要求有 bar（否则永久 missing → 自愈空转，R3）；
        · `list_date<=td`  —— 未上市股不要求（新股/待上市，R3）；
        · （data_scope 已排除北交所/指数）。
      不过滤会让停牌/退市/未上市股永久进 missing_codes，自愈每日空跑、红卡不消。
    """
    if _collect_scope() != "a_share":
        return db.execute(
            select(StockBasic.code).where(StockBasic.universe.in_(("hs300", "zz500")))
        ).scalars().all()
    return db.execute(
        select(StockBasic.code).where(
            StockBasic.data_scope == "a_share",
            StockBasic.status == "L",
            StockBasic.list_date <= td,
        )
    ).scalars().all()


def _check_coverage(db, td: date) -> dict:
    pool = _coverage_pool(db, td)
    if not pool:
        return {"name": "coverage", "level": "warn",
                "message": "行情覆盖　股票池为空", "metrics": {"pool": 0}}
    with_bar = db.execute(
        select(DailyPrice.code).distinct().where(
            DailyPrice.trade_date == td, DailyPrice.code.in_(pool))
    ).scalars().all()
    ratio = len(with_bar) / len(pool)
    level = "ok" if ratio >= COVERAGE_MIN else "fail"
    msg = f"行情覆盖　{len(with_bar)}/{len(pool)} 股票有行情（{ratio * 100:.1f}%）"
    if level != "ok":
        msg += f"，低于 {COVERAGE_MIN * 100:.0f}%"

    # missing_all = 分母内当日无 bar 的全部代码（含停牌等，供人看）。
    missing_all = sorted(set(pool) - set(with_bar))
    # suspect = 「昨日有 bar 而今日无」—— 真缺口（当日同步未跑完/失败），
    # 而非停牌。**这是自愈 stage1 的 diff-repair 输入**（Issue #13）：只看真缺口，
    # 停牌股昨日今日都无 bar、不进 suspect，自愈不再每日空跑。
    prev = db.execute(
        select(func.max(DailyPrice.trade_date))
        .where(DailyPrice.code.not_like("sh%"), DailyPrice.trade_date < td)
    ).scalar()
    suspect: list[str] = []
    if prev is not None:
        prev_bar = set(db.execute(
            select(DailyPrice.code).distinct().where(
                DailyPrice.trade_date == prev, DailyPrice.code.in_(pool))
        ).scalars().all())
        suspect = sorted(prev_bar - set(with_bar))
    # 自愈用 suspect；suspect 为空而 miss 不为空 → 是停牌面而非缺口，不触自愈
    missing_codes = suspect if _collect_scope() == "a_share" else missing_all

    # 分母漂移守卫（R2）：data_scope 标记由 stock.py 单点重标，若列表生产者漏写
    # 新 IPO，分母会悄悄偏小 → coverage 假绿。此处用 market 真值交叉校验：
    # 偏差 >2% 说明标记漂移，降 warn 提醒（不 fail —— 数据本身可能是好的）。
    metrics = {"pool": len(pool), "with_bar": len(with_bar),
               "pct": round(ratio * 100, 2),
               "missing_codes": missing_codes,
               "missing_all": missing_all,
               "suspect": suspect}
    if _collect_scope() == "a_share":
        truth = db.execute(
            select(func.count()).select_from(StockBasic)
            .where(StockBasic.market.in_(("SH", "SZ")),
                   StockBasic.status == "L",
                   StockBasic.list_date <= td)
        ).scalar() or 0
        drift = abs(len(pool) - truth) / truth if truth else 0.0
        metrics["expect_pool"] = truth
        metrics["drift_pct"] = round(drift * 100, 2)
        if drift > COVERAGE_DRIFT_WARN:
            msg += (f"　⚠️ 分母漂移 {drift * 100:.1f}%（预期 {truth}，"
                    f"实 {len(pool)}）——data_scope 标记可能未更新")
            if level == "ok":
                level = "warn"
    return {"name": "coverage", "level": level, "message": msg, "metrics": metrics}


def _check_missing_days(db, td: date) -> dict:
    latest = _market_latest(db)
    if latest is None:
        return {"name": "missing_days", "level": "warn",
                "message": "缺失交易日　无行情数据", "metrics": {}}
    days = db.execute(
        select(TradeCalendar.cal_date)
        .where(TradeCalendar.is_open.is_(True), TradeCalendar.cal_date <= latest)
        .order_by(TradeCalendar.cal_date.desc()).limit(MISSING_DAY_WINDOW)
    ).scalars().all()
    missing = []
    for d in days:
        n = db.execute(
            select(func.count()).select_from(DailyPrice)
            .where(DailyPrice.trade_date == d, DailyPrice.code.not_like("sh%"))
        ).scalar()
        if n == 0:
            missing.append(d)
    if not missing:
        return {"name": "missing_days", "level": "ok",
                "message": f"缺失交易日　最近 {len(days)} 个开市日无缺失",
                "metrics": {"window": len(days), "missing": []}}
    msg = (f"缺失交易日　最近 {len(days)} 个开市日缺失 {len(missing)} 天："
           + "、".join(str(d) for d in missing))
    return {"name": "missing_days", "level": "fail", "message": msg,
            "metrics": {"window": len(days), "missing": [str(d) for d in missing]}}


def _dup_count(db, model, *cols) -> int:
    """唯一键（cols）冲突的键组数（对无唯一约束的库同样可查，sqlite 测试依赖此点）"""
    q = select(*cols, func.count().label("c")).group_by(*cols).having(func.count() > 1)
    return len(db.execute(q).all())


def _check_duplicates(db, td: date) -> dict:
    tables = [
        ("daily_price", DailyPrice, (DailyPrice.code, DailyPrice.trade_date)),
        ("daily_valuation", DailyValuation,
         (DailyValuation.code, DailyValuation.trade_date)),
        ("financial_indicator", FinancialIndicator,
         (FinancialIndicator.code, FinancialIndicator.report_date)),
        ("factor_value", FactorValue,
         (FactorValue.code, FactorValue.factor_name, FactorValue.trade_date)),
    ]
    broken = {name: _dup_count(db, model, *cols) for name, model, cols in tables}
    broken = {k: v for k, v in broken.items() if v}
    if not broken:
        return {"name": "duplicates", "level": "ok",
                "message": "重复数据　4 张表均 0 条",
                "metrics": {name: 0 for name, _, _ in tables}}
    msg = "重复数据　" + "　".join(f"{k} {v} 个键" for k, v in broken.items())
    return {"name": "duplicates", "level": "fail", "message": msg,
            "metrics": dict(broken)}


def _check_price_anomalies(db, td: date) -> dict:
    latest = _market_latest(db)
    if latest is None:
        return {"name": "price_anomalies", "level": "warn",
                "message": "价格异常　无行情数据", "metrics": {}}
    prev = db.execute(
        select(func.max(DailyPrice.trade_date)).where(
            DailyPrice.code.not_like("sh%"), DailyPrice.trade_date < latest)
    ).scalar()
    cur = {r.code: r for r in db.execute(
        select(DailyPrice).where(DailyPrice.trade_date == latest)).scalars().all()}
    prev_close = {}
    if prev is not None:
        prev_close = {r.code: r.close for r in db.execute(
            select(DailyPrice).where(DailyPrice.trade_date == prev)).scalars().all()
            if r.close}

    fails, warns = [], []
    for code, r in cur.items():
        if r.close is None or r.close <= 0:
            fails.append(f"{code} 收盘价 {r.close}")
            continue
        if r.high is None or r.low is None or r.high < r.low:
            fails.append(f"{code} 高低价异常（低 {r.low}/高 {r.high}）")
            continue
        if r.open is not None and (
                r.high < max(r.open, r.close) or r.low > min(r.open, r.close)):
            fails.append(f"{code} 开收价超出高低区间")
            continue
        if r.volume is not None and r.volume < 0:
            fails.append(f"{code} 成交量负数")
            continue
        if r.amount is not None and r.amount < 0:
            fails.append(f"{code} 成交额负数")
            continue
        pc = prev_close.get(code)
        if pc:
            pct = abs(float(r.close) / float(pc) - 1) * 100
            if pct > LIMIT_HARD:
                fails.append(f"{code} 涨跌幅 {pct:.1f}% 超上限")
            elif pct > _board_limit(code) + BOARD_LIMIT_TOL:
                warns.append(f"{code} 涨跌幅 {pct:.1f}% 越板块限制")

    level = "fail" if fails else ("warn" if warns else "ok")
    samples = (fails + warns)[:5]
    if fails or warns:
        msg = (f"价格异常　失败 {len(fails)} / 警告 {len(warns)}"
               + ("：" + "、".join(samples) + ("…" if len(fails) + len(warns) > 5 else "")
                  if samples else ""))
    else:
        msg = "价格异常　0 条"
    return {"name": "price_anomalies", "level": level, "message": msg,
            "metrics": {"fail_count": len(fails), "warn_count": len(warns),
                        "samples": samples}}


def _check_valuation(db, td: date) -> dict:
    p_latest = _market_latest(db)
    if p_latest is None:
        return {"name": "valuation", "level": "warn",
                "message": "估值　无行情数据", "metrics": {}}
    v_latest = db.execute(select(func.max(DailyValuation.trade_date))).scalar()
    if v_latest is None:
        return {"name": "valuation", "level": "fail",
                "message": f"估值　从未同步（行情已到 {p_latest}）",
                "metrics": {"latest": None, "lag_days": None}}
    lag = (p_latest - v_latest).days
    level = ("ok" if lag < VALUATION_WARN_LAG
             else ("warn" if lag < VALUATION_FAIL_LAG else "fail"))
    msg = f"估值　已同步至 {v_latest}" + ("" if lag == 0 else f"（落后 {lag} 天）")
    return {"name": "valuation", "level": level, "message": msg,
            "metrics": {"latest": str(v_latest), "lag_days": lag}}


def _check_financial(db, td: date) -> dict:
    p_latest = _market_latest(db)
    if p_latest is None:
        return {"name": "financial", "level": "warn",
                "message": "财务　无行情数据", "metrics": {}}
    cutoff = p_latest - timedelta(days=FINANCIAL_FRESH_DAYS)
    new_cutoff = p_latest - timedelta(days=FINANCIAL_NEW_LIST_DAYS)
    # 财务已是全市场入库（finance.py 只按 stock_basic 存在性过滤），故 a_share
    # 下分母同样翻全量；默认 pool 分支与旧实现逐字等价（策略域）。
    if _collect_scope() == "a_share":
        pool = db.execute(
            select(StockBasic.code, StockBasic.list_date)
            .where(StockBasic.data_scope == "a_share",
                   StockBasic.status == "L",
                   StockBasic.list_date <= p_latest)
        ).all()
    else:
        pool = db.execute(
            select(StockBasic.code, StockBasic.list_date)
            .where(StockBasic.universe.in_(("hs300", "zz500")))
        ).all()
    if not pool:
        return {"name": "financial", "level": "warn",
                "message": "财务　股票池为空", "metrics": {}}
    latest_ann = dict(db.execute(
        select(FinancialIndicator.code, func.max(FinancialIndicator.announce_date))
        .group_by(FinancialIndicator.code)
    ).all())
    covered, stale = [], []
    for code, list_date in pool:
        if list_date and list_date > new_cutoff:
            continue  # 新股豁免：上市未满 90 天暂无财报属正常
        ann = latest_ann.get(code)
        if ann and ann >= cutoff:
            covered.append(code)
        else:
            stale.append((code, ann))
    total = len(covered) + len(stale)
    pct = (len(covered) / total * 100) if total else 0.0
    level = "ok" if pct >= FINANCIAL_COVERAGE_MIN * 100 else "fail"
    pool_anns = [ann for code, _ in pool if (ann := latest_ann.get(code))]
    max_ann = max(pool_anns) if pool_anns else None
    msg = f"财务　最近公告 {max_ann}，覆盖 {pct:.1f}%"
    if level != "ok":
        msg += f"（低于 {FINANCIAL_COVERAGE_MIN * 100:.0f}%，新股除外）"
    return {"name": "financial", "level": level, "message": msg,
            "metrics": {"latest_announce": str(max_ann) if max_ann else None,
                        "coverage_pct": round(pct, 2), "stale_count": len(stale)}}


def _check_benchmark(db, td: date) -> dict:
    p_latest = _market_latest(db)
    if p_latest is None:
        return {"name": "benchmark", "level": "warn",
                "message": "指数基准　无行情数据", "metrics": {}}
    i_latest = db.execute(
        select(func.max(DailyPrice.trade_date)).where(DailyPrice.code == "sh000300")
    ).scalar()
    if i_latest is None:
        return {"name": "benchmark", "level": "fail",
                "message": f"指数基准　沪深300 无数据（行情已到 {p_latest}）",
                "metrics": {"latest": None, "lag_days": None}}
    lag = (p_latest - i_latest).days
    level = "ok" if lag == 0 else "fail"
    msg = f"指数基准　沪深300 已同步至 {i_latest}" + ("" if lag == 0 else f"（落后 {lag} 天）")
    return {"name": "benchmark", "level": level, "message": msg,
            "metrics": {"latest": str(i_latest), "lag_days": lag}}


# ---------- 聚合 ----------

def check_data_quality(db, td: date | None = None) -> dict:
    """运行全部检查并聚合。返回 detail JSONB（task_run 记录 + 通知卡片渲染共用）"""
    if td is None:
        td = _market_latest(db)
    if td is None:
        return {"trade_date": None, "overall": "fail", "checks_total": 0,
                "ok": 0, "warn": 0, "fail": 1, "results": [],
                "check_details": {}, "message": "无行情数据"}
    checks = [_check_coverage(db, td), _check_missing_days(db, td),
              _check_duplicates(db, td), _check_price_anomalies(db, td),
              _check_valuation(db, td), _check_financial(db, td),
              _check_benchmark(db, td)]
    levels = [c["level"] for c in checks]
    overall = "fail" if "fail" in levels else ("warn" if "warn" in levels else "ok")
    results = [{"name": c["name"], "level": c["level"], "message": c["message"]}
               for c in checks]
    summary = ("全部通过" if overall == "ok"
               else f"{levels.count('fail')} 项异常" if overall == "fail"
               else f"{levels.count('warn')} 项警告")
    return {
        "trade_date": str(td), "overall": overall,
        "checks_total": len(checks),
        "ok": levels.count("ok"), "warn": levels.count("warn"),
        "fail": levels.count("fail"),
        "results": results,
        "check_details": {c["name"]: c["metrics"] for c in checks},
        "message": summary,
    }
