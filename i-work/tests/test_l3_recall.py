"""L3 画像召回（设计文档 L3-3）：整份读出、整份注入，失败不阻塞对话。

与 L1 / L2 召回不同，这里没有 top-N、没有阈值、没有字符裁剪 —— 所以断言也简单：
有行就包成 `<user-persona>`，没行就空串，出错/超时同样空串。
"""
import asyncio

import pytest

from server.memory.l3.recall import L3RecallService
from server.memory.l3.types import L3Persona
from server.storage.memory import InMemoryL3PersonaRepo


@pytest.mark.asyncio
async def test_persona_xml_wraps_the_whole_body():
    repo = InMemoryL3PersonaRepo()
    await repo.upsert(L3Persona(user_id="u1", agent_id="/root", content="# 画像正文"))

    xml = await L3RecallService(repo).persona_xml(user_id="u1", agent_id="/root")
    assert xml.startswith("<user-persona>") and xml.endswith("</user-persona>")
    assert "# 画像正文" in xml


@pytest.mark.asyncio
async def test_no_row_yields_empty_string():
    repo = InMemoryL3PersonaRepo()
    assert await L3RecallService(repo).persona_xml(user_id="u1", agent_id="/root") == ""


@pytest.mark.asyncio
async def test_no_truncation():
    """画像不做长度裁剪 —— 有界由生成侧提示词保证（doc L3-3.3）。"""
    body = "很长的一段画像。" * 500
    repo = InMemoryL3PersonaRepo()
    await repo.upsert(L3Persona(user_id="u1", agent_id="/root", content=body))

    xml = await L3RecallService(repo).persona_xml(user_id="u1", agent_id="/root")
    assert body in xml


@pytest.mark.asyncio
async def test_repo_failure_returns_empty_and_does_not_raise():
    class Boom:
        async def get(self, *a, **kw):
            raise RuntimeError("db is gone")

    assert await L3RecallService(Boom()).persona_xml(user_id="u1", agent_id="/root") == ""


@pytest.mark.asyncio
async def test_timeout_returns_empty_and_does_not_block_the_turn():
    class Slow:
        async def get(self, *a, **kw):
            await asyncio.sleep(1)
            return L3Persona(user_id="u1", agent_id="/root", content="x")

    xml = await L3RecallService(Slow(), timeout_seconds=0.01).persona_xml(
        user_id="u1", agent_id="/root",
    )
    assert xml == ""


@pytest.mark.asyncio
async def test_blank_content_is_not_injected():
    repo = InMemoryL3PersonaRepo()
    await repo.upsert(L3Persona(user_id="u1", agent_id="/root", content="   "))
    assert await L3RecallService(repo).persona_xml(user_id="u1", agent_id="/root") == ""
