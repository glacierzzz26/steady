"""停采闸门（COLLECTOR_DISABLED_JOBS）测试 —— datahub Phase 2 灰度切唯一采集方。

不变量：
- **默认空黑名单 ⇒ 行为零变化**（全部 job 注册、全部补跑探针接线）；
- 命中黑名单的 job：既不注册到 scheduler，也不注册补跑探针（防 startup_catchup 复活）。
"""
from app import config, tasks
from app import watchdog


class _Scheduler:
    """最小假调度器：记录 add_job 的 (job 名, 触发参数)。"""

    def __init__(self):
        self.jobs: list[tuple[str, dict]] = []

    def add_job(self, job, **kw):
        self.jobs.append((job.__name__, kw))


def test_default_blacklist_is_empty_and_disables_nothing():
    assert config.COLLECTOR_DISABLED_JOBS == []
    assert config.job_disabled("job_sync_calendar") is False


def test_add_job_registers_when_not_disabled():
    sched = _Scheduler()
    tasks._add_job(sched, tasks.job_sync_calendar, trigger="cron", hour=9, minute=5)
    assert [name for name, _ in sched.jobs] == ["job_sync_calendar"]


def test_add_job_skips_disabled(monkeypatch):
    monkeypatch.setattr(config, "COLLECTOR_DISABLED_JOBS", ["job_sync_calendar"])
    sched = _Scheduler()
    tasks._add_job(sched, tasks.job_sync_calendar, trigger="cron", hour=9, minute=5)
    assert sched.jobs == []


def test_add_job_only_skips_named_job(monkeypatch):
    """黑名单只停点名的 job，其它照常注册。"""
    monkeypatch.setattr(config, "COLLECTOR_DISABLED_JOBS", ["job_sync_calendar"])
    sched = _Scheduler()
    tasks._add_job(sched, tasks.job_sync_calendar, trigger="cron", hour=9, minute=5)
    tasks._add_job(sched, tasks.job_sync_daily_price,
                   trigger="cron", hour=18, minute=10)
    assert [name for name, _ in sched.jobs] == ["job_sync_daily_price"]


def test_register_catchups_all_wired_by_default():
    watchdog._catchup_probes.clear()
    tasks.register_catchups()
    assert set(watchdog._catchup_probes) == {
        "job_sync_daily_price", "job_sync_valuation", "job_sync_index",
        "job_sync_stock_list", "job_sync_calendar", "job_sync_finance",
        "job_nightly_backfill"}


def test_register_catchups_skips_disabled(monkeypatch):
    """被停采的 job 不得接探针——否则 startup_catchup 会把它复活。"""
    monkeypatch.setattr(config, "COLLECTOR_DISABLED_JOBS", ["job_sync_calendar"])
    watchdog._catchup_probes.clear()
    tasks.register_catchups()
    assert "job_sync_calendar" not in watchdog._catchup_probes
    assert len(watchdog._catchup_probes) == 6
