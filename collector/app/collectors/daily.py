"""日行情采集器：不复权 + 后复权两次拉取，计算复权因子，质量校验后入库

数据源（阶段 4 起）：**AkShare 主源**——`fetch_pair` 内部链 东财 stock_zh_a_hist →
新浪 stock_zh_a_daily（hfq 派生 adj_factor）；两者均失败且 `baostock_enabled("daily")`
→ **BaoStock 兜底**：OHLCV 与 adj_factor 走 BaoStock（build_rows 由 hfq/raw 派生因子），
经除权一致性守卫（factor_guard）校验「因子阶跃必须对应价格跳空」后才入库；守卫拒收
（平安/天齐型虚假/滞后调整，见 docs/phase2/design/数据源评估-BaoStock.md §2.2）→ 抛异常
触发 base.run 重试（重跑 AkShare 主源），BaoStock 坏因子不进库。

阶段 4 加守（08-28 601155 东财假缩股教训）：**AkShare 主源同样过守卫**（window 内
判定），并在**潜在除权日**（因子单日阶跃 > 7%，即送转/拆合股日）用 BaoStock 交叉
验证因子比值恒常——东财"假缩股"（601155：step−gap 2.7pp 落在守卫 47.6% 容差内
通过守卫，但 AkShare/BaoStock 因子比值 1.0006→0.0482 断裂）当场拒收，降级 BaoStock。
合法除权日两源因子同变，比值恒常放行；BaoStock 不可用（未装/封禁冷却）时跳过交叉
验证（守卫已兜小阶跃），不误伤合法除权日。

窗口拉取 start−7 天：多取一前交易日作守卫上下文（判 window 首行的边界因子对），
产出时丢弃（trade_date < start 的行不入库）。

新浪成交量单位为股，统一转手（/100）。
"""
import logging
from datetime import date, timedelta

import akshare as ak
import pandas as pd

from app.collectors.base import BaseCollector, to_ak_date, with_timeout
from app.cleaners import clean_daily_rows
from app.cleaners.factor_guard import factor_change_pairs, guard_factor
from app.config import baostock_enabled
from app.db import upsert
from app.models.tables import DailyPrice
from app.sources import baostock

logger = logging.getLogger(__name__)


# AkShare 列名 → 内部字段
COLUMN_MAP = {
    "日期": "trade_date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
}


def sina_symbol(code: str) -> str:
    """股票代码 → 新浪带市场前缀格式（sh600519 / sz000001 / bj830001）"""
    prefix = "sh" if code.startswith("6") else "bj" if code.startswith(("8", "4", "9")) else "sz"
    return prefix + code


def normalize_sina(df: pd.DataFrame) -> pd.DataFrame:
    """新浪列名 → 东财列名；成交量 股 → 手"""
    df = df.rename(columns={
        "date": "日期", "open": "开盘", "high": "最高",
        "low": "最低", "close": "收盘", "volume": "成交量", "amount": "成交额",
    })
    if "成交量" in df.columns:
        df["成交量"] = (df["成交量"] / 100).round(0)
    return df


def fetch_pair(code: str, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """拉取不复权 + 后复权两套行情，返回 (raw, hfq)，列为东财格式

    东财失败/超时 → 降级新浪；新浪同样为空/失败 → 抛异常触发 base.run() 重试，
    避免"返回空"被静默记为成功造成行情缺口无人知晓。
    """
    try:
        raw = with_timeout(
            ak.stock_zh_a_hist, symbol=code, period="daily",
            start_date=start, end_date=end, adjust="",
        )
        hfq = with_timeout(
            ak.stock_zh_a_hist, symbol=code, period="daily",
            start_date=start, end_date=end, adjust="hfq",
        )
        return raw, hfq
    except Exception as e:
        reason = "超时" if isinstance(e, TimeoutError) else str(e)
        logger.warning("%s 东财接口失败(%s)，降级新浪源", code, reason)
        raw = with_timeout(
            ak.stock_zh_a_daily, symbol=sina_symbol(code),
            start_date=start, end_date=end, adjust="",
        )
        hfq = with_timeout(
            ak.stock_zh_a_daily, symbol=sina_symbol(code),
            start_date=start, end_date=end, adjust="hfq",
        )
        raw, hfq = normalize_sina(raw), normalize_sina(hfq)
        if raw is None or raw.empty:
            # 双源都拿不到数据：判定失败，交给上层重试，不静默记 0 条成功
            raise RuntimeError(
                f"{code} 东财与新浪均未返回数据（东财{reason}），判定失败触发重试")
    return raw, hfq


def build_rows(code: str, raw: pd.DataFrame, hfq: pd.DataFrame) -> list[dict]:
    """不复权 + 后复权合并 → 入库行（含 adj_factor 与 prev_close）"""
    if raw.empty:
        return []
    raw = raw.rename(columns=COLUMN_MAP)
    hfq_close = hfq.set_index("日期")["收盘"] if not hfq.empty else pd.Series(dtype=float)
    rows = []
    prev_close = None
    for _, r in raw.iterrows():
        raw_close = float(r["close"])
        hfq_c = hfq_close.get(r["trade_date"])
        rows.append(
            {
                "code": code,
                "trade_date": date.fromisoformat(str(r["trade_date"])),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": raw_close,
                "volume": int(r["volume"]) if pd.notna(r["volume"]) else None,
                "amount": float(r["amount"]) if pd.notna(r["amount"]) else None,
                # 转 Python float：numpy 类型无法被 psycopg2 直接绑定
                "adj_factor": (
                    round(float(hfq_c) / raw_close, 4)
                    if hfq_c and raw_close > 0 else None
                ),
                "prev_close": prev_close,
            }
        )
        prev_close = raw_close
    return rows


# 潜在除权日触发交叉验证的因子阶跃下限：>7% 视为送转/拆合股日（年度分红 0.5-2% 不触发）
_SPLIT_STEP_MIN = 0.07
# 交叉验证比值漂移上限（ak_factor/bs_factor 应跨日恒常，锚点抵消）
_CROSS_RATIO_TOL = 0.005


def cross_check_splits(code: str, rows: list[dict], window_start) -> None:
    """潜在除权日（因子阶跃>7%）BaoStock 交叉验证 → 比值断裂即抛异常。

    AkShare 主源数据过了 guard_factor 也可能混入"假缩股"（东财 601155 型：因子
    阶跃 20× 但价格只动 13×，step−gap 2.7pp 落在守卫大阶跃容差 47.6% 内放行）。
    此处对 window 内首个因子阶跃 > 7% 的日期对，取 BaoStock 同段因子，验证
    ak_factor/bs_factor 比值跨日恒常；断裂 → 抛异常 → fetch() 落 BaoStock 兜底。
    合法除权日两源因子同变、比值恒常放行；BaoStock 不可用（未装/封禁冷却/拉取失败）
    → 跳过，不误伤合法除权日（守卫已兜小阶跃异常）。
    """
    if not baostock_enabled("daily"):
        return
    pair = None
    for d1, f1, c1, d2, f2, c2 in factor_change_pairs(rows):
        if window_start is not None and d2 < window_start:
            continue
        if f1 > 0 and f2 > 0 and abs(f2 / f1 - 1) > _SPLIT_STEP_MIN:
            pair = (d1, d2)
            break
    if pair is None:
        return
    d1, d2 = pair
    try:
        sess = baostock.get_session()
        if sess is None:
            return
        bs_raw, bs_hfq = baostock.daily_pairs(sess, code, d1, d2)
    except Exception as e:
        logger.warning("%s 除权交叉验证跳过（BaoStock 不可用：%s）", code, e)
        return
    if bs_raw.empty:
        return
    bs_rows = build_rows(code, bs_raw, bs_hfq)
    bs_f = {r["trade_date"]: r["adj_factor"] for r in bs_rows if r.get("adj_factor")}
    ak_f = {r["trade_date"]: r["adj_factor"] for r in rows if r.get("adj_factor")}
    common = [d for d in (d1, d2) if d in bs_f and d in ak_f]
    if len(common) < 2:
        return  # 无法比对（某日因子缺失）
    base = ak_f[common[0]] / bs_f[common[0]]
    for d in common[1:]:
        ratio = ak_f[d] / bs_f[d]
        if abs(ratio / base - 1) > _CROSS_RATIO_TOL:
            logger.warning(
                "复权因子交叉验证拒收 %s %s→%s：AkShare 因子与 BaoStock 分歧"
                "（比值 %.4f→%.4f），疑似东财假除权", code, d1, d2, base, ratio)
            raise RuntimeError(f"{code} AkShare 复权因子与 BaoStock 分歧")


class DailyCollector(BaseCollector):
    """按股票代码拉取日K行情，经质量校验后入库"""

    def fetch(self, code: str, start_date, end_date, *args, **kwargs) -> list[dict]:
        # 阶段 4：AkShare 主源（fetch_pair 内部链：东财 → 新浪）；AkShare 数据过
        # guard_factor + 除权交叉验证，失败/拒收且源链含 BaoStock → BaoStock 兜底
        # （兜底数据同样过守卫）。链内无 BaoStock 则 raise，触发 base.run 重试，
        # 不静默成功。
        try:
            return self._fetch_akshare(code, start_date, end_date)
        except Exception as e:
            logger.warning("%s AkShare 拉取失败(%s)，尝试 BaoStock 兜底", code, e)
            if not baostock_enabled("daily"):
                raise
        return self._fetch_baostock(code, start_date, end_date)

    def _fetch_akshare(self, code: str, start_date, end_date) -> list[dict]:
        start_d = (date.fromisoformat(str(start_date))
                   if isinstance(start_date, str) else start_date)
        # 守卫上下文：多取 start−7 天（判 window 首行边界因子对），产出时丢弃
        start = to_ak_date(start_d - timedelta(days=7))
        end = to_ak_date(end_date)
        # 后复权用于计算复权因子（因子计算用前复权价，此处只需因子比例）
        raw, hfq = fetch_pair(code, start, end)
        rows = build_rows(code, raw, hfq)
        # 阶段 4 加守：AkShare 主源同样过守卫；潜在除权日再做 BaoStock 交叉验证
        if rows:
            _, ok = guard_factor(rows, window_start=start_d)
            if not ok:
                raise RuntimeError(f"{code} AkShare 复权因子守卫拒收")
            cross_check_splits(code, rows, start_d)
        rows = [r for r in rows if r["trade_date"] >= start_d]
        logger.info("%s AkShare 拉取 %s 条日行情", code, len(rows))
        return rows

    def _fetch_baostock(self, code: str, start_date, end_date) -> list[dict]:
        """BaoStock 兜底：守卫只对 BaoStock 数据跑；守卫拒收 → raise（不再二次降级）"""
        sess = baostock.get_session()
        if sess is None:
            raise RuntimeError(f"{code} BaoStock 不可用（未安装）且 AkShare 失败")
        start = (date.fromisoformat(str(start_date))
                 if isinstance(start_date, str) else start_date)
        raw, hfq = baostock.daily_pairs(sess, code,
                                        start - timedelta(days=7), end_date)
        if raw.empty:
            raise RuntimeError(f"{code} BaoStock 未返回数据")
        rows = build_rows(code, raw, hfq)
        _, ok = guard_factor(rows, window_start=start)
        if not ok:
            raise RuntimeError(f"{code} BaoStock 复权因子拒收")
        rows = [r for r in rows if r["trade_date"] >= start]
        logger.info("%s BaoStock 兜底拉取 %s 条日行情", code, len(rows))
        return rows

    def save(self, data):
        code = data[0]["code"] if data else "-"
        n = upsert_daily_rows(self.db, data)
        logger.info("%s 入库 %s 条（丢弃 %s 条）", code, n, len(data) - n)
        return n  # 实际入库条数（清洗丢弃 volume<=0 的停牌行），自愈据此计 repaired_count


def upsert_daily_rows(db, data: list[dict]) -> int:
    """清洗 + upsert 日行情（按 code 分组，兼容单只与全市场快照批量）

    供 DailyCollector.save 与 tasks 逐只同步共用。
    """
    from collections import defaultdict

    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in data:
        by_code[r["code"]].append(r)
    table_cols = set(DailyPrice.__table__.columns.keys())
    total = 0
    for code, items in by_code.items():
        # prev_close 仅供涨跌幅校验使用，入库前过滤掉
        clean = clean_daily_rows(code, items)
        clean = [{k: r[k] for k in table_cols if k in r} for r in clean]
        upsert(
            db,
            DailyPrice,
            clean,
            conflict_cols=["code", "trade_date"],
            update_cols=[
                "open", "high", "low", "close", "volume", "amount", "adj_factor",
            ],
        )
        total += len(clean)
    return total
