"""请求层超时补丁（Issue #14 根因修复）。

**为什么需要**：AkShare 底层 requests 调用**没有有效超时**——
- 东财 `stock_zh_a_hist` 签名 `timeout: float = None` 原样转发 → `requests.get(..., timeout=None)` 无限等；
- 新浪腿 `stock_zh_a_daily`（**09-08 实际卡死的那条腿**）是裸 `requests.get(url)`，连 timeout 参数都没有。

半开连接（对端 accept 后不回包）时 `recv` 永不返回 → 采集永久挂起。09-08 事故即此：
18:10 同步卡死 24h+，09-09 被 APScheduler `skipped`，行情静默断档两日。

**为什么不用 `socket.setdefaulttimeout`**：urllib3 在 `send()` 里显式
`sock.settimeout(self.timeout)`，而 requests 传来的 timeout 是 `None` → 覆盖掉进程默认值。
实测：`setdefaulttimeout(1.0)` 下 `requests.get(timeout=None)` 打黑洞服务 20s 仍未超时。
（故 `sources/baostock.py` 的 `_SocketTimeout` 范式**不可照搬**——baostock 自建 socket 才吃默认值。）

**本模块的修法**：给 `requests.Session.request` 打一次补丁，把 `None`/缺省的 timeout
换成真实 `(connect, read)` 元组。补在 `Session.request` 单一口子即覆盖三种形态
（`requests.get(url)` / `requests.get(url, timeout=None)` / `Session().get(url)`），
实测三者均在配置秒数内抛 `ReadTimeout`。

**只在进程入口安装**（tasks/cli/backfill 的 `__main__`），**不在 import 期装**：
测试会 `import app.tasks` / `app.collectors.daily`，import 期安装会污染整个测试集。
"""

import logging
import os
import threading

import requests

logger = logging.getLogger(__name__)

_DEFAULTS = {"connect": 5.0, "read": 15.0}
_ENV = {"connect": "COLLECTOR_HTTP_CONNECT_TIMEOUT",
        "read": "COLLECTOR_HTTP_READ_TIMEOUT"}

_lock = threading.Lock()
_installed: tuple[float, float] | None = None
# 首次安装时捕获的原始函数（身份还原用，仅测试调用 uninstall 时用）
_original_request = None
# 故障注入：已注入过的进程标记（FAULT_INJECT_ONCE 语义）
_injected = False


def _resolve(connect, read) -> tuple[float, float]:
    """连接/读超时：显式参数 > 环境变量 > 默认值（install 时读，非 import 时）"""
    def pick(given, key):
        if given is not None:
            return float(given)
        raw = os.getenv(_ENV[key])
        if raw is None or raw.strip() == "":
            return _DEFAULTS[key]
        try:
            return float(raw)
        except ValueError:
            logger.warning("环境变量 %s=%r 非法，回退默认 %s", _ENV[key], raw, _DEFAULTS[key])
            return _DEFAULTS[key]

    return pick(connect, "connect"), pick(read, "read")


def install_http_timeouts(connect: float | None = None,
                          read: float | None = None,
                          enabled: bool | None = None) -> tuple[float, float] | None:
    """安装请求层超时补丁（幂等）。返回生效的 (connect, read)；被旁路时返回 None。

    补丁规则（**只替换 None**）：
      - `None`                → (connect, read)
      - `(None, x)` / `(x, None)` → 补齐空位
      - `float` / `int`       → **原样保留**（显式 timeout 已受限，改成元组只会改变
                                异常语义而无收益）
      - 完整元组              → 不动

    `enabled=False` 或 env `COLLECTOR_HTTP_TIMEOUT_PATCH=0` → 硬旁路（当日回滚用）。
    """
    global _installed, _original_request

    if enabled is None:
        enabled = os.getenv("COLLECTOR_HTTP_TIMEOUT_PATCH",
                            "1").strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        logger.info("请求层超时补丁已旁路（COLLECTOR_HTTP_TIMEOUT_PATCH=0）")
        return None

    with _lock:
        if _installed is not None:
            logger.debug("请求层超时补丁已安装，跳过：%s", _installed)
            return _installed

        connect_s, read_s = _resolve(connect, read)

        if not hasattr(requests.Session, "request"):
            # 打不上补丁就大声失败，别让进程静默裸奔
            raise RuntimeError("requests.Session.request 不存在，请求层超时补丁无法安装")

        _original_request = requests.Session.request

        def _patched_request(self, method, url, **kwargs):
            t = kwargs.get("timeout")
            if t is None:
                kwargs["timeout"] = (connect_s, read_s)
            elif isinstance(t, tuple) and len(t) == 2 and (t[0] is None or t[1] is None):
                kwargs["timeout"] = (t[0] if t[0] is not None else connect_s,
                                     t[1] if t[1] is not None else read_s)
            if _should_inject(url):
                raise requests.exceptions.ReadTimeout(
                    f"故障注入（COLLECTOR_FAULT_INJECT_TIMEOUT_HOSTS 命中 {url}）")
            return _original_request(self, method, url, **kwargs)

        requests.Session.request = _patched_request
        _installed = (connect_s, read_s)
        logger.info(
            "请求层超时补丁已安装：connect=%ss read=%ss（requests %s / akshare %s）",
            connect_s, read_s, requests.__version__, _akshare_version())
        return _installed


def _akshare_version() -> str:
    """记录 akshare 版本——补丁依赖其内部用 requests（5 个模块已改用 curl_cffi，不受覆盖）"""
    try:
        from importlib.metadata import version
        return version("akshare")
    except Exception:
        return "未知"


def _should_inject(url: str) -> bool:
    """故障注入判定：URL 命中子串则注入一次 ReadTimeout（COLLECTOR_FAULT_INJECT_ONCE 时）"""
    global _injected
    hosts = [h for h in os.getenv("COLLECTOR_FAULT_INJECT_TIMEOUT_HOSTS", "").split(",") if h.strip()]
    if not hosts:
        return False
    once = os.getenv("COLLECTOR_FAULT_INJECT_ONCE", "").strip().lower() in ("1", "true", "yes", "on")
    if once and _injected:
        return False
    if any(h.strip() in str(url) for h in hosts):
        if once:
            _injected = True
        logger.warning("故障注入：主动对 %s 抛 ReadTimeout", url)
        return True
    return False


def uninstall_http_timeouts() -> None:
    """还原原始 requests.Session.request（**仅测试用**，保证用例间不串味）。"""
    global _installed, _original_request
    with _lock:
        if _original_request is not None:
            requests.Session.request = _original_request
        _installed = None
        _original_request = None


def installed_timeouts() -> tuple[float, float] | None:
    """当前生效的 (connect, read)；未安装返回 None（测试守卫用）。"""
    return _installed


def is_timeout(exc: BaseException) -> bool:
    """统一的超时判定：requests 的 ReadTimeout/ConnectTimeout 是 OSError **不是**
    builtins.TimeoutError，调用方自己的 with_timeout 抛的才是内置 TimeoutError。
    两处都要认得，否则降级日志里"超时"会退化成原始异常串。"""
    return isinstance(exc, (TimeoutError, requests.exceptions.Timeout))
