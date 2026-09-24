"""
QueryLoopEngine — iWork 服务端核心引擎。

会话级长生命周期协程：FIFO 消息队列 + 逐消息 per-message turn 循环。
支持 Ask / Plan / Build 三种模式。

状态机: IDLE → PROCESSING ↔ WAITING_SYNC → IDLE
              ↘ WAITING_CHILDREN ↗   （异步派发后父挂起等子回信，§9.11.8）
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from opentelemetry import trace

from server.config import settings
from server.models.session import Session
from server.models.message import Message, MessageCreate, MessageStatus
from server.models.tool_invocation import (
    ToolInvocation, InvocationState, normalize_client_result,
)
from server.storage.base import SessionRepository, MessageRepository, ToolInvocationRepository
from server.sync_waiter import SyncWaiter, user_wait_key, tool_wait_key
from server.engine.stream_buffer import StreamBuffer
from server.engine.context import ContextManager
from server.llm.client import LLMClient, LLMChunk
from server.llm.resolver import StaticModelResolver
from server.tools.dispatcher import ToolDispatcher, ToolLocation, SERVER_BUILTIN_TOOLS
from server.tools.idempotency import (
    tool_idempotency as _tool_idempotency,      # C-3 幂等档
    tool_side_effect as _tool_side_effect,      # D 副作用账本筛子（read-only 永不落账）
)
from server.tools.permission import PermissionEngine
from server.tools.scheduling import READ, SchedulingPolicy, segment_tool_calls
from server.skills.skill_registry import SKILL_TOOL_DEFINITION
from server.plugins.loader import PluginLoader
from server.engine.task_handler import TaskToolHandler
from server.models.mail import (
    MSG_RESULT,
    NO_OUTPUT_PLACEHOLDER,
    agent_id_from_path,
    agent_path_of,
)
from server.engine.token_counter import TokenCounter
from server.engine.context_compressor import ContextCompressor, CompressionConfig
from server.engine.offload import OffloadStore
from server.engine.tfidf import TFIDFRetriever
from server.hooks.chain import HookManager, HookAction
from server.hooks.config import load_hooks_config
from server.observability import (
    HookBlockedError, ToolBlockedError, LLMBlockedError, MessageBeforeBlockedError,
    get_diagnosis,
)
from server.observability.logging import get_logger
from server.observability.events import AgentEvent, AgentEventType, now as event_now
from server.observability.event_bus import EventBus
from server.observability.metrics import (
    llm_call_duration, llm_token_usage, llm_cost,
    tool_call_total, tool_call_duration, tool_permission_denied_total,
    message_total, message_duration, message_turns, queue_depth,
)

tracer = trace.get_tracer("iwork.engine")
logger = get_logger("iwork.engine")

# LLM 输出被 max_tokens 截断后，注入上下文触发续写的提示（role=user）
_TRUNCATION_CONTINUE_PROMPT = (
    "[系统提示] 你上一条回复因长度上限被截断。"
    "请直接从中断处继续输出剩余内容，不要重复已输出的部分，也不要重新开始。"
)

# 派发出去的子 agent 的交付物约定（§9.11.9「位置合同」，取代 §9.4.2 的 <final_output>
# 标签方案）。只约束**位置**、不约束格式：父侧取结果就是照单撕下最后一页
# （`_last_assistant_text` 取末条非空 assistant 文本），所以必须让模型知道末条
# 就是交付物。不加这句，末条只是"模型碰巧留在最后的那段"，框架无从判断。
_CHILD_DELIVERABLE_CONTRACT = (
    "【交付约定】你的最后一次回复必须包含本次任务的交付物；"
    "不要在交付之后再补充收尾内容。"
    "若交付物已写入文件，最后一次回复仍需给出可读的成果要点"
    "（写到哪里、包含什么、关键结论）。"
)

# 子的展示 chunk 中继到父流的白名单（§9.11.7）。**message.complete / message.error
# 绝不在此列**：父的 NDJSON 以此 break，转发一条就等于提前关掉父的流。
# reconcile 两类事件都放行，但理由不同（都非终态，转发安全）：
#   - client.tool_reconcile 是子一侧的**唯一**出路：客户端应答才结束 sync_waiter.wait，
#     答不上来子只能干等到超时（§9.11.7）；
#   - tool.reconcile_needs_confirm 在等**结束之后**才推（见 _reconcile_outcome），
#     纯提示性质——放行只是为了让子那一列也能显示"这次写可能已生效"。
_RELAY_TYPES = (
    "agent.thinking", "agent.text", "agent.status",
    "client.tool_request", "client.tool_reconcile", "tool.reconcile_needs_confirm",
    "queue.enqueued",
)


def _is_genuine_tool_result(body: object) -> bool:
    """真实工具结果（非 reconcile 应答 / 非 skip 标记）→ 允许收口为 completed。"""
    return isinstance(body, dict) and "reconcile" not in body and "skipped" not in body


@dataclass
class ToolOutcome:
    """一次工具调用的产物：先执行拿到结果，再按模型发出顺序收口（写历史 / 记账）。

    执行与收口分离是为了同轮并发：读段内多个调用并发执行，但写历史必须按模型
    发出顺序单线程做——历史拼接（sequence 分配、tool_calls 数组合并）都是读-改-写。
    """
    chunk: LLMChunk
    kind: str                   # executed | task | hook_blocked | forbidden
    tool_name: str
    tc_id: str
    tool_input: dict
    result: dict
    turn: int = 0
    step: int | None = None
    location: str = ToolLocation.CLIENT.value
    duration_ms: int = 0
    abort: bool = False         # 调用方据此终止本轮（build 被拒 / 用户取消）
    reason: str = ""            # forbidden 的原因


@dataclass
class _PendingTool:
    """已发起、还没等到结果的工具调用（`_dispatch_tool` 的产物）。

    读段靠它把"下发"和"等回投"拆成两拍：先把整段的 client.tool_request 推完，
    再并发等回投——否则每个调用各自"推一条等一条"，前端拿不到并发窗口。
    """
    chunk: LLMChunk
    tool_name: str
    tc_id: str
    tool_input: dict
    turn: int
    step: int | None
    location: str
    start: float
    invocation_id: str = ""
    request_id: str = ""
    requires_approval: bool = False


# ═══════════════════════════════════════════════════════════════
# C-3 幂等档分类已上收 server/tools/idempotency.py（query_loop 顶部 import
# tool_idempotency as _tool_idempotency），此处不再重复维护白名单。
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 服务端内置工具定义（直接读写 DB）
# ═══════════════════════════════════════════════════════════════

# 10.5.3 外部记忆召回工具（服务端，TF-IDF 检索卸载块）
RECALL_TOOLS = [
    {
        "name": "recall",
        "description": (
            "从外部记忆召回此前因上下文压缩而被卸载的信息块。"
            "当需要用到早期对话中已被移出窗口的细节时调用此工具检索。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或问题，如 'top50 SQL 的最终版本'"},
                "top_k": {"type": "integer", "description": "返回条数，默认 5"}
            },
            "required": ["query"]
        }
    },
]

# 记忆模块 L1 主动检索工具（服务端，查 l1_memories）
MEMORY_SEARCH_TOOLS = [
    {
        "name": "memory_search",
        "description": (
            "搜索长期记忆（用户偏好、历史事件、长期规则等结构化原子记忆）。"
            "当上方注入的 <relevant-memories> 不足以回答用户问题时调用。"
            "每轮对话最多调用 3 次。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或问题"},
                "top_k": {"type": "integer", "description": "返回条数，默认 5"},
            },
            "required": ["query"],
        },
    },
]

# 记忆模块 L2 主动读取工具（服务端，查 l2_scenes）
SCENE_READ_TOOLS = [
    {
        "name": "scene_read",
        "description": (
            "按场景名读取完整场景正文（跨会话整合出的历史情境叙事）。"
            "当上方 <scene-navigation> 里的摘要不足以回答用户问题时调用。"
            "场景名必须取自导航列出的名字。每轮对话最多调用 3 次。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "场景名（取自场景导航）"},
            },
            "required": ["name"],
        },
    },
]


class QueueFullError(Exception):
    def __init__(self, max_size: int):
        self.max_size = max_size
        super().__init__(f"队列已满（最多 {max_size} 条）")


class EngineManager:
    """会话级 QueryLoopEngine 的工厂 + 注册中心。"""

    def __init__(
        self,
        session_repo: SessionRepository,
        message_repo: MessageRepository,
        llm_client: LLMClient,
        session_factory=None,  # async_sessionmaker | None
        skill_registry=None,  # SkillRegistry | None
        user_mcp_repo=None,  # UserMcpRepo | None
        tool_invocation_repo=None,  # ToolInvocationRepository | None（C-1 账本）
        event_bus: EventBus | None = None,
        memory_recall=None,  # L1RecallService | None — L1 记忆召回（未启用时为 None）
        scene_recall=None,  # L2RecallService | None — L2 场景召回（未启用时为 None）
        persona_recall=None,  # L3RecallService | None — L3 画像召回（未启用时为 None）
        resolver=None,  # ModelResolver | None — 不传则退化成"单客户端"
    ):
        from server.storage.postgres import ConversationHistoryRepo, ExpertHubRepo, TeamHubRepo

        self.session_repo = session_repo
        self.message_repo = message_repo
        self.llm_client = llm_client
        # 模型解析器是引擎侧取客户端的唯一入口（主调用、上下文压缩、便宜模型档）。
        # 传了真解析器就按 `llm_model` 表解析；没传（单测直接构造）就退化成把
        # `llm_client` 用在所有模型上 —— 调用点不必分叉，行为与本类改造前一致。
        self.resolver = resolver or StaticModelResolver(llm_client)
        self.session_factory = session_factory
        self.skill_registry = skill_registry
        self.user_mcp_repo = user_mcp_repo
        self.event_bus = event_bus
        self.memory_recall = memory_recall
        self.scene_recall = scene_recall
        self.persona_recall = persona_recall
        self._engines: dict[UUID, "QueryLoopEngine"] = {}

        # 每个 EngineManager 实例一个 ConversationHistoryRepo
        self._conv_repo = ConversationHistoryRepo(session_factory) if session_factory else None

        # C-1 工具账本：无外部注入时按有无 DB 自动选实现
        if tool_invocation_repo is not None:
            self._invocation_repo = tool_invocation_repo
        elif session_factory is not None:
            from server.storage.postgres import ToolInvocationRepo
            self._invocation_repo = ToolInvocationRepo(session_factory)
        else:
            from server.storage.memory import InMemoryToolInvocationRepo
            self._invocation_repo = InMemoryToolInvocationRepo()

        # 10.5.3 卸载块存储 + TF-IDF 召回（无 session_factory 时为 None，测试走 InMemory）
        if session_factory is not None:
            from server.storage.postgres import OffloadedBlocksRepo
            offload_repo = OffloadedBlocksRepo(session_factory)
            self._offload_store = OffloadStore(offload_repo, TFIDFRetriever())
        else:
            self._offload_store = None

        # Agent 相关 repos + loader
        self._expert_repo = ExpertHubRepo(session_factory) if session_factory else None
        self._team_repo = TeamHubRepo(session_factory) if session_factory else None
        self._plugin_loader = PluginLoader()

        # 会话间投递（§9.11.7）。局部导入：mail 模块顶层 import 本模块的 QueueFullError，
        # 放到这里导入可避免环形依赖（此刻本模块已加载完毕）。
        from server.engine.mail import MailRouter
        self.mail = MailRouter(self)

        # 层2 工作区写锁注册表（§9.12.2）：key = 工作区字符串，value = 该工作区共享的
        # 一把 asyncio.Lock。**进程本地**——多副本下各进程各有一把、互相看不见
        # （要真支持跨进程得先做 session→进程亲和，见 §9.12.7；本轮单进程）。
        self._workspace_locks: dict[str, asyncio.Lock] = {}

        # 在跑的子 agent 计数（§9.12.5②）。用**计数器**而不是 asyncio.Semaphore：
        # 超限要"拒绝"而不是"排队"，而 `locked()` 判定 + `await acquire()` 之间有
        # 一个 check-then-act 窗口，两个并发 task 能同时挤过闸门。计数器是同步
        # 读写，单事件循环下没有这个窗口，语义也正好是"满了就拒"。
        self._children_running = 0

    def try_acquire_child_slot(self) -> bool:
        """占一个子 agent 名额；已满返回 False（调用方据此返回失败句柄）。"""
        if self._children_running >= settings.max_concurrent_children:
            return False
        self._children_running += 1
        return True

    def release_child_slot(self) -> None:
        """子 agent 终态时归还名额。由子引擎的 `_report_to_parent` 调用——
        子自己才知道它什么时候结束，父那边只等结果、不掌管生命周期。"""
        if self._children_running > 0:
            self._children_running -= 1

    def _workspace_lock_for(self, session: Session) -> asyncio.Lock:
        """取会话工作区对应的那把锁（没有就建一个）。

        **必须保持同步**：调用点在 get_or_create 的无 await 临界区里，一旦这里出现
        await，"同 session 双引擎双 run()"的窗口就打开了（§9.12.9 #8）。`asyncio.Lock()`
        的构造是同步的，所以这条约束成立。

        空工作区用**会话 id 当哨兵**而不是共用空串 key：`""` 的语义是"没指定工作区"，
        不是一个叫空字符串的目录。归成一把锁会让所有空工作区会话无谓地互相排队
        （子 session 默认就是 `""`，见 §9.12.7 边界 4）。非空 key 只做字符串级匹配
        （`D:\\proj` 与 `d:\\proj\\` 会算成两把锁）——服务端 realpath 不了一台它看不见的
        机器上的路径，这层归一化只能靠客户端。
        """
        workspace = (getattr(session, "workspace", "") or "").strip()
        key = f"ws:{workspace}" if workspace else f"session:{session.id}"
        lock = self._workspace_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._workspace_locks[key] = lock
        return lock

    async def get_or_create(self, session: Session) -> "QueryLoopEngine":
        # ⚠️ 临界区禁止 await：本 if 块内全是同步调用（查 dict → 构造引擎 →
        # 装配依赖 → 写回 dict → create_task），单事件循环下这才是原子的。
        # 只要塞进任何一个 await，就会产生「同 session 双引擎、双 run()」
        # ——见 §9.12.7 与 §9.12.9 第 8 条。
        if session.id not in self._engines:
            self._evict_idle(protect=session.id)
            engine = QueryLoopEngine(
                session=session,
                session_repo=self.session_repo,
                message_repo=self.message_repo,
                llm_client=self.llm_client,
                resolver=self.resolver,
                skill_registry=self.skill_registry,
                conv_repo=self._conv_repo,
                user_mcp_repo=self.user_mcp_repo,
                session_factory=self.session_factory,
                event_bus=self.event_bus,
                offload_store=self._offload_store,
                invocation_repo=self._invocation_repo,
                memory_recall=self.memory_recall,
                scene_recall=self.scene_recall,
                persona_recall=self.persona_recall,
            )
            # Wire agent-related dependencies
            engine._expert_repo = self._expert_repo
            engine._team_repo = self._team_repo
            engine._plugin_loader = self._plugin_loader
            engine._task_handler = TaskToolHandler(self, self._plugin_loader)
            engine._workspace_lock = self._workspace_lock_for(session)
            engine._mail = self.mail
            # 子引擎终态时要归还 manager 的子 agent 名额（§9.12.5②）
            engine._mgr = self
            self._engines[session.id] = engine
            engine._run_task = asyncio.create_task(engine.run())
        return self._engines[session.id]

    def get(self, session_id: UUID) -> "QueryLoopEngine | None":
        return self._engines.get(session_id)

    def _evict_idle(self, protect: UUID) -> None:
        """容量上限：_engines 只增不减，长跑进程会无界增长（§9.12.5③ / §9.12.9 #5）。

        只驱逐空闲引擎，且按插入顺序取最老的几个。**不驱逐在跑的**——宁可短暂
        超过上限，也不能掐掉正在执行的回合。被驱逐的引擎必须先 cancel 掉它的
        run() 协程再 pop：留着协程而丢掉注册项，等于造出第二个同 id 引擎的温床。
        """
        if len(self._engines) < settings.max_engines:
            return
        for session_id in list(self._engines.keys()):
            if len(self._engines) < settings.max_engines:
                break
            if session_id == protect:
                continue
            engine = self._engines.get(session_id)
            if engine is None or not engine.is_evictable():
                continue
            task = engine._run_task
            if task is not None and not task.done():
                task.cancel()
            self._engines.pop(session_id, None)

    async def cancel_tree(self, session_id: UUID) -> None:
        """取消指定会话的引擎及其所有子孙引擎（专家团子 agent）。"""
        engine = self._engines.get(session_id)
        if engine:
            engine.request_cancel()
        children = await self.session_repo.list_by_parent(session_id)
        for child in children:
            await self.cancel_tree(child.id)

    async def shutdown_tree(self, session_id: UUID) -> None:
        """归档 / 驱逐用：温和取消当前回合后**终止** run() 协程，递归到子孙。

        与 cancel_tree 的区别：cancel_tree 只置取消位——用户点「停止」后引擎要
        存活下来继续接新消息；本方法会真的 cancel 掉协程，因此调用方必须紧接着
        把引擎从 _engines 移除，否则留下一个永不消费队列的注册项。
        """
        engine = self._engines.get(session_id)
        if engine is not None:
            engine.request_cancel()
            task = engine._run_task
            if task is not None and not task.done():
                task.cancel()
        for child in await self.session_repo.list_by_parent(session_id):
            await self.shutdown_tree(child.id)


class QueryLoopEngine:
    """会话级单实例协程引擎。每个会话独立一个实例，生命周期与会话相同。

    ┌─ 引擎主循环 ─────────────────────────────┐
    │  run() → dequeue → _run_message_loop()    │
    │           ↑ 队空 IDLE，_wake_event 唤醒    │
    │           └─ 完成后循环回 dequeue          │
    └──────────────────────────────────────────┘
    """

    def __init__(
        self,
        session: Session,
        session_repo: SessionRepository,
        message_repo: MessageRepository,
        llm_client: LLMClient,
        resolver=None,  # ModelResolver | None — 不传则退化成"单客户端"
        skill_registry=None,
        conv_repo=None,  # ConversationHistoryRepo
        user_mcp_repo=None,  # UserMcpRepo | None
        session_factory=None,  # async_sessionmaker | None
        event_bus: EventBus | None = None,
        offload_store=None,  # OffloadStore | None — 10.5.3 external memory
        invocation_repo=None,  # ToolInvocationRepository | None（C-1 工具账本）
        memory_recall=None,  # L1RecallService | None — L1 记忆召回（未启用时为 None）
        scene_recall=None,  # L2RecallService | None — L2 场景召回（未启用时为 None）
        persona_recall=None,  # L3RecallService | None — L3 画像召回（未启用时为 None）
    ):
        self.session = session
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._llm = llm_client
        # 取模型客户端的唯一入口。主调用 / 上下文压缩 / 便宜模型档都从这里解析，
        # 本类不再自己读 `settings.llm_provider` 去挑厂商。
        self.resolver = resolver or StaticModelResolver(llm_client)
        self._skills = skill_registry  # SkillRegistry | None
        self._user_mcp_repo = user_mcp_repo  # UserMcpRepo | None
        self._session_factory = session_factory  # async_sessionmaker | None
        self._event_bus = event_bus  # EventBus | None
        self._invocation_repo = invocation_repo  # ToolInvocationRepository | None
        # L1 记忆召回（memory_recall.py 的 `L1RecallService`）。None = 记忆模块未启用：
        # 不召回、不注入工具指南、不注册 memory_search 工具。
        self._memory_recall = memory_recall
        # memory_search 每轮合计限次计数（消息级，_run_message_loop 开头清零）
        self._memory_search_calls = 0
        # L2 场景导航与按名读取（l2/recall.py 的 `L2RecallService`）。独立限次计数：
        # 与 memory_search 共用会静默改变"memory_search 每轮限 3 次"的既有语义。
        self._scene_recall = scene_recall
        self._scene_read_calls = 0
        # L3 画像（l3/recall.py 的 `L3RecallService`）。整份注入 system 末尾，没有工具、
        # 没有限次、也不按输入检索 —— 只有"有没有画像行"这一种分支。
        self._persona_recall = persona_recall

        self.state = "IDLE"
        self._wake_event = asyncio.Event()
        self._cancel_event = asyncio.Event()
        # 子 agent 结果到达的信号（§9.11.8）。**刻意不复用 _wake_event**：后者只被
        # run() 在队空时 await、唤醒后立刻 clear，而 enqueue() 是无条件 set 的——
        # 复用会与 run() 的 clear 打架，且任何一条新用户消息都会误唤醒挂起的父。
        self._children_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._seq = 0
        self._chunk_queue: asyncio.Queue[dict] = asyncio.Queue()
        # run() 协程句柄。归档 / 驱逐要能确定性取消它；不存下来就只能靠
        # request_cancel() 置位，而置位挡不住"引擎已被 pop、协程还在跑"（§9.12.9 #1）。
        self._run_task: asyncio.Task | None = None
        # 读段 fan-out 闸门：并发度直接等于模型一轮发出的读调用个数（§9.12.9 #4）
        self._read_semaphore = asyncio.Semaphore(settings.max_read_concurrency)
        # 层2 工作区写锁（§9.12.2）：跨 agent / 跨 session 的写-写互斥，只排写不排读。
        # 由 EngineManager 注入**同工作区共享的那一把**；此处的默认值是给不经
        # manager 直接构造引擎的场景（单测）留的，不是"会话私有锁"。
        self._workspace_lock = asyncio.Lock()
        # ── §9.11.8 异步派发：父挂起等子的状态 ──
        # 在途子任务：task_id(str) → {agent_path, dispatched_at}。父本轮收尾时若非空，
        # 就进 WAITING_CHILDREN 挂起，等子回信后在同一 message_id 上续跑。
        self._in_flight: dict[str, dict] = {}
        # 子侧：由 TaskToolHandler 在 spawn 时注入。终态时据此往父邮箱投 result 信封。
        # 三者皆为空 = 本引擎不是被派生的，就是个普通会话。
        self._parent_agent_path: str | None = None   # 父的 agent_path（result 的收件人）
        self._parent_chunk_queue: asyncio.Queue | None = None   # 展示中继的落点
        self._mgr: "EngineManager | None" = None     # 归还子 agent 名额（§9.12.5②）
        # M4 显式重跑队列：用户在旧消息上点"重新生成/继续"时注入的 job，
        # run() 优先于 dequeue 消费，保证与正常处理共用 _execute_message 的终态分流。
        self._reprocess_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._reprocess_pending: set[str] = set()  # 已排队的 message_id，防重复入队
        self._current_msg: Message | None = None
        self._attempt: int | None = None  # D 副作用分块：本轮运行序号，_record_issued 盖章
        self._mode: str = ""

        # 子组件
        self.sync_waiter = SyncWaiter(default_timeout=settings.sync_wait_timeout_seconds)
        self.stream_buffer = StreamBuffer(max_size=settings.stream_buffer_max_size)
        self.context_mgr = ContextManager(conv_repo) if conv_repo else ContextManager(message_repo)
        self.tool_dispatcher = ToolDispatcher()
        self.permission = PermissionEngine()
        # 读 / 写名单来自 server/scheduling.yaml：在这里加载，缺文件即启动报错
        self.scheduling = SchedulingPolicy()

        # Agent 相关（由 EngineManager.get_or_create 注入）
        self._expert_repo = None      # ExpertHubRepo
        self._team_repo = None        # TeamHubRepo
        self._plugin_loader = None    # PluginLoader
        self._task_handler = None     # TaskToolHandler
        self._team_lead_system_prompt: str | None = None
        self._team_lead_config = None
        self._team_skills: list[dict] = []
        self._team_members: list[dict] = []
        self._team_plugin_path: str = ""
        self._team_lead_agent_id: str | None = None
        self._disable_task_tool: bool = False
        self._agent_system_prompt: str | None = None
        self._agent_config = None
        self._mail = None             # MailRouter（由 EngineManager 注入）

        # Hook 系统
        hooks_config = load_hooks_config(
            config_path="hooks.json",
            script_dir=settings.hooks_script_dir,
        ) if settings.hooks_enabled else []
        self.hooks = HookManager(hooks_config)
        if hooks_config:
            logger.info("hooks_loaded", count=self.hooks.hook_count,
                        points=list(set(h["on"] for h in hooks_config)))

        # 上下文压缩引擎 (10.5)。**两个参数都不在这里固化**：
        # `model_context_limit` 与 `chars_per_token` 都随会话当前模型变，构造时定死
        # 会让"换了个上下文窗口小得多的模型"依然按 65536 判断、压缩永不触发。
        # 压缩用的便宜模型客户端同样每轮现解析（`_compression_model_key`）。
        self._token_counter = TokenCounter()
        # 压缩/摘要走"便宜模型"档；留空 = 与主模型相同（`resolve("")` 回落到默认模型）。
        self._compression_model_key = settings.compression_model
        self._offload_store = offload_store
        self._compressor = ContextCompressor(
            llm_client=None,
            token_counter=self._token_counter,
            config=CompressionConfig(),
            offload_store=offload_store,
        )

        # 死循环检测
        self._recent_tool_calls: list[tuple[str, str]] = []

        # 客户端工具等待超时（秒）；可被测试调小以快测超时留 issued。
        # 330s = 客户端工具上限（300s，fileOps 的 Math.min(timeoutMs, 300000)）+ 30s 余量。
        # 刻意大于客户端上限：客户端到点必先自行超时并以失败回投（stderr 带超时信息），
        # 该回投落在本窗口内 → 服务端按普通工具结果收口，不走 issued/needs_confirm 的模糊路径。
        self._tool_wait_timeout = 330.0
        # C-2 对账窗宽限：首段等待超时后，再等客户端补投结果/声明未知的窗口长度
        self._tool_reconcile_timeout = 30.0

    # ═══════════════════════════════════════════════════════════
    # 可观测性 helpers
    # ═══════════════════════════════════════════════════════════

    @property
    def _log(self):
        """返回绑定当前会话/用户上下文的 structlog logger。"""
        return logger.bind(
            session_id=str(self.session.id),
            user_id=str(self.session.user_id),
        )

    @property
    def _agent_scope(self) -> str:
        """L1 记忆的作用域标识（设计文档 teamId + agentId 的 agentId 半边）。

        用会话的 agent_path：顶层会话 /root，子 agent /root/{member_id} —— 子 agent
        有自己的 conversation_history，因此各自独立抽取向、各自独立召回。
        """
        return getattr(self.session, "agent_path", "") or ""

    def _memory_guides(self) -> str:
        """记忆工具指南（稳定部分）：L1 的与 L2 的拼接后一起进 system 末尾。

        两者都关掉时返回空串，行为与未接线时完全一致。
        """
        parts = []
        if self._memory_recall is not None:
            parts.append(self._memory_recall.guide_xml)
        if self._scene_recall is not None:
            parts.append(self._scene_recall.guide_xml)
        return "\n\n".join(p for p in parts if p)

    async def _emit(self, event_type: AgentEventType, data: dict, *, message_id: UUID | None = None) -> None:
        """通过 EventBus 发射事件（仅 Stream + Audit 消费）。"""
        if self._event_bus is None:
            return
        mid = message_id or (self._current_msg.id if self._current_msg else uuid4())
        await self._event_bus.emit(AgentEvent(
            type=event_type,
            timestamp=event_now(),
            session_id=self.session.id,
            user_id=self.session.user_id,
            message_id=mid,
            data=data,
        ))

    def _needs_audit(self, tool_name: str) -> bool:
        """判断工具是否需要审计记录。"""
        return tool_name in ("read_file", "glob", "grep", "write_file", "edit_file", "bash")

    # ═══════════════════════════════════════════════════════════
    # 数据块推送
    # ═══════════════════════════════════════════════════════════

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _push_chunk(self, chunk: dict) -> None:
        chunk["seq"] = self._next_seq()
        if self._current_msg and self._current_msg.agent_id:
            chunk.setdefault("agent_id", self._current_msg.agent_id)
        self.stream_buffer.push(chunk)
        await self._chunk_queue.put(chunk)
        await self._tee_to_parent(chunk)

    async def _tee_to_parent(self, chunk: dict) -> None:
        """子 agent 的展示 chunk 也进父的流（§9.11.7 / 设计决定 D）。

        父不再阻塞读子的队列（异步派发），所以中继只能由子自己推。
        白名单**必须**排除 message.complete / message.error：父的 NDJSON 以此
        break（routes.py 的 chunk_generator），转发过去等于提前关掉父的流。

        中继出去的子 chunk 一律自报家门：flat `agent_id`（展示层不留路径，§9.11.13）
        + 子会话 id。后者是**硬需求**：客户端的 `client.tool_request` 回投要打到子的
        session 上（服务端按 `tool_wait_key(子的 session.id, request_id)` 匹配），
        只有 agent_id 时客户端只能拿 lead 会话回投，服务端查不到 → 结果被当重复吞掉，
        子干等满 330s。父自己的 chunk 没有该字段，客户端回落 lead。
        """
        if self._parent_chunk_queue is None:
            return
        if chunk.get("type", "") not in _RELAY_TYPES:
            return
        relay = dict(chunk)
        relay["agent_id"] = agent_id_from_path(self.session.agent_path)
        relay["session_id"] = str(self.session.id)
        await self._parent_chunk_queue.put(relay)

    def request_cancel(self) -> None:
        """请求取消当前正在处理的消息，中断 LLM 流式生成。"""
        self._cancel_event.set()
        # 挂起等子的父不在两个软取消检查点（回合顶部 / LLM 流中）上，只置 _cancel_event
        # 它会一直等到子回来才反应。唤醒它去检查取消位（§9.11.8）。
        self._children_event.set()

    def is_evictable(self) -> bool:
        """能否被 EngineManager 驱逐出 _engines（容量上限回收，§9.12.9 #5）。

        只有完全空闲才算：不在处理消息、输出队列已排空、没有在途子任务。
        挂起等子（WAITING_CHILDREN）的引擎不可驱逐——驱逐会取消 run()，
        把父那条消息永久留在 processing。
        """
        return (
            self.state == "IDLE"
            and self._current_msg is None
            and not self._in_flight
            and self._chunk_queue.empty()
        )

    async def _emit_cancelled(self, msg: Message) -> None:
        await self._push_chunk({
            "type": "message.error",
            "message_id": str(msg.id),
            "message": "已停止",
            "code": "cancelled",
            "fatal": False,
        })

    # ═══════════════════════════════════════════════════════════
    # C-1 同步等待回投（路由端薄封装，键控 waiter 的用户侧）
    # ═══════════════════════════════════════════════════════════

    def resolve_user_decision(self, value: object) -> None:
        """路由端确认/编辑/回答 Plan 决策：唤醒当前消息的用户决策等待。

        引擎串行处理单条消息，决策等待点 = 正在处理消息的 user key。
        """
        if self._current_msg is not None:
            self.sync_waiter.resolve(
                user_wait_key(self.session.id, self._current_msg.id), value
            )

    async def submit_client_tool_result(self, request_id: str, body: dict) -> dict:
        """路由端 /tool-result 入口：幂等护栏 + 回投客户端工具等待。

        方案 X 下 /cancel 不再 resolve tool key，引擎在途工具正常落地、由引擎消费结果。
        本入口只兜两类边角：
          - completed（一次性终态）→ 不 resolve，返回 duplicate（防重放双写）；
          - issued ∧ 引擎在等 → resolve 唤醒引擎（引擎随后 mark completed + 注入上下文）；
          - issued ∧ 无等待者 ∧ 真实结果 → 引擎已因硬取消/进程中断离开等待（该轮已终止），
            迟到的真实回投直接 mark completed（工具确已执行，如实入账）。
        无账本环境（旧客户端/未接线）直接 resolve，保持兼容。
        """
        # 唯一入口处把客户端 status 归一成账本契约的 success：账本/审计/metric/hook 与
        # 副作用清单都读 result.success，不补齐则失败会被读成成功。
        body = normalize_client_result(body)
        key = tool_wait_key(self.session.id, request_id)
        if self._invocation_repo is None:
            self.sync_waiter.resolve(key, body)
            return {"received": True, "request_id": request_id}
        inv = await self._invocation_repo.get(self.session.id, request_id)
        if inv is None:
            self._log.warning("tool_result_unknown", request_id=request_id)
            return {"received": True, "request_id": request_id, "duplicate": True}
        if inv.state == InvocationState.COMPLETED:
            self._log.info(
                "tool_result_duplicate", request_id=request_id,
                state=inv.state.value,
            )
            return {"received": True, "request_id": request_id, "duplicate": True}
        if inv.state == InvocationState.ISSUED:
            if self.sync_waiter.has_waiter(key):
                self.sync_waiter.resolve(key, body)
                return {"received": True, "request_id": request_id}
            # 无等待者 = 该轮已终止、引擎不再消费 → 迟到的真实结果直接收口为 completed
            if _is_genuine_tool_result(body):
                await self._mark_invocation(
                    str(inv.invocation_id), InvocationState.COMPLETED, result=body,
                )
                await self._emit_network_approvals(body, inv.tool_name, request_id)
                return {"received": True, "request_id": request_id, "reconciled_late": True}
        self._log.info(
            "tool_result_duplicate", request_id=request_id,
            state=inv.state.value,
        )
        return {"received": True, "request_id": request_id, "duplicate": True}

    # ═══════════════════════════════════════════════════════════
    # C-1 工具执行账本（invocation_id == 客户端 request_id）
    # ═══════════════════════════════════════════════════════════

    async def _resolve_attempt(self, msg: Message, reprocess_mode: str | None) -> int | None:
        """本轮运行序号（D 副作用分块）：首跑 / regenerate 递增开新块；continue 复用当前块。

        读库失败或仓储缺失返回 None（该轮工具账不盖章，前端归为旧块）。
        """
        if self._invocation_repo is None:
            return None
        try:
            cur = await self._invocation_repo.max_attempt(self.session.id, msg.id)
        except Exception:
            self._log.exception("attempt_resolve_error", message_id=str(msg.id))
            return None
        if reprocess_mode == "continue":
            return max(cur, 1)  # 续跑并入当前块
        return cur + 1  # 首跑 / regenerate 都开新块

    async def _record_issued(
        self, msg: Message, invocation_id: str, tool_name: str, tool_input: dict,
        location: str, turn: int, requires_approval: bool = False,
    ) -> None:
        """执行前落 issued 账（先落意图再执行）。"""
        if self._invocation_repo is None:
            return
        try:
            await self._invocation_repo.create(ToolInvocation(
                invocation_id=UUID(invocation_id),
                session_id=self.session.id,
                message_id=msg.id,
                turn=turn,
                attempt=self._attempt,
                tool_name=tool_name,
                location=location,
                state=InvocationState.ISSUED,
                input=tool_input or {},
                side_effect=_tool_side_effect(tool_name),  # D：read-only 永不落账，其余算副作用
                idempotency=_tool_idempotency(tool_name),  # C-3 档，供对账/重放裁决
                requires_approval=requires_approval,
            ))
        except Exception:
            self._log.exception("invocation_issue_error", tool=tool_name)

    async def _mark_invocation(
        self, invocation_id: str, state: InvocationState,
        result: dict | None = None, error: str | None = None,
    ) -> None:
        """按状态机更新账本；任何失败不影响主流程。"""
        if self._invocation_repo is None:
            return
        try:
            ok = await self._invocation_repo.mark(
                self.session.id, invocation_id, state.value,
                result=result, error=error,
            )
        except Exception:
            self._log.exception("invocation_mark_error", invocation_id=invocation_id)
            return
        if not ok:
            # mark 静默 False（行缺失 / 状态机守卫）不抛异常，只有这里显式记账才能事后归因；
            # 不记的话账停在 issued 而日志空白，排查时无从区分"没调用"与"调用被拒"。
            try:
                cur = await self._invocation_repo.get(self.session.id, invocation_id)
                row_state = cur.state.value if cur is not None else "missing"
            except Exception:
                row_state = "lookup_failed"
            self._log.warning(
                "invocation_mark_missed",
                invocation_id=invocation_id,
                want_state=state.value,
                row_state=row_state,
            )

    async def _emit_network_approvals(
        self, result: dict, tool_name: str, request_id: str,
    ) -> None:
        """把随工具结果回传的域名审批（NETWORK_APPROVAL）落审计。"""
        for appr in (result.get("network_approvals") or []):
            if not isinstance(appr, dict):
                continue
            await self._emit(AgentEventType.NETWORK_APPROVAL, {
                "host": appr.get("host"),
                "protocol": appr.get("protocol"),
                "decision": appr.get("decision"),
                "approved": appr.get("approved"),
                "tool_name": tool_name,
                "request_id": request_id,
            })

    async def _reconcile_client_tool(
        self, msg: Message, tc_id: str, tool_name: str, tool_input: dict,
        invocation_id: str, request_id: str, turn: int,
    ) -> dict | None:
        """C-2 对账窗：客户端工具首段等待超时后的收口裁决。

        返回本轮工具 outcome dict（调用方据此走公共收尾 append_tool_result）；
        返回 None 表示 Build 应在窗内中止（abort）。账本语义：
          - 窗内客户端补投真实结果（reconcile executed 应答，或经 outbox 迟到的普通回投）
            → mark completed，用真实结果收口，防"判失败后重复执行"；
          - state:unknown / 超窗无应答 → 按 C-3 幂等档裁决：
              read-only / idempotent：原调用 superseded（可安全重放），本轮以"不确定(只读)"
                失败返回，LLM 自行安全重试（每次都是新的只读 issued）；
              non-idempotent 写类：不自动重放——账留 issued(ambiguous) 并推
                tool.reconcile_needs_confirm，本轮以"不确定需确认"失败返回；尾部 issued 账
                天然拦截后续自动重跑（M4 regenerate 的 needs_confirm 门槛），杜绝静默双写。
        """
        idem = _tool_idempotency(tool_name)
        await self._push_chunk({
            "type": "client.tool_reconcile",
            "request_id": request_id,
            "invocation_id": invocation_id,
            "tool_name": tool_name,
            "message_id": str(msg.id),
            "idempotency": idem,
        })
        recon = await self.sync_waiter.wait(
            tool_wait_key(self.session.id, invocation_id),
            timeout=self._tool_reconcile_timeout,
        )
        if recon == "abort":
            return None

        if isinstance(recon, dict) and recon.get("reconcile") is True:
            executed = None
            if recon.get("state") == "executed":
                executed = recon.get("result")
                if not isinstance(executed, dict):
                    executed = {"success": True, "output": str(executed or "")}
            # state == "unknown" → executed 保持 None，落 ambiguous
        elif isinstance(recon, dict):
            # 非 reconcile 应答的普通 dict = 客户端 outbox 于窗内迟到的真实结果
            executed = recon
        else:
            executed = None

        if executed is not None:
            await self._mark_invocation(
                invocation_id, InvocationState.COMPLETED, result=executed,
            )
            await self._emit_network_approvals(executed, tool_name, request_id)
            return executed

        elapsed_ms = int((self._tool_wait_timeout + self._tool_reconcile_timeout) * 1000)
        if idem in ("read-only", "idempotent"):
            # C-3：判不出但可安全重放 → 原调用 superseded（供 C-1 重跑门槛放行），
            # 本轮以"不确定(可重放)"失败交 LLM 自行重试（每次都是新的只读 issued）。
            await self._mark_invocation(
                invocation_id, InvocationState.SUPERSEDED, error="uncertain_replayable",
            )
            return {
                "success": False,
                "error": "执行结果不确定（只读/可重放工具，可安全重试）",
                "reconcile": True,
                "replayable": True,
                "idempotency": idem,
                "duration_ms": elapsed_ms,
            }
        # 写类保守：不自动重放。账留 issued(ambiguous)，推需确认提示（尾部 issued 账
        # 会拦 M4 regenerate 的自动重跑）。M7：失败结果带幂等档 hint，供 LLM/UI 知悉边界。
        await self._push_chunk({
            "type": "tool.reconcile_needs_confirm",
            "message_id": str(msg.id),
            "request_id": request_id,
            "invocation_id": invocation_id,
            "tool_name": tool_name,
            "message": "该写操作执行结果不确定，可能已生效，请在界面确认后再继续",
        })
        return {
            "success": False,
            "error": "执行结果不确定：该写操作可能已生效，需用户确认后才能继续",
            "reconcile": True,
            "needs_confirm": True,
            "replayable": False,
            "idempotency": idem,
            "duration_ms": elapsed_ms,
        }

    def drain_queue(self) -> list[dict]:
        """重连时调用：原子排空队列，配合 stream_buffer 回放后去重。"""
        chunks: list[dict] = []
        while not self._chunk_queue.empty():
            try:
                chunks.append(self._chunk_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return chunks

    # ═══════════════════════════════════════════════════════════
    # Agent 路由
    # ═══════════════════════════════════════════════════════════

    async def _route_by_agent_type(self, msg: Message) -> None:
        """Post-enqueue routing: configure engine state based on agent_type."""
        self._log.info(
            "route_by_agent_type", agent_id=msg.agent_id, agent_type=msg.agent_type
        )
        if not msg.agent_id or not msg.agent_type:
            self._log.warning("route_by_agent_type_skipped", reason="missing agent_id or agent_type")
            return

        agent_type = msg.agent_type
        agent_id = msg.agent_id

        if agent_type == "team":
            await self._setup_team_lead(agent_id, msg)
        elif agent_type == "expert":
            await self._setup_expert_agent(agent_id, msg)

    async def _setup_team_lead(self, team_id: str, msg: Message) -> None:
        """Configure the engine as a team lead agent with task delegation tool."""
        if not self._team_repo:
            self._log.warning("team_repo_not_available", team_id=team_id)
            return

        team = None
        try:
            team = await self._team_repo.get_by_id(UUID(team_id))
        except (ValueError, TypeError):
            team = await self._team_repo.get_by_name(team_id)
        if not team:
            self._log.warning("team_not_found", team_id=team_id)
            return

        lead_agent_id = team.lead_agent_id or ""
        lead_expert = None
        if lead_agent_id and self._expert_repo:
            lead_expert = await self._expert_repo.get_by_name(lead_agent_id)

        plugin_path = str(Path(__file__).parent.parent / "plugins" / "experts" / team.name)
        if not os.path.isdir(plugin_path):
            fallback = team.plugin_path
            if not os.path.isabs(fallback):
                fallback = str(Path(__file__).parent.parent / fallback)
            if os.path.isdir(fallback):
                plugin_path = fallback
        if lead_expert:
            lead_path = lead_expert.plugin_path
            if not os.path.isabs(lead_path):
                lead_path = str(Path(__file__).parent.parent / lead_path)
            if os.path.isdir(lead_path):
                plugin_path = lead_path

        # Load lead agent's .md file
        if self._plugin_loader and lead_agent_id:
            try:
                load_name = lead_expert.name if lead_expert else lead_agent_id
                agent_md = self._plugin_loader.load_agent_md(plugin_path, load_name)
                self._team_lead_system_prompt = agent_md.system_prompt
                self._team_lead_config = agent_md
            except FileNotFoundError:
                self._log.warning("agent_md_not_found", agent_id=lead_agent_id, plugin_path=plugin_path)

        # If no .md found, use plugin.json description
        if not self._team_lead_system_prompt:
            self._team_lead_system_prompt = (
                f"你是 {team.display_name} 的领队。{team.description}\n"
                f"你可以使用 task 工具将任务委派给团队成员。收到用户任务后，先分析拆解，"
                f"再通过 task 工具分派给合适的专家成员，最后汇总结果。"
            )

        # Discover plugin skills
        if self._plugin_loader:
            self._team_skills = self._plugin_loader.discover_skills(plugin_path)

        self._team_members = team.members
        self._team_plugin_path = plugin_path
        self._team_lead_agent_id = lead_agent_id or team_id

        if lead_agent_id:
            lead_member = next(
                (m for m in self._team_members if m.get("id") == lead_agent_id), None
            )
            msg.agent_id = lead_member["id"] if lead_member else lead_agent_id

        self._log.info(
            "team_lead_setup", team_id=team_id, lead=lead_agent_id,
            members=len(self._team_members), skills=len(self._team_skills),
        )

    async def _setup_expert_agent(self, expert_id: str, msg: Message) -> None:
        """Configure the engine as a single expert agent."""
        expert = None
        if self._expert_repo:
            try:
                expert = await self._expert_repo.get_by_id(UUID(expert_id))
            except (ValueError, TypeError):
                expert = await self._expert_repo.get_by_name(expert_id)

        member = None
        plugin_path = ""
        if expert:
            plugin_path = str(Path(__file__).parent.parent / "plugins" / "experts" / expert.name)
            if not os.path.isdir(plugin_path):
                fallback = expert.plugin_path
                if not os.path.isabs(fallback):
                    fallback = str(Path(__file__).parent.parent / fallback)
                if os.path.isdir(fallback):
                    plugin_path = fallback
            display_name = expert.display_name
            fallback_desc = expert.description
        elif self._team_members and self._team_plugin_path:
            member = next((m for m in self._team_members if m.get("id") == expert_id), None)
            if member:
                plugin_path = self._team_plugin_path
                display_name = member.get("name", {}).get("zh", member.get("id", expert_id))
                fallback_desc = member.get("profession", {}).get("zh", "")
            else:
                self._log.warning("expert_not_found", expert_id=expert_id)
                return
        else:
            self._log.warning("expert_not_found", expert_id=expert_id)
            return

        if self._plugin_loader and plugin_path:
            try:
                agent_md = self._plugin_loader.load_agent_md(plugin_path, expert_id)
                prompt = agent_md.system_prompt
                self._agent_config = agent_md
            except FileNotFoundError:
                prompt = f"你是 {display_name}。{fallback_desc}"
            # 只对**被派发出去的子**注入位置合同（§9.11.9）：单 expert 会话不经过结果
            # 提取，加了只会污染它的 prompt。必须拼在局部变量上再一次性赋值——本函数
            # **每条消息**都会被调到（经 _route_by_agent_type），直接往
            # self._agent_system_prompt 追加会跨次累积。
            if self._parent_agent_path is not None:
                prompt = f"{prompt}\n\n{_CHILD_DELIVERABLE_CONTRACT}"
            self._agent_system_prompt = prompt
            self._team_skills = self._plugin_loader.discover_skills(plugin_path)

    def _build_task_tool_definition(self) -> dict | None:
        """Build the task tool def with agent_path enum from team members."""
        if self._disable_task_tool or not self._team_members:
            return None

        member_paths = [
            agent_path_of(m["id"]) for m in self._team_members
            if m.get("role") != "lead"
        ]
        if not member_paths:
            return None

        return {
            "name": "task",
            "description": (
                "委派任务给专家子 agent。每个子 agent 有独立的系统提示词和技能。"
                "调用后子 agent 会独立执行任务并返回结果。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_path": {
                        "type": "string",
                        "enum": member_paths,
                        "description": "要委派的子 agent 路径（取自 <available_agents> 的 <name>）",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "任务描述，越详细越好。包含任务目标、期望输出格式、约束条件等",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["spawn", "followup"],
                        "description": (
                            "spawn：新建子会话并继承父上下文（fork_turns 仅此模式生效）；"
                            "followup：复用同一子 agent 已有会话，沿用其自身历史（fork_turns 被忽略）"
                        ),
                    },
                    "fork_turns": {
                        "type": "string",
                        "description": (
                            "仅 spawn 生效。父上下文继承档位：none（不继承）/ all（全量）/ "
                            "正整数 N（最近 N 轮）。留空则用该 agent 的默认档"
                        ),
                    },
                },
                "required": ["agent_path", "prompt", "mode"],
            },
        }

    def _build_plugin_skills_xml(self) -> str:
        """Build <available_skills> XML from plugin-discovered skills."""
        if not self._team_skills:
            return ""
        lines = ["<available_skills>"]
        for s in self._team_skills:
            lines.append(f"  <skill>")
            lines.append(f"    <name>{s.get('skill_name', 'unknown')}</name>")
            desc = s.get("content", "")[:200].replace("\n", " ")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    def _build_team_members_xml(self) -> str:
        """Build <available_agents> XML from team members' .md files, same pattern as skills."""
        if not self._team_members:
            return ""
        lines = ["<available_agents>"]
        for m in self._team_members:
            if m.get("role") == "lead":
                continue
            member_id = m["id"]
            description = ""
            if self._plugin_loader and self._team_plugin_path:
                try:
                    agent_md = self._plugin_loader.load_agent_md(
                        self._team_plugin_path, member_id
                    )
                    description = agent_md.description
                except FileNotFoundError:
                    pass
            if not description:
                display = m.get("name", {}).get("zh", member_id)
                prof = m.get("profession", {}).get("zh", "")
                description = f"{display} - {prof}"
            lines.append("  <agent>")
            lines.append(f"    <name>{agent_path_of(member_id)}</name>")
            lines.append(f"    <description>{description}</description>")
            lines.append("  </agent>")
        lines.append("</available_agents>")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════════════════════

    async def run(self) -> None:
        """会话主循环：休眠 → 唤醒 → reprocess/出队 → 处理 → 循环。

        单一消费纪律：普通消息走 dequeue；M4 重跑（regenerate/continue）经
        _reprocess_queue 注入，优先于新消息执行。所有单消息处理都经 _execute_message，
        保证终态分流一致。
        """
        while True:
            # ── 优先消费重跑请求（M4，用户在旧消息上点"重新生成/继续"）──
            if not self._reprocess_queue.empty():
                job = self._reprocess_queue.get_nowait()
                self._reprocess_pending.discard(str(job["message_id"]))
                msg = await self._message_repo.get(job["message_id"])
                if msg is not None and msg.session_id == self.session.id:
                    await self._execute_message(msg, reprocess_mode=job["mode"])
                continue

            msg = await self._dequeue_next()
            if msg is None:
                self.state = "IDLE"
                await self._wake_event.wait()
                self._wake_event.clear()
                continue

            await self._execute_message(msg)

    async def _execute_message(
        self, msg: Message, *, reprocess_mode: str | None = None,
    ) -> None:
        """执行单条消息的全生命周期：置 PROCESSING → turn 循环 → 终态分流。

        run() 对 dequeue 出的新消息调用（reprocess_mode=None），也对重跑请求调用
        （mode ∈ regenerate | continue）。终态写入与 message.complete 补发都在这，
        dequeue 路径与重跑路径共用，保证两端语义一致。
        """
        self.state = "PROCESSING"
        self._current_msg = msg
        self._attempt = await self._resolve_attempt(msg, reprocess_mode)
        self._cancel_event.clear()
        msg.started_at = datetime.now(timezone.utc)
        msg.status = MessageStatus.PROCESSING
        if reprocess_mode is not None:
            # M4 重跑：重置上一轮统计，避免累计旧 turn/token；regenerate 历史已截断。
            msg.turn_count = 0
            msg.tokens_in = 0
            msg.tokens_out = 0
            msg.tool_calls_count = 0
            msg.error_message = None
        await self._message_repo.update(msg)

        self._log.info(
            "message_started", mode=msg.mode, scene=msg.scene_mode,
            model=msg.model or settings.default_model,
            reprocess=reprocess_mode or "first-run",
        )
        await self._emit(AgentEventType.MESSAGE_START, {
            "type": "message.start",
            "message_id": str(msg.id),
            "mode": msg.mode,
            "scene_mode": msg.scene_mode,
            "workspace": msg.workspace,
            "agent_id": msg.agent_id,
            "reason": "recovered" if reprocess_mode else None,
        })

        msg_start_time = time.monotonic()
        outcome = "completed"            # 本次消息终态：completed | error | cancelled
        outcome_error: str | None = None
        re_raise_cancel = False
        try:
            with tracer.start_as_current_span(
                "message",
                attributes={
                    "session.id": str(self.session.id),
                    "user.id": str(self.session.user_id),
                    "message.id": str(msg.id),
                    "mode": msg.mode,
                    "model": msg.model or settings.default_model,
                },
            ) as root_span:
                outcome = await self._run_message_loop(
                    msg, reprocess_mode=reprocess_mode,
                )
                root_span.set_attributes({
                    "message.total_turns": msg.turn_count,
                    "message.total_tokens_in": msg.tokens_in,
                    "message.total_tokens_out": msg.tokens_out,
                    "message.total_tool_calls": msg.tool_calls_count,
                    "message.status": outcome,
                })
        except asyncio.CancelledError:
            # 硬取消（外部 task.cancel / 解释器关闭）：未走软取消检查点，直接到达此处。
            # 终态落库交给 finally，随后向外传播，避免 run() task 静默死亡残留引擎。
            re_raise_cancel = True
            outcome = "cancelled"
            if not self._cancel_event.is_set():
                try:
                    await self._emit_cancelled(msg)   # 尽力补发 cancelled 通知
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            self._log.exception("message_error", error_type=type(e).__name__)
            outcome = "error"
            outcome_error = str(e) or type(e).__name__
            await self._emit(AgentEventType.MESSAGE_ERROR, {
                "type": "message.error",
                "message_id": str(msg.id),
                "message": outcome_error,
                "code": "internal_error",
                "fatal": True,
            })
        finally:
            # ── 终态分流：finally 不再无条件置 COMPLETED ──
            if outcome == "cancelled":
                msg.status = MessageStatus.CANCELLED
            elif outcome == "error":
                msg.status = MessageStatus.ERROR
                if outcome_error:
                    msg.error_message = outcome_error
            else:
                msg.status = MessageStatus.COMPLETED
            msg.completed_at = datetime.now(timezone.utc)
            if msg.started_at:
                msg.duration_ms = int(
                    (msg.completed_at - msg.started_at).total_seconds() * 1000
                )
            await self._message_repo.update(msg)

            # 子 agent 回投结果（§9.11.8 / 设计决定 E）。放在终态落库**之后**：
            # 父拿到信封就意味着"子这条消息已经定论"，不会读到半截状态。
            await self._report_to_parent(msg, outcome, msg.error_message)

            total_elapsed_s = time.monotonic() - msg_start_time
            message_total.add(1, attributes={
                "mode": msg.mode,
                "status": "success" if outcome == "completed" else outcome,
            })
            message_duration.record(total_elapsed_s, attributes={"mode": msg.mode})
            message_turns.record(msg.turn_count)

            self._log.info(
                "message_terminal",
                status=outcome, turns=msg.turn_count, tools=msg.tool_calls_count,
                tokens_in=msg.tokens_in, tokens_out=msg.tokens_out,
                duration_ms=msg.duration_ms,
            )
            # 非正常终态不强制收口遗留 issued：方案 X 下取消不再 abort 在途客户端工具，
            # 工具要么自然落地 completed、要么在超时/C-2 对账后成真·ambiguous（写类不自动
            # 放行）。ambiguous 的 issued 尾巴保留，让 M4 reprocess 的 C-1 门槛继续 needs_confirm
            # 拦截，杜绝静默双写；迟到真实回投由 submit_client_tool_result 收口为 completed。
            # D 副作用账本：不再随流推 message.effects——前端在消息终态/会话加载时
            # 自行 GET /effects，按 attempt 分组渲染分块（如实在账、跨运行追加保留）。
            # message.complete 仅在正常完成时补发；cancelled/error 已各自推过终态
            # message.error，再补 message.complete 会让下游误判为成功完成。
            if outcome == "completed":
                await self._emit(AgentEventType.MESSAGE_COMPLETE, {
                    "type": "message.complete",
                    "message_id": str(msg.id),
                    "summary": {
                        "turns": msg.turn_count,
                        "tokens_in": msg.tokens_in,
                        "tokens_out": msg.tokens_out,
                        "duration_ms": msg.duration_ms,
                        "tool_calls_count": msg.tool_calls_count,
                    },
                })
            self._current_msg = None
            self._attempt = None
        if re_raise_cancel:
            raise asyncio.CancelledError

    # ═══════════════════════════════════════════════════════════
    # 会话工具
    # ═══════════════════════════════════════════════════════════

    def _session_client_tools(self) -> list[dict]:
        """将会话注册的客户端工具转换为 LLM API 可用的 tool 定义列表。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self.session.client_tools
        ]

    async def _mcp_tools(self, msg: Message) -> list[dict]:
        """收集当前消息可用的 MCP 工具列表（从 user_mcp_servers 读取）。"""
        if self._user_mcp_repo is None:
            return []
        tools = await self._user_mcp_repo.get_user_tools(self.session.user_id)
        if not tools:
            return []

        if msg.mcp_servers:
            enabled_ids = {s.server_id for s in msg.mcp_servers}
            whitelist: dict[str, set[str] | None] = {}
            for s in msg.mcp_servers:
                whitelist[s.server_id] = set(s.enabled_tools) if s.enabled_tools else None

            filtered: list[dict] = []
            for tool in tools:
                name = tool.get("name", "")
                if "_" not in name:
                    continue
                server_id, actual_tool = name.split("_", 1)
                if server_id not in enabled_ids:
                    continue
                tool_wl = whitelist.get(server_id)
                if tool_wl is not None and actual_tool not in tool_wl:
                    continue
                filtered.append(tool)
            return filtered

        return tools

    # ═══════════════════════════════════════════════════════════
    # 消息队列操作
    # ═══════════════════════════════════════════════════════════

    async def enqueue(self, user_id: str, data: MessageCreate) -> Message:
        """HTTP handler 调用：写入消息 + 推送排队事件 + 唤醒引擎。

        幂等（摄入幂等，阶段 A）：会话锁内先按 (session, client_message_id) 查重——
        同键已存在 → 直接返回已存在消息，不再重复插入 / 唤醒 / 推送排队事件。
        """
        async with self._lock:
            if data.client_message_id:
                existing = await self._message_repo.get_by_client_message_id(
                    self.session.id, data.client_message_id
                )
                if existing is not None:
                    return existing

            count = await self._message_repo.count_pending(self.session.id)
            if count >= settings.max_queue_size:
                raise QueueFullError(settings.max_queue_size)

            msg = Message(
                session_id=self.session.id,
                user_id=user_id,
                content=data.content,
                scene_mode=data.scene_mode,
                workspace=data.workspace,
                model=data.model,
                mode=data.mode,
                agent_id=data.agent_id,
                agent_type=data.agent_type,
                files=data.files,
                skill_invocations=data.skill_invocations,
                mcp_servers=data.mcp_servers,
                client_message_id=data.client_message_id,
                # 邮箱信封（§9.11.7）：用户消息这四个字段全是默认值（msg_type="user"），
                # 只有 MailRouter 投递的 task / followup 会带非空值。
                sender_agent_id=data.sender_agent_id,
                recipient_agent_id=data.recipient_agent_id,
                msg_type=data.msg_type,
                cid=data.cid,
                status=MessageStatus.PENDING,
                queue_position=count + 1,
            )
            await self._message_repo.create(msg)

        self._wake_event.set()

        with tracer.start_as_current_span("enqueue", attributes={
            "session.id": str(self.session.id),
            "message.id": str(msg.id),
            "queue.position": msg.queue_position,
        }):
            pass  # span 仅用于记录 enqueue 事件在 trace 中的位置

        queue_depth.add(count + 1, attributes={"session_id": str(self.session.id)})

        enqueue_data = {
            "type": "queue.enqueued",
            "message_id": str(msg.id),
            "queue_position": msg.queue_position,
            "queue_size": count + 1,
            "ahead_message_id": str(self._current_msg.id) if self._current_msg else None,
            "agent_id": msg.agent_id,
        }
        await self._emit(AgentEventType.QUEUE_ENQUEUED, enqueue_data, message_id=msg.id)

        return msg

    async def deliver_result(self, msg: Message) -> Message:
        """把子 agent 的结果信封投进本引擎的邮箱（§9.11.7）。

        与 enqueue 的三点不同，都是有意的：
        1. **不查 max_queue_size**——信封不是待跑回合，不占用户的队列预算；
        2. **不置 _wake_event**——要唤醒的是挂起等子的那次调用，用 _children_event；
        3. **不推 queue.enqueued**——它不该出现在前端的队列视图里。
        幂等仍复用 enqueue 那套 (session, client_message_id) 查重，只是键换成 `res:{cid}`。
        """
        async with self._lock:
            if msg.client_message_id:
                existing = await self._message_repo.get_by_client_message_id(
                    self.session.id, msg.client_message_id
                )
                if existing is not None:
                    self._children_event.set()
                    return existing
            await self._message_repo.create(msg)
        self._children_event.set()
        return msg

    async def find_by_client_message_id(self, client_message_id: str) -> Message | None:
        """按幂等键查本会话已存在消息（HTTP 层 duplicate 预查用）。"""
        if not client_message_id:
            return None
        return await self._message_repo.get_by_client_message_id(
            self.session.id, client_message_id
        )

    async def remove_from_queue(self, msg_id: UUID) -> bool:
        """用户手动取消排队中的消息，随后通知其余消息排位变化。"""
        ok = await self._message_repo.cancel_pending(msg_id, self.session.id)
        if not ok:
            return False

        remaining = await self._message_repo.list_pending(self.session.id)
        for m in remaining:
            await self._push_chunk({
                "type": "queue.position_changed",
                "message_id": str(m.id),
                "new_position": m.queue_position,
                "queue_size": len(remaining),
            })
        return True

    async def _dequeue_next(self) -> Message | None:
        """原子出队：队首 pending → processing，随后通知其余消息排位变化。"""
        async with self._lock:
            msg = await self._message_repo.dequeue_next(self.session.id)
            if msg is None:
                return None

            remaining = await self._message_repo.list_pending(self.session.id)
            for m in remaining:
                await self._push_chunk({
                    "type": "queue.position_changed",
                    "message_id": str(m.id),
                    "new_position": m.queue_position,
                    "queue_size": len(remaining),
                })
            return msg

    # ═══════════════════════════════════════════════════════════
    # M4 · 显式重跑：regenerate（截断重跑）/ continue（原地续跑）
    # ═══════════════════════════════════════════════════════════

    async def reprocess(self, message_id: UUID, mode: str) -> dict:
        """安排一次显式重跑：regenerate 截断该消息历史后重生成，continue 从已落历史续跑。

        返回 {status, ...}：
          - scheduled：job 已入 _reprocess_queue，run() 将经 _execute_message 执行；
          - busy：引擎正处理其它消息（单消费，不抢占）；
          - not_found / invalid_mode；
          - needs_confirm：该消息尾巴存在非 read-only 的 issued 工具账（C-1），不自动放行。
        """
        if mode not in ("regenerate", "continue"):
            return {"status": "invalid_mode", "message_id": str(message_id), "mode": mode}

        msg = await self._message_repo.get(message_id)
        if msg is None or msg.session_id != self.session.id:
            return {"status": "not_found", "message_id": str(message_id)}

        # WAITING_CHILDREN 显式列出：挂起中的父也持有 _current_msg，但把判断只押在
        # 那个间接条件上太脆——重跑会截断历史，把正等着子的父的历史挖掉。
        if self.state in ("PROCESSING", "WAITING_CHILDREN") or self._current_msg is not None:
            return {"status": "busy", "message_id": str(message_id)}

        # C-1 账尾检查（保守版，M7 前仅区分 read-only）：
        # 重跑会重放工具 —— 若本消息尾巴还有未收口的 issued（非 read-only），不自动放行，
        # 返回需确认清单交客户端提示（C-2 全量裁决在 M6）。
        if self._invocation_repo is not None:
            issued = await self._invocation_repo.list_issued_by_message(
                self.session.id, message_id,
            )
            blocking = [i for i in issued if i.idempotency != "read-only"]
            if blocking:
                return {
                    "status": "needs_confirm",
                    "message_id": str(message_id),
                    "mode": mode,
                    "ambiguous_tools": [
                        {
                            "invocation_id": str(i.invocation_id),
                            "tool_name": i.tool_name,
                            "idempotency": i.idempotency,
                        }
                        for i in blocking
                    ],
                }

        if mode == "regenerate":
            # 找本消息 user 行锚点（第一条 message_id == 本消息 的 user 行）作截断边界，
            # 只删该消息自身旧回复/工具痕迹；更早/更晚消息不受影响。
            rows = await self.context_mgr.list_rows(self.session.id)
            anchor = None
            for r in rows:
                if r.get("message_id") == str(message_id) and r.get("role") == "user":
                    anchor = r.get("sequence")
                    break
            if anchor is None:
                return {"status": "not_found", "message_id": str(message_id)}
            await self.context_mgr.truncate_message_after(
                self.session.id, message_id, anchor,
            )
            # 该消息保留在历史里的 user 行作为唯一锚点；重跑不再重复 append 用户输入。
            # message.start（reason=regenerated/recovered）由 _execute_message 统一补发。

        key = str(message_id)
        if key in self._reprocess_pending:
            return {"status": "busy", "message_id": key}  # 已排队未执行，避免重复入队
        self._reprocess_pending.add(key)
        self._reprocess_queue.put_nowait({"message_id": message_id, "mode": mode})
        self._wake_event.set()
        return {"status": "scheduled", "message_id": key, "mode": mode}

    # ═══════════════════════════════════════════════════════════
    # Per-Message Loop（引擎核心）
    # ═══════════════════════════════════════════════════════════

    async def _bootstrap_message(self, msg: Message) -> str | None:
        """单条消息首跑的一次性准备：hooks → agent 路由 → 组装用户内容 → 写 user 行锚点。

        返回 None 表示可进入 turn 循环；返回非 None（"completed"）表示 hook 阻断，
        调用方直接以此终态收尾（与旧语义一致：hook 阻断按 completed 处理）。
        """
        # ── message.before hooks ──
        msg_before_input = {
            "content": msg.content,
            "mode": msg.mode,
            "model": msg.model,
            "workspace": msg.workspace,
        }
        msg_before = await self.hooks.run(
            "message.before", msg_before_input,
            session_id=str(self.session.id), user_id=str(msg.user_id),
            message_id=str(msg.id), turn=0,
        )
        if msg_before.action == HookAction.STOP:
            self._log.warning("message_blocked_by_hook", reason=msg_before.reason)
            await self._push_chunk({
                "type": "error.diagnosis",
                "message_id": str(msg.id),
                "error_code": "hook_message_blocked",
                "severity": "warning",
                "title": "消息被 Hook 阻止",
                "explanation": f"输入被 Hook 拦截: {msg_before.reason}",
                "suggestion": "请修改输入内容后重试。",
            })
            return "completed"
        if msg_before.modified_input:
            msg.content = msg_before.modified_input.get("content", msg.content)

        # ── Post-enqueue agent routing ──
        await self._route_by_agent_type(msg)

        # 构建带 context 的用户消息：工作空间 + @文件 + 用户输入
        user_content = msg.content
        if msg.workspace or msg.files:
            ctx_parts: list[str] = []
            if msg.workspace:
                ctx_parts.append(f"工作目录: {msg.workspace}")
            if msg.files:
                ctx_parts.append("@文件: " + ", ".join(msg.files))
            user_content = "[上下文] " + "; ".join(ctx_parts) + "\n\n" + user_content

        # 记录本条消息的历史归属边界：首行 user 行 = 消息边界锚点（turn=0）
        self.context_mgr.set_active_message(msg.id, 0)
        await self.context_mgr.append_text(
            self.session.id, "user", user_content
        )
        return None

    async def _resume_turn_start(self, message_id: UUID) -> int:
        """续跑（continue）起始 turn：本消息历史中已落的最大 turn + 1。

        重跑（regenerate）已截断历史至 user 行锚点，turn 从 0 重来，不走这里。
        """
        try:
            rows = await self.context_mgr.list_rows(self.session.id)
        except Exception:
            return 0
        turns = [
            r.get("turn") for r in rows
            if r.get("message_id") == str(message_id) and r.get("turn") is not None
        ]
        return (max(turns) + 1) if turns else 0

    async def _run_message_loop(
        self, msg: Message, *, reprocess_mode: str | None = None,
    ) -> str:
        """单条消息的 turn 循环。

        返回终态：'completed' | 'cancelled' | 'error'。超时 / 内容过滤 / 取消在各自
        分支先 emit 终态 chunk 再 return，run() 据此写 DB 终态并决定是否补 message.complete。

        reprocess_mode（M4 显式重跑）：None = 首跑（先 _bootstrap_message 写 user 行锚点）；
        "regenerate" = 同消息重跑（历史已截断至该 user 行，不重复 append，turn 从 0 重来）；
        "continue" = 从 DB 已落该消息的历史轮数续跑。
        """
        outcome = "completed"
        turn = 0
        terminal = False
        mode = msg.mode
        self._mode = mode
        plan_confirmed = False
        build_step = 0
        truncation_retries = 0
        start_time = time.monotonic()

        if reprocess_mode is None:
            blocked = await self._bootstrap_message(msg)
            if blocked:
                return blocked  # hook 阻断：按原语义以 completed 收尾
        elif reprocess_mode == "continue":
            turn = await self._resume_turn_start(msg.id)

        # ── L1 记忆召回：每条用户消息算一次，整个工具循环复用 ──
        # 刻意不放进 ContextManager.build()：build() 每个 turn（工具调用轮）都调一次，
        # 放那儿会重复检索，且"最后一条 user 消息"会随工具结果追加而漂移。
        recall_block = ""
        self._memory_search_calls = 0
        if self._memory_recall is not None:
            recall_block = await self._memory_recall.recall_block(
                user_id=str(msg.user_id),
                agent_id=self._agent_scope,
                query=msg.content or "",
            )

        # ── L2 场景导航：同样每条消息算一次（导航全量、不过滤，不进用户前缀）──
        scene_nav_xml = ""
        self._scene_read_calls = 0
        if self._scene_recall is not None:
            scene_nav_xml = await self._scene_recall.navigation_xml(
                user_id=str(msg.user_id),
                agent_id=self._agent_scope,
            )

        # ── L3 画像：整份注入 system 末尾的稳定区（比场景导航变得少，排在它前面）──
        persona_xml = ""
        if self._persona_recall is not None:
            persona_xml = await self._persona_recall.persona_xml(
                user_id=str(msg.user_id),
                agent_id=self._agent_scope,
            )

        while turn < settings.max_turns and not terminal:

            # ── 超时检查 ──
            if time.monotonic() - start_time > settings.message_timeout_seconds:
                self._log.warning("message_timeout", turn=turn)
                outcome = "error"
                msg.error_message = "消息执行超时"
                await self._emit(AgentEventType.MESSAGE_ERROR, {
                    "type": "message.error",
                    "message_id": str(msg.id),
                    "message": "消息执行超时",
                    "code": "execution_timeout",
                    "fatal": True,
                })
                break

            # ── 取消检查 ──
            if self._cancel_event.is_set():
                await self._emit_cancelled(msg)
                return "cancelled"

            # 本 turn 内所有历史 append 归属 (msg.id, turn)
            self.context_mgr.set_active_message(msg.id, turn)

            # 本轮流里攒下的工具调用 (chunk, build 步号)，流结束后按读 / 写分段跑
            pending: list[tuple[LLMChunk, int | None]] = []

            # ── Turn span ──
            with tracer.start_as_current_span(
                "message_loop",
                attributes={"agent.turn": turn},
            ):
                # ── 1. 构建上下文 ──
                with tracer.start_as_current_span(
                    "build_context",
                ) as ctx_span:
                    plugin_skills_xml = self._build_plugin_skills_xml()
                    if msg.agent_type:
                        available_skills_xml = plugin_skills_xml
                    else:
                        available_skills_xml = await self._skills.build_available_skills_xml(UUID(msg.user_id)) if self._skills else ""
                    rules_xml = await self._build_rules_xml()
                    agents_xml = self._build_team_members_xml()
                    server_tools = []
                    if available_skills_xml:
                        server_tools.append(SKILL_TOOL_DEFINITION)
                    server_tools.extend(RECALL_TOOLS)
                    if self._memory_recall is not None:
                        server_tools.extend(MEMORY_SEARCH_TOOLS)
                    if self._scene_recall is not None:
                        server_tools.extend(SCENE_READ_TOOLS)
                    # Inject task tool for team lead
                    task_tool_def = self._build_task_tool_definition()
                    if task_tool_def:
                        server_tools.append(task_tool_def)
                    mcp_tools = await self._mcp_tools(msg)
                    # Determine system prompt override
                    prompt_override = None
                    if self._team_lead_system_prompt and msg.agent_type == "team":
                        prompt_override = self._team_lead_system_prompt
                    elif self._agent_system_prompt and msg.agent_type == "expert":
                        prompt_override = self._agent_system_prompt
                    ctx = await self.context_mgr.build(
                        self.session.id, turn, mode, msg.scene_mode,
                        client_tools=self._session_client_tools(),
                        mcp_tools=mcp_tools,
                        server_tools=server_tools,
                        available_skills_xml=available_skills_xml,
                        rules_xml=rules_xml,
                        available_agents_xml=agents_xml,
                        system_prompt_override=prompt_override,
                        offload_store=self._offload_store,
                        shell_env=getattr(self.session, "shell_env", ""),
                        recall_block=recall_block,
                        memory_guide_xml=self._memory_guides(),
                        persona_xml=persona_xml,
                        scene_nav_xml=scene_nav_xml,
                    )
                    ctx_span.set_attributes({
                        "context.estimated_tokens": len(ctx.system_prompt) // 4 + sum(len(json.dumps(m)) // 4 for m in ctx.messages),
                        "context.message_count": len(ctx.messages),
                        "context.tool_def_count": len(ctx.available_tools or []),
                    })

                await self._emit(AgentEventType.CONTEXT_BUILT, {
                    "message_id": str(msg.id),
                    "turn": turn,
                    "messages": len(ctx.messages),
                    "tools": len(ctx.available_tools or []),
                })

            # ── 模型解析：本轮真正发请求要用的客户端与能力参数 ──
            # 逐轮现取而不是会话级缓存：`model` 是**消息级**字段（"重新生成"旧消息要用
            # 当时那个模型），缓存会让重跑用错模型。解析器内部按 model_key 缓存客户端
            # 实例，所以现取不会真的重建 httpx 连接池。
            resolved = await self.resolver.resolve(
                msg.model or getattr(self.session, "model", "")
            )

            # ── 上下文压缩 (10.5) ──
            # 上限取**主模型**的窗口（真正约束请求的是它，不是压缩模型）；
            # 摘要客户端取便宜模型档。
            compression = await self.resolver.resolve(self._compression_model_key)
            ctx.messages, comp_report = await self._compressor.compress(
                session_id=str(self.session.id),
                messages=ctx.messages,
                system_prompt=ctx.system_prompt,
                tools=ctx.available_tools,
                current_turn=turn,
                mode=mode,
                llm_client=compression.client,
                model_context_limit=resolved.context_window,
                compress_threshold_tokens=resolved.compress_threshold_tokens,
                chars_per_token=resolved.chars_per_token,
            )
            if comp_report.compressed:
                self._log.info(
                    "context_compressed",
                    turn=turn,
                    original_tokens=comp_report.original_tokens,
                    compressed_tokens=comp_report.compressed_tokens,
                    ratio=f"{comp_report.compression_ratio:.2f}",
                    layers=comp_report.layers,
                )
                ctx_span.set_attributes({
                    "context.compressed": True,
                    "context.compression_ratio": f"{comp_report.compression_ratio:.2f}",
                })

            if turn == 0:
                logger.info("context_system\n%s", ctx.system_prompt)
            logger.info(
                "context_ready  turn=%d  messages=%d  system_len=%d",
                turn, len(ctx.messages), len(ctx.system_prompt),
            )

            # ── 2. LLM 流式调用 ──
            # ── llm.before hooks ──
            llm_before_input = {
                "provider": settings.llm_provider,
                "model": msg.model or settings.default_model,
                "messages": ctx.messages,
                "tools": ctx.available_tools,
                "thinking_budget": None,
            }
            llm_before = await self.hooks.run(
                "llm.before", llm_before_input,
                session_id=str(self.session.id), user_id=str(msg.user_id),
                message_id=str(msg.id), turn=turn,
            )
            if llm_before.action == HookAction.STOP:
                raise LLMBlockedError("llm.before", llm_before.reason)
            if llm_before.modified_input:
                ctx.messages = llm_before.modified_input.get("messages", ctx.messages)
                if "tools" in llm_before.modified_input:
                    ctx.available_tools = llm_before.modified_input["tools"]

            # ── LLM call span ──
            with tracer.start_as_current_span(
                "llm_call",
                attributes={
                    # 报**实际解析出来的**模型，不是 settings 里那个全局默认值 ——
                    # 否则 trace 里每个模型看起来都跑在同一个模型上。
                    "llm.provider": resolved.protocol,
                    "llm.model": resolved.model_api_name or resolved.model_key,
                    "llm.stream": True,
                    "agent.turn": turn,
                },
            ) as llm_span:
                llm_start = time.monotonic()
                response_chunks: list[LLMChunk] = []
                llm_text: list[str] = []
                llm_reasoning: list[str] = []
                plan_reask = False
                first_token_sent = False
                tokens_in = 0
                tokens_out = 0
                async for chunk in resolved.client.stream(
                    messages=ctx.messages,
                    system=ctx.system_prompt,
                    tools=ctx.available_tools,
                    tool_choice="none" if mode == "ask" else "auto",
                ):
                    # ── 取消检查 ──
                    if self._cancel_event.is_set():
                        await self._emit_cancelled(msg)
                        return "cancelled"
                    # ── 首个 token 到达（TTFT） ──
                    if not first_token_sent and chunk.type in ("thinking", "text"):
                        first_token_sent = True
                        ttft_ms = int((time.monotonic() - llm_start) * 1000)
                        llm_span.add_event("first_token", {"ttft_ms": ttft_ms})
                        await self._emit(AgentEventType.LLM_FIRST_TOKEN, {
                            "turn": turn, "model": msg.model, "ttft_ms": ttft_ms,
                        })
                    # ── 重试通知 ──
                    if chunk.type == "retry":
                        await self._emit(AgentEventType.SYSTEM_STATUS, {
                            "type": "system.status",
                            "message_id": str(msg.id),
                            "code": chunk.retry_code or "llm_retrying",
                            "message": chunk.delta,
                            "detail": None,
                            "attempt": chunk.retry_attempt or 0,
                            "max_retries": chunk.retry_max or 0,
                        })
                    # ── 文本 + 思考：所有模式直接流推客户端 ──
                    elif chunk.type in ("thinking", "text"):
                        if chunk.type == "thinking":
                            llm_reasoning.append(chunk.delta)
                        else:
                            llm_text.append(chunk.delta)
                        await self._push_chunk({
                            "type": f"agent.{chunk.type}",
                            "delta": chunk.delta,
                            "turn": turn,
                            "message_id": str(msg.id),
                        })
                    # ── 工具调用 ──
                    elif chunk.type == "tool_use":
                        if mode == "ask":
                            continue  # Ask 模式不应出现 tool_use，忽略并继续

                        # 死循环检测（检测到后注入上下文警告，不终止）
                        await self._check_loop_detection(chunk)

                        # JSON 解析失败：错误注入上下文让 LLM 修正
                        tool_input = chunk.tool_input or {}
                        if tool_input.get("_parse_error"):
                            self._log.warning(
                                "tool_parse_error", tool=chunk.tool_name,
                                error=tool_input.get("_error"),
                            )
                            error_feedback = (
                                f"工具调用 '{chunk.tool_name}' 的参数 JSON 解析失败: {tool_input.get('_error')}。"
                                f"原始参数文本: {tool_input.get('_raw_arguments', '')}。"
                                "请修正 JSON 格式后重新调用。"
                            )
                            await self.context_mgr.append_text(
                                self.session.id, "user", f"[系统: {error_feedback}]"
                            )
                            msg.tool_calls_count += 1
                            continue

                        # Plan 模式未确认时，LLM 只能调 plan.question 追问
                        if mode == "plan" and not plan_confirmed and chunk.tool_name == "plan_question":
                            # 先将提问前的 assistant 文本刷入上下文，保证顺序
                            if llm_text or llm_reasoning:
                                await self.context_mgr.append_text(
                                    self.session.id, "assistant", "".join(llm_text),
                                    reasoning="".join(llm_reasoning) if llm_reasoning else None,
                                )
                                llm_text.clear()
                                llm_reasoning.clear()
                            # 提问前若模型还发过别的工具（Plan 未确认时并非只能提问），
                            # 先把它们跑完并落历史——否则攒在 pending 里被 break 丢掉。
                            if pending:
                                await self._run_segments(msg, pending, turn)
                                pending.clear()
                            tool_input = chunk.tool_input or {}
                            await self._emit(AgentEventType.PLAN_QUESTION, {
                                "type": "plan.question",
                                "message_id": str(msg.id),
                                "question": tool_input.get("question", ""),
                                "options": tool_input.get("options"),
                                "input_type": tool_input.get("input_type", "select"),
                                "context": tool_input.get("context"),
                            })
                            answer = await self.sync_waiter.wait(
                                user_wait_key(self.session.id, msg.id), timeout=None,
                            )
                            if self._cancel_event.is_set():
                                return "cancelled"
                            if answer is None:
                                await self._push_chunk({
                                    "type": "plan.question_timeout",
                                    "message_id": str(msg.id),
                                })
                                await self.context_mgr.append_text(
                                    self.session.id, "user",
                                    "[用户未回应此问题，请跳过并继续]"
                                )
                            else:
                                await self.context_mgr.append_user_response(
                                    self.session.id,
                                    tool_input.get("question", ""),
                                    str(answer),
                                )
                            plan_reask = True
                            break  # 退出当前流，下一轮带着答案重新调 LLM

                        # 工具执行前，先把已积累的文本+reasoning刷入上下文。
                        # 仅在首轮有内容时写入；后续连续 tool call 由
                        # append_tool_call_to_last_assistant 自动合并到同一行。
                        full_text = "".join(llm_text)
                        full_reasoning = "".join(llm_reasoning) if llm_reasoning else None
                        if full_text or full_reasoning:
                            await self.context_mgr.append_text(
                                self.session.id, "assistant", full_text,
                                reasoning=full_reasoning,
                            )
                            self._log.info(
                                "assistant_response", turn=turn,
                                reasoning_len=len(full_reasoning or ""),
                                text_len=len(full_text),
                                thinking=(full_reasoning or "")[:500], text=full_text[:500],
                            )
                        llm_text.clear()
                        llm_reasoning.clear()

                        tool_name = chunk.tool_name or "unknown"
                        try:
                            tool_policy = self.permission.build_policy(
                                {"workspace": getattr(self.session, "workspace", "") or ""}
                            )
                        except Exception:
                            tool_policy = None
                        self._log.info(
                            "tool_call", turn=turn, name=tool_name,
                            input=json.dumps(chunk.tool_input or {}, ensure_ascii=False),
                            policy=json.dumps(tool_policy, ensure_ascii=False, default=str)
                            if tool_policy is not None else None,
                        )
                        # 工具调用只在流尾一次性吐出，先攒着：等流结束按读 / 写分段跑
                        # （读段并发、写段串行）。步号在到达时定序，与模型发出顺序一致。
                        if mode == "build":
                            build_step += 1
                            pending.append((chunk, build_step))
                        else:
                            pending.append((chunk, None))

                    response_chunks.append(chunk)

                # ── LLM span events ──
                tokens_in = 0
                tokens_out = 0
                stop_reason = ""
                for c in response_chunks:
                    if c.type == "end_turn" and c.usage:
                        tokens_in = c.usage.get("input_tokens", 0)
                        tokens_out = c.usage.get("output_tokens", 0)
                    if c.type == "end_turn":
                        stop_reason = c.stop_reason or ""
                llm_span.add_event("tokens", {
                    "input": tokens_in, "output": tokens_out,
                })
                if stop_reason:
                    llm_span.set_attribute("llm.stop_reason", stop_reason)

            # ── 将本 turn assistant 回复写回对话历史 ──
            full_text = "".join(llm_text)
            full_reasoning = "".join(llm_reasoning) if llm_reasoning else None
            if full_text or full_reasoning:
                await self.context_mgr.append_text(
                    self.session.id, "assistant", full_text, reasoning=full_reasoning,
                )
                self._log.info("assistant_response", turn=turn,
                               reasoning_len=len(full_reasoning or ""),
                               text_len=len(full_text))

            # ── 跑本轮流里攒下的工具：读段并发、写段串行，结果按模型发出顺序落历史 ──
            # 位置必须夹在 assistant 文本写回之后：工具行先落库就会破坏
            # context.append_tool_result 依赖的"reasoning_content 与 tool_calls 同消息"。
            if pending:
                if await self._run_segments(msg, pending, turn):
                    terminal = True
                pending.clear()

            # ── plan_question 拿到答案后，用新上下文重启 turn ──
            if plan_reask:
                # plan_question 分支已就地跑完 pending；这里还有剩 = 那条路被改坏了，
                # 真放过去就是工具静默消失
                assert not pending, "plan_question 早退漏跑 pending 工具"
                continue

            # ── Plan 模式 turn 0 兜底 ──
            if mode == "plan" and not plan_confirmed and turn == 0:
                has_plan_question = any(
                    c.type == "tool_use" and c.tool_name == "plan_question"
                    for c in response_chunks
                )
                if not has_plan_question and llm_text:
                    full_text = "".join(llm_text)
                    if self._looks_like_question(full_text):
                        self._log.warning("plan_text_question_detected")
                        await self.context_mgr.append_text(
                            self.session.id, "user",
                            "[系统提示：你违反了 Plan 模式的规则——必须在 plan_question 工具中提问，"
                            "不能直接在文本中提问。请重新调用 plan_question 工具。]"
                        )
                        continue

            # ── LLM turn 日志 + token 统计 + Metrics ──
            llm_ms = (time.monotonic() - llm_start) * 1000
            for c in response_chunks:
                if c.type == "end_turn" and c.usage:
                    msg.tokens_in += c.usage.get("input_tokens", 0)
                    msg.tokens_out += c.usage.get("output_tokens", 0)
            # ── 校准 token 计数器 ──
            if hasattr(self, '_token_counter') and msg.tokens_in > 0:
                total_text = ctx.system_prompt
                for m in ctx.messages:
                    total_text += json.dumps(m, ensure_ascii=False)
                self._token_counter.calibrate(total_text, msg.tokens_in)

            self._log.info("llm_turn", turn=turn, mode=mode,
                           events=len(response_chunks), duration_ms=int(llm_ms),
                           tokens_in=msg.tokens_in, tokens_out=msg.tokens_out)

            # ── OTel Metrics ──
            provider = settings.llm_provider
            model = msg.model or settings.default_model
            llm_call_duration.record(llm_ms / 1000, attributes={
                "model": model, "provider": provider, "status": "ok",
            })
            llm_token_usage.add(tokens_in, attributes={"model": model, "token_type": "input"})
            llm_token_usage.add(tokens_out, attributes={"model": model, "token_type": "output"})

            # ── EventBus: LLM_CALL_COMPLETED (仅 Audit) ──
            await self._emit(AgentEventType.LLM_CALL_COMPLETED, {
                "turn": turn, "model": model,
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "duration_ms": int(llm_ms),
            })

            # ── llm.after hooks ──
            last_usage = {}
            stop_reason = ""
            content_list = []
            tool_calls_list = []
            for c in response_chunks:
                if c.type == "end_turn" and c.usage:
                    last_usage = c.usage
                if c.type == "end_turn":
                    stop_reason = c.stop_reason or ""
                if c.type in ("text", "thinking"):
                    content_list.append({"type": c.type, "text": c.delta})
                if c.type == "tool_use":
                    tool_calls_list.append({
                        "name": c.tool_name or "",
                        "args": c.tool_input or {},
                    })

            await self.hooks.run(
                "llm.after", {
                    "stop_reason": stop_reason,
                    "tokens": {"input": last_usage.get("input_tokens", 0),
                               "output": last_usage.get("output_tokens", 0)},
                    "content": content_list,
                    "tool_calls": tool_calls_list,
                },
                session_id=str(self.session.id), user_id=str(msg.user_id),
                message_id=str(msg.id), turn=turn,
            )

            # ── EventBus + Chunk: TOKEN_USAGE ──
            token_data = {
                "type": "token.usage",
                "message_id": str(msg.id),
                "tokens_in": msg.tokens_in,
                "tokens_out": msg.tokens_out,
            }
            await self._emit(AgentEventType.TOKEN_USAGE, token_data)

            # ── 3. 终止判断 ──
            if any(c.stop_reason == "content_filter" for c in response_chunks):
                self._log.warning("content_filter")
                outcome = "error"
                msg.error_message = "内容被安全策略拦截，请修改输入后重试。"
                await self._push_chunk({
                    "type": "message.error",
                    "message_id": str(msg.id),
                    "message": "内容被安全策略拦截，请修改输入后重试。",
                    "code": "content_filter",
                    "fatal": True,
                })
                break

            if any(c.stop_reason == "max_tokens" for c in response_chunks):
                # LLM 输出达 max_tokens 上限被截断：注入续写提示并重跑，最多 N 次
                truncation_retries += 1
                if truncation_retries <= settings.max_truncation_retries:
                    self._log.warning(
                        "llm_truncated_continue",
                        attempt=truncation_retries,
                        max_retries=settings.max_truncation_retries,
                    )
                    await self._emit(AgentEventType.SYSTEM_STATUS, {
                        "type": "system.status",
                        "message_id": str(msg.id),
                        "code": "llm_continuing",
                        "message": (
                            f"输出达长度上限，正在从中断处继续 "
                            f"({truncation_retries}/{settings.max_truncation_retries})..."
                        ),
                        "detail": None,
                        "attempt": truncation_retries,
                        "max_retries": settings.max_truncation_retries,
                    })
                    await self.context_mgr.append_text(
                        self.session.id, "user", _TRUNCATION_CONTINUE_PROMPT,
                    )
                    # 不置 terminal：落到本轮末尾的 turn += 1，自然进入下一轮续写
                else:
                    self._log.warning("llm_truncated_exhausted", attempts=truncation_retries)
                    outcome = "error"
                    msg.error_message = "输出多次达到长度上限，已停止生成。"
                    await self._push_chunk({
                        "type": "message.error",
                        "message_id": str(msg.id),
                        "message": "输出多次达到长度上限，已停止生成。",
                        "code": "max_tokens_exceeded",
                        "fatal": True,
                    })
                    break
            elif any(c.stop_reason == "end_turn" for c in response_chunks):
                if mode == "plan" and not plan_confirmed:
                    await self._emit(AgentEventType.PLAN_GENERATED, {
                        "type": "plan.generated",
                        "message_id": str(msg.id),
                    })
                    result = await self.sync_waiter.wait(
                        user_wait_key(self.session.id, msg.id), timeout=None,
                    )
                    if result == "confirmed":
                        await self._emit(AgentEventType.PLAN_INTERACTION, {
                            "action": "confirmed",
                            "message_id": str(msg.id),
                        })
                        plan_confirmed = True
                        turn += 1
                        continue
                    elif result == "rejected":
                        await self._emit(AgentEventType.PLAN_INTERACTION, {
                            "action": "rejected",
                            "message_id": str(msg.id),
                        })
                        await self.context_mgr.append_text(
                            self.session.id, "assistant",
                            "计划已取消，请重新描述您的需求。"
                        )
                        terminal = True
                    elif result == "edited":
                        await self._emit(AgentEventType.PLAN_INTERACTION, {
                            "action": "edited",
                            "message_id": str(msg.id),
                        })
                        continue
                    else:
                        terminal = True
                else:
                    terminal = True
            elif mode == "ask":
                terminal = True

            # ── 挂起等子（§9.11.8 / 设计决定 A）──
            # 本轮已收尾但还有在途子任务时，父**不能**就此终结：题面要求子结果回来后
            # 在**同一条消息、同一 turn 序列**上续跑，所以这里是**内联 await**，不是
            # 提前 return 等重入。理由：_execute_message 的 finally 无条件写终态 +
            # completed_at，提前返回要再穿一个 outcome 值进去；重入又会重跑
            # _bootstrap_message，把 role=user 锚点行写第二遍。
            if terminal and outcome == "completed" and self._in_flight:
                forced = await self._suspend_for_children(msg, turn, start_time)
                if forced:
                    outcome = forced          # 预算耗尽：内部已 emit 终态
                else:
                    terminal = False          # 子都回来了 → 让 while 自然续跑

            turn += 1
            # ── End of message_loop span ──

        # ── 汇总 ──
        msg.turn_count = turn

        # ── 循环体正常退出但取消被置位（build-abort / plan-reject / 跨 await 的 cancel）
        #    → 归入 cancelled，不跑 message.after（避免对半截内容做收尾处理）──
        if outcome == "completed" and self._cancel_event.is_set():
            await self._emit_cancelled(msg)
            return "cancelled"

        # ── message.after hooks ──
        await self.hooks.run(
            "message.after", {
                "status": outcome,
                "total_turns": turn,
                "tokens": {"input": msg.tokens_in, "output": msg.tokens_out},
                "cost": None,
                "response_text": "",
            },
            session_id=str(self.session.id), user_id=str(msg.user_id),
            message_id=str(msg.id), turn=turn,
        )

        return outcome

    # ═══════════════════════════════════════════════════════════
    # 异步派发：父挂起等子 / 子回投结果（§9.11.8）
    # ═══════════════════════════════════════════════════════════

    async def _suspend_for_children(
        self, msg: Message, turn: int, start_time: float,
    ) -> str | None:
        """进 WAITING_CHILDREN 等所有在途子任务回信，返回 None = 可以续跑。

        返回 "error" = 消息总预算在挂起期耗尽（已 emit 终态，不再续跑）。
        被取消则返回 None，交给循环顶部的取消检查点收口——父对 POST /cancel 的
        反应依赖 request_cancel 同时 set _children_event（两个软取消检查点都不在挂起路径上）。

        **不持 _lock**：子投递结果时要走 deliver_result 拿同一把 _lock，父若持锁去等
        就成了自杀式互等。挂起点只碰自己的状态与子结果，不需要那把锁。
        """
        self.state = "WAITING_CHILDREN"
        msg.turn_count = turn
        await self._message_repo.update(msg)
        self._log.info("waiting_children", in_flight=len(self._in_flight), turn=turn)
        await self._push_chunk({
            "type": "task.waiting",
            "message_id": str(msg.id),
            # 顶层的 agent_id 是**父（lead）那一列**，与 agent.status / session.publish
            # 同义；下面 in_flight 每一项里的 agent_id 是**成员**的扁平 id，客户端拿它
            # 去匹配委派卡片的 `to`（§9.11.13：展示层只用扁平 id，不出现路径）。
            "agent_id": msg.agent_id,
            "turn": turn,
            "in_flight": [
                {
                    "task_id": task_id,
                    "agent_path": info["agent_path"],
                    "agent_id": agent_id_from_path(info["agent_path"]),
                }
                for task_id, info in self._in_flight.items()
            ],
        })

        try:
            while self._in_flight and not self._cancel_event.is_set():
                # 每轮先取走已到的结果：clear() 与 wait() 之间没有 await，信号不会丢，
                # 但"结果在上一轮 drain 之后才落库"这件事只能靠下一轮的 drain 追上，
                # 所以 drain 必须在 wait **之前**（否则会等到切片超时才醒）。
                await self._drain_child_results(msg)
                if not self._in_flight:
                    break

                remaining = settings.message_timeout_seconds - (time.monotonic() - start_time)
                if remaining <= 0:
                    self._log.warning("waiting_children_timeout", turn=turn)
                    msg.error_message = "等待子 agent 回信超时"
                    await self._emit(AgentEventType.MESSAGE_ERROR, {
                        "type": "message.error",
                        "message_id": str(msg.id),
                        "message": "等待子 agent 回信超时",
                        "code": "execution_timeout",
                        "fatal": True,
                    })
                    return "error"
                try:
                    await asyncio.wait_for(
                        self._children_event.wait(),
                        # 切片上限：sync_wait_timeout_seconds 只是唤醒周期，不是总预算
                        timeout=min(remaining, settings.sync_wait_timeout_seconds),
                    )
                except asyncio.TimeoutError:
                    pass
                self._children_event.clear()
        finally:
            # 取消 / 超时也复位：引擎不能停在 WAITING_CHILDREN 上
            self.state = "PROCESSING"
            msg.turn_count = turn
        return None

    async def _drain_child_results(self, msg: Message) -> None:
        """取走邮箱里已到的子结果，以 role=user 注入父上下文（§9.11.8）。

        注入行**必须**带结构化前缀：否则它与用户手敲的文字在历史里不可区分，模型会把
        "子 agent 的产出"当成用户新指令，重跑 / 审计也认不出这段的来路。
        """
        rows = await self._message_repo.list_pending_results(
            self.session.id, self.session.agent_path,
        )
        for row in rows:
            task_id = row.cid or ""
            info = self._in_flight.pop(task_id, None)
            payload = self._decode_result_payload(row.content)
            if info is None:
                # 进程重启后 _in_flight 是空的，重启前派的子结果会落到这里（§9.11.8 缺口）
                self._log.warning("orphan_child_result", task_id=task_id,
                                  sender=row.sender_agent_id)
            agent_path = info["agent_path"] if info else (row.sender_agent_id or "?")
            status = payload.get("status") or "completed"
            output = payload.get("output") or ""
            header = f"[子 agent {agent_path} 的产出 · task_id={task_id}]"
            if status == "completed":
                block = f"{header}\n{output or NO_OUTPUT_PLACEHOLDER}"
            else:
                block = (
                    f"{header}\n子任务未正常完成（{status}）："
                    f"{payload.get('error') or output or '无详情'}"
                )
            await self.context_mgr.append_text(self.session.id, "user", block)
            # 标为已消费：否则下一轮 drain 会反复读到同一行
            row.status = MessageStatus.COMPLETED
            await self._message_repo.update(row)
            # 这两个字段的方向不能反：客户端把 `agent_id` 当作**列**（lead 的扁平 id）、
            # `to` 当作列里那张委派卡片的收件人（成员的扁平 id）——它靠
            # `a.id === agent_id` 找列、再靠 `delegation.to === to` 找卡片来收尾
            # （multiAgentStore.ts:707-722）。填成路径就等于两边都查不到，
            # 卡片会永远停在"执行中"。
            await self._push_chunk({
                "type": "agent.status",
                "agent_id": msg.agent_id,
                "to": agent_id_from_path(agent_path),
                "status": "done" if status == "completed" else "failed",
                "output_preview": (output or payload.get("error") or "")[:200] or None,
                # 全文单列一个字段：卡片折叠态用上面那 200 字预览，展开态要读全文，
                # 前端拿不到的东西没法展开。回落与 preview 保持一致——否则失败时
                # 预览有错误串、点开却是空的。
                "output": (output or payload.get("error") or "") or None,
            })

    @staticmethod
    def _decode_result_payload(content: str | None) -> dict:
        try:
            payload = json.loads(content or "{}")
        except (json.JSONDecodeError, TypeError):
            return {"output": content or ""}
        return payload if isinstance(payload, dict) else {"output": content or ""}

    async def _last_assistant_text(self, message_id: UUID) -> str:
        """本消息最后一段**非空**助手文本 = 本回合的产出（§9.11.9）。

        取"最后一段非空"而不是"最后一行"：以工具调用收尾的回合，末行为空串，
        但那不代表子没产出——它已经说过结论了。确实一句都没说才回退到占位符。
        """
        try:
            rows = await self.context_mgr.list_rows(self.session.id)
        except Exception:
            self._log.exception("child_result_read_history_failed")
            return ""
        for row in reversed(rows):
            if row.get("message_id") != str(message_id):
                continue
            if row.get("role") == "assistant" and (row.get("content") or "").strip():
                return row["content"]
        return ""

    async def _report_to_parent(self, msg: Message, outcome: str, error: str | None) -> None:
        """子回合终态 → 往父邮箱投 result 信封（§9.11.7 / 设计决定 E）。

        发送点必须在**子自己**这里：异步派发后父已经不读子的 chunk 队列了，
        只有子知道这一刻回合结束了。父靠 deliver_result 置 _children_event 醒来。
        """
        # task_id 取自**本条消息**的 cid，不是引擎级字段：followup 复用同一子会话，
        # 同一引擎会接连跑多次派发，引擎级字段会被后一次派发覆盖，先跑完的那条
        # 就会拿着别人的 task_id 回投（结果互相顶掉）。
        # 这条守卫同时是**归还并发名额**的准绳：cid 与 _parent_agent_path 都只在派发
        # 时盖上，所以"没有 cid"就等于"这个回合没占名额"——重跑子会话里的消息
        # 会走到这里，必须原样返回，否则它会去还一个别人占着的名额。
        if self._parent_agent_path is None or msg.cid is None:
            return
        try:
            payload = {
                "status": outcome,
                "output": await self._last_assistant_text(msg.id),
                "error": error,
            }
            await self._mail.send(
                sender_path=self.session.agent_path,
                root_session_id=self.session.root_session_id or self.session.id,
                recipient_path=self._parent_agent_path,
                msg_type=MSG_RESULT,
                payload=payload,
                cid=UUID(msg.cid),
            )
        except Exception:
            # 投不出去不能让子自己的终态受影响：父会挂到预算耗尽，然后按超时收口
            self._log.exception("child_result_send_failed", task_id=msg.cid)
        finally:
            # 名额在此归还：**不放在父的收口路径**上——父可能被取消 / 超时而仍在途，
            # 那样名额就永久泄漏，几次之后就再也派不出子任务了。
            if self._mgr is not None:
                self._mgr.release_child_slot()

    # ═══════════════════════════════════════════════════════════
    # 工具执行
    # ═══════════════════════════════════════════════════════════

    async def _check_tool_permission(self, tool_name: str, tool_input: dict) -> tuple[str, str]:
        """三态权限检查（纯判定：不写历史、不推事件）。

        返回 ('skip' | 'needs_approval' | 'forbidden', 原因)。forbidden 的事件推送与
        历史收口交给 _finalize_tool —— 回灌顺序必须按模型发出顺序，不能在这儿抢写。
        """
        try:
            verdict = self.permission.evaluate(
                tool_name, tool_input or {},
                {
                    "workspace": getattr(self.session, "workspace", "") or "",
                    "shell_env": getattr(self.session, "shell_env", "") or "",
                },
            )
        except Exception:
            self._log.exception("permission_evaluate_error", tool=tool_name)
            return "skip", ""  # 引擎异常不误伤，放行交给沙箱兜底

        if verdict.verdict == "forbidden":
            self._log.warning("tool_permission_denied", tool=tool_name, reason=verdict.reason)
            tool_permission_denied_total.add(1, attributes={"tool_name": tool_name})
            return "forbidden", verdict.reason or ""
        return verdict.verdict, ""

    async def _execute_tool(
        self, msg: Message, chunk: LLMChunk, turn: int,
        step: int | None = None,
    ) -> ToolOutcome:
        """执行单个工具调用：权限 → 发起 → 等结果。**不写历史**（写段 / 单条走这条）。

        历史/事件/钩子的收口交给 _finalize_tool，由分段执行器按模型发出顺序调用；
        权限被禁也照样返回 outcome，同轮历史顺序才等于模型发出顺序。
        """
        dispatched = await self._dispatch_tool(msg, chunk, turn, step)
        if isinstance(dispatched, ToolOutcome):
            return dispatched
        return await self._await_tool(msg, dispatched)

    async def _dispatch_tool(
        self, msg: Message, chunk: LLMChunk, turn: int,
        step: int | None = None,
    ) -> "ToolOutcome | _PendingTool":
        """发起单个工具调用：权限 → tool.before → 落 issued 账 → 推 client.tool_request。

        返回 _PendingTool（已下发，还等着回投 / 待执行）或 ToolOutcome（已定论：
        权限禁止 / Hook 阻止 / task 委派完毕）。拆出来是为了让读段能把整段的请求
        先推完再一起等，而不是"推一条等一条"。
        """
        tool_name = chunk.tool_name or "unknown"
        tc_id = chunk.tool_call_id or str(uuid4())
        tool_input = chunk.tool_input or {}
        tool_start = time.monotonic()

        verdict, reason = await self._check_tool_permission(tool_name, tool_input)
        if verdict == "forbidden":
            return ToolOutcome(
                chunk=chunk, kind="forbidden", tool_name=tool_name, tc_id=tc_id,
                tool_input=tool_input, turn=turn, step=step,
                result={"success": False, "error": f"权限禁止: {reason}"},
                reason=reason, location=self.tool_dispatcher.classify(tool_name).value,
            )
        requires_approval = verdict == "needs_approval"

        # ── Task tool interception ──
        if tool_name == "task" and self._task_handler:
            agent_path = tool_input.get("agent_path", "")
            prompt = tool_input.get("prompt", "")
            # 展示层用扁平 id，路径本身不进展示（§9.11.13）
            member_id = agent_id_from_path(agent_path)
            lead_agent_id = self._current_msg.agent_id if self._current_msg else self._team_lead_agent_id

            # Push session.publish event
            await self._push_chunk({
                "type": "session.publish",
                "agent_id": lead_agent_id,
                "to": member_id,
                "task_type": "delegate",
                "prompt": prompt,
            })

            # 派发（§9.11.8）：只建会话 / fork / 投递，**不等**子回合结束。
            # 父本轮收尾时会看到 _in_flight 非空，进 WAITING_CHILDREN 挂起。
            try:
                result = await self._task_handler.execute(
                    parent_session_id=self.session.id,
                    parent_user_id=str(msg.user_id),
                    parent_agent_path=self.session.agent_path,
                    agent_path=agent_path,
                    prompt=prompt,
                    team_plugin_path=self._team_plugin_path,
                    team_members=self._team_members,
                    parent_chunk_queue=self._chunk_queue,
                    mode=tool_input.get("mode", "spawn"),
                    fork_turns=tool_input.get("fork_turns", ""),
                    lead_agent_id=lead_agent_id,
                    parent_shell_env=getattr(self.session, "shell_env", ""),
                    parent_context_mgr=self.context_mgr,
                    parent_root_session_id=self.session.root_session_id or self.session.id,
                    parent_client_tools=self.session.client_tools,
                    parent_workspace=self.session.workspace,
                    parent_model=self.session.model,
                )
            except Exception as exc:
                self._log.exception("task_handler_error", agent=agent_path)
                result = {"success": False, "error": str(exc), "agent_path": agent_path}

            # 失败句柄不进在途清单——否则父会一直等一个永远不会来的结果
            if result.get("success") and result.get("task_id"):
                self._in_flight[result["task_id"]] = {
                    "agent_path": agent_path,
                    "dispatched_at": time.monotonic(),
                }

            # `running` 而不是自造一个 `dispatched`：客户端的状态文案与配色是
            # 按 {idle, thinking, running, done, waiting, failed} 穷举的，
            # 表里没有的值会渲染成空白标签（MultiAgentPanel STATUS_TEXT）。
            await self._push_chunk({
                "type": "agent.status",
                "agent_id": lead_agent_id,
                "to": member_id,
                "status": "running" if result.get("success") else "failed",
                "output_preview": None if result.get("success") else result.get("error"),
            })

            # 历史注入 + tool.after 钩子交给收口阶段（保证同轮历史按模型发出顺序）
            return ToolOutcome(
                chunk=chunk, kind="task", tool_name=tool_name, tc_id=tc_id,
                tool_input=tool_input, turn=turn, step=step, result=result,
                location=ToolLocation.CLIENT.value,
                duration_ms=int((time.monotonic() - tool_start) * 1000),
            )

        # ── tool.before hooks ──
        location = self.tool_dispatcher.classify(tool_name)
        try:
            tool_before = await self.hooks.run(
                "tool.before", {
                    "tool_name": tool_name,
                    "args": chunk.tool_input or {},
                    "location": location.value,
                },
                session_id=str(self.session.id), user_id=str(msg.user_id),
                message_id=str(msg.id), turn=turn,
            )
        except Exception:
            self._log.exception("hook_tool_before_error", tool=tool_name)
            tool_before = None

        if tool_before is not None and tool_before.action == HookAction.STOP:
            self._log.warning("tool_blocked_by_hook", tool=tool_name, reason=tool_before.reason)
            return ToolOutcome(
                chunk=chunk, kind="hook_blocked", tool_name=tool_name, tc_id=tc_id,
                tool_input=chunk.tool_input or {}, turn=turn, step=step,
                result={"success": False, "error": f"Hook 阻止: {tool_before.reason}"},
                reason=tool_before.reason or "", location=location.value,
                duration_ms=int((time.monotonic() - tool_start) * 1000),
            )
        if tool_before is not None and tool_before.modified_input:
            if "args" in tool_before.modified_input:
                chunk.tool_input = tool_before.modified_input["args"]

        # ── 发起（先落 issued 账再下发，结果回来 mark，防重复消费）──
        # 这里**不等**结果：读段要把整段请求先推完，再由 _await_tool 并发等回投。
        if location == ToolLocation.CLIENT:
            # 客户端工具：invocation_id == request_id
            invocation_id = str(uuid4())
            request_id = invocation_id
            await self._record_issued(
                msg, invocation_id, tool_name, chunk.tool_input or {},
                location.value, turn, requires_approval=requires_approval,
            )
            try:
                policy = self.permission.build_policy(
                    {"workspace": getattr(self.session, "workspace", "") or ""}
                )
            except Exception:
                self._log.exception("permission_build_policy_error", tool=tool_name)
                policy = None
            await self._push_chunk({
                "type": "client.tool_request",
                "request_id": request_id,
                "tool_name": tool_name,
                "input": chunk.tool_input or {},
                "message_id": str(msg.id),
                "tool_call_id": tc_id,
                "step": step,
                "requires_approval": requires_approval,
                "policy": policy,
                # C-3：幂等档随包下发，客户端据 hint 决定超时/断线后能否自动重试
                "idempotency": _tool_idempotency(tool_name),
            })
        else:
            # 服务端工具：记忆/召回，引擎直接读写 DB
            invocation_id = str(uuid4())
            request_id = ""
            await self._record_issued(
                msg, invocation_id, tool_name, chunk.tool_input or {},
                location.value, turn,
            )

        return _PendingTool(
            chunk=chunk, tool_name=tool_name, tc_id=tc_id,
            tool_input=chunk.tool_input or {}, turn=turn, step=step,
            location=location.value, start=tool_start,
            invocation_id=invocation_id, request_id=request_id,
            requires_approval=requires_approval,
        )

    async def _await_tool(self, msg: Message, pending: _PendingTool) -> ToolOutcome:
        """等一次已发起的调用出结果：客户端回投 / 服务端直执行。**不写历史**。"""
        chunk = pending.chunk
        tool_name = pending.tool_name
        tc_id = pending.tc_id
        tool_input = pending.tool_input
        turn = pending.turn
        step = pending.step
        location = ToolLocation(pending.location)
        invocation_id = pending.invocation_id
        request_id = pending.request_id

        def _elapsed_ms() -> int:
            return int((time.monotonic() - pending.start) * 1000)

        result: dict = {}
        with tracer.start_as_current_span(
            "tool_dispatch",
            attributes={"tool.name": tool_name, "tool.location": location.value},
        ):
            with tracer.start_as_current_span("tool_execute") as tool_span:
                if location == ToolLocation.CLIENT:
                    result_val = await self.sync_waiter.wait(
                        tool_wait_key(self.session.id, invocation_id),
                        timeout=self._tool_wait_timeout,
                    )
                    if result_val == "abort":
                        # 用户取消：账留 issued（可能已执行），交恢复/对账裁决；不收口历史
                        return ToolOutcome(
                            chunk=chunk, kind="executed", tool_name=tool_name, tc_id=tc_id,
                            tool_input=tool_input, turn=turn, step=step,
                            result={}, location=location.value, abort=True,
                            duration_ms=_elapsed_ms(),
                        )
                    if isinstance(result_val, dict) and result_val.get("skipped"):
                        await self._mark_invocation(
                            invocation_id, InvocationState.SKIPPED, error="user_skipped",
                        )
                        return ToolOutcome(
                            chunk=chunk, kind="skipped", tool_name=tool_name, tc_id=tc_id,
                            tool_input=tool_input, turn=turn, step=step,
                            result={}, location=location.value,
                            duration_ms=_elapsed_ms(),
                        )
                    if result_val is None:
                        # 首段等待超时：不立刻判失败，进 C-2 对账窗让客户端补投/声明未知
                        reconciled = await self._reconcile_client_tool(
                            msg, tc_id, tool_name, tool_input,
                            invocation_id, request_id, turn,
                        )
                        if reconciled is None:  # 窗内用户取消（abort）→ 终止 Build
                            return ToolOutcome(
                                chunk=chunk, kind="executed", tool_name=tool_name, tc_id=tc_id,
                                tool_input=tool_input, turn=turn, step=step,
                                result={}, location=location.value, abort=True,
                                duration_ms=_elapsed_ms(),
                            )
                        result = reconciled
                    else:
                        result = result_val if isinstance(result_val, dict) else {"success": False}
                        await self._mark_invocation(
                            invocation_id, InvocationState.COMPLETED, result=result,
                        )
                        await self._emit_network_approvals(result, tool_name, request_id)
                else:
                    if tool_name == "recall":
                        result = await self._execute_recall_tool(tool_input)
                    elif tool_name == "memory_search":
                        result = await self._execute_memory_search(tool_input)
                    elif tool_name == "scene_read":
                        result = await self._execute_scene_read(tool_input)
                    else:
                        result = {"status": "error",
                                  "error": f"未实现的服务端工具: {tool_name}"}
                    await self._mark_invocation(
                        invocation_id, InvocationState.COMPLETED, result=result,
                    )

                tool_ms = _elapsed_ms()
                tool_span.set_attribute("tool.success", result.get("success", False))
                tool_span.set_attribute("tool.duration_ms", tool_ms)

        # ── OTel Metrics ──
        status = "success" if result.get("success", False) else "failed"
        tool_call_total.add(1, attributes={
            "tool_name": tool_name, "location": location.value, "status": status,
        })
        tool_call_duration.record(tool_ms / 1000, attributes={
            "tool_name": tool_name, "location": location.value,
        })

        # 事件 / tool.after 钩子 / 历史注入交给收口阶段（保证同轮历史按模型发出顺序）
        return ToolOutcome(
            chunk=chunk, kind="executed", tool_name=tool_name, tc_id=tc_id,
            tool_input=tool_input, turn=turn, step=step, result=result,
            location=location.value, duration_ms=tool_ms,
        )

    async def _finalize_tool(self, msg: Message, outcome: ToolOutcome) -> None:
        """收口一次工具调用：事件 / 钩子 / 历史注入 / 计数。

        由分段执行器按模型发出顺序**串行**调用——历史拼接（sequence 分配、
        tool_calls 数组合并）是读-改-写，只能单线程按序做。
        """
        if outcome.abort:
            return  # 用户取消：账留 issued，交对账裁决，不落历史

        tool_name = outcome.tool_name
        result = outcome.result

        if outcome.kind == "forbidden":
            await self._emit(AgentEventType.TOOL_PERMISSION_DENIED, {
                "tool_name": tool_name,
                "error": outcome.reason or "权限禁止",
                "location": outcome.location,
            })
        elif outcome.kind == "hook_blocked":
            await self._emit(AgentEventType.TOOL_FAILED, {
                "tool_name": tool_name, "error": f"Hook 阻止: {outcome.reason}",
            })
            await self._push_chunk({
                "type": "error.diagnosis",
                "message_id": str(msg.id),
                "error_code": "hook_denied",
                "severity": "warning",
                "title": "Hook 拦截",
                "explanation": f"工具 '{tool_name}' 被 Hook 阻止: {outcome.reason}",
                "suggestion": "如需执行此操作，请修改输入参数或联系管理员。",
            })
        elif outcome.kind == "skipped":
            await self.context_mgr.append_skip_feedback(
                self.session.id, outcome.tc_id, tool_name, outcome.tool_input,
            )
            return
        elif outcome.kind == "task":
            try:
                await self.hooks.run(
                    "tool.after", {
                        "tool_name": tool_name,
                        "args": outcome.tool_input,
                        "result": result,
                        "duration_ms": outcome.duration_ms,
                    },
                    session_id=str(self.session.id), user_id=str(msg.user_id),
                    message_id=str(msg.id), turn=outcome.turn,
                )
            except Exception:
                pass
        else:  # executed
            await self._emit(AgentEventType.TOOL_EXECUTED, {
                "tool_name": tool_name,
                "location": outcome.location,
                "duration_ms": outcome.duration_ms,
                "success": result.get("success", False),
                "input": outcome.tool_input,
                "output": str(result)[:500] if result else "",
            })
            await self.hooks.run(
                "tool.after", {
                    "tool_name": tool_name,
                    "success": result.get("success", False),
                    "result": json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result),
                    "duration_ms": outcome.duration_ms,
                },
                session_id=str(self.session.id), user_id=str(msg.user_id),
                message_id=str(msg.id), turn=outcome.turn,
            )

        await self.context_mgr.append_tool_result(
            self.session.id, outcome.tc_id, tool_name, outcome.tool_input, result
        )
        msg.tool_calls_count += 1

        if outcome.kind == "executed":
            self._log.info("tool_executed", tool=tool_name, location=outcome.location,
                           success=result.get("success", False),
                           duration_ms=outcome.duration_ms, result=str(result)[:500])

    # ═══════════════════════════════════════════════════════════
    # 单轮多工具的分段调度（docs/chapters/14 §2）
    # ═══════════════════════════════════════════════════════════

    async def _run_segments(
        self, msg: Message, pending: list[tuple[LLMChunk, int | None]], turn: int,
    ) -> bool:
        """把本轮的多个工具调用按读 / 写分段执行，返回是否应终止本轮（abort）。

        读段：整段的请求先一股脑下发，再并发等回投；结果**按模型发出顺序**逐条收口。
        写段：一条一条来，执行完就收口。

        收口必须串行且保序——历史拼接（`MAX(sequence)+1`、tool_calls 数组合并）是
        读-改-写，并发写会丢 tool_call 块、产生孤儿 tool 行。
        """
        offset = 0
        for seg in segment_tool_calls([chunk for chunk, _ in pending], self.scheduling):
            pairs = pending[offset:offset + len(seg.calls)]
            offset += len(seg.calls)
            if seg.kind == READ:
                outcomes = await self._run_read_segment(msg, pairs, turn)
                for outcome in outcomes:
                    await self._finalize_tool(msg, outcome)
                if any(o.abort for o in outcomes):
                    return True
            else:
                for chunk, step in pairs:
                    # 层2 工作区写锁（§9.12.2 / §9.12.9 #9）：按调用取锁，跨会话串行化
                    # 同一工作区的写-写；读段不取锁（读写冲突只读到半截，可恢复，见 §9.12.2）。
                    if chunk.tool_name == "task":
                        # `task` 必须**跳过**取锁：它的真实写发生在子 agent 里，而子自己
                        # 会来取同一把锁——父若持锁等子，子取不到锁，父子双双卡死。
                        # （子 workspace 默认 ""，父若也是空工作区则两把锁合一，必现。）
                        outcome = await self._execute_tool(msg, chunk, turn, step=step)
                        await self._finalize_tool(msg, outcome)
                    else:
                        async with self._workspace_lock:
                            outcome = await self._execute_tool(msg, chunk, turn, step=step)
                            await self._finalize_tool(msg, outcome)
                    if outcome.abort:
                        return True
        return False

    async def _run_read_segment(
        self, msg: Message, pairs: list[tuple[LLMChunk, int | None]], turn: int,
    ) -> list[ToolOutcome]:
        """读段：先全部下发（不等），再并发等回投，按模型发出顺序返回结果。

        下发必须按模型顺序串行（每步含权限判定 / Hook / 工具账本记账这些读-改-写，
        见 §9.12.8）；并发的只有等回投那一段，用 _read_semaphore 给它加个闸——
        否则并发度直接等于模型一轮发出的读调用个数（§9.12.9 #4）。
        """
        dispatched = [
            await self._dispatch_tool(msg, chunk, turn, step=step)
            for chunk, step in pairs
        ]
        pending_tools = [d for d in dispatched if isinstance(d, _PendingTool)]

        async def _gated(p):
            async with self._read_semaphore:
                return await self._await_tool(msg, p)

        awaited = await asyncio.gather(*(_gated(p) for p in pending_tools))
        # 已定论（权限禁止 / Hook 阻止）的按原位插回，保持模型发出顺序
        merged: list[ToolOutcome] = []
        await_iter = iter(awaited)
        for d in dispatched:
            merged.append(next(await_iter) if isinstance(d, _PendingTool) else d)
        return merged

    # ═══════════════════════════════════════════════════════════
    # Rules 全文构建
    # ═══════════════════════════════════════════════════════════

    async def _build_rules_xml(self) -> str:
        """从 DB 查询当前用户的规则列表，拼接为 <rules> XML。"""
        if self._session_factory is None:
            return ""
        from server.db.models import OrmRule
        async with self._session_factory() as db:
            rows = (await db.execute(
                OrmRule.__table__.select()
                .where(OrmRule.user_id == self.session.user_id)
                .order_by(OrmRule.priority.desc())
            )).mappings().all()
        if not rows:
            return ""
        lines = ["<rules>",
                  "以下规则由用户设定，具有最高优先级，必须严格遵守："]
        for r in rows:
            lines.append(f"## {r['name']}\n{r['content']}")
        lines.append("</rules>")
        return "\n".join(lines)

    async def _execute_recall_tool(self, tool_input: dict) -> dict:
        """10.5.3 外部记忆召回：TF-IDF 检索卸载块。"""
        if self._offload_store is None:
            return {"status": "error", "error": "外部记忆未初始化"}
        query = tool_input.get("query", "")
        if not query:
            return {"status": "error", "error": "recall 需要 query 参数"}
        try:
            top_k = int(tool_input.get("top_k", 5))
        except (TypeError, ValueError):
            top_k = 5
        results = await self._offload_store.recall(str(self.session.id), query, top_k=top_k)
        if not results:
            return {"status": "searched_nothing_found", "query": query}
        blocks = [
            {"label": r.get("label", ""), "content": r.get("content", "")}
            for r in results
        ]
        return {"status": "ok", "query": query, "blocks": blocks}

    async def _execute_memory_search(self, tool_input: dict) -> dict:
        """L1 记忆主动检索（doc L1-3.5 主动检索兜底）。

        每轮（消息级）合计限 settings.l1_search_tool_max_calls 次 —— 计数在
        _run_message_loop 开头清零，跨 turn 累计，防止模型无休止地搜。
        """
        if self._memory_recall is None:
            return {"status": "error", "error": "记忆模块未启用"}

        limit = settings.l1_search_tool_max_calls
        if self._memory_search_calls >= limit:
            return {
                "status": "exhausted",
                "error": f"本轮记忆搜索次数已用尽（最多 {limit} 次），请根据已有信息回答",
            }

        query = tool_input.get("query", "")
        if not query:
            return {"status": "error", "error": "memory_search 需要 query 参数"}

        self._memory_search_calls += 1
        try:
            top_k = int(tool_input.get("top_k", settings.l1_recall_top_k))
        except (TypeError, ValueError):
            top_k = settings.l1_recall_top_k

        try:
            memories = await self._memory_recall.search(
                user_id=str(self.session.user_id),
                agent_id=self._agent_scope,
                query=query,
                top_k=top_k,
            )
        except Exception as exc:
            self._log.warning("memory_search_failed", error=str(exc))
            return {"status": "error", "error": "记忆检索失败"}

        if not memories:
            return {"status": "searched_nothing_found", "query": query}
        return {
            "status": "ok",
            "query": query,
            "memories": [m.to_dict() for m in memories],
        }

    async def _execute_scene_read(self, tool_input: dict) -> dict:
        """L2 场景按名读取（doc L2-3.5 渐进式披露）。

        每轮（消息级）独立限 settings.l2_scene_read_max_calls 次 —— 与 memory_search
        各记各的，共用会静默改变 L1 那 3 次的既有语义。
        """
        if self._scene_recall is None:
            return {"status": "error", "error": "场景记忆未启用"}

        limit = settings.l2_scene_read_max_calls
        if self._scene_read_calls >= limit:
            return {
                "status": "exhausted",
                "error": f"本轮场景读取次数已用尽（最多 {limit} 次），请根据已有信息回答",
            }

        name = (tool_input.get("name") or "").strip()
        if not name:
            return {"status": "error", "error": "scene_read 需要 name 参数"}

        self._scene_read_calls += 1
        user_id = str(self.session.user_id)
        scene = await self._scene_recall.read_scene(
            user_id=user_id, agent_id=self._agent_scope, name=name,
        )
        if scene is None:
            # 未命中回可用名单：等价于文档"只能 read 清单里的文件，禁止编造文件名"
            available = await self._scene_recall.names(
                user_id=user_id, agent_id=self._agent_scope,
            )
            return {
                "status": "not_found",
                "error": f"场景 '{name}' 不存在",
                "available_scenes": available,
            }
        return {
            "status": "ok",
            "name": scene.name,
            "heat": scene.heat,
            "updated_at": scene.updated_at.isoformat() if scene.updated_at else None,
            "content": scene.content,
        }

    async def _check_loop_detection(self, chunk: LLMChunk) -> bool:
        """连续 3 次相同工具且输入相同 → 注入上下文警告，让 LLM 自己调整。"""
        key = (
            chunk.tool_name or "",
            hashlib.md5(json.dumps(chunk.tool_input or {}, sort_keys=True).encode()).hexdigest(),
        )
        self._recent_tool_calls.append(key)
        if len(self._recent_tool_calls) > 3:
            self._recent_tool_calls.pop(0)
        if len(self._recent_tool_calls) == 3 and len(set(self._recent_tool_calls)) == 1:
            self._log.warning(
                "loop_detected", tool=chunk.tool_name,
            )
            await self.context_mgr.append_text(
                self.session.id, "user",
                "[系统: 检测到你连续3次调用了相同的工具且参数完全一致。"
                "请检查之前的工具结果是否有异常，尝试不同的方法继续，"
                "或者向用户说明当前遇到的困难。]"
            )
            self._recent_tool_calls.clear()
        return False

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        """检测文本是否包含向用户提问的迹象（而非输出计划）。"""
        stripped = text.rstrip()
        if stripped.endswith("？") or stripped.endswith("?"):
            return True
        question_patterns = [
            "请问", "您觉得", "你希望", "你想", "您希望",
            "你能", "能否", "可以吗", "行吗", "好吗",
        ]
        return any(p in text for p in question_patterns)
