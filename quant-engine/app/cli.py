"""量化引擎手动入口

用法：
    python -m app.cli factors [--date 2026-08-19]        # 手动计算因子
    python -m app.cli signals [--date 2026-08-19]        # 手动生成策略信号
    python -m app.cli backtest [--start 2025-01-01]      # 回测并打印报告
                                [--end 2026-08-20] [--top-n 20]
                                [--fill-mode t_close|t1_open] [--save]
    python -m app.cli backtest-deviation [--start 2025-01-01]  # T vs T+1 偏差报告
                                [--end 2026-08-20] [--top-n 20]
    python -m app.cli factor-stats [--start 2025-01-01]  # 预计算因子检验统计
                                [--end 2026-08-20]       # （factor_stat/factor_corr）
"""
import argparse
import logging
import sys
from datetime import date, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("cli")


def _default_trade_date(db) -> date | None:
    """最近一个已有行情数据的交易日（与定时任务同口径）"""
    from sqlalchemy import func, select

    from app.models.tables import DailyPrice

    return db.execute(
        select(func.max(DailyPrice.trade_date))
        .where(DailyPrice.code.not_like("sh%"))
    ).scalar()


def cmd_factors(args) -> bool:
    from app.db import get_session
    from app.factor_service import compute_and_store

    db = get_session()
    td = date.fromisoformat(args.date) if args.date else _default_trade_date(db)
    if td is None:
        logger.error("无行情数据，请先同步日行情")
        return False
    stats = compute_and_store(db, td)
    logger.info("因子计算完成 %s：%s", td, stats)
    return True


def cmd_signals(args) -> bool:
    from app.db import get_session
    from app.tasks import generate_signals

    db = get_session()
    td = date.fromisoformat(args.date) if args.date else _default_trade_date(db)
    if td is None:
        logger.error("无行情数据，请先同步日行情")
        return False
    n = generate_signals(db, td)
    logger.info("策略信号完成 %s：%s 条", td, n)
    return True


def cmd_notify(args) -> bool:
    """发送一条飞书测试卡片（验证 webhook / 配置 / 网络连通）"""
    from app.db import get_session
    from app.notify import FeishuNotifier, load_config

    db = get_session()
    cfg = load_config(db)
    db.close()
    if not cfg["enabled"]:
        logger.error("飞书通知未启用（请在设置页开启 app_config.feishu.enabled）")
        return False
    if not cfg["webhook"]:
        logger.error("未配置 webhook（请在设置页填写 app_config.feishu.webhook_url）")
        return False
    notifier = FeishuNotifier(cfg)
    ok = notifier.send_test(wait=True)
    logger.info("测试卡片%s送达", "已" if ok else "未（见上方错误）")
    return ok


def cmd_backtest(args) -> bool:
    from app.backtest.engine import BacktestEngine
    from app.backtest_service import build_replay_strategy, create_job, run_and_save
    from app.db import get_session

    db = get_session()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    fill_mode = args.fill_mode or "t_close"
    if args.save:
        # 任务队列：提交 job（幂等）并同步执行落库
        job = create_job(db, start, end, args.top_n or 20, fill_mode=fill_mode)
        if job.status == "done":
            print(f"回测任务 #{job.id} 已有结果（done），无需重跑")
            db.close()
            return True
        run_and_save(db, job)  # failed 已在 create_job 中重置为 pending，同步重跑
        print(f"回测任务 #{job.id} 已保存：{job.status}（fill_mode={fill_mode}）")
        db.close()
        return job.status == "done"

    strategy = build_replay_strategy(db, args.top_n)
    engine = BacktestEngine(strategy, args.start, args.end, db=db,
                            fill_mode=fill_mode)
    report = engine.run()

    print("\n===== 回测报告（多因子轮动）=====")
    print(f"区间 {report['start']} ~ {report['end']}，"
          f"{report['trading_days']} 个交易日，成交假设 {report['fill_mode']}")
    p = report["portfolio"]
    print(f"期末净值 {report['final_value']:.2f}（初始 100000）")
    print(f"总收益    {p['total_return']:+.2%}")
    print(f"年化收益  {p['annualized_return']:+.2%}")
    print(f"最大回撤  {p['max_drawdown']:.2%}")
    print(f"夏普比率  {p['sharpe'] if p['sharpe'] is not None else 'N/A'}")
    if "benchmark" in report:
        b = report["benchmark"]
        print(f"基准(HS300) 总收益 {b['total_return']:+.2%}，"
              f"最大回撤 {b['max_drawdown']:.2%}")
        print(f"超额收益  {report['excess_return']:+.2%}")
    print(f"成交 {report['trades']} 笔，期末持仓 {report['positions']} 只")
    print("=" * 30)
    return True


def cmd_factor_stats(args) -> bool:
    """预计算因子检验统计（factor_stat/factor_corr，幂等 upsert，默认全历史）"""
    from datetime import date as _date

    from app.db import get_session
    from app.factor_research import precompute_factor_stat

    db = get_session()
    result = precompute_factor_stat(
        db, start=_date.fromisoformat(args.start) if args.start else None,
        end=_date.fromisoformat(args.end) if args.end else None)
    db.close()
    if result.get("skipped"):
        logger.error("因子检验预计算跳过：%s", result["skipped"])
        return False
    logger.info("因子检验预计算完成：%s", result)
    return True


def cmd_backtest_deviation(args) -> bool:
    """T vs T+1 偏差报告：同一区间跑两种成交假设，输出对比表（Iteration 3 交付物）"""
    from app.backtest.engine import BacktestEngine
    from app.backtest_service import _clone_strategy, build_replay_strategy
    from app.db import get_session

    db = get_session()
    strategy = build_replay_strategy(db, args.top_n)
    strategy.preload(args.start, args.end)
    engines = {
        "t_close": BacktestEngine(strategy, args.start, args.end, db=db,
                                  fill_mode="t_close"),
        "t1_open": BacktestEngine(_clone_strategy(strategy, db, args.top_n),
                                  args.start, args.end, db=db, fill_mode="t1_open"),
    }
    reports = {m: e.run() for m, e in engines.items()}
    db.close()

    print("\n===== 回测偏差报告（T 日收盘 vs T+1 开盘）=====")
    print(f"区间 {args.start} ~ {args.end} · top_n {args.top_n or '策略默认'}\n")
    hdr = f"{'假设':<8}{'总收益':>10}{'年化':>10}{'最大回撤':>10}{'夏普':>8}{'成交':>6}"
    print(hdr)
    for m in ("t_close", "t1_open"):
        r = reports[m]
        p = r["portfolio"]
        sharpe = f"{p['sharpe']:.2f}" if p["sharpe"] is not None else "N/A"
        print(f"{m:<8}{p['total_return']:>+10.2%}{p['annualized_return']:>+10.2%}"
              f"{p['max_drawdown']:>10.2%}{sharpe:>8}{r['trades']:>6}")
    tc = reports["t_close"]["portfolio"]["annualized_return"]
    t1 = reports["t1_open"]["portfolio"]["annualized_return"]
    print(f"\n年化偏差（T+1 − T 收盘）：{t1 - tc:+.2%}"
          f"{'  ← T+1 反而更好' if t1 > tc else '  ← T 日收盘高估收益（未来函数信号）'}")
    print("=" * 48)
    return True


def main():
    parser = argparse.ArgumentParser(prog="quant-engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_f = sub.add_parser("factors", help="手动计算因子")
    p_f.add_argument("--date", help="交易日 YYYY-MM-DD（默认最近交易日）")

    p_s = sub.add_parser("signals", help="手动生成策略信号")
    p_s.add_argument("--date", help="交易日 YYYY-MM-DD（默认最近交易日）")

    p_b = sub.add_parser("backtest", help="历史回测")
    p_b.add_argument("--start", default=(date.today() - timedelta(days=365 * 2))
                     .isoformat(), help="起始日 YYYY-MM-DD（默认近 2 年）")
    p_b.add_argument("--end", default=date.today().isoformat(),
                     help="结束日 YYYY-MM-DD")
    p_b.add_argument("--top-n", type=int, help="目标持仓数（默认取策略配置 20）")
    p_b.add_argument("--fill-mode", choices=["t_close", "t1_open"],
                     default="t_close", help="成交假设（默认 t_close）")
    p_b.add_argument("--save", action="store_true",
                     help="提交回测任务并写入 backtest_job/backtest_result（默认仅打印报告）")

    p_fs = sub.add_parser("factor-stats", help="预计算因子检验统计（factor_stat/factor_corr）")
    p_fs.add_argument("--start", help="起始日 YYYY-MM-DD（默认 factor_value 最早日）")
    p_fs.add_argument("--end", help="结束日 YYYY-MM-DD（默认 factor_value 最晚日）")

    p_d = sub.add_parser("backtest-deviation", help="T vs T+1 偏差报告")
    p_d.add_argument("--start", default=(date.today() - timedelta(days=365 * 2))
                     .isoformat(), help="起始日 YYYY-MM-DD（默认近 2 年）")
    p_d.add_argument("--end", default=date.today().isoformat(),
                     help="结束日 YYYY-MM-DD")
    p_d.add_argument("--top-n", type=int, help="目标持仓数（默认取策略配置 20）")

    p_n = sub.add_parser("notify", help="飞书通知")
    p_n.add_argument("--test", action="store_true", help="发送一条测试卡片")

    args = parser.parse_args()

    try:
        if args.cmd == "factors":
            ok = cmd_factors(args)
        elif args.cmd == "signals":
            ok = cmd_signals(args)
        elif args.cmd == "backtest":
            ok = cmd_backtest(args)
        elif args.cmd == "factor-stats":
            ok = cmd_factor_stats(args)
        elif args.cmd == "backtest-deviation":
            ok = cmd_backtest_deviation(args)
        elif args.cmd == "notify":
            ok = cmd_notify(args)
        else:
            parser.error(f"未知命令: {args.cmd}")
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
