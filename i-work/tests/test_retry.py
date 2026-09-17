import pytest
import httpx
from server.llm.retry import is_retriable, retry_with_backoff


def test_retriable_network_error():
    assert is_retriable(httpx.NetworkError("boom")) is True


def test_retriable_timeout():
    assert is_retriable(httpx.TimeoutException("timeout")) is True


def test_retriable_429():
    req = httpx.Request("POST", "http://x")
    resp = httpx.Response(429, request=req)
    assert is_retriable(httpx.HTTPStatusError("rate limited", request=req, response=resp)) is True


def test_retriable_502():
    req = httpx.Request("POST", "http://x")
    resp = httpx.Response(502, request=req)
    assert is_retriable(httpx.HTTPStatusError("bad gateway", request=req, response=resp)) is True


def test_non_retriable_401():
    req = httpx.Request("POST", "http://x")
    resp = httpx.Response(401, request=req)
    assert is_retriable(httpx.HTTPStatusError("unauthorized", request=req, response=resp)) is False


@pytest.mark.asyncio
async def test_retry_exhausted():
    call_count = 0

    async def failing():
        nonlocal call_count
        call_count += 1
        raise httpx.NetworkError("fail")

    with pytest.raises(httpx.NetworkError):
        await retry_with_backoff(failing, max_retries=3)
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_non_retriable_raises_immediately():
    call_count = 0

    async def failing():
        nonlocal call_count
        call_count += 1
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(401, request=req)
        raise httpx.HTTPStatusError("unauthorized", request=req, response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_with_backoff(failing, max_retries=3)
    assert call_count == 1  # 不重试
