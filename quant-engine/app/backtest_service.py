"""回测任务服务：任务队列（backtest_job → backtest_result）

消费方：
- tasks.py 每 5 分钟 claim pending 任务 → run_and_save（自动闭环）
- cli.py backtest --save 提交后同步执行（手动/测试）

幂等：同 (strategy_name, start_date, end_date, top_n) 唯一（uq_backtest_job），
重复提交返回已有任务；failed 任务重新提交会重置为 pending 重跑。
"""
import json
import logging
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import upsert
from app.models.tables import BacktestJob, BacktestResult

logger = logging.getLogger("backtest_service")

FACTOR_NAMES = ["ma_trend", "macd_signal", "pe_ratio", "pb_ratio",
                "roe_quality", "debt_risk"]


def build_replay_strategy(db, top_n: int | None = None,
                          strategy_name: str = "multi_factor"):
    """构建内存重放策略

    Iteration 4 权重口径：权重 = 指定策略的 factor_weights（缺失因子回退
    factor_definition）；参数（top_n/buffer/风控）来自该策略行 params。
    """
    from sqlalchemy import select

    from app.backtest.replay import ReplayStrategy
    from app.models.tables import FactorDefinition, Strategy as StrategyModel

    defs = list(db.execute(
        select(FactorDefinition).where(FactorDefinition.name.in_(FACTOR_NAMES))
    ).scalars())
    weights = {d.name: float(d.weight) for d in defs}
    categories = {d.name: d.category for d in defs}

    params = {}
    row = db.execute(select(StrategyModel).where(
        StrategyModel.name == strategy_name)).scalar()
    if row is not None:
        if row.params:
            params = dict(row.params)
        if row.factor_weights:
            weights.update(dict(row.factor_weights))
    if top_n:
        params["top_n"] = top_n
    return ReplayStrategy(db, params, weights, categories)


def create_job(db, start: date, end: date, top_n: int = 20,
               fill_mode: str = "t_close",
               strategy_name: str = "multi_factor") -> BacktestJob:
    """创建回测任务（幂等：同参数同假设已存在直接返回；failed 重置为 pending 重跑）

    fill_mode 纳入唯一键：t_close 与 t1_open 同区间不同假设可并存。
    Iteration 4：strategy_name 参数化，支持多策略回测（A/B 对比）。
    """
    stmt = pg_insert(BacktestJob).values(
        strategy_name=strategy_name, start_date=start, end_date=end,
        top_n=top_n, fill_mode=fill_mode,
    ).on_conflict_do_update(
        index_elements=["strategy_name", "start_date", "end_date", "top_n",
                        "fill_mode"],
        set_={"status": "pending", "error": None, "finished_at": None},
        where=(BacktestJob.status == "failed"),
    )
    db.execute(stmt)
    db.commit()
    return db.execute(select(BacktestJob).where(
        BacktestJob.strategy_name == strategy_name,
        BacktestJob.start_date == start,
        BacktestJob.end_date == end,
        BacktestJob.top_n == top_n,
        BacktestJob.fill_mode == fill_mode,
    )).scalar_one()


def claim_job(db) -> BacktestJob | None:
    """原子领取一个 pending 任务（UPDATE ... WHERE status='pending' RETURNING）

    必须用子查询把 UPDATE 锁到单行（ORDER BY id LIMIT 1，FIFO 领取）：不带
    limit 的 update().returning() 是批量更新——会把所有 pending 一次置 running，
    RETURNING 只取回第一行，其余行被静默挂起永不消费（2.3b 在 factor_trial
    消费循环 e2e 抓到同类坑，此处同款一并修复；多任务并发时按 id 顺序领取）。
    """
    sub = select(BacktestJob.id).where(BacktestJob.status == "pending") \
        .order_by(BacktestJob.id).limit(1).scalar_subquery()
    row = db.execute(
        update(BacktestJob).where(BacktestJob.id == sub)
        .values(status="running").returning(BacktestJob)
    ).first()
    if row is None:
        return None
    db.commit()
    return row[0]


def _push_backtest_card(db, job: BacktestJob, status: str, report: dict | None = None):
    """回测完成/失败推送飞书（事件型：尊重 notify_config.backtest 开关）"""
    from app.models.tables import NotifyConfig
    from app.notify import FeishuNotifier, load_config

    cfg = load_config(db)
    if not cfg["enabled"]:
        return
    ev = db.execute(
        select(NotifyConfig).where(NotifyConfig.event_key == "backtest")
    ).scalar()
    if ev is None or not ev.enabled:
        return
    notifier = FeishuNotifier(cfg)
    if status == "done" and report:
        p = report.get("portfolio", {})
        sharpe = p.get("sharpe")
        content = (
            f"**区间** {job.start_date} ~ {job.end_date}"
            f"（{report.get('trading_days')} 个交易日）\n"
            f"**总收益** {(p.get('total_return') or 0) * 100:+.2f}%\n"
            f"**年化收益** {(p.get('annualized_return') or 0) * 100:+.2f}%\n"
            f"**最大回撤** {(p.get('max_drawdown') or 0) * 100:.2f}%\n"
            f"**夏普比率** {sharpe if sharpe is not None else 'N/A'}\n"
            f"**成交** {report.get('trades', 0)} 笔 · 持仓 {report.get('positions', 0)} 只"
        )
        notifier.send_card("📊 Steady · 回测完成", content, template="green",
                           footer=f"任务 #{job.id} · 结果已保存")
    else:
        content = (f"**任务** #{job.id}\n"
                   f"**区间** {job.start_date} ~ {job.end_date}\n"
                   f"**错误** {job.error or '未知错误'}")
        notifier.send_card("❌ Steady · 回测失败", content, template="red",
                           footer=f"任务 #{job.id}")


def _clone_strategy(strategy, db, top_n: int, strategy_name: str = "multi_factor"):
    """浅拷贝预加载数据给配对运行的第二个策略实例（只读数据共享，holdings 独立）"""
    s = build_replay_strategy(db, top_n, strategy_name=strategy_name)
    s.series = strategy.series
    s.grid = strategy.grid
    s._date_pos = strategy._date_pos
    s.pool = strategy.pool
    s.industry = strategy.industry  # 行业映射（preload 已加载，clone 沿用）
    return s


def run_and_save(db, job: BacktestJob):
    """执行回测并把结果写入 backtest_result；失败置 failed + error（不 panic）

    Iteration 3：同一 job 配对跑 t_close + t1_open 两种成交假设（共享一次
    preload，浅拷贝数据给第二实例），落 primary 模式指标 + t1_deviation
    （年化收益 T+1 偏差）。
    """
    from app.backtest.engine import BacktestEngine

    try:
        fill_mode = job.fill_mode or "t_close"
        alt_mode = "t1_open" if fill_mode == "t_close" else "t_close"
        strategy = build_replay_strategy(db, job.top_n,
                                         strategy_name=job.strategy_name)
        strategy.preload(str(job.start_date), str(job.end_date))
        primary_strat = strategy if fill_mode == "t_close" \
            else _clone_strategy(strategy, db, job.top_n, job.strategy_name)
        alt_strat = _clone_strategy(strategy, db, job.top_n, job.strategy_name) \
            if fill_mode == "t_close" else strategy

        primary = BacktestEngine(primary_strat, str(job.start_date),
                                 str(job.end_date), db=db, fill_mode=fill_mode)
        alt = BacktestEngine(alt_strat, str(job.start_date),
                             str(job.end_date), db=db, fill_mode=alt_mode)
        report = primary.run()
        alt_report = alt.run()
        # 年化收益 T vs T+1 偏差（positive = T+1 反而更好）
        report["t1_deviation"] = round(
            (alt_report.get("portfolio", {}).get("annualized_return") or 0)
            - (report.get("portfolio", {}).get("annualized_return") or 0), 4)
        nav_series = report.pop("nav_series", [])
        p = report.get("portfolio", {})
        upsert(db, BacktestResult, [{
            "job_id": job.id,
            "fill_mode": fill_mode,
            "t1_deviation": report.get("t1_deviation"),
            "total_return": p.get("total_return"),
            "annualized_return": p.get("annualized_return"),
            "max_drawdown": p.get("max_drawdown"),
            "sharpe": p.get("sharpe"),
            "trading_days": report.get("trading_days"),
            "final_value": report.get("final_value"),
            "trades": report.get("trades"),
            "positions": report.get("positions"),
            "turnover": report.get("turnover"),  # 年化单边换手（Iteration 4）
            "cost": report.get("cost"),          # 年化交易成本占比（Iteration 4）
            "benchmark_return": report.get("benchmark", {}).get("total_return"),
            "excess_return": report.get("excess_return"),
            "nav": nav_series,  # JSONB 列直接传 list，SQLAlchemy JSON 类型自动序列化
        }], conflict_cols=["job_id"], update_cols=[
            "fill_mode", "t1_deviation",
            "total_return", "annualized_return", "max_drawdown", "sharpe",
            "trading_days", "final_value", "trades", "positions",
            "turnover", "cost",
            "benchmark_return", "excess_return", "nav",
        ])
        db.execute(update(BacktestJob).where(BacktestJob.id == job.id).values(
            status="done", finished_at=datetime.now()))
        db.commit()
        logger.info("回测任务 %s 完成（%s ~ %s，%s 个交易日，fill_mode=%s，"
                    "总收益 %+.2f%%，T+1 年化偏差 %+.2f%%）",
                    job.id, job.start_date, job.end_date, report.get("trading_days"),
                    fill_mode, (p.get("total_return") or 0) * 100,
                    (report.get("t1_deviation") or 0) * 100)
        _push_backtest_card(db, job, "done", report)
    except Exception as exc:
        db.rollback()
        logger.exception("回测任务 %s 失败", job.id)
        db.execute(update(BacktestJob).where(BacktestJob.id == job.id).values(
            status="failed", error=str(exc)[:500], finished_at=datetime.now()))
        db.commit()
        _push_backtest_card(db, job, "failed")


def consume_pending():
    """领取并执行所有 pending 任务（每 5 分钟调用一次）"""
    from app.db import get_session

    db = get_session()
    try:
        while True:
            job = claim_job(db)
            if job is None:
                return
            run_and_save(db, job)
    finally:
        db.close()
