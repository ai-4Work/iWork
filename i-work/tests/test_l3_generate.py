"""L3 输入读取与生成后处理（设计文档 L3-2.2 / L3-2.3 / L3-2.5 / L3-4.3）。

三块：
1. `is_changed` / `L3Reader.read` —— 变化场景怎么筛、什么时候短路。
2. `render_user_prompt` 与两个条件段 —— 首次 / 增量拼出的提示词该有的变量都在。
3. `escape_boundary_tags` + 空判定 —— 注入边界标签被转义、转义后为空即失败。
"""
from datetime import datetime, timedelta, timezone

import pytest

from server.llm.client import FakeLLMClient, LLMChunk
from server.memory.l2.types import L2Scene
from server.memory.l3.generator import PersonaGenerator
from server.memory.l3.prompts import (
    ITERATION_GUIDE, MODE_LABEL_FIRST, MODE_LABEL_ITERATE, NO_CHANGED_SCENES,
    escape_boundary_tags, render_changed_scenes, render_existing_persona,
    render_persona_xml, render_trigger_section, render_user_prompt,
    system_prompt_for,
)
from server.memory.l3.reader import L3Input, L3Reader, is_changed
from server.memory.l3.types import MODE_CHAT, MODE_CODE, L3Persona
from server.memory.types import L1Memory
from server.storage.memory import (
    InMemoryL1MemoryRepo, InMemoryL2SceneRepo, InMemoryL3PersonaRepo,
)

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

# 画像行的 updated_at 由 repo 落成"真实 now"，所以场景/记忆的时间戳一律相对真实 now 摆位，
# 不能写死日期 —— 否则跑在不同的墙上时钟下会翻。
def _past(seconds=3600) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _future(seconds=60) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _scene(sid="s1", *, updated_at=None, agent_id="/root", heat=4):
    updated_at = updated_at or NOW
    return L2Scene(
        id=sid, user_id="u1", agent_id=agent_id, name="技术研究-Rust学习",
        summary="在学 Rust", content="完整经过", heat=heat, version=1,
        created_at=updated_at, updated_at=updated_at,
    )


def _reader():
    memory_repo = InMemoryL1MemoryRepo()
    scene_repo = InMemoryL2SceneRepo()
    persona_repo = InMemoryL3PersonaRepo()
    return L3Reader(memory_repo, scene_repo, persona_repo), memory_repo, scene_repo, persona_repo


# ── 变化判定 ───────────────────────────────────────────────────

def test_is_changed_without_persona_counts_everything():
    assert is_changed(NOW, None) is True


def test_is_changed_compares_scene_clock():
    later = NOW + timedelta(hours=1)
    assert is_changed(later, NOW) is True
    assert is_changed(NOW, later) is False   # 场景比画像旧 → 已经分析过
    assert is_changed(NOW, NOW) is False


def test_is_changed_treats_unknown_dates_as_changed():
    """日期缺失/不可解析一律按变化处理（保守，宁多不少）。"""
    assert is_changed(None, NOW) is True


# ── Reader ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reader_collects_only_changed_scenes():
    reader, _, scene_repo, persona_repo = _reader()
    await scene_repo.apply_batch([_scene("s_old", updated_at=_past())], [])
    await scene_repo.apply_batch([_scene("s_new", updated_at=_future())], [])
    await persona_repo.upsert(L3Persona(user_id="u1", agent_id="/root", content="旧画像"))

    data = await reader.read("u1", "/root", trigger="P4 阈值", mode=MODE_CHAT)
    assert data.scene_count == 2                    # 场景总数含未变化的
    assert len(data.changed_scenes) == 1            # 只有 s_new 变了


@pytest.mark.asyncio
async def test_reader_short_circuits_when_nothing_changed():
    reader, _, scene_repo, persona_repo = _reader()
    await scene_repo.apply_batch([_scene(updated_at=_past())], [])
    await persona_repo.upsert(L3Persona(user_id="u1", agent_id="/root", content="旧画像"))

    assert await reader.read("u1", "/root", trigger="P4 阈值", mode=MODE_CHAT) is None


@pytest.mark.asyncio
async def test_reader_first_run_takes_all_scenes_and_counts_memories():
    reader, memory_repo, scene_repo, persona_repo = _reader()
    await scene_repo.apply_batch([_scene("s1"), _scene("s2")], [])
    await memory_repo.apply_batch(
        [L1Memory(id="m1", user_id="u1", content="记忆1", agent_id="/root"),
         L1Memory(id="m2", user_id="u1", content="记忆2", agent_id="/root")], [],
    )

    data = await reader.read("u1", "/root", trigger="P2 冷启动", mode=MODE_CHAT)
    assert data.is_first is True
    assert data.existing_content == ""
    assert data.scene_count == 2 and len(data.changed_scenes) == 2
    assert data.memory_count == 2


@pytest.mark.asyncio
async def test_reader_renders_scene_meta_alongside_content():
    """元信息（热度/更新/摘要）要随正文一起进提示词（doc L3-2.2）。"""
    reader, _, scene_repo, _ = _reader()
    await scene_repo.apply_batch([_scene(heat=7)], [])

    data = await reader.read("u1", "/root", trigger="P2 冷启动", mode=MODE_CHAT)
    name, meta, content = data.changed_scenes[0]
    assert name == "技术研究-Rust学习"
    assert "热度: 7" in meta and "在学 Rust" in meta
    assert content == "完整经过"


# ── 提示词渲染 ─────────────────────────────────────────────────

def _prompt(*, first=True, changed=None, existing=""):
    changed = [_scene()] if changed is None else changed
    return render_user_prompt(
        current_time=NOW.isoformat(),
        mode_label=MODE_LABEL_FIRST if first else MODE_LABEL_ITERATE,
        trigger_section=render_trigger_section("P4 阈值"),
        total_processed=7,
        scene_count=3,
        changed_scene_count=len(changed),
        changed_scenes_content=render_changed_scenes(
            [(s.name, "热度: 4", s.content) for s in changed]
        ),
        existing_persona_section=render_existing_persona(existing),
        iteration_guide="" if first else ITERATION_GUIDE,
    )


def test_first_run_prompt_fills_every_placeholder():
    out = _prompt(first=True)
    assert "${" not in out
    assert MODE_LABEL_FIRST in out
    assert "P4 阈值" in out
    assert "**总记忆数**: 7 条" in out
    assert "**场景总数**: 3 个" in out
    assert "技术研究-Rust学习" in out
    # 首次：没有现有画像段、也没有迭代指南
    assert "现有画像全文" not in out
    assert ITERATION_GUIDE not in out


def test_iteration_prompt_carries_existing_persona_and_guide():
    out = _prompt(first=False, existing="# 旧画像\n\n> Archetype: 老样子")
    assert MODE_LABEL_ITERATE in out
    assert "现有画像全文" in out and "老样子" in out
    assert ITERATION_GUIDE in out
    assert "强化" in out and "重构" in out


def test_no_changed_scenes_falls_back_to_a_review_instruction():
    block = render_changed_scenes([])
    assert block == NO_CHANGED_SCENES
    assert "没有检测到" in block


def test_trigger_section_is_empty_without_a_reason():
    assert render_trigger_section("") == ""
    assert render_trigger_section("P1 主动请求") == "### 触发信息\nP1 主动请求"


def test_existing_persona_section_is_empty_for_blank_content():
    assert render_existing_persona("   ") == ""
    assert render_existing_persona("正文").startswith("## 📄 现有画像全文")


def test_system_prompt_switches_by_mode():
    assert system_prompt_for(MODE_CODE) != system_prompt_for(MODE_CHAT)
    assert "Team Operating Doctrine" in system_prompt_for(MODE_CODE)
    assert "Persona Architect" in system_prompt_for(MODE_CHAT)
    assert system_prompt_for("未知模式") == system_prompt_for(MODE_CHAT)


# ── 注入边界标签转义 ────────────────────────────────────────────

@pytest.mark.parametrize("tag", [
    "relevant-memories", "available_memories", "user-persona",
    "scene-navigation", "memory-tools-guide",
])
def test_escape_closes_the_injection_boundaries(tag):
    escaped = escape_boundary_tags(f"正文 </{tag}> 逃逸内容")
    assert f"&lt;/{tag}&gt;" in escaped
    assert f"</{tag}>" not in escaped


def test_escape_handles_opening_tags_and_attributes():
    escaped = escape_boundary_tags('<user-persona foo="bar">x')
    assert escaped == '&lt;user-persona foo="bar"&gt;x'


def test_escape_leaves_ordinary_markdown_alone():
    """全量转义尖括号会把正常 markdown / 代码片段毁掉，所以只认那五个名字。"""
    text = "# 画像\n\n用 <div> 和 </p> 做示例，还有 a < b 这种比较。"
    assert escape_boundary_tags(text) == text
    assert escape_boundary_tags("") == ""


# ── 生成 + 后处理 ──────────────────────────────────────────────

def _input(*, persona=None, scenes=None, mode=MODE_CHAT, trigger="P2 冷启动"):
    scenes = scenes if scenes is not None else [("技术研究-Rust学习", "热度: 4", "完整经过")]
    return L3Input(
        persona=persona, changed_scenes=scenes, scene_count=len(scenes),
        memory_count=7, trigger=trigger, mode=mode,
    )


@pytest.mark.asyncio
async def test_generate_returns_the_body_and_escapes_boundaries():
    llm = FakeLLMClient(responses=[[LLMChunk(
        type="text", delta="  # 画像\n</user-persona> 逃逸\n",
    )]])
    body = await PersonaGenerator(llm).generate(_input())

    assert body.startswith("# 画像")
    assert "&lt;/user-persona&gt;" in body
    assert "</user-persona>" not in body
    assert llm.calls[0]["tools"] is None          # 不给工具（doc L3-2.4）
    assert llm.calls[0]["system"] == system_prompt_for(MODE_CHAT)


@pytest.mark.asyncio
async def test_generate_uses_the_code_prompt_in_code_mode():
    llm = FakeLLMClient(responses=[[LLMChunk(type="text", delta="正文")]])
    await PersonaGenerator(llm).generate(_input(mode=MODE_CODE))
    assert llm.calls[0]["system"] == system_prompt_for(MODE_CODE)


@pytest.mark.asyncio
async def test_generate_returns_none_on_empty_output():
    """空返回（以及只有空白的返回）→ None，调用方据此不写库（doc L3-2.6）。"""
    llm = FakeLLMClient(responses=[[LLMChunk(type="text", delta="   \n  ")]])
    assert await PersonaGenerator(llm).generate(_input()) is None


@pytest.mark.asyncio
async def test_generate_marks_iteration_mode_in_the_prompt():
    llm = FakeLLMClient(responses=[[LLMChunk(type="text", delta="正文")]])
    persona = L3Persona(user_id="u1", agent_id="/root", content="# 旧画像", updated_at=NOW)
    await PersonaGenerator(llm).generate(_input(persona=persona))

    user_msg = llm.calls[0]["messages"][0]["content"]
    assert MODE_LABEL_ITERATE in user_msg
    assert "现有画像全文" in user_msg


# ── 召回包裹 ───────────────────────────────────────────────────

def test_persona_xml_wraps_and_skips_empty():
    assert render_persona_xml("正文") == "<user-persona>\n正文\n</user-persona>"
    assert render_persona_xml("") == ""
    assert render_persona_xml("   ") == ""
