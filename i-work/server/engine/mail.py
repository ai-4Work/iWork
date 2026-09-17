"""MailRouter —— Session Mailbox 的路由 + 门（ch9 §9.11.7）。

发信三步：**过门 → 寻址 → 投递**。

- 门把"父-子互发、不能子-子"从软约束变硬约束，写死在这一处；
- 寻址按 `(root_session_id, agent_path)` 直接查库（§9.11.3），所以发信方只需
  知道**路径**，不必持有对方 session/引擎对象；
- 投递分两条路：可跑的（task/followup）走 `enqueue()`，吃它的幂等 + 队列上限 +
  唤醒；不可跑的（result/status）走 `deliver_result()`，它们不是待跑回合。
"""
from __future__ import annotations

import json
from uuid import UUID

from server.engine.query_loop import QueueFullError
from server.models.mail import (
    MSG_RESULT,
    MSG_STATUS,
    RESULT_MSG_TYPES,
    TRIGGER_TURN,
    MailMessage,
)
from server.models.message import Message, MessageCreate, MessageStatus
from server.models.session import Session


class MailRejected(Exception):
    """投递被拒。`reason` ∈ gate | unresolved | queue_full | unknown_type。

    一律"拒绝"而非"排队"（对齐 Codex 的超限语义）：让父 agent 立刻看到一条明确的
    失败回执，而不是默默把它塞进一个可能永远不排到的队。
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(f"{reason}{': ' + detail if detail else ''}")


def _parent_path(path: str) -> str:
    """去掉最后一段。`/root` 的父是 ""（空串，任何路径都不等于它）。"""
    path = path.rstrip("/")
    if "/" not in path[1:]:
        return ""
    return path.rsplit("/", 1)[0]


def segment_gate(sender_path: str, recipient_path: str) -> bool:
    """父子门：recipient 是 sender 的**直接子**或**直接父**才放行（§9.11.7）。

    判定只看**路径段数 + 前缀**，不做 session-id 比较——今天两层下两者等价，但
    路径版自动支持多层：将来放开"成员可再派生"，这个门不用改。
    兄弟（同父互不为父子）与发给自己一律拒绝；`_parent_path` 严格缩短路径，
    所以"父等于自己"不可能成立，无需额外自反判断。
    """
    return (
        _parent_path(recipient_path) == sender_path       # 直接子
        or _parent_path(sender_path) == recipient_path    # 直接父
    )


class MailRouter:
    """挂在 EngineManager 上的会话间投递器。"""

    def __init__(self, manager):
        self._manager = manager

    async def _resolve(self, root_session_id: UUID, agent_path: str) -> Session:
        session = await self._manager.session_repo.get_by_path(root_session_id, agent_path)
        if session is None:
            raise MailRejected(
                "unresolved",
                f"agent_path={agent_path!r} root={root_session_id} 无对应会话",
            )
        return session

    async def send(
        self,
        *,
        sender_path: str,
        root_session_id: UUID,
        recipient_path: str,
        msg_type: str,
        payload: dict,
        cid: UUID | None = None,
    ) -> Message:
        """投一封信，返回落库后的信封行。被拒时抛 MailRejected。

        `root_session_id` 取**发件人会话的** root（顶层会话就是它自己），因为同一棵
        树里父子的 root 相同——这是父亲找到孩子的定位键。
        """
        if msg_type not in TRIGGER_TURN:
            raise MailRejected("unknown_type", msg_type)
        if not segment_gate(sender_path, recipient_path):
            raise MailRejected(
                "gate", f"{sender_path!r} 不能直投 {recipient_path!r}（仅限直系父子）"
            )

        recipient = await self._resolve(root_session_id, recipient_path)

        envelope = MailMessage(
            sender=sender_path,
            recipient=recipient_path,
            type=msg_type,
            payload=payload,
            cid=cid,
            trigger_turn=TRIGGER_TURN[msg_type],
        )

        if msg_type in RESULT_MSG_TYPES:
            return await self._deliver_result(recipient, envelope)

        # cid 是"每次 task 调用新 mint 的 task_id"，既当关联 id 又当幂等键：
        # 同一次派发重复投递会被 enqueue 的去重吞掉，第二次 followup 不会误继承。
        client_message_id = f"env:{cid}" if cid else None
        engine = await self._manager.get_or_create(recipient)
        data = MessageCreate(
            content=json.dumps(payload, ensure_ascii=False, default=str),
            scene_mode=recipient.scene_mode,
            workspace=recipient.workspace,
            model=recipient.model,
            mode=recipient.mode,
            client_message_id=client_message_id,
            sender_agent_id=sender_path,
            recipient_agent_id=recipient_path,
            msg_type=msg_type,
            cid=str(cid) if cid else None,
        )
        try:
            return await engine.enqueue(recipient.user_id, data)
        except QueueFullError as e:
            raise MailRejected("queue_full", str(e))

    async def _deliver_result(self, recipient: Session, envelope: MailMessage) -> Message:
        """结果信封进收件人邮箱：不走 enqueue（不占队列上限、不唤醒 run()）。"""
        msg = Message(
            session_id=recipient.id,
            user_id=recipient.user_id,
            # 正文即 payload 的 JSON；取信方 json.loads 还原
            content=json.dumps(envelope.payload, ensure_ascii=False, default=str),
            scene_mode=recipient.scene_mode,
            workspace=recipient.workspace,
            model=recipient.model,
            mode=recipient.mode,
            client_message_id=f"res:{envelope.cid}" if envelope.cid else None,
            sender_agent_id=envelope.sender,
            recipient_agent_id=envelope.recipient,
            msg_type=envelope.type,
            cid=str(envelope.cid) if envelope.cid else None,
            status=MessageStatus.PENDING,
        )
        engine = await self._manager.get_or_create(recipient)
        return await engine.deliver_result(msg)
