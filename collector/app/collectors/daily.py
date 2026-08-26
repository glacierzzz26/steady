"""日行情采集器：不复权 + 后复权两次拉取，计算复权因子，质量校验后入库

数据源（BAOSTOCK_ENABLED=1 时）：**混合模式**——OHLCV 走 BaoStock（全池逐位一致），
adj_factor 仍走 Tushare（BaoStock 派生因子 51% 有分段阶跃、平安/天齐为已实证缺陷，
见 docs/phase2/design/数据源评估-BaoStock.md §2.2，不能作因子唯一来源）；
失败降级 Tushare → 东财（stock_zh_a_hist）→ 新浪（stock_zh_a_daily）。
新浪成交量单位为股，统一转手（/100）。

因子拉取策略（2026-08-27 修正）：adj_factor 免费档限频苛刻（prod 5次/天、
dev 1次/小时），**逐股 factor_map() 在全市场同步第一只就打爆配额**，原混合路径
实际永远降级 AkShare。改为**按交易日批量**：`adj_factor(trade_date=) 一次覆盖
全市场`，按窗口内缺失日期逐日补模块级缓存（1 天 1 次调用），同一次同步后续股票
全部命中缓存。
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import akshare as ak
import pandas as pd
import requests

from app.collectors.base import BaseCollector, to_ak_date
from app.cleaners import clean_daily_rows
from app.config import REQUEST_TIMEOUT, baostock_enabled
from app.db import upsert
from app.models.tables import DailyPrice
from app.sources import baostock, tushare

logger = logging.getLogger(__name__)

# Tushare 因子按交易日批量缓存（限频 5次/天 → 1 天 1 次调用全市场，绝不逐股）。
# key = "YYYY-MM-DD" → {ts_code: float}；历史因子不可变，缓存可跨进程长期复用，
# 仅用大小上限兜底内存。
_FACTOR_CACHE: dict[str, dict[str, float]] = {}
_FACTOR_LOCK = threading.Lock()
_MAX_FACTOR_CACHE_DATES = 30


def _fill_factor_cache(pro, dates) -> None:
    """为窗口内缺失的交易日批量补因子缓存（每日期 1 次 adj_factor 调用）

    一次每日同步的窗口通常只有 1 个新交易日 → 全市场仅 1 次调用，其余股票全命中
    缓存；限频命中/数据未就绪抛异常时由调用方整段降级 Tushare → AkShare。
    """
    with _FACTOR_LOCK:
        need = [d for d in dates if d not in _FACTOR_CACHE]
        for i, d in enumerate(need):
            fac = tushare.factor_map_by_date(pro, d)
            if fac:  # 空结果（Tushare 未就绪）不缓存，避免被永久记住而不再重试
                _FACTOR_CACHE[d] = fac
            if i < len(need) - 1:
                # prod 实测 adj_factor 限频 1次/分钟：多日期窗口必须错开，
                # 否则首个多日窗口在 1 分钟内连发 N 次调用直接打爆配额
                time.sleep(61)
        if len(_FACTOR_CACHE) > _MAX_FACTOR_CACHE_DATES:
            # dict 保插入序：淘汰最旧日期，只留最近 _MAX_FACTOR_CACHE_DATES 个
            for d in list(_FACTOR_CACHE)[:-_MAX_FACTOR_CACHE_DATES]:
                _FACTOR_CACHE.pop(d, None)


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


def _with_timeout(fn, *args, timeout=None, **kwargs):
    """在线程内执行 AkShare 请求并施加超时。

    AkShare 底层 requests 未设置 timeout，对端半开连接时会永久挂起
    （曾因此卡死整个同步）。此包装器兜底：超时抛 TimeoutError，
    由调用方按"降级/重试"处理，而不是无限等待。
    """
    if timeout is None:
        timeout = REQUEST_TIMEOUT
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return ex.submit(fn, *args, **kwargs).result(timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"请求超时（>{timeout}s）")


def fetch_pair(code: str, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """拉取不复权 + 后复权两套行情，返回 (raw, hfq)，列为东财格式

    东财失败/超时 → 降级新浪；新浪同样为空/失败 → 抛异常触发 base.run() 重试，
    避免"返回空"被静默记为成功造成行情缺口无人知晓。
    """
    try:
        raw = _with_timeout(
            ak.stock_zh_a_hist, symbol=code, period="daily",
            start_date=start, end_date=end, adjust="",
        )
        hfq = _with_timeout(
            ak.stock_zh_a_hist, symbol=code, period="daily",
            start_date=start, end_date=end, adjust="hfq",
        )
        return raw, hfq
    except Exception as e:
        reason = "超时" if isinstance(e, TimeoutError) else str(e)
        logger.warning("%s 东财接口失败(%s)，降级新浪源", code, reason)
        raw = _with_timeout(
            ak.stock_zh_a_daily, symbol=sina_symbol(code),
            start_date=start, end_date=end, adjust="",
        )
        hfq = _with_timeout(
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


class DailyCollector(BaseCollector):
    """按股票代码拉取日K行情，经质量校验后入库"""

    def fetch(self, code: str, start_date, end_date, *args, **kwargs) -> list[dict]:
        # BaoStock 混合主源（阶段 1 开关）：OHLCV 走 BaoStock，adj_factor 走 Tushare。
        # BaoStock 派生因子全池 51% 有分段阶跃（平安虚假调整/天齐配股滞后为实证缺陷），
        # 不能作因子唯一来源（§2.2 复核结论）；因子缺失时整段降级 Tushare 保住连续性。
        # 因子按交易日批量拉取（_fill_factor_cache）：限频 5次/天 → 1 天 1 次全市场。
        if baostock_enabled():
            sess = baostock.get_session()
            if sess is not None:
                try:
                    raw, hfq = baostock.daily_pairs(sess, code, start_date, end_date)
                    if raw.empty:
                        raise RuntimeError(f"{code} BaoStock 未返回数据")
                    pro = tushare.make_pro(self.db)
                    if pro is None:
                        raise RuntimeError(f"{code} 混合模式缺 Tushare 因子源")
                    rows = build_rows(code, raw, hfq)
                    dates = sorted({str(r["trade_date"]) for r in rows})
                    _fill_factor_cache(pro, dates)
                    ts_code = tushare.ts_code(code)
                    for r in rows:
                        f = _FACTOR_CACHE.get(str(r["trade_date"]), {}).get(ts_code)
                        if f is not None:
                            r["adj_factor"] = round(f, 4)
                        elif r["volume"]:
                            # 有成交却缺因子：绝不让 BaoStock 派生因子混入（§2.2），
                            # 整段降级 Tushare 保因子连续性
                            raise RuntimeError(
                                f"{code} {r['trade_date']} 缺 Tushare 因子")
                    logger.info("%s BaoStock OHLCV + Tushare 因子 %s 条", code, len(rows))
                    return rows
                except Exception as e:
                    logger.warning("%s BaoStock 混合拉取失败(%s)，降级 Tushare", code, e)
        # Tushare 主源：daily + adj_factor 一次拉取，失败/空数据降级 AkShare
        pro = tushare.make_pro(self.db)
        if pro is not None:
            try:
                raw, hfq = tushare.daily_pairs(pro, code, start_date, end_date)
                if raw.empty:
                    raise RuntimeError(f"{code} Tushare 未返回数据")
                rows = build_rows(code, raw, hfq)
                logger.info("%s Tushare 拉取 %s 条日行情", code, len(rows))
                return rows
            except Exception as e:
                logger.warning("%s Tushare 失败(%s)，降级 AkShare", code, e)
        start = to_ak_date(start_date)
        end = to_ak_date(end_date)
        # 后复权用于计算复权因子（因子计算用前复权价，此处只需因子比例）
        raw, hfq = fetch_pair(code, start, end)
        rows = build_rows(code, raw, hfq)
        logger.info("%s AkShare 拉取 %s 条日行情", code, len(rows))
        return rows

    def save(self, data):
        code = data[0]["code"] if data else "-"
        n = upsert_daily_rows(self.db, data)
        logger.info("%s 入库 %s 条（丢弃 %s 条）", code, n, len(data) - n)
        return True


def upsert_daily_rows(db, data: list[dict]) -> int:
    """清洗 + upsert 日行情（按 code 分组，兼容单只与全市场快照批量）

    供 DailyCollector.save 与 tasks 的 Tushare 全市场快照共用。
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
