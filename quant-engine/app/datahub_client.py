"""datahub HTTP 客户端（Phase 3 读切换的**唯一出网点**）。

设计要点：
- **单一进程级单例** + 短 TTL 缓存（`DATAHUB_CACHE_TTL`，默认 60s），降低读放大。
- **Bearer 鉴权**：`Authorization: Bearer <DATAHUB_TOKEN>`。
- **信封解析**：`{code,message,data,meta}` → `data`；`code != 0` 视为解析错误。
- **重试只对瞬时故障**（超时/连接错/5xx）；4xx 快速失败（含 401 鉴权错，fail-closed）。
- **显式传 timeout**（connect,read）；不安装 `requests.Session` 全局补丁——遵 collector
  `sources/net.py`「补丁仅 `__main__` 安装」纪律，避免污染测试进程的其它网络调用。
- `reset_client()` 供测试丢弃单例（同时清缓存）。
"""
import logging
import time

import requests

from app import config

logger = logging.getLogger(__name__)

# 可重试的瞬时 HTTP 状态码（4xx 一律快速失败）
_RETRY_STATUS = frozenset({500, 502, 503, 504})


class DatahubError(Exception):
    """datahub 读路径的基类异常。"""


class DatahubHTTPError(DatahubError):
    """非 2xx 响应（4xx 快速失败；5xx 重试耗尽后抛出）。"""


class DatahubTimeout(DatahubError):
    """连接/读超时或连接错误（重试耗尽后抛出）。"""


class DatahubParseError(DatahubError):
    """响应非 JSON 或信封 `code != 0`。"""


def _parse_envelope(resp) -> list[dict]:
    try:
        body = resp.json()
    except ValueError as e:
        raise DatahubParseError(
            f"datahub 响应非 JSON: {resp.text[:200]}") from e
    if not isinstance(body, dict) or body.get("code") != 0:
        detail = body.get("message") if isinstance(body, dict) else type(body).__name__
        raise DatahubParseError(f"datahub 信封异常（code!=0）: {detail}")
    data = body.get("data")
    if data is None:
        return []  # 空结果是合法值（raw 数据集语义）
    if not isinstance(data, list):
        raise DatahubParseError(
            f"datahub data 非列表: {type(data).__name__}")
    return data


class DatahubClient:
    """datahub HTTP 读客户端（可注入 session 便于单测）。"""

    def __init__(self, session=None):
        self._session = session if session is not None else requests.Session()
        self._cache: dict[tuple, tuple[float, list]] = {}

    def reset(self) -> None:
        self._cache.clear()

    def fetch(self, dataset_id: str, params: dict | None = None) -> list[dict]:
        """取数据集 `dataset_id`，返回 `data` 列表。

        同一 URL+参数在 TTL 内命中缓存；失败抛 `DatahubError` 子类。
        """
        params = params or {}
        url = f"{config.datahub_base_url().rstrip('/')}/v1/datasets/{dataset_id}"
        ttl = config.datahub_cache_ttl()
        key = (url, tuple(sorted((k, str(v)) for k, v in params.items())))
        now = time.monotonic()
        if ttl > 0:
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
        data = self._request(url, params)
        if ttl > 0:
            self._cache[key] = (now, data)
        return data

    def _request(self, url: str, params: dict) -> list[dict]:
        headers = {"Authorization": f"Bearer {config.datahub_token()}"}
        timeout = (config.datahub_connect_timeout(), config.datahub_read_timeout())
        retries = max(0, config.datahub_retries())
        delay = config.datahub_retry_delay()
        for attempt in range(retries + 1):
            last = attempt == retries
            try:
                resp = self._session.get(
                    url, params=params, headers=headers, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                if last:
                    raise DatahubTimeout(
                        f"datahub 请求超时/连接失败: {url}: {e}") from e
                logger.warning("datahub 请求失败（第 %d 次），重试: %s", attempt + 1, e)
                time.sleep(delay)
                continue
            if resp.status_code in _RETRY_STATUS:
                if last:
                    raise DatahubHTTPError(
                        f"datahub 返回 {resp.status_code}: {url}")
                logger.warning("datahub 返回 %d（第 %d 次），重试: %s",
                               resp.status_code, attempt + 1, url)
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise DatahubHTTPError(
                    f"datahub 返回 {resp.status_code}: {url}: {resp.text[:200]}")
            return _parse_envelope(resp)
        raise DatahubHTTPError(f"datahub 请求未完成: {url}")  # pragma: no cover


_client: "DatahubClient | None" = None


def get_client() -> DatahubClient:
    """进程级单例。"""
    global _client
    if _client is None:
        _client = DatahubClient()
    return _client


def reset_client() -> None:
    """丢弃单例（测试用：清缓存，防跨用例串味）。"""
    global _client
    _client = None
