"""quant-engine 测试全局夹具。"""
import pytest

from app import datahub_client

# 读切换相关 env：每个用例前清空，保证「默认读本地」不被宿主环境串味。
_DATAHUB_ENV = (
    "DATAHUB_READ_DATASETS",
    "DATAHUB_BASE_URL",
    "DATAHUB_TOKEN",
    "DATAHUB_HTTP_CONNECT_TIMEOUT",
    "DATAHUB_HTTP_READ_TIMEOUT",
    "DATAHUB_RETRIES",
    "DATAHUB_RETRY_DELAY",
    "DATAHUB_CACHE_TTL",
    "DATAHUB_FALLBACK_LOCAL",
)


@pytest.fixture(autouse=True)
def _isolate_datahub(monkeypatch):
    """清空 DATAHUB_* env + 丢弃客户端单例（清 TTL 缓存），防跨用例串味。"""
    for key in _DATAHUB_ENV:
        monkeypatch.delenv(key, raising=False)
    datahub_client.reset_client()
    yield
    datahub_client.reset_client()
