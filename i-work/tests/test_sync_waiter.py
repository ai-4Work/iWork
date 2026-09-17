import asyncio
import pytest
from uuid import uuid4
from server.sync_waiter import SyncWaiter


@pytest.mark.asyncio
async def test_wait_and_resolve():
    waiter = SyncWaiter()
    sid = uuid4()

    async def resolver():
        await asyncio.sleep(0.05)
        waiter.resolve(sid, "confirmed")

    asyncio.create_task(resolver())
    result = await waiter.wait(sid, timeout=1.0)
    assert result == "confirmed"


@pytest.mark.asyncio
async def test_wait_timeout():
    waiter = SyncWaiter()
    result = await waiter.wait(uuid4(), timeout=0.01)
    assert result is None
