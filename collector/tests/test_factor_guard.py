"""复权因子除权一致性守卫测试（阶段 3：adj_factor 切 BaoStock 派生因子）"""
from datetime import date

from app.cleaners.factor_guard import factor_change_pairs, guard_factor


def _rows(dates, closes, factors):
    return [{"code": "600519", "trade_date": d, "close": c, "adj_factor": f}
            for d, c, f in zip(dates, closes, factors)]


def test_normal_dividend_pass():
    """正常分红：因子阶跃 3% 且价格对应跳空 3% → 接受"""
    rows = _rows(
        [date(2026, 8, 3), date(2026, 8, 4)],
        [10.0, 10.0 / 1.03],   # 除权日收盘降 3%
        [2.0, 2.06],           # 因子 +3%
    )
    _, ok = guard_factor(rows)
    assert ok


def test_small_dividend_with_market_noise_pass():
    """微分红 1% + 市场噪声 1%（gap 2%）→ 落在 1.5% 绝对容差内放行"""
    rows = _rows(
        [date(2026, 8, 3), date(2026, 8, 4)],
        [10.0, 10.0 / 1.02],   # gap ≈ +2%
        [2.0, 2.02],           # step +1%
    )
    _, ok = guard_factor(rows)
    assert ok  # |0.01 − 0.02| = 0.01 ≤ max(0.015, 0.005)


def test_large_dividend_with_modest_market_move_pass():
    """大分红 10% + 市场涨 4%（gap 14%）→ 大阶跃按比例容差（0.5·|step|）内放行"""
    rows = _rows(
        [date(2026, 8, 3), date(2026, 8, 4)],
        [10.0, 10.0 / 1.14],   # gap +14%
        [2.0, 2.20],           # step +10%
    )
    _, ok = guard_factor(rows)
    assert ok  # |0.10 − 0.14| = 0.04 ≤ max(0.015, 0.05)


def test_pingan_type_fake_adjust_reject():
    """平安型虚假调整：因子阶跃 16.7% 但 close 反涨（无跳空）→ 拒收"""
    rows = _rows(
        [date(2020, 12, 30), date(2020, 12, 31)],
        [10.0, 10.5],          # close 反涨 +5%
        [2.0, 2.334],          # 因子 +16.7%
    )
    _, ok = guard_factor(rows)
    assert not ok


def test_tianqi_type_late_adjust_reject():
    """天齐型滞后调整：因子阶跃 16% 但价格无跳空 → 拒收"""
    rows = _rows(
        [date(2020, 1, 1), date(2020, 1, 2)],
        [10.0, 10.0],          # 无跳空
        [2.0, 2.32],           # 因子 +16%
    )
    _, ok = guard_factor(rows)
    assert not ok


def test_window_start_ignores_context_bad_pairs():
    """window_start 只判窗口内变化对：窗口前（守卫上下文）的坏数据不误拒"""
    rows = _rows(
        [date(2020, 12, 30), date(2020, 12, 31),
         date(2026, 8, 3), date(2026, 8, 4)],
        [10.0, 10.5, 10.5, 10.5 / 1.03],   # 12-31→08-03 因子与收盘均连续
        [2.0, 2.334, 2.334, 2.405],         # 坏对在窗口前；08-04 正常分红 +3%
    )
    ws = date(2026, 8, 3)
    # 无 window_start：12-30→12-31 的坏对（step 16.7% 无跳空）被判违规
    _, ok = guard_factor(rows)
    assert not ok
    # 有 window_start：上下文坏对忽略，窗口内正常分红放行
    _, ok = guard_factor(rows, window_start=ws)
    assert ok


def test_factor_change_pairs_break_on_missing():
    """因子/收盘缺失断开连续性上下文：None 两侧都不成对"""
    rows = [
        {"trade_date": date(2026, 8, 1), "close": 10.0, "adj_factor": 2.0},
        {"trade_date": date(2026, 8, 2), "close": 10.0, "adj_factor": None},
        {"trade_date": date(2026, 8, 3), "close": 10.0, "adj_factor": 2.5},
    ]
    assert list(factor_change_pairs(rows)) == []


def test_empty_rows_pass():
    _, ok = guard_factor([])
    assert ok
