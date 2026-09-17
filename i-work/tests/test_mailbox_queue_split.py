"""Stage 3 · 邮箱列与队列按类型分流（§9.11.12 #2 / #3 / #11）。

核心不变量：**信封（result/status）与队列消息（user/task/followup）是两张互斥视图**。
信封若漏进队列视图，最坏情况是吃掉 max_queue_size 预算，把合法用户消息挤成 429；
反过来若队列消息漏进邮箱视图，父会把子派给自己的任务当成子结果消费掉。
四个仓储方法 + 一个新方法的过滤器必须完全一致，故这里逐个钉。
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from server.db.models import OrmMessage
from server.models.mail import (
    MSG_FOLLOWUP,
    MSG_RESULT,
    MSG_STATUS,
    MSG_TASK,
    agent_id_from_path,
    agent_path_of,
)
from server.models.message import Message, MessageStatus
from server.storage.memory import InMemoryMessageRepo


async def _mk(repo, session_id, *, msg_type, content="c", recipient=None,
              sender=None, cid=None, created_at=None):
    m = Message(
        session_id=session_id,
        user_id="u1",
        content=content,
        scene_mode="office",
        workspace="/tmp/test",
        model="claude-sonnet-4-6",
        mode="ask",
        msg_type=msg_type,
        recipient_agent_id=recipient,
        sender_agent_id=sender,
        cid=cid,
    )
    if created_at is not None:
        m.created_at = created_at
    return await repo.create(m)


# ═══════════════════════════════════════════════════════════════
# 路径 helpers
# ═══════════════════════════════════════════════════════════════

def test_agent_path_helpers_round_trip():
    assert agent_path_of("member-a") == "/root/member-a"
    assert agent_id_from_path("/root/member-a") == "member-a"
    # 路径进不了展示层：展示用的扁平 id 拿回来就是成员 id 本身
    assert agent_id_from_path(agent_path_of("writer")) == "writer"
    assert agent_id_from_path("/root/") == "root"


# ═══════════════════════════════════════════════════════════════
# 信封不出现在队列视图
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_result_envelope_invisible_to_queue_views():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    me = "/root"

    await _mk(repo, sid, msg_type="user", content="hi")
    env = await _mk(repo, sid, msg_type=MSG_RESULT, content='{"answer":"done"}',
                    recipient=me, sender="/root/member-a", cid=str(uuid4()))

    # 队列视图：只有用户消息
    pending = await repo.list_pending(sid)
    assert len(pending) == 1 and pending[0].msg_type == "user"
    assert env.id not in {m.id for m in pending}
    assert await repo.count_pending(sid) == 1

    # 邮箱视图：只有收件人是我的结果信封
    inbox = await repo.list_pending_results(sid, me)
    assert [m.id for m in inbox] == [env.id]
    # 发给别人的信封不进我的邮箱
    assert await repo.list_pending_results(sid, "/root/member-b") == []


@pytest.mark.asyncio
async def test_envelope_never_dequeued_and_gets_no_queue_position():
    repo = InMemoryMessageRepo()
    sid = uuid4()

    # 先塞两个信封，再塞两条用户消息：信封排在最前，若不过滤会被第一个出队
    await _mk(repo, sid, msg_type=MSG_RESULT, recipient="/root", sender="/root/m1")
    await _mk(repo, sid, msg_type=MSG_STATUS, recipient="/root", sender="/root/m1")
    u1 = await _mk(repo, sid, msg_type="user", content="first")
    u2 = await _mk(repo, sid, msg_type="user", content="second")

    got = await repo.dequeue_next(sid)
    assert got is not None and got.id == u1.id, "首批出队的必须是用户消息而非信封"

    # 重排只数可跑类型 → 剩下的用户消息是 1，信封拿不到排位
    pending = await repo.list_pending(sid)
    assert [(m.id, m.queue_position) for m in pending] == [(u2.id, 1)]
    envelopes = [
        m for m in repo._messages.values()
        if m.session_id == sid and m.msg_type in (MSG_RESULT, MSG_STATUS)
    ]
    assert len(envelopes) == 2
    assert all(e.queue_position is None for e in envelopes)


@pytest.mark.asyncio
async def test_task_and_followup_are_runnable_like_user():
    """父→子的信封要能唤醒子开新回合，所以必须和 user 一样可出队。"""
    repo = InMemoryMessageRepo()
    sid = uuid4()
    t = await _mk(repo, sid, msg_type=MSG_TASK, recipient="/root/m1", sender="/root")
    f = await _mk(repo, sid, msg_type=MSG_FOLLOWUP, recipient="/root/m1", sender="/root")

    assert {m.id for m in await repo.list_pending(sid)} == {t.id, f.id}
    assert await repo.count_pending(sid) == 2
    assert (await repo.dequeue_next(sid)).id == t.id
    assert (await repo.dequeue_next(sid)).id == f.id
    assert await repo.dequeue_next(sid) is None


@pytest.mark.asyncio
async def test_inbox_is_ordered_by_arrival():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    base = datetime.now(timezone.utc)
    old = await _mk(repo, sid, msg_type=MSG_RESULT, recipient="/root",
                    sender="/root/m1", cid="c-old",
                    created_at=base - timedelta(minutes=5))
    new = await _mk(repo, sid, msg_type=MSG_RESULT, recipient="/root",
                    sender="/root/m2", cid="c-new", created_at=base)

    assert [m.id for m in await repo.list_pending_results(sid, "/root")] == [old.id, new.id]


# ═══════════════════════════════════════════════════════════════
# 信封一旦被消费（离开 pending）就不再出现在邮箱里
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_consumed_envelope_leaves_inbox():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    env = await _mk(repo, sid, msg_type=MSG_RESULT, recipient="/root",
                    sender="/root/m1", cid="c1")
    assert len(await repo.list_pending_results(sid, "/root")) == 1

    env.status = MessageStatus.COMPLETED
    await repo.update(env)
    assert await repo.list_pending_results(sid, "/root") == []


# ═══════════════════════════════════════════════════════════════
# 转换器 round-trip（PgMessageRepo.update 也走 from_pydantic + merge）
# ═══════════════════════════════════════════════════════════════

def test_orm_message_converters_carry_mailbox_columns():
    cid = str(uuid4())
    m = Message(
        session_id=uuid4(), user_id=str(uuid4()), content='{"a":1}',
        scene_mode="office", workspace="/tmp/w", model="claude-sonnet-4-6",
        mode="ask", msg_type=MSG_RESULT,
        sender_agent_id="/root/m1", recipient_agent_id="/root", cid=cid,
    )

    orm = OrmMessage.from_pydantic(m)
    assert (orm.sender_agent_id, orm.recipient_agent_id, orm.msg_type, orm.cid) == (
        "/root/m1", "/root", MSG_RESULT, cid,
    )

    back = orm.to_pydantic()
    assert (back.sender_agent_id, back.recipient_agent_id, back.msg_type, back.cid) == (
        "/root/m1", "/root", MSG_RESULT, cid,
    )
    # merge 只复制 __dict__ 里有的属性 —— 这是"update() 不清列"的前提
    for col in ("sender_agent_id", "recipient_agent_id", "msg_type", "cid"):
        assert col in orm.__dict__


def test_msg_type_defaults_to_user_for_client_messages():
    """客户端入队路径不传 msg_type，必须仍是 user（否则历史过滤语义会漂）。"""
    m = Message(
        session_id=uuid4(), user_id=str(uuid4()), content="hi",
        scene_mode="office", workspace="/tmp/w", model="m", mode="ask",
    )
    assert m.msg_type == "user"
