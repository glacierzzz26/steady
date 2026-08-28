"""采集服务配置（环境变量，覆盖默认值）"""
import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


# 请求限速（秒/只）：回填与每日同步共用，避免触发 AkShare 限速
RATE_LIMIT_SECONDS = _int("COLLECTOR_RATE_LIMIT", 3)

# 回填批次大小（只）
BACKFILL_BATCH_SIZE = _int("COLLECTOR_BATCH_SIZE", 50)

# 回填历史区间（默认近 10 年）
BACKFILL_START = _str("COLLECTOR_BACKFILL_START", "20160801")
BACKFILL_END = _str("COLLECTOR_BACKFILL_END", "")

# 财务回填报告期数（默认 20 个季度 ≈ 5 年）
BACKFILL_FINANCE_QUARTERS = _int("COLLECTOR_FINANCE_QUARTERS", 20)

# 每日增量财务同步的报告期数（默认最近 4 个季度，覆盖财报季尾部披露）
FINANCE_SYNC_QUARTERS = _int("COLLECTOR_FINANCE_QUARTERS_SYNC", 4)

# 每日增量同步的指数（上证指数 / 沪深300 / 中证500，作行情概览与收益基准；
# sz399106 深证综指 = 深市全市场成交额，G6 两市成交 = sh000001 + sz399106）
INDEX_CODES = _str("COLLECTOR_INDEX_CODES", "sh000001,sh000300,sh000905,sz399106")

# 每日增量同步：数据库无历史记录时的回退窗口（天）
DAILY_FALLBACK_DAYS = _int("COLLECTOR_DAILY_FALLBACK_DAYS", 30)

# 每日增量同步的股票间间隔（秒，回填用 RATE_LIMIT_SECONDS）
DAILY_SYNC_INTERVAL = _int("COLLECTOR_DAILY_INTERVAL", 1)

# 数据源请求超时（秒）：AkShare 底层 requests 无 timeout，遇到半开连接会永久挂起
# （曾卡死同步），这里统一兜底；超时抛异常走降级/重试，而非无限等待。
REQUEST_TIMEOUT = _int("COLLECTOR_REQUEST_TIMEOUT", 15)

# BaoStock 主源开关（阶段 3：prod 已全源翻 BaoStock，Tushare 依赖已移除）
BAOSTOCK_ENABLED = os.getenv("BAOSTOCK_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
# BaoStock 生效的数据源 scope（阶段 3 源级门控，逗号列表）。prod 已全源翻转
# （daily,calendar,index,valuation,finance,stock_basic）；默认 daily,calendar 供
# 无 env 的本地/测试路径，代码上线本身不改变生产数据路径。
BAOSTOCK_SOURCES = [
    s.strip() for s in os.getenv("BAOSTOCK_SOURCES", "daily,calendar").split(",")
    if s.strip()
]
# BaoStock 单次 socket 超时（秒）：登录/查询共用，防库内阻塞 connect/recv 挂死
BAOSTOCK_TIMEOUT = _int("BAOSTOCK_TIMEOUT", 60)
# 连接级失败的重试次数与间隔（秒）
BAOSTOCK_RETRIES = _int("BAOSTOCK_RETRIES", 1)
BAOSTOCK_RETRY_DELAY = _int("BAOSTOCK_RETRY_DELAY", 2)


def baostock_enabled(scope: str | None = None) -> bool:
    """BaoStock 是否作为指定 scope 的主源（env BAOSTOCK_ENABLED × BAOSTOCK_SOURCES 控制）

    scope 取值 daily/calendar/valuation/finance/index/stock_basic；无参保留
    阶段 1 全局语义（任一 scope 生效即 True）。
    """
    if not BAOSTOCK_ENABLED:
        return False
    if scope is None:
        return bool(BAOSTOCK_SOURCES)
    return scope in BAOSTOCK_SOURCES

# 热点采集（早盘简报数据源，Issue #4）：每日早晨采集一次
HOTSPOT_TOP_N = _int("COLLECTOR_HOTSPOT_TOP_N", 10)          # 板块/人气榜取 TOP N
HOTSPOT_INDICES = _str("COLLECTOR_HOTSPOT_INDICES", ".DJI,.IXIC,.INX")  # 隔夜外盘代码


def index_code_list() -> list[str]:
    return [c.strip() for c in INDEX_CODES.split(",") if c.strip()]


def hotspot_index_list() -> list[str]:
    return [c.strip() for c in HOTSPOT_INDICES.split(",") if c.strip()]
