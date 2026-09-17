"""Quant Engine 定时任务（批处理，无 HTTP 服务）

时间表（docs §7.4）：19:00 计算因子 | 19:30 生成策略信号 | 21:00 日报
每个任务执行后写 task_run 账本（幂等），供通知调度器做「该做没做」检查与失败告警。
日报（daily_report）由 notify_scheduler 在 21:00 事件统一生成推送。
"""
import logging
from collections import Counter
from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, select

from app.db import get_session, upsert
from app.models.tables import DailyPrice, StockBasic, StrategySignal
from app.notify_scheduler import tick as notify_tick
from app.task_run import already_run, record_task
from app.watchdog import (guarded, healthz_status, register_catchup,
                          spawn_startup_catchup, start_watchdog)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("tasks")


def _start_healthz(service: str, default_port: int, status_provider=None) -> None:
    """启动最小健康端点（Issue #9-3）：backend 容器不挂 docker.sock、无 docker CLI，
    探活改走内网 HTTP —— 本进程是纯 APScheduler 守护，需此 /healthz 供 backend 探测。
    BlockingScheduler 占主线程 → 用守护线程跑 stdlib ThreadingHTTPServer。

    `status_provider`（Issue #14）：返回 (code, payload) 反映调度器存活，不传维持原语义。
    """
    import json
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    port = int(os.environ.get("HEALTH_PORT", default_port))

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server 命名约定)
            if self.path != "/healthz":
                self.send_response(404)
                self.end_headers()
                return
            if status_provider is not None:
                code, payload = status_provider()
                payload = {"service": service, **payload}
            else:
                code, payload = 200, {"status": "ok", "service": service}
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 健康轮询每 ~10s 一次，静默避免刷日志
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("健康端点已启动 :%s/healthz (service=%s)", port, service)


def latest_trade_date(db) -> date | None:
    """最近一个已有行情数据的交易日（跳过指数伪股票）"""
    return db.execute(
        select(func.max(DailyPrice.trade_date))
        .where(DailyPrice.code.not_like("sh%"))
    ).scalar()


def market_ready(db, td: date) -> bool:
    """行情就绪检查：当日股票池有 bar 的比例 >= 90%，不足则跳过
    （16:30 行情同步刚完成，正常应全覆盖；比例低说明同步失败或回填未完成）"""
    pool = db.execute(
        select(StockBasic.code).where(StockBasic.universe.in_(("hs300", "zz500")))
    ).scalars().all()
    if not pool:
        return False
    with_bar = db.execute(
        select(DailyPrice.code).where(
            DailyPrice.trade_date == td, DailyPrice.code.in_(pool))
    ).scalars().all()
    return len(with_bar) / len(pool) >= 0.9


def factor_ready(db, td: date) -> bool:
    """当日 factor_value 是否有产出（generate_signals 前置检查，Issue #9-2）

    calc_factors 19:00 先行：行情就绪但因子未产（calc 崩溃 / coverage 不足被跳过）时，
    generate_signals 不应硬跑抛 RuntimeError 记 failed，而应记 skipped 交由自愈兜底。
    """
    from app.models.tables import FactorValue

    return db.execute(
        select(FactorValue.code).where(FactorValue.trade_date == td).limit(1)
    ).scalar() is not None


def failed_detail(td, e: Exception, extra: dict | None = None) -> dict:
    """失败现场 detail：错误类型+消息 + traceback + 交易日 + 调用方可补快照。

    Issue #9-2：此前失败分支只传 message、detail 恒 {}，告警与页面查不到任何现场。
    除 record_task 之外无副作用，纯函数便于单测。
    """
    import traceback

    detail = {
        "trade_date": str(td) if td else None,
        "error": f"{type(e).__name__}: {e}",
        "traceback": traceback.format_exc(limit=6),
    }
    if extra:
        detail.update(extra)
    return detail


def signal_failure_snapshot(db, td: date) -> dict:
    """信号失败快照：当日因子行数 / active 策略（供失败 detail 落库用）"""
    from app.models.tables import FactorValue, Strategy

    return {
        "factor_value_rows": db.execute(
            select(func.count()).select_from(FactorValue)
            .where(FactorValue.trade_date == td)
        ).scalar() or 0,
        "active_strategy": db.execute(
            select(Strategy.name).where(Strategy.status == "active")
        ).scalar(),
    }


@guarded
def job_calc_factors():
    """19:00 计算因子并写入 factor_value 表"""
    db = get_session()
    td = None
    try:
        td = latest_trade_date(db)
        if td is None:
            logger.warning("无行情数据，跳过因子计算")
            record_task(db, "calc_factors", date.today(), "skipped", "无行情数据")
            return
        if not market_ready(db, td):
            logger.warning("%s 行情就绪比例不足，跳过因子计算", td)
            record_task(db, "calc_factors", td, "skipped", "行情就绪比例不足")
            return
        from app.factor_service import compute_and_store

        stats = compute_and_store(db, td)
        record_task(db, "calc_factors", td, "success",
                    f"计算完成（{stats.get('factors', len(stats))} 类因子）",
                    detail={"trade_date": str(td), "stats": stats})
    except Exception as e:
        logger.exception("因子计算任务失败")
        db.rollback()
        record_task(db, "calc_factors", td or date.today(), "failed",
                    "因子计算异常", detail=failed_detail(td, e))
    finally:
        db.close()


def generate_signals(db, td: date) -> int:
    """运行 active 策略并把信号写入 strategy_signal（幂等 upsert）；
    返回写入行数（CLI 与定时任务共用）

    Iteration 4：策略按 status='active' 选取（单 active 不变量），
    config 用该行 params，评分权重用该行 factor_weights，strategy_signal
    按该策略名写入。
    """
    from app.models.tables import Strategy as StrategyModel
    from app.strategies.multi_factor import MultiFactorStrategy

    row = db.execute(
        select(StrategyModel).where(StrategyModel.status == "active")
    ).scalar()
    if row is None:
        raise RuntimeError("无 active 策略（strategy 表，请先激活一个策略）")
    config = dict(row.params or {})
    config["factor_weights"] = dict(row.factor_weights or {})
    config["name"] = row.name
    config["db"] = db
    signals = MultiFactorStrategy(config).run(str(td))
    if not signals:
        return 0
    rows = [
        {"strategy_name": row.name, "code": s.code,
         "trade_date": td, "score": s.score,
         "action": s.action, "reason": s.reason}
        for s in signals
    ]
    upsert(db, StrategySignal, rows,
           conflict_cols=["strategy_name", "code", "trade_date"],
           update_cols=["score", "action", "reason"])
    counts = Counter(r["action"] for r in rows)
    logger.info("策略信号生成完成 %s：%s 条（%s）", td, len(rows), dict(counts))
    return len(rows)


@guarded
def job_generate_signals():
    """19:30 运行多因子策略，信号写入 strategy_signal 表（幂等 upsert）"""
    db = get_session()
    td = None
    try:
        td = latest_trade_date(db)
        if td is None:
            logger.warning("无行情数据，跳过策略信号")
            record_task(db, "generate_signals", date.today(), "skipped", "无行情数据")
            return
        # 数据就绪预检（与 calc_factors 同口径）：未就绪记 skipped 而非 failed，
        # 让失败告警只盯真实异常（Issue #9-2 根因：coverage 不足日 calc 跳过 →
        # generate 硬跑抛 RuntimeError 误报 failed）
        if not market_ready(db, td):
            logger.warning("%s 行情就绪比例不足，跳过策略信号", td)
            record_task(db, "generate_signals", td, "skipped",
                        "行情就绪比例不足，跳过策略信号（calc_factors 同口径）")
            return
        if not factor_ready(db, td):
            logger.warning("%s 因子数据未就绪，跳过策略信号", td)
            record_task(db, "generate_signals", td, "skipped",
                        "因子数据未就绪（calc_factors 未产出当日因子）")
            return
        n = generate_signals(db, td)
        if n == 0:
            logger.warning("%s 无信号输出（无候选通过筛选）", td)
            record_task(db, "generate_signals", td, "skipped", "无信号输出")
            return
        counts = {a: c for a, c in db.execute(
            select(StrategySignal.action, func.count())
            .where(StrategySignal.trade_date == td)
            .group_by(StrategySignal.action)
        ).all()}
        top_buys = [r[0] for r in db.execute(
            select(StrategySignal.code).where(
                StrategySignal.trade_date == td,
                StrategySignal.action == "BUY")
            .order_by(StrategySignal.score.desc()).limit(5)
        ).all()]
        record_task(db, "generate_signals", td, "success",
                    f"生成 {n} 条信号",
                    detail={"trade_date": str(td), "total": n,
                            "counts": counts, "top_buys": top_buys})
    except Exception as e:
        logger.exception("策略信号任务失败")
        db.rollback()
        # 失败必须带现场（Issue #9-2）：traceback + 当日因子行数 + active 策略快照；
        # 快照查询自身失败（如 DB 断连）不掩盖原始异常，降级为纯现场。
        extra = {}
        if td:
            try:
                extra = signal_failure_snapshot(db, td)
            except Exception:
                pass
        record_task(db, "generate_signals", td or date.today(), "failed",
                    "策略信号生成异常", detail=failed_detail(td, e, extra))
    finally:
        db.close()


@guarded
def job_consume_backtests():
    """回测任务消费者：每 5 分钟领取 pending 任务并落库（APScheduler 线程池执行，
    与 19:00/19:30 任务并行不冲突——回测只读因子/行情，不写 factor_value）"""
    from app.backtest_service import consume_pending

    db = get_session()
    try:
        consume_pending()
        record_task(db, "backtest", date.today(), "success", "回测任务消费完成")
    except Exception:
        logger.exception("回测任务消费失败")
        db.rollback()
        record_task(db, "backtest", date.today(), "failed", "回测任务消费异常")
    finally:
        db.close()


@guarded
def job_consume_factor_trials():
    """G10 试算/寻优任务消费者：每 5 分钟领取 pending 并落库（复用 backtest_job 模式）

    只读 factor_value/行情/估值/财务，不写 factor_value，与 19:00 评分任务并行安全。
    """
    from app.factor_trial import consume_pending_trials

    db = get_session()
    try:
        consume_pending_trials()
        record_task(db, "factor_trial", date.today(), "success", "试算任务消费完成")
    except Exception:
        logger.exception("试算任务消费失败")
        db.rollback()
        record_task(db, "factor_trial", date.today(), "failed", "试算任务消费异常")
    finally:
        db.close()


def _enqueue_coverage_repair(db, td: date, missing_codes: list[str]) -> None:
    """coverage fail → 插 remediation_task pending（Issue #4 自愈队列）

    ON CONFLICT DO NOTHING：同一 (trade_date, check_name) 已存在则不覆盖——
    多轮 18:30 重跑 / 同日晚间重复体检不重置进行中的修复任务。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.tables import RemediationTask

    stmt = pg_insert(RemediationTask).values([{
        "trade_date": td, "check_name": "coverage", "status": "pending",
        "attempts": 0, "detail": {"missing_codes": missing_codes},
    }]).on_conflict_do_nothing(index_elements=["trade_date", "check_name"])
    db.execute(stmt)
    db.commit()


@guarded
def job_data_quality():
    """18:30 数据健康检查：7 项体检结果写 task_run 台账（notify_scheduler 18:35 推送）。
    执行成功即记 success（发现问题是产出而非失败）；job 崩溃才记 failed。
    coverage fail 时顺带入自愈队列（remediation_task pending），由两端消费补齐。"""

    db = get_session()
    td = None
    try:
        td = latest_trade_date(db)
        if td is None:
            logger.warning("无行情数据，跳过数据健康检查")
            record_task(db, "data_quality", date.today(), "skipped", "无行情数据")
            return
        from app.data_quality import check_data_quality

        detail = check_data_quality(db, td)
        record_task(db, "data_quality", td, "success",
                    detail["message"], detail=detail)
        # 自愈入队：coverage fail 且有缺失清单 → 插 pending（去重防轰炸）
        coverage_metrics = detail.get("check_details", {}).get("coverage", {})
        missing_codes = coverage_metrics.get("missing_codes") or []
        coverage_level = next(
            (r.get("level") for r in detail.get("results", [])
             if r.get("name") == "coverage"), None)
        if coverage_level == "fail" and missing_codes:
            _enqueue_coverage_repair(db, td, missing_codes)
            logger.warning("coverage fail → 自愈队列入队：%s 缺 %d 只",
                           td, len(missing_codes))
        logger.info("数据健康检查完成 %s：%s", td, detail["message"])
    except Exception:
        logger.exception("数据健康检查任务失败")
        db.rollback()
        record_task(db, "data_quality", td or date.today(), "failed", "数据健康检查异常")
    finally:
        db.close()


@guarded
def job_consume_remediation():
    """自愈 stage2 消费者：每 5 分钟领取 repaired 任务（Issue #4）

    stage1（collector remediation.py）补齐 daily_price 后置 repaired；本任务复检
    coverage → 全绿重算因子/信号 + 回告飞书绿卡，仍红回 pending 再走 stage1。
    """
    from app.remediation import consume_repaired

    db = get_session()
    try:
        summary = consume_repaired()
        if summary["processed"]:
            logger.info("自愈 stage2 消费完成：%s", summary)
        record_task(db, "remediation", date.today(), "success", "自愈消费完成")
    except Exception:
        logger.exception("自愈消费失败")
        db.rollback()
        record_task(db, "remediation", date.today(), "failed", "自愈消费异常")
    finally:
        db.close()


@guarded
def job_precompute_factor_stat():
    """19:05 预计算因子检验统计（2.3 G9：factor_stat/factor_corr，幂等 upsert）

    读取 factor_value + daily_price 全历史重算（区间内每因子每日 IC/分层 + 6 因子
    相关矩阵），ON CONFLICT upsert —— 数据回填/补齐后重跑即自动填满尾部。
    """
    from app.factor_research import precompute_factor_stat

    db = get_session()
    try:
        result = precompute_factor_stat(db)
        if result.get("skipped"):
            logger.warning("因子检验预计算跳过：%s", result["skipped"])
            record_task(db, "factor_stat", date.today(), "skipped", result["skipped"])
            return
        record_task(db, "factor_stat", date.today(), "success",
                    f"预计算完成（{len(result.get('factors', {}))} 因子）",
                    detail={"factors": result.get("factors", {}),
                            "dates": result.get("dates"),
                            "corr_dates": result.get("corr_dates")})
        logger.info("因子检验预计算完成：%s", result)
    except Exception:
        logger.exception("因子检验预计算失败")
        db.rollback()
        record_task(db, "factor_stat", date.today(), "failed", "因子检验预计算异常")
    finally:
        db.close()


@guarded
def job_morning_brief():
    """09:10 早盘简报（热点采集 + 昨日回顾 + 今日计划，Issue #4）。
    非交易日/无行情 skip；组装逻辑见 app/morning_brief.py。"""
    from app.morning_brief import job_morning_brief as _job

    _job()


@guarded
def job_precompute_perf():
    """21:20 策略效果度量预计算：命中率 + 实盘vs回测对照 + 因子贡献归因。

    晚于 backend 21:05 净值快照；幂等 upsert（UNIQUE(strategy_name, period_end,
    metric_type)），每日覆盖重算。逻辑见 app/performance.py。"""
    from app.performance import compute_attribution, compute_hit_rate, compute_nav_overlay

    db = get_session()
    try:
        hr = compute_hit_rate(db)
        ov = compute_nav_overlay(db)
        at = compute_attribution(db)
        record_task(db, "precompute_perf", date.today(), "success",
                    f"命中率窗口 {sorted((hr.get('windows') or {}).keys())} · "
                    f"对照 {ov.get('metrics', {}).get('drift')} · "
                    f"归因 {at.get('samples', 0)} 日/{len(at.get('monthly') or [])} 月",
                    detail={"hit_rate_buy_samples": hr.get("buy_samples"),
                            "hit_rate_period_start": hr.get("period_start"),
                            "overlay_live_points": ov.get("metrics", {}).get("live_points"),
                            "attribution_days": at.get("samples"),
                            "attribution_months": len(at.get("monthly") or []),
                            "attribution_note": at.get("note")})
        logger.info("绩效预计算完成：hit_rate=%s overlay_drift=%s attribution_days=%s",
                    sorted((hr.get("windows") or {}).keys()),
                    ov.get("metrics", {}).get("drift"), at.get("samples"))
    except Exception:
        logger.exception("绩效预计算失败")
        db.rollback()
        record_task(db, "precompute_perf", date.today(), "failed", "绩效预计算异常")
    finally:
        db.close()


@guarded
def job_perf_monthly_report():
    """每月 1 日 21:15 发送上月绩效报告卡片（notify_config['perf_report'] 门控）。

    去重键 perf_report:{YYYYMM}（月度，record_task 幂等防重发）；逻辑见
    app/performance.build_monthly_report。"""
    from sqlalchemy import select as _select

    from app.models.tables import NotifyConfig
    from app.performance import build_monthly_report

    db = get_session()
    try:
        last_month = date.today().replace(day=1)
        y, m = (last_month.year - 1, 12) if last_month.month == 1 \
            else (last_month.year, last_month.month - 1)
        key = f"perf_report:{y}{m:02d}"
        if already_run(db, key, date.today()):
            logger.info("月度绩效报告已发送（%s）", key)
            return
        content, summary = build_monthly_report(db, y, m)
        from app.notify import FeishuNotifier, load_config

        cfg = load_config(db)
        if not cfg["enabled"]:
            record_task(db, key, date.today(), "skipped", "飞书通知未启用")
            return
        ev = db.execute(
            _select(NotifyConfig).where(NotifyConfig.event_key == "perf_report")
        ).scalar()
        if ev is None or not ev.enabled:
            record_task(db, key, date.today(), "skipped", "perf_report 通知未启用")
            return
        ok = FeishuNotifier(cfg).send_card(
            f"📊 {y}-{m:02d} 月度绩效报告", content, template="blue", footer="绩效度量 · 月度")
        record_task(db, key, date.today(),
                    "success" if ok else "failed",
                    "绩效报告已推送" if ok else "绩效报告推送失败",
                    detail={"month": f"{y}-{m:02d}", "summary": summary})
    except Exception:
        logger.exception("月度绩效报告失败")
        db.rollback()
        record_task(db, f"perf_report:{date.today().strftime('%Y%m')}", date.today(),
                    "failed", "月度绩效报告异常")
    finally:
        db.close()


def _probe_factor_value(_delta: float) -> bool:
    """当日因子是否已产出（factor_value 含最新交易日行）"""
    from app.models.tables import FactorValue

    db = get_session()
    try:
        td = latest_trade_date(db)
        if td is None:
            return True  # 无行情，谈不上补算
        return db.execute(
            select(FactorValue.code).where(FactorValue.trade_date == td).limit(1)
        ).scalar() is not None
    finally:
        db.close()


def _probe_strategy_signal(_delta: float) -> bool:
    """当日策略信号是否已产出"""
    from app.models.tables import FactorValue, StrategySignal

    db = get_session()
    try:
        td = latest_trade_date(db)
        if td is None:
            return True
        return db.execute(
            select(StrategySignal.code).where(StrategySignal.trade_date == td).limit(1)
        ).scalar() is not None
    finally:
        db.close()


def _never_catchup(_delta: float) -> bool:
    """恒 True：该 job 不参与启动补跑"""
    return True


def register_catchups() -> None:
    """注册启动补跑探针（Issue #14）。因子/信号是当日链路的硬依赖，必补；
    数据质量/绩效等有自身幂等或周期语义，不补。独立成函数便于单测。"""
    register_catchup("job_calc_factors", _probe_factor_value)
    register_catchup("job_generate_signals", _probe_strategy_signal)
    for _j in ("job_data_quality", "job_precompute_factor_stat", "job_precompute_perf",
               "job_morning_brief"):
        register_catchup(_j, _never_catchup)


if __name__ == "__main__":
    scheduler = BlockingScheduler()

    scheduler.add_job(job_morning_brief, "cron", hour=9, minute=10)
    scheduler.add_job(job_calc_factors, "cron", hour=19, minute=0)
    scheduler.add_job(job_precompute_factor_stat, "cron", hour=19, minute=5)
    scheduler.add_job(job_generate_signals, "cron", hour=19, minute=30)
    scheduler.add_job(job_consume_backtests, "interval", minutes=5)
    scheduler.add_job(job_consume_factor_trials, "interval", minutes=5)
    scheduler.add_job(job_consume_remediation, "interval", minutes=5)
    scheduler.add_job(job_data_quality, "cron", hour=18, minute=30)
    scheduler.add_job(job_perf_monthly_report, "cron", day=1, hour=21, minute=15)
    scheduler.add_job(job_precompute_perf, "cron", hour=21, minute=20)
    # notify_tick 是导入的（notify_scheduler.tick），在注册处以 guarded 包一层计时
    scheduler.add_job(guarded(notify_tick), "interval", minutes=1)

    _start_healthz("quant-engine", 9201, status_provider=healthz_status)
    register_catchups()
    start_watchdog()
    spawn_startup_catchup()
    logger.info("quant-engine 调度器启动，等待定时任务...")
    scheduler.start()
