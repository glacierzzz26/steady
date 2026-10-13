"""quant-engine 数据读取的**唯一切换点**（Phase 3）。

已落 `trade_calendar` + `stock_basic` + `daily_price` + `daily_valuation` + `financial_indicator`。每个函数按闸门 `datahub_read_enabled(<dataset>)` 分派：

- **闸门关**（默认）→ 读本地库，SQL 与切换前**逐字一致**（保返回类型/语义，含
  `bool(None) == False`、R6 的降序 + limit）→ **零行为变更**。
- **闸门开** → 调 datahub HTTP API；本地 `db` 不查。

可选 `DATAHUB_FALLBACK_LOCAL=1`：datahub 读失败时记 WARNING 并回退本地（仅作应急，
默认关——datahub 权威，静默回退会掩盖故障）。
"""
import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app import config
from app.datahub_client import DatahubError, get_client
from app.models.tables import DailyPrice, DailyValuation, FinancialIndicator, \
    StockBasic, TradeCalendar

logger = logging.getLogger(__name__)

_CALENDAR = "trade_calendar"
_STOCK = "stock_basic"
_PRICE = "daily_price"
_VALUATION = "daily_valuation"
_FINANCIAL = "financial_indicator"
_LATEST = "latest_trade_date"
_DAILY_PRICE_DAYS = "daily_price_days"
_MARKET_READY = "market_ready"
# 近端查询（recent_open_days）datahub 路径的起始放宽窗口（日历日）；不足则翻倍
_RECENT_WINDOW_DAYS = 45
_RECENT_WINDOW_MAX = 730


def _iso(v) -> str:
    return v.isoformat() if isinstance(v, date) else str(v)


def _to_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _opt_date(v):
    """date 或 ISO 串 → date；空 → None（stock_basic.list_date 可为空）。"""
    return _to_date(v) if v else None


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


# ---------- stock_basic ----------
#
# datahub 侧该数据集支持 params：codes/market/universe/scope（逗号 IN）、industry、
# keyword、sort/order、limit/offset。**status / list_date 无服务端过滤** —— 本侧拉取
# 小表（全量 ~5.5k 行）后在 Python 过滤，语义与本地 SQL 等价（NULL 一律排除）。

def _remote_stock(params: dict) -> list[dict]:
    return get_client().fetch(_STOCK, params)


def _stock_fallback(method: str) -> None:
    """闸门开但 datahub 读失败：未开 fallback → 抛（失败即抛）；开 → 记 WARNING 走本地。"""
    if not config.datahub_fallback_local():
        raise
    logger.warning("datahub 读 stock_basic(%s) 失败，回退本地（FALLBACK_LOCAL=1）", method)


def _listed(row: dict, td: date) -> bool:
    """行是否「td 日已上市且未退市」：status=='L' × list_date<=td（list_date 空→False）。

    对齐本地 SQL `status='L' AND list_date<=td`：NULL 在 SQL 比较中为假，故空日期排除。
    """
    if row.get("status") != "L":
        return False
    ld = row.get("list_date")
    return bool(ld) and _to_date(ld) <= td


def pool_codes(db) -> list[str]:
    """股票池（沪深300 + 中证500）代码，**升序**。"""
    if config.datahub_read_enabled(_STOCK):
        try:
            return sorted(r["code"] for r in _remote_stock({"universe": "hs300,zz500"}))
        except DatahubError:
            _stock_fallback("pool_codes")
    return sorted(db.execute(
        select(StockBasic.code).where(StockBasic.universe.in_(("hs300", "zz500")))
    ).scalars().all())


def names_by_codes(db, codes) -> dict:
    """code→name（缺失/空名不计）；codes 空 → {}。"""
    if not codes:
        return {}
    if config.datahub_read_enabled(_STOCK):
        try:
            rows = _remote_stock({"codes": ",".join(codes)})
            return {r["code"]: r["name"] for r in rows if r.get("name")}
        except DatahubError:
            _stock_fallback("names_by_codes")
    return {r.code: r.name for r in db.execute(
        select(StockBasic.code, StockBasic.name).where(StockBasic.code.in_(codes))
    ).all() if r.name}


def industries_by_codes(db, codes) -> dict:
    """code→industry（缺失/空行业不计）；codes 空 → {}。"""
    if not codes:
        return {}
    if config.datahub_read_enabled(_STOCK):
        try:
            rows = _remote_stock({"codes": ",".join(codes)})
            return {r["code"]: r["industry"] for r in rows if r.get("industry")}
        except DatahubError:
            _stock_fallback("industries_by_codes")
    return {r.code: r.industry for r in db.execute(
        select(StockBasic.code, StockBasic.industry).where(StockBasic.code.in_(codes))
    ).all() if r.industry}


def a_share_listed_codes(db, td: date) -> list[str]:
    """采集域 a_share 内「td 日已上市未退市」代码（coverage 分母，Issue #13 三重过滤）。"""
    if config.datahub_read_enabled(_STOCK):
        try:
            rows = _remote_stock({"scope": "a_share"})
            return [r["code"] for r in rows if _listed(r, td)]
        except DatahubError:
            _stock_fallback("a_share_listed_codes")
    return db.execute(
        select(StockBasic.code).where(
            StockBasic.data_scope == "a_share",
            StockBasic.status == "L",
            StockBasic.list_date <= td)
    ).scalars().all()


def a_share_listed_count(db, td: date) -> int:
    """沪深（SH/SZ）上市未退市总数 —— 分母漂移守卫（R2）的真值。"""
    if config.datahub_read_enabled(_STOCK):
        try:
            rows = _remote_stock({"market": "SH,SZ"})
            return sum(1 for r in rows if _listed(r, td))
        except DatahubError:
            _stock_fallback("a_share_listed_count")
    return db.execute(
        select(func.count()).select_from(StockBasic)
        .where(StockBasic.market.in_(("SH", "SZ")),
               StockBasic.status == "L",
               StockBasic.list_date <= td)
    ).scalar() or 0


def pool_code_dates(db) -> list:
    """策略池 (code, list_date) 列表（financial 覆盖分母，pool 档）。"""
    if config.datahub_read_enabled(_STOCK):
        try:
            rows = _remote_stock({"universe": "hs300,zz500"})
            return [(r["code"], _opt_date(r.get("list_date"))) for r in rows]
        except DatahubError:
            _stock_fallback("pool_code_dates")
    return db.execute(
        select(StockBasic.code, StockBasic.list_date)
        .where(StockBasic.universe.in_(("hs300", "zz500")))
    ).all()


def a_share_code_dates(db, end: date) -> list:
    """采集域内已上市 (code, list_date) 列表（financial 覆盖分母，a_share 档）。"""
    if config.datahub_read_enabled(_STOCK):
        try:
            rows = _remote_stock({"scope": "a_share"})
            return [(r["code"], _opt_date(r.get("list_date")))
                    for r in rows if _listed(r, end)]
        except DatahubError:
            _stock_fallback("a_share_code_dates")
    return db.execute(
        select(StockBasic.code, StockBasic.list_date).where(
            StockBasic.data_scope == "a_share",
            StockBasic.status == "L",
            StockBasic.list_date <= end)
    ).all()


# ---------- daily_price ----------
#
# datahub 侧 params：codes/start/end/date/asof/last/fields/adj_not_null。**远端返回「按 fields
# 顺序的元组」**（trade_date 转 date），调用方 `for code, td, close, adj in rows` 几乎不改。
# 派生小数据集 `latest_trade_date`（MAX，+ `before` 取「低于某日最近交易日」）/
# `daily_price_days`（区间有行情交易日 DISTINCT）/ `market_ready`（池覆盖率）同按
# daily_price 闸门分派（供 data_quality prev 推导 / 缺日判定，Phase 4）。

_PRICE_FIELDS = ("code", "trade_date", "close", "adj_factor")


def latest_trade_date(db, before=None) -> date | None:
    """最近一个已有行情数据的交易日（跳过指数伪股票 sh*）；`before` 给定时取
    `trade_date < before` 的最近一日（严格小于；供 data_quality prev 推导）。

    对齐本地 `max(trade_date) where code NOT LIKE 'sh%' [and trade_date < before]`；
    datahub `latest_trade_date` `exclude_index=true` + `before` 同口径。无行情 → None。
    """
    if config.datahub_read_enabled(_PRICE):
        try:
            params = {"table": _PRICE, "exclude_index": "true"}
            if before is not None:
                params["before"] = _iso(before)
            rows = get_client().fetch(_LATEST, params)
            return _to_date(rows[0]["trade_date"]) if rows else None
        except DatahubError:
            _read_fallback(_PRICE, "latest_trade_date")
    q = select(func.max(DailyPrice.trade_date)).where(DailyPrice.code.not_like("sh%"))
    if before is not None:
        q = q.where(DailyPrice.trade_date < before)
    return db.execute(q).scalar()


def price_days(db, start, end, exclude_index=True) -> list[date]:
    """区间 `[start, end]` 内「有行情的交易日」去重升序列表（默认排指数 sh*）。

    对齐本地 `select(distinct trade_date) where start<=trade_date<=end
    [and code NOT LIKE 'sh%'] order by trade_date`。供 data_quality._check_missing_days。
    """
    if config.datahub_read_enabled(_PRICE):
        try:
            params = {"start": _iso(start), "end": _iso(end),
                      "exclude_index": "true" if exclude_index else "false"}
            rows = get_client().fetch(_DAILY_PRICE_DAYS, params)
            return [_to_date(r["trade_date"]) for r in rows]
        except DatahubError:
            _read_fallback(_PRICE, "price_days")
    q = select(DailyPrice.trade_date).distinct()
    if start is not None:
        q = q.where(DailyPrice.trade_date >= start)
    if end is not None:
        q = q.where(DailyPrice.trade_date <= end)
    if exclude_index:
        q = q.where(DailyPrice.code.not_like("sh%"))
    q = q.order_by(DailyPrice.trade_date)
    return list(db.execute(q).scalars().all())


def price_rows(db, codes, start=None, end=None, fields=_PRICE_FIELDS) -> list:
    """行情行（`(code, trade_date, …)` 元组，列序同 `fields`；升序 code,trade_date）。

    对齐本地 `select(*fields).where(code IN codes, start<=trade_date<=end)`。
    `codes` 空 → []（对齐 `code IN ()` 恒空）。
    """
    if not codes:
        return []
    if config.datahub_read_enabled(_PRICE):
        try:
            params = {"codes": ",".join(codes)}
            if start is not None:
                params["start"] = _iso(start)
            if end is not None:
                params["end"] = _iso(end)
            return _fetch_tuples(_PRICE, params, fields)
        except DatahubError:
            _read_fallback(_PRICE, "price_rows")
    q = select(*[getattr(DailyPrice, f) for f in fields])
    q = q.where(DailyPrice.code.in_(codes))
    if start is not None:
        q = q.where(DailyPrice.trade_date >= start)
    if end is not None:
        q = q.where(DailyPrice.trade_date <= end)
    q = q.order_by(DailyPrice.code, DailyPrice.trade_date)
    return db.execute(q).all()


def price_day(db, codes, d, fields=_PRICE_FIELDS) -> list:
    """某交易日的行情行（列序同 `fields`）；`codes` 空 = 不限码（当日全市场）。

    对齐本地 `select(*fields).where(trade_date == d [, code IN codes])`。
    """
    if config.datahub_read_enabled(_PRICE):
        try:
            params = {"date": _iso(d)}
            if codes:
                params["codes"] = ",".join(codes)
            return _fetch_tuples(_PRICE, params, fields)
        except DatahubError:
            _read_fallback(_PRICE, "price_day")
    q = select(*[getattr(DailyPrice, f) for f in fields]).where(DailyPrice.trade_date == d)
    if codes:
        q = q.where(DailyPrice.code.in_(codes))
    q = q.order_by(DailyPrice.code, DailyPrice.trade_date)
    return db.execute(q).all()


def price_latest(db, code, fields=_PRICE_FIELDS):
    """该 code 最新一行行情（`(code, trade_date, …)` 元组，列序同 `fields`）；无 → None。"""
    if config.datahub_read_enabled(_PRICE):
        try:
            rows = _fetch_tuples(_PRICE, {"codes": code, "last": "1"}, fields)
            return rows[0] if rows else None
        except DatahubError:
            _read_fallback(_PRICE, "price_latest")
    q = (select(*[getattr(DailyPrice, f) for f in fields])
         .where(DailyPrice.code == code)
         .order_by(DailyPrice.trade_date.desc()).limit(1))
    return db.execute(q).first()


def market_ready(db, td) -> bool:
    """行情就绪：当日股票池（hs300+zz500）bar 覆盖率 ≥ 0.9。

    datahub `market_ready{date}` 同口径（threshold 0.9 / universe hs300,zz500 为默认）。
    """
    if config.datahub_read_enabled(_PRICE):
        try:
            rows = get_client().fetch(_MARKET_READY, {"date": _iso(td)})
            return bool(rows[0]["ready"]) if rows else False
        except DatahubError:
            _read_fallback(_PRICE, "market_ready")
    pool = pool_codes(db)
    if not pool:
        return False
    with_bar = db.execute(
        select(DailyPrice.code).where(
            DailyPrice.trade_date == td, DailyPrice.code.in_(pool))
    ).scalars().all()
    return len(with_bar) / len(pool) >= 0.9


# ---------- daily_valuation ----------
#
# datahub 侧 params：codes/start/end/asof/last/fields。**远端返回「按 fields 顺序的元组」**
# （trade_date 转 date），使调用方 `for code, td, pe, pb in rows` 几乎不改（仅换调用 + 删 import）。
# codes 空 → 返回 []（对齐本地 `code IN ()` 恒空语义）。

_VAL_FIELDS = ("code", "trade_date", "pe_ttm", "pb")


def _read_fallback(dataset: str, method: str) -> None:
    """闸门开但 datahub 读失败：未开 fallback → 抛（失败即抛）；开 → 记 WARNING 走本地。"""
    if not config.datahub_fallback_local():
        raise
    logger.warning("datahub 读 %s(%s) 失败，回退本地（FALLBACK_LOCAL=1）", dataset, method)


def _fetch_tuples(dataset: str, params: dict, fields,
                  date_fields=("trade_date",)) -> list:
    """拉 datahub 数据集 → 按 `fields` 顺序的元组（`date_fields` 列转 `date`）。"""
    rows = get_client().fetch(dataset, {**params, "fields": ",".join(fields)})
    dset = set(date_fields)
    return [tuple(_to_date(r[f]) if f in dset else r[f] for f in fields)
            for r in rows]


def valuation_rows(db, codes, start=None, end=None, fields=_VAL_FIELDS) -> list:
    """估值行（`(code, trade_date, …)` 元组，列序同 `fields`；升序 code,trade_date）。

    对齐本地 `select(*fields).where(code IN codes, start<=trade_date<=end)`。
    """
    if not codes:
        return []
    if config.datahub_read_enabled(_VALUATION):
        try:
            params = {"codes": ",".join(codes)}
            if start is not None:
                params["start"] = _iso(start)
            if end is not None:
                params["end"] = _iso(end)
            return _fetch_tuples(_VALUATION, params, fields)
        except DatahubError:
            _read_fallback(_VALUATION, "valuation_rows")
    q = select(*[getattr(DailyValuation, f) for f in fields])
    q = q.where(DailyValuation.code.in_(codes))
    if start is not None:
        q = q.where(DailyValuation.trade_date >= start)
    if end is not None:
        q = q.where(DailyValuation.trade_date <= end)
    q = q.order_by(DailyValuation.code, DailyValuation.trade_date)
    return db.execute(q).all()


def valuation_latest(db) -> date | None:
    """最新估值交易日（`date` 或 None；无过滤，对齐本地 `max(trade_date)`）。"""
    if config.datahub_read_enabled(_VALUATION):
        try:
            rows = get_client().fetch(
                _LATEST, {"table": _VALUATION, "exclude_index": "false"})
            return _to_date(rows[0]["trade_date"]) if rows else None
        except DatahubError:
            _read_fallback(_VALUATION, "valuation_latest")
    return db.execute(select(func.max(DailyValuation.trade_date))).scalar()


def dup_keys(db, table: str) -> int:
    """唯一键重复**组数**（`table ∈ daily_price/daily_valuation/financial_indicator`）。

    datahub 侧有 UNIQUE 索引 → 恒 0；本地对无唯一约束库（sqlite 测试）同样可查。
    """
    if config.datahub_read_enabled(table):
        try:
            return len(get_client().fetch("dup_keys", {"table": table}))
        except DatahubError:
            _read_fallback(table, f"dup_keys({table})")
    from app.models.tables import (DailyPrice, DailyValuation,
                                   FinancialIndicator)
    model, key_cols = {
        "daily_price": (DailyPrice, ("code", "trade_date")),
        "daily_valuation": (DailyValuation, ("code", "trade_date")),
        "financial_indicator": (FinancialIndicator, ("code", "report_date")),
    }[table]
    cols = [getattr(model, c) for c in key_cols]
    q = (select(*cols, func.count().label("c"))
         .group_by(*cols).having(func.count() > 1))
    return len(db.execute(q).all())


# ---------- financial_indicator ----------
#
# datahub 侧 params：codes/as_of(announce_date ≤，防未来函数)/last/fields。
# 远端返回「按 fields 顺序的元组」（report_date/announce_date 转 date）。announce_date 为
# **NOT NULL**（schema），故「NULLS FIRST」顾虑不适用。字段序对齐各消费点原 select。

_FIN_FIELDS = ("code", "report_date", "announce_date", "roe", "debt_ratio")


def financial_rows(db, codes, as_of=None, fields=_FIN_FIELDS) -> list:
    """财务行（`(code, report_date, announce_date, roe, debt_ratio)` 元组，列序同 `fields`）。

    对齐本地 `select(*fields).where(code IN codes, announce_date <= as_of)`。
    **消费方（factor_service/factor_trial/replay）均按 announce_date as-of 自行排序/取值，
    不依赖返回行序** —— 故远端 (code, report_date) 序与本地 (code, announce_date) 序等价。
    """
    if not codes:
        return []
    if config.datahub_read_enabled(_FINANCIAL):
        try:
            params = {"codes": ",".join(codes)}
            if as_of is not None:
                params["as_of"] = _iso(as_of)
            return _fetch_tuples(_FINANCIAL, params, fields,
                                 date_fields=("report_date", "announce_date"))
        except DatahubError:
            _read_fallback(_FINANCIAL, "financial_rows")
    q = select(*[getattr(FinancialIndicator, f) for f in fields])
    q = q.where(FinancialIndicator.code.in_(codes))
    if as_of is not None:
        q = q.where(FinancialIndicator.announce_date <= as_of)
    q = q.order_by(FinancialIndicator.code, FinancialIndicator.announce_date)
    return db.execute(q).all()


def pool_latest_announce(db, codes=None) -> dict:
    """每 code 最新公告日 `{code: date}`（`_check_financial` 新鲜度用）。

    对齐本地 `select(code, max(announce_date)).group_by(code)`（announce_date NOT NULL →
    无空值分歧）。`codes` 非空时限定该集合（仅覆盖池内 code，语义等价：调用方按池 code 取值）。
    """
    if config.datahub_read_enabled(_FINANCIAL):
        try:
            params = {"last": "1", "fields": "code,announce_date"}
            if codes:
                params["codes"] = ",".join(codes)
            rows = get_client().fetch(_FINANCIAL, params)
            return {r["code"]: _to_date(r["announce_date"]) for r in rows
                    if r.get("announce_date")}
        except DatahubError:
            _read_fallback(_FINANCIAL, "pool_latest_announce")
    q = (select(FinancialIndicator.code,
                func.max(FinancialIndicator.announce_date))
         .group_by(FinancialIndicator.code))
    if codes:
        q = q.where(FinancialIndicator.code.in_(codes))
    return dict(db.execute(q).all())
