"""自愈 stage2（quant-engine）：复检 coverage → 重算因子/信号 → 回告飞书

Issue #4 两段式交接的第二段（数据契约见迁移 005）：
  stage1（collector/app/remediation.py）补齐 daily_price 后置 status='repaired'；
  本模块每 5 分钟轮询 repaired 队列：
    复检 coverage 全绿 → 幂等重算当日因子+信号（compute_and_store / generate_signals）
                      → status='done' → 飞书绿卡「已自动修复」
    仍红 → attempts+1：≥MAX_ATTEMPTS → 'failed'（红卡升级人工）；否则回 'pending'
          再走 stage1（下一轮补齐）
护栏：绿卡/红卡走 task_run（remedi:{check} / remedi:{check}:failed）每交易日一次；
     attempt 上限 3 次，5 分钟轮询天然隔开重试；trade 不做自愈。
"""
import logging
from datetime import date

from sqlalchemy import select

from app.data_quality import _check_coverage
from app.db import get_session
from app.models.tables import NotifyConfig, RemediationTask
from app.task_run import already_run, record_task

logger = logging.getLogger("remediation")

MAX_ATTEMPTS = 3
_CHECK = "coverage"  # 本期仅 coverage（P1 后续扩展 missing_days/valuation/...）


def consume_repaired() -> dict:
    """领取并处理 repaired 任务（由 job_consume_remediation 每 5 分钟调用）"""
    db = get_session()
    summary = {"processed": 0, "done": 0, "requeued": 0, "failed": 0, "recompute_failed": 0}
    try:
        tasks = db.execute(
            select(RemediationTask)
            .where(RemediationTask.status == "repaired")
            .order_by(RemediationTask.trade_date)
            .limit(20)
        ).scalars().all()
        for task in tasks:
            summary["processed"] += 1
            _process(db, task, summary)
    finally:
        db.close()
    return summary


def _process(db, task: RemediationTask, summary: dict) -> None:
    """处理单个 repaired 任务"""
    if _check_coverage(db, task.trade_date).get("level") != "fail":
        # 复检全绿 → 重算 + 完成 + 绿卡
        try:
            _recompute(db, task.trade_date)
        except Exception:
            logger.exception("自愈复检通过但重算失败 %s", task.trade_date)
            summary["recompute_failed"] += 1
            _bump(db, task, keep="repaired")
            return
        _finish(db, task)
        summary["done"] += 1
        return

    # 仍红 → attempt 流转
    summary["requeued" if task.attempts + 1 < MAX_ATTEMPTS else "failed"] += 1
    _bump(db, task)


def _recompute(db, td: date) -> None:
    """幂等重算当日因子与信号（compute_and_store / generate_signals 均按日 upsert，
    即使 19:00/19:30 已跑过，覆盖重算安全无副作用）"""
    from app.factor_service import compute_and_store
    from app.tasks import generate_signals

    compute_and_store(db, td)
    try:
        generate_signals(db, td)
    except RuntimeError as e:
        # 无 active 策略等配置态：不是数据问题，信号跳过不视为失败
        logger.warning("自愈重算信号跳过 %s：%s", td, e)


def _bump(db, task: RemediationTask, keep: str | None = None) -> None:
    """attempt 流转：≥MAX_ATTEMPTS → failed（红卡升级人工）；否则回 pending 再走 stage1。

    keep 指定时（复检绿但重算失败）保持原状态等下一轮重试，仅计 attempt。
    """
    task.attempts += 1
    if task.attempts >= MAX_ATTEMPTS:
        task.status = "failed"
        logger.error("自愈修复失败 %s/%s（attempts=%d）→ 升级人工",
                     task.trade_date, task.check_name, task.attempts)
    else:
        task.status = keep if keep is not None else "pending"
        logger.warning("自愈仍未绿 %s/%s（attempts=%d）→ %s",
                       task.trade_date, task.check_name, task.attempts, task.status)
    # 先落库再通知：record_task 内部失败时会 db.rollback()，若通知在前会连带回滚
    # 本任务的未提交状态变更（sqlite 单测已踩；生产 postgres 虽无此问题，顺序仍更稳）
    db.add(task)
    db.commit()
    if task.status == "failed":
        _notify_failed(db, task)


def _finish(db, task: RemediationTask) -> None:
    """复检全绿：置 done + 回告绿卡"""
    repaired = (task.detail or {}).get("repaired_count") or 0
    task.status = "done"
    task.detail = {**(task.detail or {}), "repaired_count": repaired}
    db.add(task)
    db.commit()
    logger.info("自愈完成 %s/%s（修复 %s 只）", task.trade_date, task.check_name, repaired)
    _notify_fixed(db, task, repaired)


# ---------- 飞书回告（事件型，页面 notify_config['remedi'] 可控；去重走 task_run）----------

def _notify_fixed(db, task: RemediationTask, repaired: int) -> None:
    key = f"remedi:{_CHECK}"
    if _send_card(db, key, task.trade_date, "✅ Steady · 已自动修复",
                  (f"**coverage 已自动修复**\n\n"
                   f"- 修复股票：**{repaired}** 只（diff-repair 补齐）\n"
                   f"- 复检：行情覆盖已回到阈值以上\n"
                   f"- 因子/信号：已幂等重算（{task.trade_date}）\n\n"
                   f"本次数据健康红卡已闭环，无需人工处理。"),
                  template="green", footer="自愈流水线 · 数据健康"):
        record_task(db, key, task.trade_date, "success", "已自动修复",
                    detail={"repaired_count": repaired})


def _notify_failed(db, task: RemediationTask) -> None:
    key = f"remedi:{_CHECK}:failed"
    if _send_card(db, key, task.trade_date, "❌ Steady · 自动修复失败",
                  (f"**coverage 自动修复未能闭环**（已重试 {MAX_ATTEMPTS} 次）\n\n"
                   f"- 日期：{task.trade_date}\n"
                   f"- 状态：仍低于覆盖阈值，请人工介入（检查源可用性 / 手动回填）"),
                  template="red", footer="自愈流水线 · 数据健康"):
        record_task(db, key, task.trade_date, "failed", "自动修复失败",
                    detail={"trade_date": str(task.trade_date)})


def _send_card(db, key: str, td: date, title: str, content: str,
               template: str, footer: str) -> bool:
    """发送卡片；未启用/已推送过 → False（record_task 记 skipped，不重复轰炸）"""
    if already_run(db, key, td):
        return False
    from app.notify import FeishuNotifier, load_config

    cfg = load_config(db)
    if not cfg["enabled"]:
        record_task(db, key, td, "skipped", "飞书通知未启用")
        return False
    ev = db.execute(
        select(NotifyConfig).where(NotifyConfig.event_key == "remedi")
    ).scalar()
    if ev is None or not ev.enabled:
        record_task(db, key, td, "skipped", "remedi 通知未启用")
        return False
    notifier = FeishuNotifier(cfg)
    return notifier.send_card(title, content, template=template, footer=footer)
