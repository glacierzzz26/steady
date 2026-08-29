"""策略效果度量（方向① 第一期）：命中率 + 实盘vs回测对照 + 月度绩效报告

G9 同款模式：quant-engine 预计算落 strategy_perf（迁移 006），Go/前端只读。
  幂等：UNIQUE(strategy_name, period_end, metric_type) + db.upsert，每日 21:20 覆盖重算。

指标口径：
  hit_rate    BUY 信号样本的 forward 5/10/20 交易日收益（后复权 = close×adj_factor 比值，
              跨除权日正确）；hit_rate=收益>0 占比、relative_hit=跑赢同窗口 sh000300 占比。
              同股票 30 日历天内重复 BUY 去重（保留首个，避免重叠持有期双计）。
              SELL 信号同窗口给 sell_hit_rate（SELL 后下跌=卖对了）。
  nav_overlay 实盘(account_nav 主账户) vs 回测(最新 t1_open backtest nav) vs 基准(sh000300)，
              各以起点归一 1.0，共同日期轴叠加 + 累计收益/回撤/drift 指标。
  简化说明：forward 窗口按「该标的自有交易日」推进（停牌日自动跳过），基准按 sh000300
              自有交易日，v1 接受两窗口微差（相对命中仍可比）。
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.db import upsert
from app.models.tables import (
    Account,
    AccountNav,
    BacktestResult,
    DailyPrice,
    StrategyPerf,
    StrategySignal,
)

logger = logging.getLogger(__name__)

# forward 收益观察窗口（交易日数）
WINDOWS = (5, 10, 20)
# 同股票 BUY 去重窗口（日历天，≈20 交易日），避免重叠持有期双计
_DEDUP_CAL_DAYS = 30
# 基准：沪深300 指数
_BENCHMARK_CODE = "sh000300"
DEFAULT_STRATEGY = "multi_factor"


# ---------- 工具 ----------

def _adj_series(db, codes: list[str], d0: date, d1: date) -> dict[str, list[tuple[date, float]]]:
    """区间内各 code 的 (trade_date, 后复权价) 有序序列（close×adj_factor）

    adj_factor 为 NULL（指数行，基准不需要复权）→ 按 1.0 处理，用原始 close。
    """
    rows = db.execute(
        select(DailyPrice.code, DailyPrice.trade_date,
               DailyPrice.close, DailyPrice.adj_factor)
        .where(DailyPrice.code.in_(codes),
               DailyPrice.trade_date >= d0,
               DailyPrice.trade_date <= d1)
        .order_by(DailyPrice.code, DailyPrice.trade_date)
    ).all()
    series: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for code, td, close, adj in rows:
        if close is None:
            continue
        adj = float(adj) if adj is not None and float(adj) > 0 else 1.0
        series[code].append((td, float(close) * adj))
    return series


def _fwd_ret(series: list[tuple[date, float]], d0: date, n: int) -> float | None:
    """d0 起第 n 个交易日后复权收益；d0 当日无行情或不足 n 天 → None"""
    for i, (td, _) in enumerate(series):
        if td == d0:
            j = i + n
            if j >= len(series):
                return None
            return series[j][1] / series[i][1] - 1.0
        if td > d0:
            return None  # d0 该标的无行情（停牌/未上市），样本无效
    return None


def _bench_series(db, d0: date, d1: date) -> list[tuple[date, float]]:
    """基准（sh000300）后复权序列"""
    rows = db.execute(
        select(DailyPrice.trade_date, DailyPrice.close, DailyPrice.adj_factor)
        .where(DailyPrice.code == _BENCHMARK_CODE,
               DailyPrice.trade_date >= d0,
               DailyPrice.trade_date <= d1)
        .order_by(DailyPrice.trade_date)
    ).all()
    out = []
    for td, close, adj in rows:
        if close is None:
            continue
        adj = float(adj) if adj is not None and float(adj) > 0 else 1.0
        out.append((td, float(close) * adj))
    return out


def _stats(values: list[float]) -> dict:
    """命中率聚合指标；空列表 → 全 None（前端如实显示样本不足）"""
    if not values:
        return {"hit_rate": None, "avg": None, "median": None, "samples": 0}
    vs = sorted(values)
    n = len(vs)
    med = vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2
    return {
        "hit_rate": round(sum(1 for v in vs if v > 0) / n, 4),
        "avg": round(sum(vs) / n, 4),
        "median": round(med, 4),
        "samples": n,
    }


def _max_drawdown(series: list[float]) -> float:
    """最大回撤（正数，0=无回撤）"""
    peak, mdd = -1e18, 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, 1 - v / peak)
    return round(mdd, 4)


# ---------- hit_rate ----------

def compute_hit_rate(db, strategy: str = DEFAULT_STRATEGY,
                     end_date: date | None = None) -> dict:
    """BUY/SELL 信号 forward 窗口命中率 → 落 strategy_perf(metric_type='hit_rate')

    end_date 默认今天；窗口 N 按标的自有交易日推进（停牌自动跳过）。
    返回 detail（供单测/月报断言），并 upsert 落库。
    """
    if end_date is None:
        end_date = date.today()
    # 1. 取全史 BUY/SELL 信号（≤ end_date），按 code 分组去重
    rows = db.execute(
        select(StrategySignal.code, StrategySignal.trade_date, StrategySignal.action)
        .where(StrategySignal.strategy_name == strategy,
               StrategySignal.action.in_(["BUY", "SELL"]),
               StrategySignal.trade_date <= end_date)
        .order_by(StrategySignal.trade_date)
    ).all()
    buys: dict[str, list[date]] = defaultdict(list)
    sells: dict[str, list[date]] = defaultdict(list)
    for code, td, action in rows:
        (buys if action == "BUY" else sells)[code].append(td)

    def dedupe(dates: list[date]) -> list[date]:
        kept: list[date] = []
        for d in dates:
            if not kept or (d - kept[-1]).days > _DEDUP_CAL_DAYS:
                kept.append(d)
        return kept

    buy_samples: list[tuple[str, date]] = [
        (c, d) for c, dates in buys.items() for d in dedupe(dates)]
    sell_samples: list[tuple[str, date]] = [
        (c, d) for c, dates in sells.items() for d in dedupe(dates)]
    if not buy_samples and not sell_samples:
        detail = {"windows": {}, "note": "窗口内无信号样本"}
        _store(db, strategy, end_date, end_date, "hit_rate", detail)
        return detail

    # 2. 拉复权价（覆盖信号日 ~ 最晚 forward 终点）
    codes = list({c for c, _ in buy_samples + sell_samples}) + [_BENCHMARK_CODE]
    d0 = min(d for _, d in buy_samples + sell_samples)
    d1 = max(end_date, max(d for _, d in buy_samples + sell_samples))
    series = _adj_series(db, codes, d0, d1)
    bench = series.get(_BENCHMARK_CODE, [])

    # 3. 逐窗口聚合（键用字符串，与 JSON 落库 / Go / 前端契约一致）
    windows: dict[str, dict] = {}
    for n in WINDOWS:
        buy_fwd, buy_rel, sell_fwd = [], [], []
        for code, d in buy_samples:
            fwd = _fwd_ret(series.get(code, []), d, n)
            if fwd is None:
                continue
            buy_fwd.append(fwd)
            b = _fwd_ret(bench, d, n)
            if b is not None:
                buy_rel.append(fwd - b)
        for code, d in sell_samples:
            fwd = _fwd_ret(series.get(code, []), d, n)
            if fwd is not None:
                sell_fwd.append(fwd)
        st = _stats(buy_fwd)
        rel = _stats(buy_rel)
        sell = _stats([-v for v in sell_fwd])  # SELL 后下跌=v<0；翻正后 hit_rate=下跌占比
        windows[str(n)] = {
            **st,
            "relative_hit": rel["hit_rate"],
            "sell_hit_rate": sell["hit_rate"],
            "sell_samples": sell["samples"],
            "avg_excess": rel["avg"],
        }
    detail = {
        "windows": windows,
        "buy_samples": len(buy_samples),
        "sell_samples": len(sell_samples),
        "period_start": d0.isoformat(),
    }
    _store(db, strategy, d0, end_date, "hit_rate", detail)
    logger.info("命中率计算完成 %s~%s：BUY 样本 %d，SELL 样本 %d",
                d0, end_date, len(buy_samples), len(sell_samples))
    return detail


# ---------- nav_overlay ----------

def compute_nav_overlay(db, strategy: str = DEFAULT_STRATEGY) -> dict:
    """实盘 vs 回测 vs 基准 归一净值叠加 → 落 strategy_perf(metric_type='nav_overlay')"""
    # 1. 实盘：主账户（id 最小，与 Go GetPrimary 同口径）account_nav
    live_rows = db.execute(
        select(AccountNav.trade_date, AccountNav.nav)
        .where(AccountNav.account_id == _primary_account_id(db))
        .order_by(AccountNav.trade_date)
    ).all()
    live = [(d, float(n)) for d, n in live_rows if n is not None]
    # 2. 回测：最新 t1_open backtest_result 的 nav 序列
    bt = _latest_backtest_nav(db)
    # 3. 基准
    if not live:
        detail = {"series": [], "metrics": {}, "note": "实盘净值尚无可对照样本"}
        _store(db, strategy, date.today(), date.today(), "nav_overlay", detail)
        return detail
    d0 = min(live[0][0], bt[0][0] if bt else live[0][0])
    d1 = date.today()
    bench_rows = _bench_series(db, d0, d1)
    bench = {d: v for d, v in bench_rows}
    bench_base = bench.get(live[0][0]) or (bench_rows[0][1] if bench_rows else None)

    # 4. 共同日期轴（live ∪ bt，升序），各起点归一
    axis = sorted({d for d, _ in live} | ({d for d, _ in bt} if bt else set()))
    lv = {d: v / live[0][1] for d, v in live}
    bv = {d: v / bt[0][1] for d, v in bt} if bt else {}
    series = []
    for d in axis:
        item = {"date": d.isoformat(),
                "live": round(lv[d], 4) if d in lv else None,
                "bt": round(bv[d], 4) if d in bv else None}
        if bench_base:
            item["benchmark"] = round(bench[d] / bench_base, 4) if d in bench else None
        series.append(item)

    live_cum = live[-1][1] / live[0][1] - 1
    bt_cum = (bt[-1][1] / bt[0][1] - 1) if bt else None
    metrics = {
        "live_cum_return": round(live_cum, 4),
        "bt_cum_return": round(bt_cum, 4) if bt_cum is not None else None,
        "drift": round(live_cum - (bt_cum or 0), 4),
        "live_max_drawdown": _max_drawdown([v / live[0][1] for _, v in live]),
        "bt_max_drawdown": _max_drawdown([v / bt[0][1] for _, v in bt]) if bt else None,
        "live_points": len(live),
        "bt_points": len(bt) if bt else 0,
    }
    detail = {"series": series, "metrics": metrics,
              "period_start": axis[0].isoformat(), "period_end": d1.isoformat()}
    _store(db, strategy, axis[0], d1, "nav_overlay", detail)
    logger.info("实盘vs回测对照完成：实盘 %d 点，回测 %d 点，drift=%.4f",
                len(live), len(bt) if bt else 0, metrics["drift"])
    return detail


def _primary_account_id(db) -> int:
    """主账户 = id 最小者（与 Go account_repo.GetPrimary 同口径）"""
    return db.execute(
        select(Account.id).order_by(Account.id).limit(1)
    ).scalar() or 1


def _latest_backtest_nav(db) -> list[tuple[date, float]]:
    """最新 t1_open 回测的 nav 序列 [{date,nav}]（benchmark 忽略，基准单独取）"""
    row = db.execute(
        select(BacktestResult.nav)
        .where(BacktestResult.fill_mode == "t1_open")
        .order_by(BacktestResult.created_at.desc())
        .limit(1)
    ).scalar()
    if not row:
        return []
    out = []
    for p in (row or []):
        try:
            d = date.fromisoformat(p.get("date"))
            v = float(p.get("nav"))
            if v > 0:
                out.append((d, v))
        except (TypeError, ValueError, AttributeError):
            continue
    return sorted(out)


# ---------- 月度报告 ----------

def build_monthly_report(db, year: int, month: int) -> tuple[str, dict]:
    """月度绩效报告卡片文本 + 摘要 dict（供 job 发送/记录）。

    口径：月末 account_nav 首末净值 → 月收益/回撤；当月 BUY/SELL/HOLD 计数；
    当月 hit_rate（period_end 为月末当日或之前最近的预计算行）；基准月收益。
    """
    m_start = date(year, month, 1)
    m_end = date(year, month + 1, 1) - timedelta(days=1) if month < 12 \
        else date(year, 12, 31)

    # 当月信号计数
    counts = dict(db.execute(
        select(StrategySignal.action, func.count())
        .where(StrategySignal.trade_date >= m_start,
               StrategySignal.trade_date <= m_end)
        .group_by(StrategySignal.action)
    ).all())
    n_buy, n_sell, n_hold = counts.get("BUY", 0), counts.get("SELL", 0), counts.get("HOLD", 0)

    # 月末净值（主账户）
    navs = db.execute(
        select(AccountNav.trade_date, AccountNav.nav, AccountNav.drawdown)
        .where(AccountNav.account_id == _primary_account_id(db),
               AccountNav.trade_date >= m_start,
               AccountNav.trade_date <= m_end)
        .order_by(AccountNav.trade_date)
    ).all()
    if navs:
        v0, v1 = float(navs[0][1]), float(navs[-1][1])
        month_ret = v1 / v0 - 1 if v0 else None
        month_dd = float(navs[-1][2]) if navs[-1][2] is not None else None
    else:
        month_ret = month_dd = None

    # 当月 hit_rate（取 period_end ≤ 月末 最近的预计算行）
    perf = db.execute(
        select(StrategyPerf.detail)
        .where(StrategyPerf.metric_type == "hit_rate",
               StrategyPerf.period_end <= m_end)
        .order_by(StrategyPerf.period_end.desc())
        .limit(1)
    ).scalar()

    # 基准月收益
    bench = _bench_series(db, m_start, m_end)
    bench_ret = (bench[-1][1] / bench[0][1] - 1) if len(bench) >= 2 else None

    summary = {
        "month": f"{year}-{month:02d}", "signal": {"BUY": n_buy, "SELL": n_sell, "HOLD": n_hold},
        "nav": {"return": month_ret, "max_drawdown": month_dd},
        "benchmark_return": bench_ret, "hit_rate": perf or {},
    }
    content = _format_monthly_report(summary)
    return content, summary


def _format_monthly_report(s: dict) -> str:
    """摘要 → 飞书卡片文本（仿 _daily_report_content 风格）"""
    lines = [f"**{s['month']} 月度绩效报告**", ""]
    sig = s["signal"]
    lines.append(f"**信号** BUY {sig['BUY']} · SELL {sig['SELL']} · HOLD {sig['HOLD']}")
    nav = s["nav"]
    ret = nav["return"]
    ret_s = f"{ret * 100:+.2f}%" if ret is not None else "N/A"
    dd_s = f"{nav['max_drawdown'] * 100:.2f}%" if nav["max_drawdown"] is not None else "N/A"
    br = s["benchmark_return"]
    br_s = f"{br * 100:+.2f}%" if br is not None else "N/A"
    lines.append(f"**账户** 月收益 {ret_s}（基准沪深300 {br_s}）· 最大回撤 {dd_s}")
    wins = s.get("hit_rate") or {}
    if wins:
        w5 = (wins.get("windows") or {}).get("5") or {}
        if w5.get("samples"):
            lines.append("**BUY 命中率(5日)** "
                         f"{w5['hit_rate'] * 100:.0f}%（样本 {w5['samples']}）"
                         + (f"· 相对基准 {w5['relative_hit'] * 100:.0f}%"
                            if w5.get("relative_hit") is not None else ""))
        else:
            lines.append("**BUY 命中率(5日)** 样本不足，待积累")
    else:
        lines.append("**BUY 命中率(5日)** 暂无数据")
    return "📊 Steady · 月度绩效\n\n" + "\n".join(lines)


# ---------- 落库 ----------

def _store(db, strategy: str, period_start: date, period_end: date,
           metric_type: str, detail: dict) -> None:
    """幂等 upsert strategy_perf（UNIQUE(strategy_name, period_end, metric_type)）"""
    upsert(
        db, StrategyPerf,
        [{
            "strategy_name": strategy,
            "period_start": period_start,
            "period_end": period_end,
            "metric_type": metric_type,
            "detail": detail,
        }],
        conflict_cols=["strategy_name", "period_end", "metric_type"],
        update_cols=["period_start", "detail"],
    )
