"""自愈 stage1（collector）：领取 pending 任务 → diff-repair 缺失股票 → 流转状态

Issue #4 两段式交接的第一段（数据契约见迁移 005）：
  producer（quant-engine job_data_quality coverage fail）插 pending + detail.missing_codes；
  本模块每 5 分钟轮询 pending 队列：
    读 detail.missing_codes → DailyCollector 逐只补 trade_date（走现成
    AkShare→新浪→BaoStock 源链 + cross_check_splits 除权守卫，见 daily.py）
    源被限（BaoStock 封禁/黑名单冷却）→ status='source_blocked'，不重试
      （08-28 教训：封禁期逐股登录把整链拖成数小时）
    瞬时错误 → attempts+1；≥MAX_ATTEMPTS → 'failed'（stage2 红卡升级人工）
    全部成功 → status='repaired'（stage2 复检 + 重算 + 回告绿卡）
去重：remediation_task UNIQUE(trade_date, check_name)，producer 已 ON CONFLICT DO NOTHING。
"""
import logging

from sqlalchemy import select

from app.db import get_session
from app.models.tables import RemediationTask

logger = logging.getLogger("remediation")

MAX_ATTEMPTS = 3
_BATCH = 10  # 单轮最多处理任务数（防长时间占住调度线程）


def consume_pending() -> dict:
    """领取并处理 pending 任务（由 job 每 5 分钟调用）"""
    db = get_session()
    summary = {"processed": 0, "repaired": 0, "source_blocked": 0,
               "failed": 0, "requeued": 0}
    try:
        tasks = db.execute(
            select(RemediationTask)
            .where(RemediationTask.status == "pending")
            .order_by(RemediationTask.trade_date)
            .limit(_BATCH)
        ).scalars().all()
        for task in tasks:
            summary["processed"] += 1
            _process(db, task, summary)
    finally:
        db.close()
    return summary


def _process(db, task: RemediationTask, summary: dict) -> None:
    """处理单个 pending 任务：按缺失清单定向补齐"""
    missing = (task.detail or {}).get("missing_codes") or []
    if not missing:
        # 无缺失清单（不应发生，防御）→ 直接转 repaired 交 stage2 复检定夺
        task.status = "repaired"
        db.add(task)
        db.commit()
        summary["repaired"] += 1
        return

    from app.collectors.daily import DailyCollector
    from app.sources.baostock import is_source_blocked

    collector = DailyCollector(db)
    failed_codes: list[str] = []
    repaired = 0
    for code in missing:
        try:
            rows = collector.fetch(code, task.trade_date, task.trade_date)
            if not rows:
                # 主源链（AkShare→新浪）对当日返回空时，再试 BaoStock 单源——
                # fetch 只在抛异常才降级，空返回不降级；自愈要补上「任何源有而库缺」的
                # 缺口（如停牌日东财/新浪无行、BaoStock 有），故在此补一次（拦封禁见下）
                rows = collector._fetch_baostock(code, task.trade_date,
                                                 task.trade_date) or []
            if rows:
                # save 返回实际入库条数（清洗会丢弃 volume<=0 的停牌行 → 0）
                repaired += collector.save(rows) or 0
        except Exception as e:
            if is_source_blocked(e):
                # 源被限：立即中止整批，绝不逐只反复轰（08-28 事故根因）
                task.status = "source_blocked"
                logger.error("自愈源被限 %s %s（%s）→ source_blocked，不重试",
                             task.trade_date, code, e)
                db.add(task)
                db.commit()
                summary["source_blocked"] += 1
                return
            logger.warning("自愈补齐 %s %s 失败：%s", task.trade_date, code, e)
            failed_codes.append(code)

    task.detail = {**(task.detail or {}), "repaired_count": repaired}
    if failed_codes:
        task.attempts += 1
        task.detail["failed_codes"] = failed_codes
        if task.attempts >= MAX_ATTEMPTS:
            task.status = "failed"
            summary["failed"] += 1
            logger.error("自愈补齐失败 %s：修复 %d 失败 %d（attempts=%d）→ 升级人工",
                         task.trade_date, repaired, len(failed_codes), task.attempts)
        else:
            task.status = "pending"  # 下轮重试剩余
            summary["requeued"] += 1
            logger.warning("自愈部分完成 %s：修复 %d 失败 %d（attempts=%d）",
                           task.trade_date, repaired, len(failed_codes), task.attempts)
    else:
        task.status = "repaired"
        summary["repaired"] += 1
        logger.info("自愈补齐完成 %s/%s：修复 %d 只",
                    task.trade_date, task.check_name, repaired)
    db.add(task)
    db.commit()
