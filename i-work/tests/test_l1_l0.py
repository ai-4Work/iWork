"""L0 读取与四步清洗（设计文档 L1-1.1 / L1-2.2 / L1-2.3）。"""
from uuid import uuid4

import pytest

from server.memory.l0 import L0Reader, clean_text, is_noise, text_of
from server.storage.memory import InMemoryMessageRepo


# ── 第 2 步：文本清洗 ──────────────────────────────────────────

def test_strips_injected_memory_tags():
    raw = "问题正文\n<relevant-memories>\n- [episodic] 旧事实\n</relevant-memories>"
    assert clean_text(raw, "user") == "问题正文"


def test_strips_leading_timestamps():
    assert clean_text("[2026-08-18T02:00:00.000Z] 你好", "user") == "你好"
    assert clean_text("2026-08-18 02:00:00 你好", "user") == "你好"


def test_media_and_base64_become_image_placeholder():
    assert clean_text("[media attached: a.png]", "user") == ""
    assert clean_text("看这个 data:image/png;base64," + "A" * 200, "user") == "看这个 [image]"
    assert clean_text("裸 base64 " + "QUJD" * 30, "user") == "裸 base64 [image]"


def test_system_exec_lines_and_control_chars_dropped():
    assert clean_text("System: [bash] Exec completed in 12ms\n继续", "user") == "继续"
    assert clean_text("a\x00b", "user") == "ab"


def test_code_fences_stripped_only_for_assistant():
    raw = "说明\n```python\nprint(1)\n```\n结论"
    assert "print(1)" not in clean_text(raw, "assistant")
    assert "print(1)" in clean_text(raw, "user")


def test_text_of_handles_openai_array_content():
    content = [
        {"type": "text", "text": "前半"},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "后半"},
    ]
    assert text_of(content) == "前半\n后半"
    assert text_of(None) == ""


# ── 第 4 步：结构过滤 ──────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "", "   \n  ", "NO_REPLY", "/new", "/reset", "/compact",
    "[pre-compaction memory flush] 摘要", "内存冲刷：请总结",
    "???", "？？", "!!!", "……", "——",
])
def test_noise_is_dropped(text):
    assert is_noise(text) is True


@pytest.mark.parametrize("text", [
    "帮我看看这个 bug", "ok", "为什么 A 比 B 快？", "1+1=2",
])
def test_real_content_survives(text):
    assert is_noise(text) is False


# ── 增量读取 ───────────────────────────────────────────────────

async def _seed(repo, session_id, rows):
    for role, content in rows:
        await repo.append_message(session_id, {"role": role, "content": content})


@pytest.mark.asyncio
async def test_read_batch_splits_new_and_background():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    await _seed(repo, sid, [
        ("user", "第一个问题"), ("assistant", "第一个回答"),
        ("user", "第二个问题"), ("assistant", "第二个回答"),
    ])
    reader = L0Reader(repo, batch_size=10, background_size=1)

    batch = await reader.read_batch(sid, cursor=2)
    assert [m.content for m in batch.new_messages] == ["第二个问题", "第二个回答"]
    assert [m.content for m in batch.background] == ["第一个回答"]
    assert batch.pending_user == 1
    assert batch.read_cursor == 4


@pytest.mark.asyncio
async def test_non_chat_roles_are_filtered_out():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    await _seed(repo, sid, [
        ("system", "你是助手"), ("user", "问题"), ("tool", "{}"), ("assistant", "回答"),
    ])
    reader = L0Reader(repo)

    batch = await reader.read_batch(sid, cursor=0)
    assert [m.role for m in batch.new_messages] == ["user", "assistant"]
    assert batch.read_cursor == 4          # 游标跳过被过滤的行，否则会卡死


@pytest.mark.asyncio
async def test_backlog_is_capped_and_cursor_stops_at_batch():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    await _seed(repo, sid, [("user", f"问题 {i}") for i in range(6)])
    reader = L0Reader(repo, batch_size=2)

    batch = await reader.read_batch(sid, cursor=0)
    assert [m.content for m in batch.new_messages] == ["问题 0", "问题 1"]
    assert batch.pending_total == 6        # 积压数照实报，供级联判定
    assert batch.read_cursor == 2          # 游标只走完这一批


@pytest.mark.asyncio
async def test_current_end_reports_max_sequence():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    await _seed(repo, sid, [("user", "a"), ("assistant", "b")])
    assert await L0Reader(repo).current_end(sid) == 2
    assert await L0Reader(repo).current_end(uuid4()) == 0
