"""Stage 5 · 父→子上下文 fork 与清洗（§9.11.12 #6 / #13）。

三档 + 清洗规则 + 不盖印。用真实父子两个 ContextManager 走一遍，断言**子的历史形状**
——不是断言中间函数返回值，因为"洗完之后子看到什么"才是这个特性的产出。
"""
from uuid import uuid4

import pytest

from server.config import settings
from server.engine.context import ContextManager
from server.engine.fork import (
    FORK_ALL,
    FORK_NONE,
    clean_rows,
    fork_parent_history,
    normalize_fork_turns,
    select_recent,
)
from server.storage.memory import InMemoryMessageRepo


def _row(role, content, message_id="m1", turn=0, **extra):
    return {"role": role, "content": content, "message_id": message_id,
            "turn": turn, "sequence": None, **extra}


def _parent_history():
    """两轮对话，每轮都带工具脚手架 —— 典型形状。"""
    return [
        # ── 第 1 轮（message m1）──
        _row("user", "帮我分析这个仓库", "m1", 0),
        _row("assistant", "", "m1", 1,
             tool_calls=[{"id": "t1", "function": {"name": "read_file"}}]),
        _row("tool", "文件内容……", "m1", 1, tool_call_id="t1"),
        _row("assistant", "这个仓库是 agent 服务", "m1", 2),
        # ── 第 2 轮（message m2）──
        _row("user", "那它的并发模型呢", "m2", 0),
        _row("assistant", "", "m2", 1,
             tool_calls=[{"id": "t2", "function": {"name": "bash"}}]),
        _row("tool", "stdout……", "m2", 1, tool_call_id="t2"),
        _row("assistant", "单事件循环 + await 是唯一切换点", "m2", 2),
    ]


# ═══════════════════════════════════════════════════════════════
# normalize_fork_turns
# ═══════════════════════════════════════════════════════════════

def test_normalize_fork_turns_accepts_valid_and_falls_back():
    assert normalize_fork_turns("none") == FORK_NONE
    assert normalize_fork_turns("ALL") == FORK_ALL
    assert normalize_fork_turns("5") == "5"
    assert normalize_fork_turns(" 03 ") == "3"
    # 空 / 非法 / 非正数 → 走全局默认
    assert normalize_fork_turns("") == settings.fork_turns_default
    assert normalize_fork_turns(None) == settings.fork_turns_default
    assert normalize_fork_turns("很多") == settings.fork_turns_default
    assert normalize_fork_turns("0") == settings.fork_turns_default
    assert normalize_fork_turns("-2") == settings.fork_turns_default
    # 显式 default 覆盖 settings
    assert normalize_fork_turns("", default="all") == FORK_ALL


# ═══════════════════════════════════════════════════════════════
# 切档
# ═══════════════════════════════════════════════════════════════

def test_select_recent_none_and_all():
    rows = _parent_history()
    assert select_recent(rows, FORK_NONE) == []
    assert select_recent(rows, FORK_ALL) == rows


def test_select_recent_keeps_last_rounds_with_their_questions():
    """N 轮的 N = 一次用户请求；提问那句必须在，否则子继承的是半截上下文。"""
    rows = _parent_history()

    one = select_recent(rows, "1")
    assert [r["content"] for r in one] == [
        "那它的并发模型呢", "", "stdout……", "单事件循环 + await 是唯一切换点",
    ]

    two = select_recent(rows, "2")
    assert two == rows

    # 要的轮数超过已有轮数 → 全给，不报错
    assert select_recent(rows, "9") == rows


# ═══════════════════════════════════════════════════════════════
# 清洗
# ═══════════════════════════════════════════════════════════════

def test_clean_drops_tool_rows_and_tool_call_blocks():
    cleaned = clean_rows(_parent_history())
    assert cleaned == [
        {"role": "user", "content": "帮我分析这个仓库"},
        {"role": "assistant", "content": "这个仓库是 agent 服务"},
        {"role": "user", "content": "那它的并发模型呢"},
        {"role": "assistant", "content": "单事件循环 + await 是唯一切换点"},
    ]
    # 工具细节与 reasoning 一律不搬运
    for row in cleaned:
        assert set(row) == {"role", "content"}


def test_clean_drops_parent_system_and_developer_rows():
    """父的指令块换成子自己的（子引擎各自 _setup_expert_agent 出自己的 prompt）。"""
    rows = [
        _row("system", "你是父的 lead 指令"),
        _row("developer", "父的 developer 块"),
        _row("user", "问题"),
        _row("assistant", "答案"),
    ]
    assert clean_rows(rows) == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "答案"},
    ]


def test_clean_drops_pending_task_call_from_last_assistant_row():
    """父那条还没写 tool_result 的 task 调用不能被子继承 —— 否则子拿到自己的派发句柄。"""
    rows = [
        _row("user", "拆一下这个任务"),
        _row("assistant", "我这就派两个子任务", "m1", 1, tool_calls=[
            {"id": "t9", "function": {"name": "task",
                                      "arguments": '{"agent_path":"/root/m1"}'}},
        ]),
    ]
    cleaned = clean_rows(rows)
    assert cleaned == [
        {"role": "user", "content": "拆一下这个任务"},
        {"role": "assistant", "content": "我这就派两个子任务"},
    ]
    assert "agent_path" not in str(cleaned)


# ═══════════════════════════════════════════════════════════════
# 端到端：父历史 → 子历史
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fork_writes_cleaned_history_into_child():
    repo = InMemoryMessageRepo()
    parent_id, child_id = uuid4(), uuid4()
    for row in _parent_history():
        await repo.append_message(parent_id, dict(row))

    parent_ctx = ContextManager(repo)
    child_ctx = ContextManager(repo)

    n = await fork_parent_history(
        parent_context_mgr=parent_ctx, parent_session_id=parent_id,
        child_context_mgr=child_ctx, child_session_id=child_id,
        fork_turns=FORK_ALL,
    )

    child_rows = await repo.get_history(child_id)
    assert n == 4 and len(child_rows) == 4
    assert [r["role"] for r in child_rows] == ["user", "assistant", "user", "assistant"]
    assert all(r.get("tool_calls") is None for r in child_rows)
    assert all(r.get("message_id") is None for r in child_rows), "fork 段不盖印"
    # 父历史没被就地改坏
    assert len(await repo.get_history(parent_id)) == 8


@pytest.mark.asyncio
async def test_fork_none_writes_nothing():
    repo = InMemoryMessageRepo()
    parent_id, child_id = uuid4(), uuid4()
    for row in _parent_history():
        await repo.append_message(parent_id, dict(row))

    n = await fork_parent_history(
        parent_context_mgr=ContextManager(repo), parent_session_id=parent_id,
        child_context_mgr=ContextManager(repo), child_session_id=child_id,
        fork_turns=FORK_NONE,
    )
    assert n == 0
    assert await repo.get_history(child_id) == []


@pytest.mark.asyncio
async def test_fork_n_slices_then_cleans():
    repo = InMemoryMessageRepo()
    parent_id, child_id = uuid4(), uuid4()
    for row in _parent_history():
        await repo.append_message(parent_id, dict(row))

    await fork_parent_history(
        parent_context_mgr=ContextManager(repo), parent_session_id=parent_id,
        child_context_mgr=ContextManager(repo), child_session_id=child_id,
        fork_turns="1",
    )
    child_rows = await repo.get_history(child_id)
    assert [r["content"] for r in child_rows] == [
        "那它的并发模型呢", "单事件循环 + await 是唯一切换点",
    ]


@pytest.mark.asyncio
async def test_regenerate_anchor_is_the_childs_own_prompt_not_a_fork_row():
    """fork 段不盖印，所以子 regenerate 时找锚点不会把某条**继承来的 user 行**当成本消息。

    这是不盖印真正要防的：regenerate 靠「第一条 message_id == 本消息 且 role == user」
    定位截断边界（`query_loop.py:1398-1405`）。fork 段里天然有 user 行，若给它们盖上
    子的 message_id，锚点就会落在继承来的那句父提问上，截断边界提前 → 子自己的旧回复
    删不干净。
    """
    repo = InMemoryMessageRepo()
    parent_id, child_id, child_msg_id = uuid4(), uuid4(), uuid4()
    for row in _parent_history():
        await repo.append_message(parent_id, dict(row))

    child_ctx = ContextManager(repo)
    await fork_parent_history(
        parent_context_mgr=ContextManager(repo), parent_session_id=parent_id,
        child_context_mgr=child_ctx, child_session_id=child_id,
        fork_turns=FORK_ALL,
    )
    child_ctx.set_active_message(child_msg_id, 0)
    await child_ctx.append_text(child_id, "user", "子收到的 prompt")
    await child_ctx.append_text(child_id, "assistant", "子的回答")

    # regenerate 的锚点扫描逻辑（与 query_loop.py:1398-1405 一致）
    rows = await child_ctx.list_rows(child_id)
    anchor = next(
        r["sequence"] for r in rows
        if r.get("message_id") == str(child_msg_id) and r.get("role") == "user"
    )
    assert anchor == 5, "锚点必须是子自己那句 prompt（fork 4 行之后）"

    await child_ctx.truncate_message_after(child_id, child_msg_id, anchor)
    kept = await repo.get_history(child_id)
    assert [r["content"] for r in kept] == [
        "帮我分析这个仓库", "这个仓库是 agent 服务",
        "那它的并发模型呢", "单事件循环 + await 是唯一切换点",
        "子收到的 prompt",
    ], "继承来的 4 行前史 + 子自己的提问留下；子那条旧回复被截掉"
