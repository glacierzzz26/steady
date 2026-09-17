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

**限速（Issue #13）**：扩池后 missing_codes 可达数千只，旧实现逐只串行零 sleep
（`_BATCH=10` 限的是任务数不是股票数）会把 18:35 的自愈拖成数小时并持续轰源。
现加四道闸：单轮总时限、单只间隔、单轮股票上限、连续失败熔断；超时/超上限 →
`status` 保持 pending、**attempts 不增**、剩余写回 detail（下轮继续，不算失败）。
熔断才是真正防 08-28 的机制 —— `is_source_blocked` 抓不到 `RemoteDisconnected`
这类瞬时错误，连败 20 只即判源不稳、中止本轮。
"""
import logging
import time

from sqlalchemy import select

from app.db import get_session
from app.models.tables import RemediationTask

logger = logging.getLogger("remediation")

MAX_ATTEMPTS = 3
_TASK_BATCH = 5          # 单轮最多处理任务数（防长时间占住调度线程）
_CODE_BATCH = 200        # 单任务单轮最多补的股票数（超出留下轮，防一轮跑数小时）
_TIME_BUDGET = 240.0     # 单轮总时限（秒），超时交下轮
_FAIL_STREAK_MAX = 20    # 连续失败熔断：连败 N 只 → 判源不稳，中止本轮（保留 pending）


def _interval() -> float:
    """单只之间间隔（秒）；env `REMEDIATION_INTERVAL`，默认 1s"""
    import os
    try:
        return float(os.getenv("REMEDIATION_INTERVAL", "1"))
    except ValueError:
        return 1.0


def consume_pending() -> dict:
    """领取并处理 pending 任务（由 job 每 5 分钟调用）"""
    db = get_session()
    summary = {"processed": 0, "repaired": 0, "source_blocked": 0,
               "failed": 0, "requeued": 0, "progress_limited": 0}
    started = time.monotonic()
    try:
        tasks = db.execute(
            select(RemediationTask)
            .where(RemediationTask.status == "pending")
            .order_by(RemediationTask.trade_date)
            .limit(_TASK_BATCH)
        ).scalars().all()
        for task in tasks:
            if time.monotonic() - started > _TIME_BUDGET:
                logger.info("自愈单轮时限 %ss 到，剩余任务交下轮", _TIME_BUDGET)
                break
            summary["processed"] += 1
            _process(db, task, summary, started)
    finally:
        db.close()
    return summary


def _process(db, task: RemediationTask, summary: dict,
             started: float | None = None) -> None:
    """处理单个 pending 任务：按缺失清单定向补齐（带限速与熔断）

    `started`：本轮起始时刻（time.monotonic）；None → 以本任务开始计（单测直调）。
    """
    if started is None:
        started = time.monotonic()
    missing = (task.detail or {}).get("missing_codes") or []
    if not missing:
        # 无缺失清单（不应发生，防御）→ 直接转 repaired 交 stage2 复检定夺
        task.status = "repaired"
        db.add(task)
        db.commit()
        summary["repaired"] += 1
        return

    from app.collectors.daily import DailyCollector
    from app.sources import tencent
    from app.sources.baostock import is_source_blocked as baostock_blocked

    def _blocked(exc: Exception) -> bool:
        # 任一源被限（BaoStock 封禁冷却 / 腾讯 403-429）→ source_blocked，不重试
        return baostock_blocked(exc) or tencent.is_source_blocked(exc)

    interval = _interval()
    collector = DailyCollector(db)
    failed_codes: list[str] = []
    repaired = 0
    fail_streak = 0
    done = 0
    limited = False
    for code in missing:
        # 单轮时限：超时则保留剩余（连同未跑的）留下轮
        if time.monotonic() - started > _TIME_BUDGET:
            limited = True
            logger.info("自愈单轮时限到 %s，中止本轮（已处理 %s/%s）",
                        task.trade_date, done, len(missing))
            break
        # 单轮股票上限
        if done >= _CODE_BATCH:
            limited = True
            logger.info("自愈单轮股票上限 %s 到 %s，剩余留下轮",
                        _CODE_BATCH, task.trade_date)
            break
        done += 1
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
            fail_streak = 0
        except Exception as e:
            if _blocked(e):
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
            fail_streak += 1
            if fail_streak >= _FAIL_STREAK_MAX:
                # 连败熔断：瞬时错误（RemoteDisconnected 等 is_source_blocked 抓不到）
                # 成片出现时中止本轮，保留 pending（不增 attempts），下轮再试
                limited = True
                logger.error("自愈连败 %s 只 → 判源不稳，中止本轮 %s",
                             fail_streak, task.trade_date)
                break
        if interval > 0:
            time.sleep(interval)

    task.detail = {**(task.detail or {}), "repaired_count": repaired}

    if limited:
        # 限速中止：剩余（含未处理的）写回 detail，保持 pending，**attempts 不增**
        processed = set(missing[:done])
        remaining = [c for c in missing if c not in processed]
        task.detail["missing_codes"] = remaining
        task.detail.pop("failed_codes", None)
        task.status = "pending"
        db.add(task)
        db.commit()
        summary["progress_limited"] = summary.get("progress_limited", 0) + 1
        logger.info("自愈限速中止 %s：本轮补 %d 只，剩 %d 只下轮",
                    task.trade_date, repaired, len(remaining))
        return

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
