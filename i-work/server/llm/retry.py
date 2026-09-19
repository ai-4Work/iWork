import asyncio
import httpx

RETRIABLE_STATUSES = {429, 502, 503}


def is_retriable(exc: Exception) -> bool:
    """判断异常是否可通过重试恢复。"""
    if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRIABLE_STATUSES
    return False


async def retry_with_backoff(fn, max_retries: int = 3):
    """指数退避重试：共 max_retries 次尝试，退避 2^n 秒（1s、2s…）。
    最后一次不再退避，直接抛。
    非可重试异常直接 raise，不浪费尝试次数。
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            return await fn()
        except Exception as e:
            last_exc = e
            if not is_retriable(e):
                raise
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    raise last_exc  # type: ignore
