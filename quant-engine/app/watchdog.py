"""任务级看门狗（Issue #14，quant-engine 侧）：识别「进程活着但 job 卡死」并自愈 + 告警。

与 collector/app/watchdog.py 同构（两个镜像无共享包，仓库已重复 `_start_healthz` 先例）。
差异：
  - 告警直接走本地 `app.task_run.record_task`（本服务就是账本的写入方）；
  - job 集合与上限按本引擎的时间表校准。

**自愈方式**：`os._exit(1)` → Docker `restart: unless-stopped` 拉起。
⚠️ **必须 `os._exit`**：SystemExit 会触发 `concurrent.futures.thread._python_exit`，
后者 join 每个 worker 线程 → 卡死 worker 不可杀，`sys.exit` 会卡在退不出去。

**为何用独立 daemon 线程**：APScheduler 默认 executor 仅 10 worker，若 10 个 job 卡死
占满，看门狗自身就永无机会跑。独立线程不受 executor/max_instances/主循环阻塞影响。
"""
import functools
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime

logger = logging.getLogger("watchdog")

# 各 job 上限（秒）；env `QE_JOB_LIMIT_<JOB 大写>` 可覆盖
JOB_LIMITS = {
    "job_morning_brief": 1800,
    "job_calc_factors": 2700,
    "job_precompute_factor_stat": 2700,
    "job_generate_signals": 1800,
    "job_data_quality": 3600,
    "job_consume_backtests": 900,
    "job_consume_factor_trials": 900,
    "job_consume_remediation": 900,
    "job_precompute_perf": 1800,
    "job_perf_monthly_report": 900,
    "notify_tick": 300,  # 每 1 分钟一跳——超过 300s 即真信号
}
DEFAULT_LIMIT = 3600
EXIT_THRESHOLD_FILE = os.getenv("QE_WATCHDOG_EXIT_FILE", "/app/logs/.qe_watchdog_exits")
MAX_EXITS_PER_DAY = 3
SCAN_INTERVAL = 60

# 各 job 今日计划时刻 (时, 分)，与 tasks.py add_job 一致，供启动补跑判定时间窗
JOB_SCHEDULE = {
    "job_morning_brief": (9, 10),
    "job_data_quality": (18, 30),
    "job_calc_factors": (19, 0),
    "job_precompute_factor_stat": (19, 5),
    "job_generate_signals": (19, 30),
    "job_precompute_perf": (21, 20),
}

_running: dict[str, float] = {}
_lock = threading.Lock()
_catchup_probes: dict[str, callable] = {}


def _limit(job: str) -> float:
    env = os.getenv(f"QE_JOB_LIMIT_{job.upper()}")
    if env:
        try:
            return float(env)
        except ValueError:
            logger.warning("环境变量 QE_JOB_LIMIT_%s=%r 非法，忽略", job.upper(), env)
    return float(JOB_LIMITS.get(job, DEFAULT_LIMIT))


def register_catchup(job: str, probe) -> None:
    """注册「该 job 今日是否已完成」判定（返回 True = 已完成，无需补跑）"""
    _catchup_probes[job] = probe


@contextmanager
def job_guard(job: str):
    with _lock:
        _running[job] = time.monotonic()
    try:
        yield
    finally:
        with _lock:
            _running.pop(job, None)


def guarded(fn):
    """装饰器：以函数名作为 job 名计时（两条调用路径——调度器与补跑——都受保护）"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with job_guard(fn.__name__):
            return fn(*args, **kwargs)

    return wrapper


def is_running(job: str) -> bool:
    return job in _running


def overdue_jobs(now: float | None = None) -> list[tuple[str, float]]:
    """超限仍在跑的 job [(job, 已运行秒数)]。纯函数，无 DB 无线程。"""
    if now is None:
        now = time.monotonic()
    with _lock:
        snapshot = dict(_running)
    return [(j, now - s) for j, s in snapshot.items() if now - s > _limit(j)]


def _exits_today() -> int:
    try:
        with open(EXIT_THRESHOLD_FILE, encoding="utf-8") as f:
            day, n = f.read().strip().split()
            return int(n) if day == date.today().isoformat() else 0
    except Exception:
        return 0


def _bump_exits() -> int:
    n = _exits_today() + 1
    try:
        os.makedirs(os.path.dirname(EXIT_THRESHOLD_FILE), exist_ok=True)
        with open(EXIT_THRESHOLD_FILE, "w", encoding="utf-8") as f:
            f.write(f"{date.today().isoformat()} {n}")
    except Exception:
        logger.warning("写退出计数文件失败: %s", EXIT_THRESHOLD_FILE)
    return n


def alert_and_exit(reason: str, job: str, elapsed: float = 0.0, exit_fn=os._exit) -> None:
    """告警 + 退出（exit_fn 可注入以便测试）。顺序：日志文件 → task_run → os._exit。"""
    from app.task_run import record_task

    n = _bump_exits()
    detail = {"reason": reason, "job": job, "elapsed": round(elapsed, 1),
              "exit_count": n, "pid": os.getpid(),
              "at": datetime.now().isoformat(timespec="seconds")}
    message = (f"引擎任务卡死：{job} 已运行 {round(elapsed)}s（{reason}），"
               f"触发容器重启（今日第 {n} 次）")
    logger.critical("%s — 详情 %s", message, detail)

    try:
        log_path = os.path.join(os.path.dirname(EXIT_THRESHOLD_FILE), "watchdog.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{detail['at']} {message} {detail}\n")
    except Exception:
        pass

    try:
        from app.db import get_session

        db = get_session()
        try:
            record_task(db, "watchdog", date.today(), "failed",
                        message + ("" if n <= MAX_EXITS_PER_DAY else "（超今日重启预算）"),
                        detail=detail)
        finally:
            db.close()
    except Exception:
        logger.exception("写 watchdog task_run 失败（告警退回文件日志）")

    if n <= MAX_EXITS_PER_DAY:
        logger.critical("看门狗触发进程退出（os._exit(1)）→ 依赖 Docker restart 拉起")
        exit_fn(1)
    else:
        logger.critical("今日已退出 %s 次，超上限 %s —— 仅告警不再自动重启",
                        n, MAX_EXITS_PER_DAY)


def healthz_status() -> tuple[int, dict]:
    overdue = overdue_jobs()
    if overdue:
        job, elapsed = overdue[0]
        return 503, {"status": "stale", "job": job,
                     "elapsed": round(elapsed, 1), "overdue": len(overdue)}
    return 200, {"status": "ok", "running": len(_running)}


def startup_catchup(window_hours: float = 6.0,
                    now: datetime | None = None) -> list[str]:
    """重启后补跑错过的日频任务（MemoryJobStore 不会重跑已过去的触发点）。

    门控：交易日 + 距计划时刻 window_hours 内 + 未在跑 + 探针判定未完成。
    `now` 可注入（默认当前时刻）——时间窗判定依赖"现在几点"，不注入就没法在任意钟点稳定测试。
    """
    from sqlalchemy import select

    from app.db import get_session
    from app.models.tables import TradeCalendar

    db = get_session()
    try:
        is_open = db.execute(
            select(TradeCalendar.is_open).where(TradeCalendar.cal_date == date.today())
        ).scalar()
    finally:
        db.close()
    if not is_open:
        logger.info("非交易日或日历缺失，跳过启动补跑")
        return []

    if now is None:
        now = datetime.now()
    ran: list[str] = []
    for job, probe in _catchup_probes.items():
        if is_running(job):
            continue
        hh, mm = JOB_SCHEDULE.get(job, (None, None))
        if hh is None:
            continue
        delta = (now - now.replace(hour=hh, minute=mm, second=0, microsecond=0)).total_seconds() / 3600.0
        if not (0 <= delta <= window_hours):
            continue
        try:
            if probe(delta):
                continue
        except Exception:
            logger.exception("补跑探针失败，跳过 %s", job)
            continue
        logger.warning("启动补跑：%s 今日计划 %02d:%02d 未完成，立即补跑", job, hh, mm)
        from app import tasks as _tasks

        fn = getattr(_tasks, job, None)
        if fn is None:
            continue
        try:
            fn()
            ran.append(job)
        except Exception:
            logger.exception("启动补跑失败：%s", job)
    return ran


def _watchdog_loop() -> None:
    while True:
        time.sleep(SCAN_INTERVAL)
        try:
            overdue = overdue_jobs()
            if overdue:
                job, elapsed = max(overdue, key=lambda x: x[1])
                alert_and_exit(f"运行超时 >{_limit(job)}s", job, elapsed)
        except Exception:
            logger.exception("看门狗巡检异常（继续）")


def start_watchdog() -> threading.Thread:
    t = threading.Thread(target=_watchdog_loop, name="watchdog", daemon=True)
    t.start()
    logger.info("看门狗已启动（间隔 %ss，退出预算 %s/日）", SCAN_INTERVAL, MAX_EXITS_PER_DAY)
    return t


def spawn_startup_catchup(delay: int = 60) -> threading.Thread:
    def _go():
        time.sleep(delay)
        try:
            ran = startup_catchup()
            if ran:
                logger.info("启动补跑完成：%s", ran)
        except Exception:
            logger.exception("启动补跑异常")

    t = threading.Thread(target=_go, name="startup_catchup", daemon=True)
    t.start()
    return t
