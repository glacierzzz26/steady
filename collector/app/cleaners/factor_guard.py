"""复权因子除权一致性守卫（阶段 3：adj_factor 切 BaoStock 派生因子的拒收层）

背景（docs/phase2/design/数据源评估-BaoStock.md §2.2/§6）：BaoStock 派生因子
全池 51% 有分段阶跃，其中平安银行（2020-12-31 虚假调整 16.7%，close 反涨）与
天齐锂业（2020-01-02 滞后调整 16%，无对应价格跳空）为实证缺陷。本守卫在入库前
校验「因子阶跃必须对应价格跳空」，不符 → 整段拒收、由采集器降级 AkShare
（东财/新浪 hfq 派生因子），保该股单源一致。

判据（后复权连续性）：hfq[d-1] ≈ hfq[d]，即 close[d-1]·f[d-1] ≈ close[d]·f[d]
    ⇒  step = f[d]/f[d-1] − 1  ≈  gap = close[d-1]/close[d] − 1

虚假/滞后调整 = 因子阶跃但价格无对应跳空 ⇒ step 与 gap 明显背离（可正可负）。
注意：计划文档 `serialized-tinkering-meteor.md` 把 step 写作 f[d-1]/f[d]−1，
那会让 step 与 gap 恒反号、把所有正常除权日都误拒；此处按后复权连续性用
f[d]/f[d-1]−1（两者差一个符号），实证判据一致——平安（step=+16.7% 但
close 反涨 → gap<0）、天齐（step=+16% 无跳空 → gap≈0）均命中。

容差 max(0.07, 0.5·|step|)：step−gap ≈ 除权日当日市场涨跌（后复权连续性只
除息、保留市场收益），故小阶跃的容差下限须吸收市场异动幅度。dev 对账实证：
茅台 2008-06-16 真实除息 step 0.57% / gap 3.45%（大盘大跌）、天齐 2013-06-13
钱荒日 step 0.24% / gap 6.77%——1.5%/3% 下限均误拒，7% 下限吸收。大阶跃按
±50% 相对背离容忍（送转日 step≈gap 天然放行；平安 2020-12-31 虚假 16.7%、
天齐 2020-01-02 滞后 16% 仍拒——16% > 0.5·16%=8%）。

残余误伤面：**纯大跌日（无除权）**——step≈0 但 gap>7%（如 000009 2021-08-26
gap 8.4%，DB Tushare 因子当日仅动 0.1%，与 BaoStock 一致，确非除权），单日
跌幅 >7% 即触发。7% 下限不再上调（放大会漏过平安 16.8% 级真异常）；历史重算
脚本 rewrite_adj_factor 在对账层用 DB↔BaoStock 比值一致性兜底（guard 拒收但
比值 ≤0.5% → 误伤，判定可重写；比值漂移 → 真异常，保留 Tushare）。
"""
import logging

logger = logging.getLogger(__name__)

# 容差绝对下限：7%（吸收除权日市场异动；1.5%/3% 曾误拒茅台 2008-06-16、
# 天齐 2013-06-13 等真实小分红 + 大跌日，dev 对账逐级提升）
_MIN_TOL = 0.07
# 大阶跃相对容差系数：|step−gap| 超过 0.5·|step| 才判背离（防市场异动误伤）
_REL_TOL = 0.5


def factor_change_pairs(rows):
    """相邻交易日复权因子变化对 → 迭代 (d1, f1, c1, d2, f2, c2)

    因子或收盘缺失（None）断开连续性上下文：前一行不再作为下一对的 d1。
    """
    prev = None
    for r in rows:
        f, c = r.get("adj_factor"), r.get("close")
        if f is None or c is None:
            prev = None
            continue
        if prev is not None and abs(f - prev[1]) > 1e-9:
            yield (prev[0], prev[1], prev[2], r["trade_date"], f, c)
        prev = (r["trade_date"], f, c)


def guard_factor(rows: list[dict], window_start=None) -> tuple[list[dict], bool]:
    """除权一致性校验 → (rows, ok)。

    :param window_start: 仅对 trade_date >= window_start 的因子变化对判违规；
        更早的行只作上下文（采集窗口 start−7 拉取的边界行不因自身旧数据被拒）。
        None 则全部判定（历史重算脚本 rewrite_adj_factor 用）。
    :return: (rows, ok)；ok=False 时调用方整段降级，保该股单源一致。
    """
    if not rows:
        return rows, True
    code = rows[0].get("code")
    for d1, f1, c1, d2, f2, c2 in factor_change_pairs(rows):
        if window_start is not None and d2 < window_start:
            continue
        if f1 <= 0 or f2 <= 0 or c1 <= 0 or c2 <= 0:
            continue
        step = f2 / f1 - 1
        gap = c1 / c2 - 1
        tol = max(_MIN_TOL, _REL_TOL * abs(step))
        if abs(step - gap) > tol:
            logger.warning(
                "复权因子拒收 %s %s→%s（%s→%s）：step=%.4f gap=%.4f 容差=%.4f",
                code, d1, d2, f1, f2, step, gap, tol)
            return rows, False
    return rows, True
