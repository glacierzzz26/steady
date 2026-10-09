"""datahub HTTP 客户端单测（注入 fake session，不联网）。

覆盖：信封解析、Bearer 头/URL/参数、4xx 快速失败、超时与 5xx 重试、
解析错误、TTL 缓存命中、单例重置。
"""
import pytest
import requests

from app import datahub_client
from app.datahub_client import (DatahubClient, DatahubHTTPError,
                                DatahubParseError, DatahubTimeout)


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeSession:
    """按序返回排队响应/异常；记录每次调用参数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params,
                           "headers": headers, "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def enable(monkeypatch):
    """把闸门所需 env 置齐（客户端只读 base_url/token/timeout/retries）。"""
    monkeypatch.setenv("DATAHUB_BASE_URL", "http://dh:8100")
    monkeypatch.setenv("DATAHUB_TOKEN", "secret-token")
    monkeypatch.setenv("DATAHUB_RETRY_DELAY", "0")


def _client(responses):
    session = FakeSession(responses)
    return DatahubClient(session=session), session


def test_fetch_parses_envelope_and_sends_auth(enable):
    rows = [{"cal_date": "2026-08-10", "is_open": True, "exchange": "SSE"}]
    client, session = _client([FakeResp(200, {"code": 0, "message": "ok", "data": rows})])
    got = client.fetch("trade_calendar", {"start": "2026-08-01", "end": "2026-08-31"})
    assert got == rows
    call = session.calls[0]
    assert call["url"] == "http://dh:8100/v1/datasets/trade_calendar"
    assert call["params"] == {"start": "2026-08-01", "end": "2026-08-31"}
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert call["timeout"] == (5.0, 10.0)


def test_empty_and_missing_data_are_ok(enable):
    client, _ = _client([FakeResp(200, {"code": 0, "data": []}),
                         FakeResp(200, {"code": 0})])
    assert client.fetch("trade_calendar") == []
    assert client.fetch("trade_calendar") == []


def test_4xx_fast_fail_without_retry(enable):
    client, session = _client([FakeResp(401, {"detail": "bad token"}, text="bad token")])
    with pytest.raises(DatahubHTTPError):
        client.fetch("trade_calendar")
    assert len(session.calls) == 1  # 不重试


def test_timeout_retries_then_raises(enable):
    client, session = _client([requests.Timeout("t1"), requests.Timeout("t2")])
    with pytest.raises(DatahubTimeout):
        client.fetch("trade_calendar")
    assert len(session.calls) == 2  # retries=1 → 2 次尝试


def test_5xx_retries_then_succeeds(enable):
    rows = [{"cal_date": "2026-08-10", "is_open": True}]
    client, session = _client([FakeResp(503, text="down"),
                               FakeResp(200, {"code": 0, "data": rows})])
    assert client.fetch("trade_calendar") == rows
    assert len(session.calls) == 2


def test_parse_error_on_non_json(enable):
    client, _ = _client([FakeResp(200, payload=None, text="<html>")])
    with pytest.raises(DatahubParseError):
        client.fetch("trade_calendar")


def test_parse_error_on_nonzero_code(enable):
    client, _ = _client([FakeResp(200, {"code": 1, "message": "boom"})])
    with pytest.raises(DatahubParseError):
        client.fetch("trade_calendar")


def test_ttl_cache_hit_avoids_second_request(enable):
    rows = [{"cal_date": "2026-08-10", "is_open": True}]
    client, session = _client([FakeResp(200, {"code": 0, "data": rows})])
    params = {"start": "2026-08-01", "end": "2026-08-31"}
    assert client.fetch("trade_calendar", params) == rows
    assert client.fetch("trade_calendar", params) == rows
    assert len(session.calls) == 1  # TTL（默认 60s）内命中缓存


def test_get_client_singleton_and_reset(enable):
    assert datahub_client.get_client() is datahub_client.get_client()
    first = datahub_client.get_client()
    datahub_client.reset_client()
    assert datahub_client.get_client() is not first
