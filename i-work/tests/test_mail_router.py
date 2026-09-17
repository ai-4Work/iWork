"""Stage 4 · MailRouter 与父子路径门（§9.11.12 #4）。

门是这次改造里唯一的**安全边界**：它把"父-子互发、不能子-子"从软约束变硬约束。
判定只看路径段数 + 前缀，所以除了两层，还专门用**合成的三段路径**证明它与深度无关。
"""
import asyncio
import json
from uuid import uuid4

import pytest

from server.config import settings
from server.engine.mail import MailRejected, segment_gate
from server.models.mail import MSG_FOLLOWUP, MSG_RESULT, MSG_STATUS, MSG_TASK
from server.models.session import Session


async def _mk_session(repo, *, user_id, agent_path, root_session_id, parent_id=None):
    s = Session(
        user_id=user_id, mode="ask", workspace="/tmp/test",
        model="claude-sonnet-4-6", agent_path=agent_path,
        root_session_id=root_session_id, parent_id=parent_id,
    )
    await repo.create(s)
    return s


async def _team(session_repo):
    """lead 会话（/root，自己就是树根）+ 一个子会话（/root/m1）。"""
    lead = await _mk_session(session_repo, user_id="lead", agent_path="/root",
                             root_session_id=None)
    lead.root_session_id = lead.id
    await session_repo.update(lead)
    child = await _mk_session(session_repo, user_id="child", agent_path="/root/m1",
                              root_session_id=lead.id, parent_id=lead.id)
    return lead, child


async def _freeze(engine_manager, session):
    """摘掉引擎的 run() 协程，让投递结果不被后台消费者吃掉（测试确定性）。"""
    engine = await engine_manager.get_or_create(session)
    engine._run_task.cancel()
    await asyncio.sleep(0)
    return engine


# ═══════════════════════════════════════════════════════════════
# 门
# ═══════════════════════════════════════════════════════════════

def test_gate_allows_direct_child_and_parent():
    assert segment_gate("/root", "/root/m1") is True          # 父 → 直接子
    assert segment_gate("/root/m1", "/root") is True          # 直接子 → 父


def test_gate_rejects_siblings_and_self():
    assert segment_gate("/root/m1", "/root/m2") is False      # 兄弟
    assert segment_gate("/root/m1", "/root/m1") is False      # 自己
    assert segment_gate("/root", "/root") is False


def test_gate_is_depth_agnostic():
    """合成三段路径：同一套判定在更深一层同样成立。"""
    assert segment_gate("/root/a/b", "/root/a/b/c") is True   # 直接子
    assert segment_gate("/root/a/b/c", "/root/a/b") is True   # 直接父
    assert segment_gate("/root/a/b", "/root/a") is True       # 直接父（少一段就是父）
    assert segment_gate("/root/a/b", "/root") is False        # 隔代（祖父）不放行
    assert segment_gate("/root/a/b/c", "/root/a") is False    # 隔代（往上两跳）
    assert segment_gate("/root/a/b", "/root/a/c") is False    # 堂兄弟
    assert segment_gate("/root/a/b", "/root/c/d") is False    # 异支


# ═══════════════════════════════════════════════════════════════
# 投递：task / followup（可跑）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_send_task_lands_in_child_queue_as_envelope(engine_manager, session_repo,
                                                          message_repo):
    lead, child = await _team(session_repo)
    await _freeze(engine_manager, child)

    cid = uuid4()
    msg = await engine_manager.mail.send(
        sender_path="/root", root_session_id=lead.id, recipient_path="/root/m1",
        msg_type=MSG_TASK, payload={"prompt": "写一个快排"}, cid=cid,
    )

    assert msg.session_id == child.id
    assert msg.msg_type == MSG_TASK
    assert msg.sender_agent_id == "/root"
    assert msg.recipient_agent_id == "/root/m1"
    assert msg.cid == str(cid)
    # payload 走 content 的 JSON 编码，取信方 json.loads 还原
    assert json.loads(msg.content) == {"prompt": "写一个快排"}
    # 可跑类型：确实排进了子的队列
    assert [m.id for m in await message_repo.list_pending(child.id)] == [msg.id]


@pytest.mark.asyncio
async def test_send_followup_is_runnable_too(engine_manager, session_repo, message_repo):
    lead, child = await _team(session_repo)
    await _freeze(engine_manager, child)

    msg = await engine_manager.mail.send(
        sender_path="/root", root_session_id=lead.id, recipient_path="/root/m1",
        msg_type=MSG_FOLLOWUP, payload={"prompt": "改成迭代版"}, cid=uuid4(),
    )
    assert msg.msg_type == MSG_FOLLOWUP
    assert await message_repo.count_pending(child.id) == 1
    assert [m.id for m in await message_repo.list_pending(child.id)] == [msg.id]


@pytest.mark.asyncio
async def test_task_delivery_is_idempotent_on_same_cid(engine_manager, session_repo,
                                                       message_repo):
    lead, child = await _team(session_repo)
    await _freeze(engine_manager, child)

    cid = uuid4()
    first = await engine_manager.mail.send(
        sender_path="/root", root_session_id=lead.id, recipient_path="/root/m1",
        msg_type=MSG_TASK, payload={"prompt": "x"}, cid=cid,
    )
    again = await engine_manager.mail.send(
        sender_path="/root", root_session_id=lead.id, recipient_path="/root/m1",
        msg_type=MSG_TASK, payload={"prompt": "x"}, cid=cid,
    )
    assert again.id == first.id
    assert len(await message_repo.list_pending(child.id)) == 1


# ═══════════════════════════════════════════════════════════════
# 投递：result / status（不可跑，进邮箱不进队列）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_send_result_lands_in_parent_mailbox_not_queue(engine_manager, session_repo,
                                                             message_repo):
    lead, child = await _team(session_repo)
    parent_engine = await _freeze(engine_manager, lead)
    assert parent_engine._children_event.is_set() is False

    cid = uuid4()
    msg = await engine_manager.mail.send(
        sender_path="/root/m1", root_session_id=lead.id, recipient_path="/root",
        msg_type=MSG_RESULT, payload={"answer": "done"}, cid=cid,
    )

    # 邮箱可见、队列不可见、不占 max_queue_size 预算
    assert [m.id for m in await message_repo.list_pending_results(lead.id, "/root")] == [msg.id]
    assert await message_repo.list_pending(lead.id) == []
    assert await message_repo.count_pending(lead.id) == 0
    assert json.loads(msg.content) == {"answer": "done"}
    # 投递同时唤醒了挂起等子的那次调用
    assert parent_engine._children_event.is_set() is True


@pytest.mark.asyncio
async def test_result_delivery_is_idempotent_on_same_cid(engine_manager, session_repo,
                                                         message_repo):
    lead, child = await _team(session_repo)
    await _freeze(engine_manager, lead)
    await _freeze(engine_manager, child)

    cid = uuid4()
    first = await engine_manager.mail.send(
        sender_path="/root/m1", root_session_id=lead.id, recipient_path="/root",
        msg_type=MSG_RESULT, payload={"answer": "a"}, cid=cid,
    )
    again = await engine_manager.mail.send(
        sender_path="/root/m1", root_session_id=lead.id, recipient_path="/root",
        msg_type=MSG_RESULT, payload={"answer": "a"}, cid=cid,
    )
    assert again.id == first.id
    assert len(await message_repo.list_pending_results(lead.id, "/root")) == 1


@pytest.mark.asyncio
async def test_status_envelope_also_goes_to_mailbox(engine_manager, session_repo,
                                                    message_repo):
    lead, child = await _team(session_repo)
    await _freeze(engine_manager, lead)

    msg = await engine_manager.mail.send(
        sender_path="/root/m1", root_session_id=lead.id, recipient_path="/root",
        msg_type=MSG_STATUS, payload={"phase": "searching"}, cid=uuid4(),
    )
    assert msg.msg_type == MSG_STATUS
    assert await message_repo.count_pending(lead.id) == 0


# ═══════════════════════════════════════════════════════════════
# 拒绝路径
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sibling_send_is_rejected_and_writes_nothing(engine_manager, session_repo,
                                                           message_repo):
    lead, child = await _team(session_repo)
    sibling = await _mk_session(session_repo, user_id="sib", agent_path="/root/m2",
                                root_session_id=lead.id, parent_id=lead.id)

    with pytest.raises(MailRejected) as ei:
        await engine_manager.mail.send(
            sender_path="/root/m1", root_session_id=lead.id,
            recipient_path="/root/m2", msg_type=MSG_TASK, payload={}, cid=uuid4(),
        )
    assert ei.value.reason == "gate"
    # 门是硬边界：被拒的信一封都不许落库
    assert await message_repo.list_pending(sibling.id) == []


@pytest.mark.asyncio
async def test_unresolved_recipient_is_rejected(engine_manager, session_repo):
    lead, _ = await _team(session_repo)
    with pytest.raises(MailRejected) as ei:
        await engine_manager.mail.send(
            sender_path="/root", root_session_id=lead.id,
            recipient_path="/root/nobody", msg_type=MSG_TASK, payload={}, cid=uuid4(),
        )
    assert ei.value.reason == "unresolved"


@pytest.mark.asyncio
async def test_unknown_msg_type_is_rejected(engine_manager, session_repo):
    lead, _ = await _team(session_repo)
    with pytest.raises(MailRejected) as ei:
        await engine_manager.mail.send(
            sender_path="/root", root_session_id=lead.id,
            recipient_path="/root/m1", msg_type="gossip", payload={}, cid=uuid4(),
        )
    assert ei.value.reason == "unknown_type"


@pytest.mark.asyncio
async def test_queue_full_rejects_instead_of_queueing(engine_manager, session_repo,
                                                      monkeypatch):
    """超限一律拒绝，不排队（对齐 Codex 语义）。"""
    monkeypatch.setattr(settings, "max_queue_size", 2)
    lead, child = await _team(session_repo)
    await _freeze(engine_manager, child)

    for _ in range(2):
        await engine_manager.mail.send(
            sender_path="/root", root_session_id=lead.id, recipient_path="/root/m1",
            msg_type=MSG_TASK, payload={"prompt": "x"}, cid=uuid4(),
        )
    with pytest.raises(MailRejected) as ei:
        await engine_manager.mail.send(
            sender_path="/root", root_session_id=lead.id, recipient_path="/root/m1",
            msg_type=MSG_TASK, payload={"prompt": "overflow"}, cid=uuid4(),
        )
    assert ei.value.reason == "queue_full"
