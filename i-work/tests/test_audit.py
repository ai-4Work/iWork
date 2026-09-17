"""审计写入裁剪：action/resource 超列宽不得导致整条记录丢失，detail 保留完整内容。"""
import json
from uuid import uuid4

import pytest

from server.observability.audit import (
    AuditSubscriber,
    audit_log,
    _ACTION_MAX_LEN,
    _RESOURCE_MAX_LEN,
)
from server.observability.events import AgentEvent, AgentEventType, now


class _FakeDB:
    """冒充 AsyncSession：记录 execute 的参数，忽略 SQL。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params):
        self.calls.append(params)

    async def commit(self):
        pass


def _tool_event(tool_name: str, tool_input: dict) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.TOOL_EXECUTED,
        timestamp=now(),
        session_id=uuid4(),
        user_id=uuid4(),
        message_id=uuid4(),
        data={"tool_name": tool_name, "input": tool_input},
    )


@pytest.mark.asyncio
async def test_long_bash_command_clipped_but_detail_intact():
    db = _FakeDB()
    command = "cd C:/x; node send.js --to a@b.com; " + "y" * 500
    await AuditSubscriber(lambda: db).handle(_tool_event("bash", {"command": command}))

    params = db.calls[0]
    assert params["res"] == command[:_RESOURCE_MAX_LEN]
    assert len(params["res"]) == _RESOURCE_MAX_LEN
    # 完整命令仍保留在 detail，未丢信息
    assert json.loads(params["det"])["input"]["command"] == command


@pytest.mark.asyncio
async def test_short_resource_untouched():
    db = _FakeDB()
    await AuditSubscriber(lambda: db).handle(_tool_event("bash", {"command": "ls -la"}))

    params = db.calls[0]
    assert params["res"] == "ls -la"
    assert params["act"] == "tool.shell_exec"


@pytest.mark.asyncio
async def test_long_action_clipped():
    db = _FakeDB()
    tool_name = "mcp__" + "a" * 60
    await AuditSubscriber(lambda: db).handle(_tool_event(tool_name, {"command": "x"}))

    params = db.calls[0]
    assert len(params["act"]) == _ACTION_MAX_LEN
    assert params["act"] == f"tool.{tool_name}"[:_ACTION_MAX_LEN]


@pytest.mark.asyncio
async def test_missing_resource_is_none():
    db = _FakeDB()
    await AuditSubscriber(lambda: db).handle(_tool_event("bash", {}))

    assert db.calls[0]["res"] is None


@pytest.mark.asyncio
async def test_non_str_resource_coerced():
    db = _FakeDB()
    await AuditSubscriber(lambda: db).handle(_tool_event("read_file", {"file_path": 12345}))

    assert db.calls[0]["res"] == "12345"


@pytest.mark.asyncio
async def test_audit_log_clips_action_and_resource():
    db = _FakeDB()
    await audit_log(
        db,
        action="user." + "z" * 80,
        user_id=uuid4(),
        detail={"ok": True},
        resource="message/" + "m" * 200,
    )

    params = db.calls[0]
    assert len(params["act"]) == _ACTION_MAX_LEN
    assert len(params["res"]) == _RESOURCE_MAX_LEN
