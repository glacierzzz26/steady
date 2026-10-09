"""quant-engine 数据读取的**唯一切换点**（Phase 3）。

本增量只落 `trade_calendar`。每个函数按闸门 `datahub_read_enabled("calendar")` 分派：

- **闸门关**（默认）→ 读本地库，SQL 与切换前**逐字一致**（保返回类型/语义，含
  `bool(None) == False`、R6 的降序 + limit）→ **零行为变更**。
- **闸门开** → 调 datahub HTTP API；本地 `db` 不查。

可选 `DATAHUB_FALLBACK_LOCAL=1`：datahub 读失败时记 WARNING 并回退本地（仅作应急，
默认关——datahub 权威，静默回退会掩盖故障）。
"""
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app import config
from app.datahub_client import DatahubError, get_client
from app.models.tables import TradeCalendar

logger = logging.getLogger(__name__)

_CALENDAR = "trade_calendar"
# 近端查询（recent_open_days）datahub 路径的起始放宽窗口（日历日）；不足则翻倍
_RECENT_WINDOW_DAYS = 45
_RECENT_WINDOW_MAX = 730


def _iso(v) -> str:
    return v.isoformat() if isinstance(v, date) else str(v)


def _to_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _remote_cal_dates(start, end) -> list[date]:
    rows = get_client().fetch(_CALENDAR, {
        "start": _iso(start), "end": _iso(end), "is_open": "true"})
    return sorted(_to_date(r["cal_date"]) for r in rows)


def cal_dates(start, end, db=None) -> list[date]:
    """[start, end] 区间内的交易日（**升序**）。start/end 可为 date 或 ISO 字符串。"""
    if config.datahub_read_enabled(_CALENDAR):
        try:
            return _remote_cal_dates(start, end)
        except DatahubError:
            if not config.datahub_fallback_local():
                raise
            logger.warning("datahub 读 trade_calendar 失败，回退本地（FALLBACK_LOCAL=1）")
    rows = db.execute(
        select(TradeCalendar.cal_date)
        .where(TradeCalendar.cal_date >= start,
               TradeCalendar.cal_date <= end,
               TradeCalendar.is_open.is_(True))
        .order_by(TradeCalendar.cal_date)
    ).scalars().all()
    return [d for d in rows]


def is_open(d, db=None) -> bool:
    """`d` 是否交易日；无记录视为 False（语义同切换前）。"""
    if config.datahub_read_enabled(_CALENDAR):
        try:
            return bool(_remote_cal_dates(d, d))
        except DatahubError:
            if not config.datahub_fallback_local():
                raise
            logger.warning("datahub 读 trade_calendar 失败，回退本地（FALLBACK_LOCAL=1）")
    return bool(db.execute(
        select(TradeCalendar.is_open).where(TradeCalendar.cal_date == d)
    ).scalar())


def recent_open_days(end, limit, db=None) -> list[date]:
    """`<= end` 的最近 `limit` 个交易日（**降序**，保 data_quality R6 语义）。

    datahub 无服务端 limit：按放宽窗口拉取后取末 `limit`。`end` 可为 date 或字符串。
    """
    if config.datahub_read_enabled(_CALENDAR):
        try:
            end_d = _to_date(end)
            window = _RECENT_WINDOW_DAYS
            while True:
                rows = _remote_cal_dates(end_d - timedelta(days=window), end_d)
                if len(rows) >= limit or window >= _RECENT_WINDOW_MAX:
                    return sorted(rows, reverse=True)[:limit]
                window *= 2
        except DatahubError:
            if not config.datahub_fallback_local():
                raise
            logger.warning("datahub 读 trade_calendar 失败，回退本地（FALLBACK_LOCAL=1）")
    rows = db.execute(
        select(TradeCalendar.cal_date)
        .where(TradeCalendar.is_open.is_(True), TradeCalendar.cal_date <= end)
        .order_by(TradeCalendar.cal_date.desc()).limit(limit)
    ).scalars().all()
    return [d for d in rows]
