"""quant-engine 看门狗测试（Issue #14 / L3，**验收 #3** 的引擎侧镜像）。

与 `collector/tests/test_watchdog.py` 同构（两个镜像无共享包，仓库已重复 `_start_healthz`
先例）。差异只在告警落地：本服务就是账本写入方，`alert_and_exit` 直接调
`app.task_run.record_task`，故断言「`record_task` 收到 (task_name='watchdog', failed)」
而非断言 SQL 语句。

**不碰真库**：`record_task` / `get_session` 均打桩；`exit_fn` 注入 → 不会真的退出进程。
`startup_catchup` 的时间窗依赖"现在几点"，故显式传入固定 `now`。
"""
import time
from datetime import date, datetime

import pytest

from app import watchdog

# 固定时刻：job_calc_factors 计划 19:00 / job_generate_signals 计划 19:30
NOW_AFTER_PLAN = datetime(2026, 9, 16, 20, 0)
NOW_BEFORE_PLAN = datetime(2026, 9, 16, 8, 0)


@pytest.fixture(autouse=True)
def _clean_state():
    def _reset():
        with watchdog._lock:
            watchdog._running.clear()
        watchdog._catchup_probes.clear()
    _reset()
    yield
    _reset()


# ---------- overdue_jobs：纯函数 ----------

def test_overdue_detects_and_clears(monkeypatch):
    monkeypatch.setitem(watchdog.JOB_LIMITS, "job_x", 100)
    with watchdog.job_guard("job_x"):
        assert watchdog.overdue_jobs(time.monotonic()) == []
    assert watchdog.overdue_jobs(time.monotonic()) == []


def test_overdue_reports_elapsed(monkeypatch):
    monkeypatch.setitem(watchdog.JOB_LIMITS, "job_x", 100)
    with watchdog._lock:
        watchdog._running["job_x"] = 1000.0
    assert watchdog.overdue_jobs(now=1150.0) == [("job_x", 150.0)]


def test_notify_tick_limit_is_tight():
    """notify_tick 每 1 分钟一跳——上限必须远小于 DEFAULT，否则卡死一个月都发现不了"""
    assert watchdog._limit("notify_tick") <= 600
    assert watchdog._limit("notify_tick") < watchdog.DEFAULT_LIMIT


def test_limit_env_override(monkeypatch):
    monkeypatch.setitem(watchdog.JOB_LIMITS, "job_x", 100)
    monkeypatch.setenv("QE_JOB_LIMIT_JOB_X", "5")
    assert watchdog._limit("job_x") == 5.0


def test_limit_env_invalid_falls_back(monkeypatch):
    monkeypatch.setitem(watchdog.JOB_LIMITS, "job_x", 100)
    monkeypatch.setenv("QE_JOB_LIMIT_JOB_X", "abc")
    assert watchdog._limit("job_x") == 100.0


def test_limit_unknown_job_uses_default():
    assert watchdog._limit("job_never_seen") == float(watchdog.DEFAULT_LIMIT)


def test_guarded_tracks_run_and_clears(monkeypatch):
    monkeypatch.setitem(watchdog.JOB_LIMITS, "f", 1)
    seen = {}

    @watchdog.guarded
    def f():
        seen["during"] = [j for j, _ in watchdog.overdue_jobs(now=time.monotonic() + 60)]
        return "r"

    assert f() == "r"
    assert seen["during"] == ["f"]
    assert watchdog.overdue_jobs() == []


# ---------- healthz_status ----------

def test_healthz_ok():
    code, payload = watchdog.healthz_status()
    assert code == 200 and payload["status"] == "ok"


def test_healthz_overdue_returns_503(monkeypatch):
    monkeypatch.setitem(watchdog.JOB_LIMITS, "job_x", 1)
    with watchdog._lock:
        watchdog._running["job_x"] = 0.0
    code, payload = watchdog.healthz_status()
    assert code == 503 and payload["status"] == "stale"
    assert payload["job"] == "job_x"


# ---------- alert_and_exit：走 record_task ----------

@pytest.fixture
def recorded(monkeypatch):
    """记录 record_task / get_session 调用（不碰真库）"""
    calls = []

    class FakeDB:
        def close(self):
            calls.append(("close",))

    monkeypatch.setattr("app.db.get_session", lambda: FakeDB())

    def fake_record(db, task_name, run_date, status, message="", detail=None):
        calls.append({
            "task_name": task_name, "run_date": run_date, "status": status,
            "message": message, "detail": detail,
        })

    monkeypatch.setattr("app.task_run.record_task", fake_record)
    return calls


@pytest.fixture
def exit_file(tmp_path, monkeypatch):
    p = tmp_path / ".qe_watchdog_exits"
    monkeypatch.setattr(watchdog, "EXIT_THRESHOLD_FILE", str(p))
    return p


def _capture_exit():
    calls = []
    return calls, (lambda code: calls.append(code))


def test_alert_records_failed_task_run_and_exits(recorded, exit_file):
    exits, exit_fn = _capture_exit()
    watchdog.alert_and_exit("运行超时 >300s", "notify_tick", 400.0, exit_fn=exit_fn)

    assert exits == [1], "应在预算内退出"
    rec = [c for c in recorded if isinstance(c, dict)][0]
    assert rec["task_name"] == "watchdog"
    assert rec["run_date"] == date.today()
    assert rec["status"] == "failed"
    assert "notify_tick" in rec["message"] and "400" in rec["message"]
    assert rec["detail"]["job"] == "notify_tick"
    assert rec["detail"]["exit_count"] == 1
    assert "pid" in rec["detail"] and "at" in rec["detail"]


def test_alert_writes_file_log(recorded, exit_file):
    exits, exit_fn = _capture_exit()
    watchdog.alert_and_exit("原因", "job_x", 10.0, exit_fn=exit_fn)
    log = exit_file.parent / "watchdog.log"
    assert log.exists() and "job_x" in log.read_text()


def test_exit_budget_alert_only_after_max(recorded, exit_file):
    """超 MAX_EXITS_PER_DAY 后只告警不退出（防重启抖动）"""
    exits, exit_fn = _capture_exit()
    for i in range(1, watchdog.MAX_EXITS_PER_DAY + 1):
        watchdog.alert_and_exit(f"第{i}次", "job_x", 1.0, exit_fn=exit_fn)
    assert exits == [1] * watchdog.MAX_EXITS_PER_DAY

    exits.clear()
    recorded.clear()
    watchdog.alert_and_exit("超预算", "job_x", 1.0, exit_fn=exit_fn)
    assert exits == [], "超预算后不得再退出"
    rec = [c for c in recorded if isinstance(c, dict)][0]
    assert "超今日重启预算" in rec["message"]
    assert rec["detail"]["exit_count"] == watchdog.MAX_EXITS_PER_DAY + 1


def test_exits_counter_survives_and_resets_per_day(exit_file):
    assert watchdog._exits_today() == 0
    assert watchdog._bump_exits() == 1
    exit_file.write_text("2020-01-01 9")
    assert watchdog._exits_today() == 0


def test_alert_db_failure_does_not_prevent_exit(exit_file, monkeypatch):
    """DB 卡死正是要自杀的场景——写账本失败绝不能拦住退出"""
    def boom():
        raise RuntimeError("DB 挂了")
    monkeypatch.setattr("app.db.get_session", boom)
    exits, exit_fn = _capture_exit()
    watchdog.alert_and_exit("DB 卡死", "job_x", 1.0, exit_fn=exit_fn)
    assert exits == [1]
    assert (exit_file.parent / "watchdog.log").exists()


# ---------- startup_catchup：因子/信号 ----------

@pytest.fixture
def calendar(monkeypatch):
    """交易日历判定打桩（startup_catchup 经 get_session 读 is_open）"""
    def _set(is_open):
        class FakeDB:
            def execute(self, stmt):
                class R:
                    def scalar(self_inner):
                        return is_open
                return R()

            def close(self):
                pass
        monkeypatch.setattr("app.db.get_session", lambda: FakeDB())
    return _set


def test_catchup_skipped_on_non_trading_day(calendar):
    calendar(False)
    watchdog.register_catchup("job_calc_factors", lambda d: False)
    assert watchdog.startup_catchup(now=NOW_AFTER_PLAN) == []


def test_catchup_skipped_when_calendar_missing(calendar):
    calendar(None)
    watchdog.register_catchup("job_calc_factors", lambda d: False)
    assert watchdog.startup_catchup(now=NOW_AFTER_PLAN) == []


def test_catchup_runs_calc_factors(calendar, monkeypatch):
    """核心：交易日 + 19:00 已过 + 当日因子未产出 → 补算"""
    calendar(True)
    watchdog.register_catchup("job_calc_factors", lambda d: False)
    ran = []
    monkeypatch.setattr("app.tasks.job_calc_factors", lambda: ran.append("factors"))
    assert watchdog.startup_catchup(now=NOW_AFTER_PLAN) == ["job_calc_factors"]
    assert ran == ["factors"]


def test_catchup_runs_generate_signals(calendar, monkeypatch):
    calendar(True)
    watchdog.register_catchup("job_generate_signals", lambda d: False)
    ran = []
    monkeypatch.setattr("app.tasks.job_generate_signals", lambda: ran.append("signals"))
    assert watchdog.startup_catchup(now=NOW_AFTER_PLAN) == ["job_generate_signals"]
    assert ran == ["signals"]


def test_catchup_skipped_when_already_produced(calendar, monkeypatch):
    calendar(True)
    watchdog.register_catchup("job_calc_factors", lambda d: True)
    ran = []
    monkeypatch.setattr("app.tasks.job_calc_factors", lambda: ran.append("factors"))
    assert watchdog.startup_catchup(now=NOW_AFTER_PLAN) == []
    assert ran == []


def test_catchup_skipped_before_planned_time(calendar, monkeypatch):
    calendar(True)
    watchdog.register_catchup("job_calc_factors", lambda d: False)
    ran = []
    monkeypatch.setattr("app.tasks.job_calc_factors", lambda: ran.append("factors"))
    assert watchdog.startup_catchup(now=NOW_BEFORE_PLAN) == []
    assert ran == []


def test_catchup_running_job_not_disturbed(calendar, monkeypatch):
    calendar(True)
    watchdog.register_catchup("job_calc_factors", lambda d: False)
    ran = []
    monkeypatch.setattr("app.tasks.job_calc_factors", lambda: ran.append("factors"))
    with watchdog.job_guard("job_calc_factors"):
        assert watchdog.startup_catchup(now=NOW_AFTER_PLAN) == []
    assert ran == []


def test_catchup_probe_failure_isolated(calendar, monkeypatch):
    calendar(True)

    def boom(_d):
        raise RuntimeError("探针炸了")
    watchdog.register_catchup("job_calc_factors", boom)
    watchdog.register_catchup("job_generate_signals", lambda d: False)
    ran = []
    monkeypatch.setattr("app.tasks.job_generate_signals", lambda: ran.append("signals"))
    assert watchdog.startup_catchup(now=NOW_AFTER_PLAN) == ["job_generate_signals"]
    assert ran == ["signals"]


def test_catchup_job_exception_isolated(calendar, monkeypatch):
    calendar(True)

    def boom():
        raise RuntimeError("补跑失败")
    watchdog.register_catchup("job_calc_factors", lambda d: False)
    watchdog.register_catchup("job_generate_signals", lambda d: False)
    ran = []
    monkeypatch.setattr("app.tasks.job_calc_factors", boom)
    monkeypatch.setattr("app.tasks.job_generate_signals", lambda: ran.append("signals"))
    watchdog.startup_catchup(now=NOW_AFTER_PLAN)
    assert ran == ["signals"]


def test_catchup_probe_receives_delta_hours(calendar):
    calendar(True)
    got = []
    watchdog.register_catchup("job_calc_factors", lambda d: got.append(d) or True)
    watchdog.startup_catchup(now=NOW_AFTER_PLAN)
    want = (NOW_AFTER_PLAN - datetime(2026, 9, 16, 19, 0)).total_seconds() / 3600
    assert len(got) == 1 and abs(got[0] - want) < 1e-6


# ---------- 真实探针接线 ----------

def test_register_catchups_wires_real_probes():
    from app import tasks

    watchdog._catchup_probes.clear()
    tasks.register_catchups()
    assert {"job_calc_factors", "job_generate_signals"} <= set(watchdog._catchup_probes)
    assert watchdog._catchup_probes["job_morning_brief"](0) is True  # _never_catchup
    # 注册了探针却没排进 JOB_SCHEDULE → 永远轮不到补跑（静默失效），必须钉住
    assert set(watchdog._catchup_probes) <= set(watchdog.JOB_SCHEDULE)
    # 反向：JOB_SCHEDULE 里每条都应有探针，否则那条时间窗是死配置
    assert set(watchdog.JOB_SCHEDULE) <= set(watchdog._catchup_probes)


def test_job_limits_cover_scheduled_jobs():
    """定时注册的 job 都该有显式上限（否则走 DEFAULT=1h，长任务会被误判）"""
    scheduled = {
        "job_morning_brief", "job_calc_factors", "job_precompute_factor_stat",
        "job_generate_signals", "job_data_quality", "job_consume_backtests",
        "job_consume_factor_trials", "job_consume_remediation",
        "job_perf_monthly_report", "job_precompute_perf", "notify_tick",
    }
    missing = scheduled - set(watchdog.JOB_LIMITS)
    assert not missing, f"以下定时 job 缺显式上限，将走 DEFAULT_LIMIT：{missing}"
