"""G10 试算/寻优任务消费者（2.3b）：参数化重算 + 检验，DB 队列消费

设计定稿《因子研究闭环》§4.2/§4.3/§6.2：
- IC 等统计数学复用 factor_research 同一份实现（G9/G10 同口径），
  "试算与 FactorLab 数字必然一致"是唯一可接受的状态。
- 试算/寻优定位为「对已有因子参数化重算 + 检验」，不解析任意公式：
  - ma_trend     params {"window": int}            window→短均线，long 缺省 20（MA5/MA20 结构）
                 params {"short": int, "long": int}  全显式控制
  - macd_signal  params {"fast": int, "slow": int, "signal": int}   缺省 12/26/9
  - value/quality/risk 因子无计算参数（as-of 取值），params 忽略
- 变体因子名约定：基础因子名 + "_" + 后缀（如 ma_trend_ma10、ma_trend_v2）；
  resolve_base_factor 按前缀解析，取最长匹配。
- 寻优热力图 = 参数轴 × 持有期 IC（设计 §6.2 例 {"window":[...],"horizon":[...]}）。

factor_trial.params 消费契约（Go 侧提交，迁移 003）：
  - 单组试算 {"start","end","params":{...}}   → result 为 trial 检验结果
  - 参数寻优 {"start","end","param_grid":{...}} → result 含 heatmap 网格
kind 由 params 是否含 param_grid 区分（§5 不加列）。
"""
import logging
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select, update

from app.db import get_session
from app.factor_research import (HORIZONS, PRICE_TAIL_DAYS, QUANTILE_H,
                                 adj_close_frame, forward_returns, ic_series,
                                 icir, quantile_returns_by_date)
from app.factor_service import (FACTOR_DIRECTION, PRICE_WINDOW_DAYS,
                                factor_raw_value, normalize_cross_section,
                                parametrized_params, pool_codes)
from app.models.tables import (DailyPrice, DailyValuation, FactorTrial,
                               FinancialIndicator)

logger = logging.getLogger(__name__)

# 非 "horizon" 即计算参数轴；仅支持单计算参数轴（设计 §6.2 热力图 = 参数 × 持有期）
HORIZON_KEY = "horizon"


# ---------- 变体因子解析 ----------

def resolve_base_factor(name: str) -> str:
    """变体因子名 → 基础因子（6 因子规范名）。约定：变体名 = 基础名 + "_" + 后缀。"""
    if name in FACTOR_DIRECTION:
        return name
    for base in sorted(FACTOR_DIRECTION, key=len, reverse=True):
        if name.startswith(base + "_"):
            return base
    raise ValueError(
        f"未知因子: {name}（试算/寻优仅支持 6 基础因子及其变体，变体名 = 基础名 + '_' + 后缀）")


# ---------- 区间输入一次性加载（trial 全区间计算，避免逐日重复查库） ----------

def load_range_inputs(db, start: date, end: date) -> dict:
    """[start, end] 区间因子输入一次加载。

    - qfq: {code: 前复权收盘 Series}（close × adj_factor；趋势信号对锚点不敏感，
      锚点在 MA/MACD 比较中抵消，见 factor_service 注释，直接乘不加锚）
    - adj_close: DataFrame(date×code) 复权收盘 pivot（IC 前向收益用）
    - valuation/financial: {code: DataFrame}，as-of 由 value/financial 因子函数内部过滤
    """
    codes = pool_codes(db)
    price_start = start - timedelta(days=PRICE_WINDOW_DAYS)
    price_end = end + timedelta(days=PRICE_TAIL_DAYS)

    price_rows = db.execute(
        select(DailyPrice.code, DailyPrice.trade_date, DailyPrice.close,
               DailyPrice.adj_factor)
        .where(DailyPrice.code.in_(codes),
               DailyPrice.trade_date >= price_start,
               DailyPrice.trade_date <= price_end)
        .order_by(DailyPrice.code, DailyPrice.trade_date)
    ).all()
    per_code: dict[str, dict] = {}
    pivot: dict[date, dict] = {}
    for code, td, close, adj in price_rows:
        if close is None or adj is None:
            continue
        v = float(close) * float(adj)
        per_code.setdefault(code, {})[td] = v
        pivot.setdefault(td, {})[code] = v
    qfq = {c: pd.Series(d).sort_index() for c, d in per_code.items()}
    adj_close = pd.DataFrame.from_dict(pivot, orient="index").sort_index()

    val_start = start - timedelta(days=30)
    val_rows = db.execute(
        select(DailyValuation.code, DailyValuation.trade_date,
               DailyValuation.pe_ttm, DailyValuation.pb)
        .where(DailyValuation.code.in_(codes),
               DailyValuation.trade_date >= val_start,
               DailyValuation.trade_date <= end)
        .order_by(DailyValuation.code, DailyValuation.trade_date)
    ).all()
    valuation: dict[str, pd.DataFrame] = {}
    for code, td, pe, pb in val_rows:
        valuation.setdefault(code, []).append(
            {"trade_date": td, "pe_ttm": pe, "pb": pb})
    valuation = {c: pd.DataFrame(rows) for c, rows in valuation.items()}

    fin_rows = db.execute(
        select(FinancialIndicator.code, FinancialIndicator.report_date,
               FinancialIndicator.announce_date, FinancialIndicator.roe,
               FinancialIndicator.debt_ratio)
        .where(FinancialIndicator.code.in_(codes),
               FinancialIndicator.announce_date <= end)
    ).all()
    financial: dict[str, pd.DataFrame] = {}
    for code, rd, ad, roe, debt in fin_rows:
        financial.setdefault(code, []).append(
            {"report_date": rd, "announce_date": ad, "roe": roe,
             "debt_ratio": debt})
    financial = {c: pd.DataFrame(rows) for c, rows in financial.items()}

    return {"codes": codes, "qfq": qfq, "adj_close": adj_close,
            "valuation": valuation, "financial": financial}


# ---------- 参数化因子值计算 ----------

def _trend_value_series(base: str, qfq: pd.Series, params: dict) -> pd.Series:
    """单只股票趋势因子全段原始值 Series（rolling/ewm 只用过去数据，天然 as-of）"""
    from app.factors.trend import ma_trend, macd_signal
    if base == "ma_trend":
        return ma_trend(qfq, short=params["short"], long=params["long"])
    return macd_signal(qfq, fast=params["fast"], slow=params["slow"],
                       signal=params["signal"])


def normalized_frame(base: str, params: dict, start: date, end: date,
                     inputs: dict) -> pd.DataFrame:
    """区间内变体因子逐日横截面归一化 → DataFrame(index=trade_date, columns=code)

    趋势因子：整段向量化预计算 per-code 原始值，逐日取值（与 as-of 等价）；
    价值/财务因子：逐日走已验证的 as-of 函数。归一化复用 normalize_cross_section
    （winsorize → 百分位 → 方向调整），与 daily 评分路径完全一致。
    """
    codes = inputs["codes"]
    direction = FACTOR_DIRECTION[base]
    dates = sorted(dt for dt in inputs["adj_close"].index if start <= dt <= end)

    if base in ("ma_trend", "macd_signal"):
        series = {c: _trend_value_series(base, inputs["qfq"][c], params)
                  for c in codes if c in inputs["qfq"]}
    else:
        series = None

    frame: dict[date, dict] = {}
    for dt in dates:
        raw: dict[str, float | None] = {}
        for code in codes:
            if series is not None:
                s = series.get(code)
                v = s.get(dt) if s is not None else None
                raw[code] = None if (v is None or pd.isna(v)) else float(v)
            else:
                inp = {"close": inputs["qfq"].get(code, pd.Series()),
                       "valuation": inputs["valuation"].get(code, pd.DataFrame()),
                       "financial": inputs["financial"].get(code, pd.DataFrame())}
                raw[code] = factor_raw_value(base, inp, dt)
        norm = normalize_cross_section(raw, direction)
        if norm:
            frame[dt] = {code: nm for code, (_, _, nm) in norm.items()}
    return pd.DataFrame.from_dict(frame, orient="index").sort_index()


# ---------- 检验统计（复用 factor_research 同口径函数） ----------

def _range_mean_ic(ic: pd.Series, start: date, end: date) -> float | None:
    """区间内 IC 均值（仅统计区间内的日期）"""
    s = ic[(ic.index >= start) & (ic.index <= end)].dropna()
    if s.empty:
        return None
    return float(s.mean())


def trial_result(db, factor: str, params: dict | None, start: date, end: date) -> dict:
    """单组试算：参数化重算 + 全量检验（结果与 FactorLab 同口径）"""
    base = resolve_base_factor(factor)
    p = parametrized_params(base, params)
    inputs = load_range_inputs(db, start, end)
    norm = normalized_frame(base, p, start, end, inputs)
    if norm.empty:
        raise ValueError("区间内因子值全为空（可能无行情数据）")

    fwd = {h: forward_returns(inputs["adj_close"], h) for h in HORIZONS}
    ic_by_h = {h: ic_series(norm, fwd[h]) for h in HORIZONS}

    # 默认检验口径 horizon=5（对齐 Go factor_stat 聚合与 FactorLab 主指标）
    ic5 = ic_by_h[QUANTILE_H]
    ic5 = ic5[(ic5.index >= start) & (ic5.index <= end)]
    mean = _range_mean_ic(ic5, start, end)
    std = float(ic5.dropna().std(ddof=0)) if len(ic5.dropna()) > 1 else None
    ir = icir(ic5)

    qret = quantile_returns_by_date(norm, fwd[QUANTILE_H], q=5)
    qret = qret[(qret.index >= start) & (qret.index <= end)]
    quantiles = [
        {"group": g, "ret": _num(qret[g].mean()) if g in qret and not qret[g].dropna().empty else None}
        for g in range(1, 6)
    ]
    wins = total = 0
    for dt in qret.index:
        if pd.notna(qret.loc[dt, 1]) and pd.notna(qret.loc[dt, 5]):
            total += 1
            if qret.loc[dt, 1] > qret.loc[dt, 5]:
                wins += 1
    monotonic = wins / total if total > 0 else None

    return {
        "factor": factor,
        "params": p,
        "dates": {"start": str(start), "end": str(end)},
        "ic_series": [{"date": str(dt), "ic": _num(v)} for dt, v in ic5.items()],
        "ic_mean": mean,
        "ic_std": std,
        "icir": ir,
        "ic_decay": [{"horizon": h, "ic": _range_mean_ic(ic_by_h[h], start, end)}
                     for h in HORIZONS],
        "quantiles": quantiles,
        "monotonic": monotonic,
    }


def optimize_result(db, factor: str, param_grid: dict, start: date, end: date) -> dict:
    """参数寻优：参数轴 × 持有期 IC 热力图（设计 §6.2）

    热力图 {param, param_values, horizons, grid}：grid[i][j] = 参数值 i × 持有期 j
    的区间 IC 均值。仅支持单计算参数轴：多个非 horizon 键中取「取值最多」的那个作轴
    （其余键取各自网格首值钉住）。取取值最多而非键序，是因为 params 经 PG JSONB
    存储后键序被字典序重排（jsonb 规范化），按「第一个键」选轴会跨库/跨前端不一致；
    用户实际变化的参数正是取值最多的那个。同长度并列时按键字典序取更前，恒确定性。
    """
    base = resolve_base_factor(factor)
    axis_keys = [k for k in param_grid if k != HORIZON_KEY]
    axis = max(axis_keys, key=lambda k: (len(param_grid[k]), k)) if axis_keys else None
    horizons = [int(h) for h in param_grid.get(HORIZON_KEY, (QUANTILE_H,))]
    if not horizons:
        horizons = [QUANTILE_H]

    if axis is None:
        # 无计算参数轴（value/quality/risk）：仅持有期维度，单行热力图
        combos = [({}, "default")]
        param_values: list = ["default"]
        axis = "base"
    else:
        vals = param_grid[axis]
        other = {k: param_grid[k][0] for k in param_grid
                 if k != axis and k != HORIZON_KEY}
        combos = [(dict(other, **{axis: v}), v) for v in vals]
        param_values = list(vals)

    inputs = load_range_inputs(db, start, end)
    fwd = {h: forward_returns(inputs["adj_close"], h) for h in set(horizons) | set(HORIZONS)}

    grid = []
    for params, _label in combos:
        p = parametrized_params(base, params)
        norm = normalized_frame(base, p, start, end, inputs)
        if norm.empty:
            raise ValueError("区间内因子值全为空（可能无行情数据）")
        row = []
        for h in horizons:
            row.append(_range_mean_ic(ic_series(norm, fwd[h]), start, end))
        grid.append(row)

    return {
        "factor": factor,
        "params": parametrized_params(base, None),
        "dates": {"start": str(start), "end": str(end)},
        "heatmap": {
            "param": axis,
            "param_values": param_values,
            "horizons": horizons,
            "grid": grid,
        },
    }


# ---------- DB 队列消费（对齐 backtest_job 模式） ----------

def _num(v) -> float | None:
    """NaN/None → None（避免 JSON 写 float('nan')）"""
    if v is None or pd.isna(v):
        return None
    return float(v)


def claim_trial(db):
    """原子领取一个 pending 任务（UPDATE ... WHERE status='pending' RETURNING）

    必须用子查询把 UPDATE 锁到单行（ORDER BY id LIMIT 1，FIFO 领取）：不带
    limit 的 update().returning() 是批量更新——会把所有 pending 一次置 running，
    RETURNING 只取回第一行，其余行被静默挂起永不消费（2.3b e2e 抓到的坑，
    backtest_service.claim_job 同款一并修复）。
    """
    sub = select(FactorTrial.id).where(FactorTrial.status == "pending") \
        .order_by(FactorTrial.id).limit(1).scalar_subquery()
    row = db.execute(
        update(FactorTrial).where(FactorTrial.id == sub)
        .values(status="running").returning(FactorTrial)
    ).first()
    if row is None:
        return None
    db.commit()
    return row[0]


def run_trial_and_save(db, trial: FactorTrial) -> None:
    """执行并落库：done(result) / failed(error)"""
    try:
        p = trial.params or {}
        start = date.fromisoformat(str(p.get("start")))
        end = date.fromisoformat(str(p.get("end")))
        if "param_grid" in p:
            result = optimize_result(db, trial.factor_name, p["param_grid"], start, end)
        else:
            result = trial_result(db, trial.factor_name, p.get("params"), start, end)
        trial.result = result
        trial.status = "done"
        trial.error = None
        trial.finished_at = datetime.now()
        logger.info("factor_trial %s done（%s，%s）", trial.id, trial.factor_name,
                    "optimize" if "param_grid" in p else "trial")
    except Exception as exc:  # noqa: BLE001 任务级兜底，单个失败不阻塞队列
        trial.status = "failed"
        trial.error = str(exc)[:500]
        trial.finished_at = datetime.now()
        logger.warning("factor_trial %s failed：%s", trial.id, exc)
    db.commit()


def consume_pending_trials() -> None:
    """领取并执行所有 pending 试算任务（每 5 分钟调用一次）"""
    db = get_session()
    try:
        while True:
            trial = claim_trial(db)
            if trial is None:
                return
            run_trial_and_save(db, trial)
    finally:
        db.close()
