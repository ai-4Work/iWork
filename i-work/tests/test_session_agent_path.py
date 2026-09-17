"""Stage 2 · 会话 agent 树寻址列（agent_path / root_session_id）。

重点回归 **脆弱点 1**：OrmSession 的两个转换器是逐字段手写的，而 PgSessionRepo.update
走 from_pydantic + db.merge —— 只要有一个转换器漏掉新列，任何一次
`session_repo.update()`（含 routes.py 首条消息写标题那次）就会把该列冲成 NULL。
下面的 round-trip 与 __dict__ 断言就是钉这个的。
"""
from uuid import uuid4

import pytest

from server.db.models import OrmSession
from server.models.session import Session
from server.storage.memory import InMemorySessionRepo


def _session(**over):
    base = dict(
        user_id=str(uuid4()), mode="ask", workspace="/tmp/test",
        model="claude-sonnet-4-6",
    )
    base.update(over)
    return Session(**base)


# ═══════════════════════════════════════════════════════════════
# 默认值
# ═══════════════════════════════════════════════════════════════

def test_session_defaults_are_root():
    s = _session()
    assert s.agent_path == "/root"
    assert s.root_session_id is None


# ═══════════════════════════════════════════════════════════════
# 转换器 round-trip（脆弱点 1 的回归测试）
# ═══════════════════════════════════════════════════════════════

def test_orm_session_converters_carry_addressing_columns():
    root = uuid4()
    s = _session(agent_path="/root/member-a", root_session_id=root, parent_id=root)

    orm = OrmSession.from_pydantic(s)
    assert orm.agent_path == "/root/member-a"
    assert orm.root_session_id == root

    back = orm.to_pydantic()
    assert back.agent_path == "/root/member-a"
    assert back.root_session_id == root


def test_from_pydantic_populates_dict_so_merge_does_not_null_columns():
    """merge 只复制源对象 __dict__ 里有的属性；列没被写进去就谈不上保住。

    这是 PG 侧「update() 不清空新列」这个行为的直接前提，因此不依赖真库即可断言。
    """
    orm = OrmSession.from_pydantic(
        _session(agent_path="/root/m1", root_session_id=uuid4())
    )
    assert "agent_path" in orm.__dict__
    assert "root_session_id" in orm.__dict__


# ═══════════════════════════════════════════════════════════════
# get_by_path（InMemory 镜像）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_by_path_finds_child_and_is_scoped_to_root():
    repo = InMemorySessionRepo()
    root_id = uuid4()

    top = _session(agent_path="/root", root_session_id=root_id)
    child_a = _session(agent_path="/root/member-a", root_session_id=root_id, parent_id=root_id)
    child_b = _session(agent_path="/root/member-b", root_session_id=root_id, parent_id=root_id)
    # 另一棵树下同路径的会话，不能被抓出来
    other_tree = _session(agent_path="/root/member-a", root_session_id=uuid4())
    for s in (top, child_a, child_b, other_tree):
        await repo.create(s)

    got = await repo.get_by_path(root_id, "/root/member-a")
    assert got is not None and got.id == child_a.id

    assert (await repo.get_by_path(root_id, "/root")) is not None
    assert (await repo.get_by_path(uuid4(), "/root/member-a")) is None
    assert (await repo.get_by_path(root_id, "/root/member-zzz")) is None
