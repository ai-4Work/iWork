from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from server.config import settings
from server.engine.fork import FORK_NONE, fork_parent_history, normalize_fork_turns
from server.models.mail import agent_id_from_path
from server.models.message import MessageCreate
from server.models.session import Session
from server.observability.logging import get_logger

logger = get_logger("iwork.engine")


class TaskToolHandler:
    """Handles task tool execution for team lead agents.

    只做**派发**：路径 → 成员配置 → 子会话 → fork → 投递 → 立刻返回句柄（§9.11.8）。
    不回读子的产出——异步派发后父不阻塞，子的终态由子自己在
    `QueryLoopEngine._report_to_parent` 投进父的邮箱（设计决定 E）。
    父子之间的正式通信轴是 Session Mailbox（§9.11.7）；本文件只负责派发时刻的
    会话创建、上下文 fork 与子引擎接线。
    """

    def __init__(self, engine_manager, plugin_loader):
        self._mgr = engine_manager
        self._loader = plugin_loader

    async def execute(
        self,
        *,
        parent_session_id: UUID,
        parent_user_id: str,
        parent_agent_path: str,
        agent_path: str,
        prompt: str,
        team_plugin_path: str,
        team_members: list[dict],
        parent_chunk_queue: asyncio.Queue,
        mode: str = "spawn",
        fork_turns: str = "",
        lead_agent_id: str | None = None,
        parent_shell_env: str = "",
        parent_context_mgr=None,
        parent_root_session_id: UUID | None = None,
        parent_client_tools: list | None = None,
        parent_workspace: str = "",
        parent_model: str = "",
    ) -> dict:
        """派一个子任务：定位/新建子会话 → （spawn 时）fork 父上下文 → 投递 → 返回句柄。

        返回 {"success": True, "status": "dispatched", "task_id", "agent_path", "note"}；
        失败返回 {"success": False, "error", ...}，父据此**不**登记在途（否则会一直等
        一个永远不会来的结果）。并发超限也是失败句柄的一种（§9.12.5②）。
        """
        # 1. 路径 → 成员配置。展示层用扁平 id，路径本身不进展示（§9.11.13）
        member_id = agent_id_from_path(agent_path)
        member = next((m for m in team_members if m.get("id") == member_id), None)
        if not member:
            return {"success": False, "agent_path": agent_path,
                    "error": f"Agent '{agent_path}' not found in team members"}

        # 1.5 子 agent 并发闸门（§9.12.5②）。**满了就拒，不排队**：排队的子任务
        #     会挂在父的在途清单里，父一直等，而队列深度对用户不可见——现象只是
        #     "卡住"。拒绝至少立刻返回一句能读懂的话。
        #     位置在建子会话**之前**：成员路径非法已经在上一步挡掉，不该再白烧名额。
        if not self._mgr.try_acquire_child_slot():
            return {"success": False, "agent_path": agent_path,
                    "error": f"子 agent 并发已达上限 {settings.max_concurrent_children}，"
                             "请等已有子任务返回后再派"}

        root_session_id = parent_root_session_id or parent_session_id

        # 名额移交：**成功派发后由子自己归还**（子终态走 _report_to_parent），
        # 所以这里只在抛异常时兜底收回——否则一次失败就永久吃掉一个名额。
        try:
            # 2. 定位子会话。唯一约束 (root_session_id, agent_path) 决定了**一棵树里
            #    一个路径只有一个子会话**，所以 spawn 与 followup 的差别是"要不要重投
            #    上下文"，而不是"要不要新建"：spawn 在路径已存在时退化为复用，否则
            #    第二次派同一成员就会撞唯一索引（§9.11.6）。
            # client_tools 必须从父继承：工具是**同一个客户端**执行的，与"哪个 agent
            # 发出的请求"无关。不继承则子的上下文里只有 server tools，子想写文件/跑命令
            # 连工具都看不见，只能把整篇产物当纯文本吐回来（§9.11.5 同理适用于工具面）。
            # workspace / model 一起兜底：成员配置没写时用父的，否则空 workspace 会让
            # 权限降级（写操作被判越界）、空 model 会落到硬编码的兜底模型上。
            child_session = await self._mgr.session_repo.get_by_path(root_session_id, agent_path)
            if child_session is None:
                child_session = Session(
                    id=uuid4(),
                    user_id=parent_user_id,
                    title=f"[子任务] {member_id}",
                    mode="build",
                    scene_mode="office",
                    model=member.get("model", "") or parent_model,
                    workspace=member.get("workspace", "") or parent_workspace,
                    shell_env=parent_shell_env,
                    client_tools=list(parent_client_tools or []),
                    parent_id=parent_session_id,
                    agents=[],  # 子 agent 自己不能再派 task
                    agent_path=agent_path,
                    root_session_id=root_session_id,
                )
                await self._mgr.session_repo.create(child_session)

            # 3. 让子引擎的 _setup_expert_agent 加载 .md（子自己的 system prompt）
            child_engine = await self._mgr.get_or_create(child_session)
            child_engine._team_members = team_members
            child_engine._team_plugin_path = team_plugin_path
            child_engine._disable_task_tool = True

            # 4. fork 父历史：仅 spawn 且子历史为空时（§9.11.5）。子已有历史说明这是
            #    复用会话，再 fork 一次会把继承段叠两遍。
            if mode == "spawn" and parent_context_mgr is not None:
                if not await child_engine.context_mgr.list_rows(child_session.id):
                    resolved = normalize_fork_turns(
                        fork_turns, default=self._agent_fork_turns(team_plugin_path, member_id),
                    )
                    if resolved != FORK_NONE:
                        n = await fork_parent_history(
                            parent_context_mgr=parent_context_mgr,
                            parent_session_id=parent_session_id,
                            child_context_mgr=child_engine.context_mgr,
                            child_session_id=child_session.id,
                            fork_turns=resolved,
                        )
                        logger.info("task_fork", agent=agent_path, fork_turns=resolved, rows=n)

            # 5. 接线（§9.11.8）：展示中继的落点 + 回投结果的收件人。必须在 enqueue
            #    **之前**——子一起来就要用到它们。task_id 不走引擎字段，而是盖在子那条
            #    消息的 cid 上：followup 复用同一子会话时同一引擎会接连跑多次派发，
            #    引擎级字段会被后一次覆盖（§9.11.6）。
            task_id = str(uuid4())
            child_engine._parent_chunk_queue = parent_chunk_queue
            child_engine._parent_agent_path = parent_agent_path

            # 6. 投递 prompt，不等子回合结束
            child_msg = await child_engine.enqueue(parent_user_id, MessageCreate(
                content=prompt,
                scene_mode="office",
                workspace=child_session.workspace,
                model=child_session.model or "Claude Haiku 4.5",
                mode="build",
                agent_id=member_id,
                agent_type="expert",
                cid=task_id,
            ))
        except Exception:
            self._mgr.release_child_slot()
            raise

        logger.info("task_dispatched", agent=agent_path, task_id=task_id,
                    mode=mode, child_message_id=str(child_msg.id))

        return {
            "success": True,
            "status": "dispatched",
            "task_id": task_id,
            "agent_path": agent_path,
            "note": (
                "子任务已在后台执行，本工具**不等**它结束。子 agent 的产出稍后会以"
                "一条 user 消息送达（带 [子 agent ... 的产出] 前缀）。"
                "dispatched 是「已派发」而非失败，不要重复派发同一个任务。"
                "但**不必停下来等**：这一轮你可以继续推进与它无关的工作，也可以继续"
                "派发其它独立子任务（一轮可派多个）。只有确实没有可推进的事情时，才"
                "结束本轮——子结果回来会自动唤醒你，在同一条消息上接着继续。"
            ),
        }

    def _agent_fork_turns(self, team_plugin_path: str, member_id: str) -> str:
        """agent `.md` frontmatter 里的 fork_turns（空 = 交给全局默认）。"""
        if not (self._loader and team_plugin_path):
            return ""
        try:
            return self._loader.load_agent_md(team_plugin_path, member_id).fork_turns or ""
        except FileNotFoundError:
            return ""

