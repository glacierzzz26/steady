"""阶段 3：rewrite_adj_factor 对账分类器（classify 纯函数）单测

分类键语义（详见脚本 classify docstring）：
  accepted    → 可重写（guard 通过 + 比值一致，或填补 NULL）
  db_anomaly  → 可重写（比值漂移但 DB 因子不自洽 = DB 侧虚假调整，如 601699）
  false_pos   → 可重写（guard 拒收但比值一致 = 大跌日误伤）
  drifted     → 保留 DB（两源自洽但跨源口径分歧，如 000002）
  rejected    → 保留 DB（guard 拒收且比值漂移/不可比 = 真异常，如 000001）
"""
import importlib.util
import pathlib
import sys

sys.path.insert(0, "collector")

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "rewrite_adj_factor.py"
_spec = importlib.util.spec_from_file_location("rewrite_adj_factor", _SCRIPT)
_raf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_raf)


def test_accept_guard_pass_ratio_ok():
    assert _raf.classify(ok=True, db_ok=True, drift=False, ratios=True) == "accepted"


def test_accept_fill_null():
    assert _raf.classify(ok=True, db_ok=True, drift=False, ratios=False) == "accepted"


def test_db_anomaly_rewrite():
    # 601699 型：BaoStock 自洽但 DB 因子不自洽（单日虚假调整）→ 改写 BaoStock
    assert _raf.classify(ok=True, db_ok=False, drift=True, ratios=True) == "db_anomaly"


def test_db_anomaly_precedes_source_guard_misfire():
    # 2026-08-29 修复：源守卫在熔断/暴跌日误伤（ok=False）不得短路 db_anomaly。
    # 601699/601567/600460 型：DB 因子不自洽 + 比值漂移 → 仍判改写源值（glitch 修复），
    # 而非原顺序的 rejected（glitch 滞留 DB）。
    assert _raf.classify(ok=False, db_ok=False, drift=True, ratios=True) == "db_anomaly"


def test_reject_true_anomaly_unchanged_by_reorder():
    # 平安/天齐型不受重排影响：DB/Tushare 自洽（db_ok=True）→ 仍 rejected 保留
    assert _raf.classify(ok=False, db_ok=True, drift=True, ratios=True) == "rejected"


def test_drift_keep_db():
    # 000002 型：两源自洽但跨源口径分歧 → 保留 DB
    assert _raf.classify(ok=True, db_ok=True, drift=True, ratios=True) == "drifted"


def test_reject_true_anomaly():
    # 000001 型：BaoStock 因子缺陷（guard 拒收 + 漂移）→ 保留 DB
    assert _raf.classify(ok=False, db_ok=True, drift=True, ratios=True) == "rejected"


def test_reject_no_overlap():
    # guard 拒收且无可比对 → 保留 DB
    assert _raf.classify(ok=False, db_ok=True, drift=False, ratios=False) == "rejected"


def test_false_pos_rewrite():
    # 000009 型：大跌日误伤（guard 拒收但比值一致）→ 判定可重写
    assert _raf.classify(ok=False, db_ok=True, drift=False, ratios=True) == "false_pos"
