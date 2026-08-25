"""因子检验统计（2.3 因子研究闭环 G9 核心）：纯 pandas 单实现 + 预计算写库

设计定稿《因子研究闭环》§4.1/§4.2：IC 等统计数学全部收在本模块（Python/pandas），
backend Go 只做「读 factor_stat / factor_corr 表 + 轻聚合（mean/std/比值/矩阵平均）」，
避免两套 IC 实现漂移导致 FactorLab 与试算数字不一致。

口径（研究地基，防未来函数）：
- Rank IC = 横截面 Spearman(因子归一值@T, 前向收益@T→T+H)。用 factor_value.normalized
  （已按方向统一为「越高越好」），故正 IC = 因子方向正确。
- 前向收益 = adj_close[T+H] / adj_close[T] - 1；adj_close = close × adj_factor
  （锚点因子在比值中抵消，与 factor_service 前复权口径等价）。
- 因子值 T 日已知（as-of 窗口计算）、收益严格在 T 之后 → 无 look-ahead。
- ICIR 定义：mean(IC)/std(IC)（总体标准差 ddof=0；年化 ×sqrt(252/H) 为前端展示口径，
  数字落地前注明）。Go 聚合时必须与本定义一致。

预计算写库：factor_stat 按 (因子, 交易日) 幂等 upsert；factor_corr 按 trade_date upsert。
"""
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from app.db import upsert
from app.factor_service import FACTOR_DIRECTION, pool_codes
from app.models.tables import DailyPrice, FactorCorr, FactorStat, FactorValue

logger = logging.getLogger(__name__)

HORIZONS = (1, 5, 10, 20, 60)   # IC 衰减固定档位（对齐前端 1~60 日衰减曲线）
QUANTILE_H = 5                  # 分层收益前向窗口（对齐设计 §4.1）
QUANTILES = 5                   # 分层组数
CORR_FACTORS = list(FACTOR_DIRECTION.keys())  # 相关性矩阵固定因子序（6 因子）
PRICE_TAIL_DAYS = 120           # 前向收益回看缓冲：60 交易日 ≈ 85 自然日 + 余量


# ---------- 纯 pandas 统计函数（单测用合成数据断言已知值） ----------

def adj_close_frame(rows) -> pd.DataFrame:
    """行情行 → 复权收盘 pivot：DataFrame(index=trade_date, columns=code)

    rows: (code, trade_date, close, adj_factor) 可迭代；close/adj_factor 缺失跳过。
    """
    data: dict[date, dict[str, float]] = {}
    for code, td, close, adj in rows:
        if close is None or adj is None:
            continue
        data.setdefault(td, {})[code] = float(close) * float(adj)
    return pd.DataFrame.from_dict(data, orient="index").sort_index()


def forward_returns(adj_close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """T→T+H 前向收益（复权收盘比 -1）。尾部无足够前向数据的行自动为 NaN。"""
    return adj_close.shift(-horizon) / adj_close - 1.0


def ic_series(normalized: pd.DataFrame, fwd_ret: pd.DataFrame) -> pd.Series:
    """逐交易日横截面 Rank IC → Series(index=trade_date)

    每日期：corr(normalized@T, rank(前向收益@T))，Pearson-on-ranks = Spearman。
    normalized 已方向统一，故正 IC = 因子方向正确；有效样本 <2 或全并列的日期为 NaN。
    """
    fwd_rank = fwd_ret.rank(axis=1, method="average")
    out: dict[date, float] = {}
    for dt in normalized.index:
        df = pd.concat([normalized.loc[dt], fwd_rank.loc[dt]], axis=1).dropna()
        if len(df) < 2:
            continue
        v = df.iloc[:, 0].corr(df.iloc[:, 1])
        if pd.notna(v):
            out[dt] = float(v)
    return pd.Series(out, dtype=float)


def icir(ic: pd.Series, min_periods: int = 5) -> float | None:
    """ICIR = mean(IC)/std(IC)（总体标准差，非年化；年化 ×sqrt(252/H) 是展示口径）。

    非 NaN 样本 < min_periods 或 std=0 → None。
    """
    s = ic.dropna()
    if len(s) < min_periods:
        return None
    std = s.std(ddof=0)
    if std == 0 or pd.isna(std):
        return None
    return float(s.mean() / std)


def quantile_groups(s: pd.Series, q: int = QUANTILES) -> pd.Series:
    """横截面分 q 组（1=因子最优组，q=最差组）。

    基于秩位置均分：rank(method='first') 得唯一秩 1..N，按位置均匀分 q 桶，
    恒出满组（N≥q）、对因子值并列鲁棒。
    修正前实现 rank(pct=True, method='average') × q 截断：顶部有并列时并列股
    pct<1.0、乘 q 截断后全部落入次优桶 → Q1（最优组）恒空、factor_stat.q1 全 NULL
    （生产 484 天真实数据暴露，2026-08-26 修复）。
    """
    r = s.rank(method="first")              # 1..N，唯一秩
    n = len(r)
    bucket = (((r - 1) * q) // n).astype(int) + 1   # 位置桶 1..q（1=最低）
    return q - bucket + 1                             # 翻转：1=最优


def quantile_returns_by_date(normalized: pd.DataFrame, fwd_ret: pd.DataFrame,
                             q: int = QUANTILES) -> pd.DataFrame:
    """每交易日每分组组均前向收益 → DataFrame(index=trade_date, columns=1..q)

    Q1=因子最优组（其组均前向收益应最高）。样本不足 q 只的日期整行 NaN（不参与均值）。
    """
    cols = normalized.columns.intersection(fwd_ret.columns)
    norm, fwd = normalized[cols], fwd_ret[cols]
    out: dict[date, pd.Series] = {}
    for dt in norm.index:
        df = pd.concat([norm.loc[dt], fwd.loc[dt]], axis=1).dropna()
        if len(df) < q:
            out[dt] = pd.Series(float("nan"), index=range(1, q + 1))
            continue
        bucket = quantile_groups(df.iloc[:, 0], q)
        out[dt] = df.iloc[:, 1].groupby(bucket).mean().reindex(range(1, q + 1))
    return pd.DataFrame(out).T


def _row_frame(per_factor: dict[str, pd.DataFrame], dt: date,
               factors: list[str]) -> pd.DataFrame:
    """单日横截面 DataFrame（列 = factors）：缺因子 → 全 NaN 列，不 KeyError。

    保证相关性矩阵恒为 factors 全列（precompute 用 6 因子规范序 → 恒 6×6），
    缺失因子由 Go 按 NaN 跳过，位置序与规范序一一对应。
    """
    cols: dict[str, pd.Series] = {}
    for f in factors:
        pf = per_factor.get(f)
        cols[f] = pf.loc[dt] if (pf is not None and dt in pf.index) \
            else pd.Series(float("nan"), index=pd.Index([], dtype=object))
    return pd.DataFrame(cols).dropna(how="all")


def per_date_corr_matrix(per_factor: dict[str, pd.DataFrame], dt: date,
                         factors: list[str]) -> pd.DataFrame | None:
    """单日 6 因子 Spearman 相关矩阵（行/列序 = factors）。

    某因子当日全缺失 → 该行/列 NaN（Go 平均时按 NaN 跳过）；有效样本 <2 返回 None。
    """
    df = _row_frame(per_factor, dt, factors)
    if len(df) < 2 or df.shape[1] < 2:
        return None
    c = df.corr(method="spearman")
    return c.reindex(index=factors, columns=factors)


def factor_correlation(per_factor: dict[str, pd.DataFrame],
                       factors: list[str] | None = None) -> pd.DataFrame:
    """6 因子两两 Spearman 相关（对横截面归一值），跨日平均。

    per_factor: {factor: DataFrame(index=date, columns=code)}（normalized）。
    返回 (n×n) 平均相关矩阵；某对因子无任何共现日期 → NaN。
    """
    factors = factors or CORR_FACTORS
    dates = sorted(set().union(*(pf.index for pf in per_factor.values())))
    acc = pd.DataFrame(0.0, index=factors, columns=factors)
    cnt = pd.DataFrame(0, index=factors, columns=factors)
    for dt in dates:
        m = per_date_corr_matrix(per_factor, dt, factors)
        if m is None:
            continue
        for i in factors:
            for j in factors:
                v = m.loc[i, j]
                if pd.notna(v):
                    acc.loc[i, j] += v
                    cnt.loc[i, j] += 1
    return acc / cnt.replace(0, pd.NA)


# ---------- 数据库读写 ----------

def _num(v) -> float | None:
    """NaN/None → None（psycopg2 会把 float('nan') 写成 PG 'NaN'，数值列应存 NULL）"""
    if v is None or pd.isna(v):
        return None
    return float(v)


def _normalized_pivots(db, factors: list[str]) -> dict[str, pd.DataFrame]:
    """factor_value.normalized → 每因子 DataFrame(index=date, columns=code)"""
    rows = db.execute(
        select(FactorValue.code, FactorValue.factor_name, FactorValue.trade_date,
               FactorValue.normalized)
        .where(FactorValue.factor_name.in_(factors))
        .order_by(FactorValue.trade_date, FactorValue.code)
    ).all()
    pivots: dict[str, dict] = {f: {} for f in factors}
    for code, fname, td, norm in rows:
        if norm is None:
            continue
        pivots[fname].setdefault(td, {})[code] = float(norm)
    return {f: pd.DataFrame.from_dict(pivots[f], orient="index").sort_index()
            for f in factors}


def precompute_factor_stat(db, factors: list[str] | None = None,
                           start: date | None = None, end: date | None = None,
                           horizons: tuple[int, ...] = HORIZONS,
                           quantile_h: int = QUANTILE_H) -> dict:
    """预计算区间内每因子每日 IC/分层 → factor_stat；6 因子相关性 → factor_corr。

    幂等：factor_stat 按 (factor_name, trade_date) upsert、factor_corr 按 trade_date upsert。
    区间默认 factor_value 全历史；IC 尾部因前向收益未走完而为 NULL（正常，数据补齐后
    重跑即填）。返回 {factor: 写入行数, dates: 区间, corr_dates: 矩阵日期数}。
    """
    factors = factors or list(FACTOR_DIRECTION)
    min_d, max_d = db.execute(
        select(func.min(FactorValue.trade_date), func.max(FactorValue.trade_date))
        .where(FactorValue.factor_name.in_(factors))
    ).one()
    if min_d is None:
        return {"skipped": "factor_value 无数据"}
    start, end = start or min_d, end or max_d

    pivots = {f: df[(df.index >= start) & (df.index <= end)]
              for f, df in _normalized_pivots(db, factors).items()}
    # 区间内无数据的因子丢弃（避免空 pivot 在相关性矩阵处 KeyError），factor 集合跟随
    pivots = {f: df for f, df in pivots.items() if not df.empty}
    factors = list(pivots.keys())

    # 行情：前向收益需要 [end, end+tail] 的复权收盘；start 之前不需要（IC 无 warmup）
    codes = pool_codes(db)
    price_rows = db.execute(
        select(DailyPrice.code, DailyPrice.trade_date, DailyPrice.close,
               DailyPrice.adj_factor)
        .where(DailyPrice.code.in_(codes),
               DailyPrice.trade_date >= start,
               DailyPrice.trade_date <= end + timedelta(days=PRICE_TAIL_DAYS))
    ).all()
    adj_close = adj_close_frame(price_rows)
    fwd = {h: forward_returns(adj_close, h) for h in horizons}

    factor_count = {}
    for factor in factors:
        norm = pivots[factor]
        ic_by_h = {h: ic_series(norm, fwd[h]) for h in horizons}
        qret = quantile_returns_by_date(norm, fwd[quantile_h], q=QUANTILES)
        rows = []
        for dt in norm.index:
            row = {"factor_name": factor, "trade_date": dt}
            for h in horizons:
                row[f"ic_{h}d"] = _num(ic_by_h[h].get(dt))
            for g in range(1, QUANTILES + 1):
                row[f"q{g}"] = _num(qret.loc[dt, g] if dt in qret.index else None)
            rows.append(row)
        upsert(db, FactorStat, rows,
               conflict_cols=["factor_name", "trade_date"],
               update_cols=["ic_1d", "ic_5d", "ic_10d", "ic_20d", "ic_60d",
                            "q1", "q2", "q3", "q4", "q5"])
        factor_count[factor] = len(rows)
    logger.info("factor_stat 预计算完成：%s（区间 %s ~ %s）", factor_count, start, end)

    # factor_corr：per-date 6×6 矩阵（Go 读区间做矩阵平均）。恒用 6 因子规范序，
    # 缺数据的因子列为 NaN（NULL），保证矩阵恒 6×6 与规范序一一对应。
    corr_factors = CORR_FACTORS
    corr_dates = sorted(set().union(*(pf.index for pf in pivots.values())))
    corr_rows = []
    for dt in corr_dates:
        m = per_date_corr_matrix(pivots, dt, corr_factors)
        if m is None:
            continue
        # matrix 直接给 Python 列表（None→JSON null），由 SQLAlchemy JSON 列序列化一次。
        # 若此处 json.dumps 成字符串再入 JSONB 列会双重编码：DB 存成 JSON 字符串
        # （外层带引号），Go json.Unmarshal 到 [][]*float64 直接失败（e2e 抓到的 bug）。
        matrix = [[_num(m.loc[i, j]) for j in corr_factors] for i in corr_factors]
        corr_rows.append({"trade_date": dt, "matrix": matrix})
    if corr_rows:
        upsert(db, FactorCorr, corr_rows, conflict_cols=["trade_date"],
               update_cols=["matrix"])
    logger.info("factor_corr 预计算完成：%s 个交易日矩阵", len(corr_rows))

    return {"factors": factor_count, "dates": (str(start), str(end)),
            "corr_dates": len(corr_rows)}
