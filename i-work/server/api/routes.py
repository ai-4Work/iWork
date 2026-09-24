"""
iWork Server API 路由。

设计文档 1.9 节定义的完整接口:

  会话管理:
    GET    /sessions                        — 查询会话列表
    POST   /sessions                        — 创建会话 + 注册 client_tools
    GET    /sessions/{id}                   — 获取会话详情
    PATCH  /sessions/{id}                   — 更新会话配置
    DELETE /sessions/{id}                   — 归档会话

  会话内操作:
    POST   /sessions/{id}/messages          — 发送消息（NDJSON 流）
    GET    /sessions/{id}/stream            — 断线重连续传
    GET    /sessions/{id}/queue             — 查询队列状态
    DELETE /sessions/{id}/queue/{msg_id}    — 取消排队
    POST   /sessions/{id}/plan/confirm      — 确认计划
    POST   /sessions/{id}/plan/edit         — 编辑计划
    POST   /sessions/{id}/cancel            — 取消当前操作（plan 拒绝 / build 终止）
    POST   /sessions/{id}/plan/answer       — 回答 plan.question
    POST   /sessions/{id}/tool-result/{request_id} — 工具结果回传（confirm/skip/result 统一入口）
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from server.models.message import MessageCreate
from server.models.mail import agent_id_from_path
from server.models.tool_invocation import normalize_client_result
from server.models.session import (
    Session, SessionCreate, SessionUpdate, SessionStatus,
    SessionListItem, SessionDetail, AgentConfig,
)
from server.api.deps import (
    get_db, get_engine_manager, get_current_user,
    require_permission, require_session_access,
)
from server.authz.service import load_user_scope
from server.engine.query_loop import QueueFullError
from server.observability.audit import audit_log

logger = logging.getLogger("iwork.api")

# ── 会话集合路由（无 session_id 前缀）─────────────────────────
router_sessions = APIRouter(prefix="/sessions")

# ── 会话内操作路由（含 session_id 前缀）───────────────────────
router = APIRouter(prefix="/sessions/{session_id}")


# ═══════════════════════════════════════════════════════════════
# 会话辅助
# ═══════════════════════════════════════════════════════════════

async def _get_engine(session_id: UUID, engine_mgr):
    session = await engine_mgr.session_repo.get(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.status == SessionStatus.ARCHIVED:
        raise HTTPException(410, "会话已归档")
    return await engine_mgr.get_or_create(session)


def _group_history_rows(rows) -> dict[str, list[dict]]:
    """把 conversation_history 行按消息分组（历史读取端点用，纯函数便于测试）。

    行对象需暴露：message_id / sequence / role / content / reasoning_content /
    tool_call_id / turn。按 sequence 推进：message_id 标注行归属自身并成为新的
    current；未标注(老行)归属最近一个已见消息；若尚未见任何消息则跳过。
    """
    buckets: dict[str, list[dict]] = {}
    current_mid: str | None = None
    for r in rows:
        if getattr(r, "message_id", None):
            current_mid = str(r.message_id)
            buckets.setdefault(current_mid, [])
        elif current_mid is None:
            continue
        buckets[current_mid].append({
            "sequence": getattr(r, "sequence", None),
            "role": getattr(r, "role", None),
            "content": getattr(r, "content", None),
            "reasoning_content": getattr(r, "reasoning_content", None),
            "tool_call_id": getattr(r, "tool_call_id", None),
            "turn": getattr(r, "turn", None),
        })
    return buckets


# ═══════════════════════════════════════════════════════════════
# 会话列表 —— GET /sessions
# ═══════════════════════════════════════════════════════════════

@router_sessions.get("")
async def list_sessions(
    status: str = Query("active", description="过滤条件: active, archived, all"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    engine_mgr=Depends(get_engine_manager),
    user_id: UUID = Depends(get_current_user),
):
    sessions, total = await engine_mgr.session_repo.list_by_user(
        user_id=str(user_id),
        status=status,
        limit=limit,
        offset=offset,
    )
    # 获取每个会话的消息数
    items = []
    for s in sessions:
        msg_count = await engine_mgr.message_repo.count_pending(s.id)
        items.append(SessionListItem(
            id=s.id,
            title=s.title,
            mode=s.mode,
            scene_mode=s.scene_mode,
            model=s.model,
            workspace=s.workspace,
            message_count=msg_count,
            created_at=s.created_at,
            updated_at=s.updated_at,
            status=s.status,
        ))
    return {"sessions": [i.model_dump(mode="json") for i in items], "total": total}


# ═══════════════════════════════════════════════════════════════
# 会话创建 —— POST /sessions
# ═══════════════════════════════════════════════════════════════

@router_sessions.post("", status_code=201)
async def create_session(
    body: SessionCreate,
    engine_mgr=Depends(get_engine_manager),
    user_id: UUID = Depends(get_current_user),
):
    # 幂等校验：同 ID 已存在则返回 409
    existing = await engine_mgr.session_repo.get(body.id)
    if existing:
        raise HTTPException(409, detail={
            "error": "duplicate",
            "message": "该 session_id 已存在",
            "existing_session": {
                "id": str(existing.id),
                "title": existing.title,
                "created_at": existing.created_at.isoformat(),
            },
        })

    # 基础校验
    if not body.client_tools:
        raise HTTPException(400, detail={
            "error": "invalid_request",
            "message": "client_tools 不能为空",
        })

    # 多 Agent 校验
    if body.agents:
        leads = [a for a in body.agents if a.role == "lead"]
        if len(leads) > 1:
            raise HTTPException(400, detail={
                "error": "invalid_agents",
                "message": f"only one lead agent is allowed, got {len(leads)}",
            })
        if len(leads) == 0:
            raise HTTPException(400, detail={
                "error": "invalid_agents",
                "message": "at least one lead agent is required when agents are specified",
            })
        for a in body.agents:
            if a.agent_type == "team" and a.role != "lead":
                raise HTTPException(400, detail={
                    "error": "invalid_agents",
                    "message": "agent_type='team' must have role='lead'",
                })

        # 校验 agent_id 真实存在
        for a in body.agents:
            if a.agent_type == "expert":
                if engine_mgr._expert_repo:
                    expert = None
                    try:
                        expert = await engine_mgr._expert_repo.get_by_id(UUID(a.agent_id))
                    except (ValueError, TypeError):
                        pass
                    if expert is None:
                        raise HTTPException(400, detail={
                            "error": "invalid_agents",
                            "message": f"expert agent '{a.agent_id}' not found",
                        })
            elif a.agent_type == "team":
                if engine_mgr._team_repo:
                    team = None
                    try:
                        team = await engine_mgr._team_repo.get_by_id(UUID(a.agent_id))
                    except (ValueError, TypeError):
                        pass
                    if team is None:
                        raise HTTPException(400, detail={
                            "error": "invalid_agents",
                            "message": f"team '{a.agent_id}' not found",
                        })

    session = Session(
        id=body.id,
        user_id=str(user_id),
        title="新建任务",
        mode=body.mode,
        scene_mode=body.scene_mode,
        model=body.model,
        workspace=body.workspace,
        shell_env=body.shell_env,
        client_tools=body.client_tools,
        agents=body.agents,
        # 顶层会话：自己是 agent 树的根，路径固定 /root（§9.11.4）。子会话由
        # task 派发生成，root_session_id 指向本会话、路径为 /root/{member_id}。
        agent_path="/root",
        root_session_id=body.id,
    )
    await engine_mgr.session_repo.create(session)

    # 预热引擎（后台启动 run() 协程）
    await engine_mgr.get_or_create(session)

    logger.info(
        "session.created  id=%s  mode=%s  scene=%s  model=%r  workspace=%r  "
        "shell_env=%r  tools=%d=%r  agents=%d",
        str(session.id), session.mode, session.scene_mode,
        session.model, session.workspace,
        session.shell_env,
        len(session.client_tools),
        [t.get("name") if isinstance(t, dict) else t for t in session.client_tools],
        len(session.agents),
    )

    return {
        "id": str(session.id),
        "title": session.title,
        "mode": session.mode,
        "scene_mode": session.scene_mode,
        "model": session.model,
        "workspace": session.workspace,
        "shell_env": session.shell_env,
        "client_tools_count": len(session.client_tools),
        "agents_count": len(session.agents),
        "agents": [a.model_dump(mode="json") for a in session.agents],
        "created_at": session.created_at.isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# 会话详情 —— GET /sessions/{id}
# ═══════════════════════════════════════════════════════════════

@router_sessions.get("/{session_id}")
async def get_session(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    session = await engine_mgr.session_repo.get(session_id)
    if session is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "会话不存在"})

    engine = engine_mgr.get(session_id)
    current_processing = None
    queue_size = 0
    if engine:
        queue_size = await engine._message_repo.count_pending(session_id)
        if engine._current_msg:
            current_processing = {
                "message_id": str(engine._current_msg.id),
                "started_at": engine._current_msg.started_at.isoformat() if engine._current_msg.started_at else None,
            }

    detail = SessionDetail(
        id=session.id,
        title=session.title,
        mode=session.mode,
        scene_mode=session.scene_mode,
        model=session.model,
        workspace=session.workspace,
        status=session.status,
        client_tools=session.client_tools,
        agents=session.agents,
        current_processing=current_processing,
        queue_size=queue_size,
        message_count=0,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
    return detail.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════
# 会话更新 —— PATCH /sessions/{id}
# ═══════════════════════════════════════════════════════════════

@router_sessions.patch("/{session_id}")
async def update_session(
    session_id: UUID,
    body: SessionUpdate,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    session = await engine_mgr.session_repo.get(session_id)
    if session is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "会话不存在"})
    if session.status == SessionStatus.ARCHIVED:
        raise HTTPException(410, detail={
            "error": "session_archived",
            "message": "该会话已归档，无法更新配置",
        })

    # 仅更新传入的非 None 字段
    updates = body.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(session, field, value)

    await engine_mgr.session_repo.update(session)

    detail = SessionDetail(
        id=session.id,
        title=session.title,
        mode=session.mode,
        scene_mode=session.scene_mode,
        model=session.model,
        workspace=session.workspace,
        status=session.status,
        client_tools=session.client_tools,
    )
    return detail.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════
# 会话删除（归档）—— DELETE /sessions/{id}
# ═══════════════════════════════════════════════════════════════

@router_sessions.delete("/{session_id}")
async def delete_session(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    session = await engine_mgr.session_repo.get(session_id)
    if session is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "会话不存在"})

    # 清理子会话及其引擎：先终止 run() 协程，再摘注册项，最后归档。
    # 顺序反了会留下「引擎已被 pop、协程还在跑」——既继续往已归档的 session 写
    # 历史，又让后续 get_or_create 再建一个同 id 引擎（§9.12.9 第 1 条）。
    sub_sessions = await engine_mgr.session_repo.list_by_parent(session_id)
    await engine_mgr.shutdown_tree(session_id)
    for child in sub_sessions:
        engine_mgr._engines.pop(child.id, None)
        await engine_mgr.session_repo.archive(child.id)

    engine_mgr._engines.pop(session_id, None)
    await engine_mgr.session_repo.archive(session_id)
    return {
        "status": "archived",
        "id": str(session_id),
        "sub_sessions_archived": len(sub_sessions),
    }


# ═══════════════════════════════════════════════════════════════
# 主对话 —— POST /sessions/{id}/messages
# 整个引擎的入口：用户消息入队 → 唤醒引擎 → 建立 NDJSON 流推给前端
# ═══════════════════════════════════════════════════════════════

@router.post("/messages")
async def send_message(
    session_id: UUID,
    body: MessageCreate,
    engine_mgr=Depends(get_engine_manager),
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_session_access),
):
    logger.info("send_message  session=%s  content=%.200s  mode=%s  model=%s  scene=%s  workspace=%s  agent=%s/%s",
        str(session_id), body.content, body.mode, body.model,
        body.scene_mode, body.workspace, body.agent_type, body.agent_id)

    # 1. 拿到会话专属的引擎实例
    engine = await _get_engine(session_id, engine_mgr)

    # 1.25. 摄入幂等（阶段 A）：同 (session, client_message_id) 已存在 → duplicate ack，
    # 不再入队 / 审计 / 改标题 / 唤醒引擎。
    if body.client_message_id:
        existing = await engine.find_by_client_message_id(body.client_message_id)
        if existing is not None:
            logger.info(
                "message.duplicate  session=%s  client_message_id=%s  message_id=%s  status=%s",
                str(session_id), body.client_message_id, str(existing.id), existing.status,
            )
            return {
                "duplicate": True,
                "message_id": str(existing.id),
                "client_message_id": body.client_message_id,
                "status": existing.status.value if hasattr(existing.status, "value") else existing.status,
            }

    # 1.5. 校验 agent_id（如果提供，必须在 session.agents 列表中）
    if body.agent_id and body.agent_type:
        session = engine.session
        if session.agents:
            agent_ids = [a.agent_id for a in session.agents]
            if body.agent_id not in agent_ids:
                raise HTTPException(400, detail={
                    "error": "invalid_agent",
                    "message": f"agent_id '{body.agent_id}' not in session agents list",
                    "available_agents": agent_ids,
                })

    # 1.6. 跨协议中途换模型：拒绝。
    # 历史消息是按 OpenAI 形状**落库**的（`reasoning_content`、`tool_calls` +
    # `role:"tool"`），Anthropic 消费不了这种形状 —— 中途从 DeepSeek 切到 Anthropic，
    # 等于把一份非法历史发过去，报错来自厂商、排查方向全反。
    # 同一协议内随便切（deepseek-chat ↔ 内网 vLLM 都是 OpenAI 兼容），跨协议请新建会话。
    session_model = engine.session.model or ""
    if body.model and session_model and body.model != session_model:
        requested = await engine_mgr.resolver.resolve(body.model)
        current = await engine_mgr.resolver.resolve(session_model)
        if requested.protocol != current.protocol:
            raise HTTPException(400, detail={
                "error": "protocol_mismatch",
                "message": (
                    f"本会话用的是 {current.protocol} 协议，"
                    f"不能中途切到 {requested.protocol} 协议的模型。请新建会话。"
                ),
                "session_protocol": current.protocol,
                "requested_protocol": requested.protocol,
            })

    # 2. 消息写入队列 + _wake_event.set() 唤醒引擎后台协程
    try:
        msg = await engine.enqueue(str(user_id), body)
    except QueueFullError as e:
        logger.warning("queue.full  session=%s", str(session_id))
        raise HTTPException(429, detail={"error": "queue_full", "message": str(e)})

    # 审计：用户发送消息
    await audit_log(
        db,
        action="user.message_sent",
        user_id=user_id,
        session_id=session_id,
        message_id=msg.id,
        resource=f"message/{msg.id}",
        detail={
            "mode": body.mode,
            "model": body.model,
            "scene_mode": body.scene_mode,
            "workspace": body.workspace,
            "file_count": len(body.files or []),
            "content_preview": body.content[:100],
            "skill_ids": [s.skill_id for s in body.skill_invocations],
            "mcp_servers": [s.server_id for s in body.mcp_servers],
        },
    )

    logger.info(
        "\033[1;36mmessage.enqueued  session=%s  mode=%s  scene=%s  skills=%s  model=%s  workspace=%s  files=%s  mcp=%s  content=%s\033[0m",
        str(session_id), body.mode, body.scene_mode, [(s.skill_id, s.skill_name) for s in body.skill_invocations], body.model, body.workspace,
        body.files, [s.server_id for s in body.mcp_servers], body.content,
    )

    # 3. 首条消息自动设置会话标题
    session = engine.session
    if session.title == "新建任务":
        session.title = body.content[:20]
        await engine_mgr.session_repo.update(session)

    # 4. 建立 NDJSON 流：引擎 _push_chunk() 写入 _chunk_queue，
    #    这里 await get() 实时取出，逐行 yield 给前端
    async def chunk_generator():
        while True:
            chunk = await engine._chunk_queue.get()
            if chunk.get("type") in ("session.publish", "agent.status"):
                logger.info("stream.send  seq=%s  type=%s  q=%s  chunk=%s",
                            chunk.get("seq"), chunk.get("type"),
                            engine._chunk_queue.qsize(),
                            json.dumps(chunk, default=str, ensure_ascii=False))
            yield json.dumps(chunk, default=str) + "\n"
            if chunk.get("type") in ("message.complete", "message.error"):
                break

    return StreamingResponse(
        chunk_generator(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════
# 重连 —— GET /sessions/{id}/stream
# ═══════════════════════════════════════════════════════════════

@router.get("/stream")
async def reconnect_stream(
    session_id: UUID,
    since_seq: int = 0,
    since_message_id: UUID | None = None,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")

    replay = engine.stream_buffer.drain(since_seq=since_seq)

    async def replay_generator():
        # ── 回放断线期间遗漏的数据块 ──
        max_replayed_seq = 0
        for chunk in replay:
            max_replayed_seq = chunk.get("seq", 0)
            yield json.dumps(chunk, default=str) + "\n"

        # ── 排空队列，跳过已回放的数据块（去重）──
        for chunk in engine.drain_queue():
            if chunk.get("seq", 0) > max_replayed_seq:
                yield json.dumps(chunk, default=str) + "\n"

        # ── 继续实时消费 ──
        while True:
            chunk = await engine._chunk_queue.get()
            yield json.dumps(chunk, default=str) + "\n"

    return StreamingResponse(
        replay_generator(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════
# 会话历史读取 + 引擎状态（B 恢复 / M5 hydrate 用）
# conversation_history 为真相源；按 message_id 组装每条消息的 turn 内容，
# 供客户端重启后回读已完成消息。engine_mgr 单例不经 engine 存活即可读 DB。
# ═══════════════════════════════════════════════════════════════

@router.get("/messages")
async def session_messages(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    db=Depends(get_db),
    _: None = Depends(require_session_access),
):
    session = await engine_mgr.session_repo.get(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.status == SessionStatus.ARCHIVED:
        raise HTTPException(410, "会话已归档")

    from server.db.models import OrmMessage, OrmConversationHistory
    msgs = (await db.execute(
        select(OrmMessage)
        .where(OrmMessage.session_id == session_id)
        .order_by(OrmMessage.created_at, OrmMessage.id)
    )).scalars().all()

    hist = (await db.execute(
        select(OrmConversationHistory)
        .where(OrmConversationHistory.session_id == session_id)
        .order_by(OrmConversationHistory.sequence)
    )).scalars().all()

    bucket_by_mid = _group_history_rows(hist)

    messages_out = []
    for m in msgs:
        messages_out.append({
            "id": str(m.id),
            "client_message_id": m.client_message_id,
            "content": m.content,
            "mode": m.mode,
            "status": m.status,
            "turn_count": m.turn_count,
            "tokens_in": m.tokens_in,
            "tokens_out": m.tokens_out,
            "tool_calls_count": m.tool_calls_count,
            "error_message": m.error_message,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "started_at": m.started_at.isoformat() if m.started_at else None,
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
            # 客户端按 msg_type='result' 剔除子 agent 结果信封（content 是 JSON，
            # 不是对话）。缺了它，团队会话回读会把信封当一条 user 消息渲染出来。
            "msg_type": m.msg_type,
            # 委派卡片重建：result 信封靠 cid 对回它那次派发；发件人是子 agent_path，
            # 出站前转成扁平 id（§9.11.13：展示层不出现路径）。
            "cid": m.cid,
            "from_agent_id": agent_id_from_path(m.sender_agent_id) if m.sender_agent_id else None,
            "rows": bucket_by_mid.get(str(m.id), []),
        })
    return {"session_id": str(session_id), "messages": messages_out}


@router.get("/agents")
async def session_agents(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    """该会话树下已派发过的成员子会话（§9.11.4）——子列历史回读的入口。

    子 agent 的对话不在父会话里（父只收一行 result 信封），而在**子会话自己**的
    conversation_history 里；客户端要逐列回填就得先知道有哪些子会话，本端点补的
    就是这个前置。子会话 id 可直接喂给 `GET /messages`（那个端点只按 session_id 取数，
    不区分父子、也不要求引擎存活）。

    只返回**直接**子会话：今天的树只有两层（子被 `_disable_task_tool` 禁掉再派发，
    `task_handler.py:110`）；将来放开多层，这里改按 root_session_id 递归。
    `agent_id` 出站即扁平 id（§9.11.13）。
    """
    session = await engine_mgr.session_repo.get(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    children = await engine_mgr.session_repo.list_by_parent(session_id)
    children.sort(key=lambda c: c.created_at)
    return {
        "session_id": str(session_id),
        "agents": [
            {"session_id": str(c.id), "agent_id": agent_id_from_path(c.agent_path or "")}
            for c in children if c.agent_path
        ],
    }


@router.get("/state")
async def session_state(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    db=Depends(get_db),
    _: None = Depends(require_session_access),
):
    from server.db.models import OrmMessage
    engine = engine_mgr.get(session_id)
    cur = engine._current_msg if engine is not None else None
    processing_ids = (await db.execute(
        select(OrmMessage.id).where(
            OrmMessage.session_id == session_id,
            OrmMessage.status == "processing",
        )
    )).scalars().all()
    # 在途子任务（§9.11.8）：`state == "WAITING_CHILDREN"` 时前端靠它渲染"在等谁"，
    # 否则只能看到一个不动的进度条。agent_id 用扁平 id —— 路径是内部寻址，
    # 不进展示（§9.11.13）。
    in_flight = [
        {
            "task_id": task_id,
            "agent_path": info.get("agent_path"),
            "agent_id": agent_id_from_path(info.get("agent_path") or ""),
            "dispatched_at": info.get("dispatched_at"),
        }
        for task_id, info in (engine._in_flight.items() if engine is not None else [])
    ]
    return {
        "session_id": str(session_id),
        "engine_alive": engine is not None,
        "state": (engine.state if engine is not None else "idle"),
        "current_message_id": str(cur.id) if cur is not None else None,
        "in_flight": in_flight,
        "interrupted_candidates": [str(x) for x in processing_ids],
    }


@router.get("/effects")
async def session_effects(
    session_id: UUID,
    message_id: UUID | None = None,
    db=Depends(get_db),
    _: None = Depends(require_session_access),
):
    """D 工具动作账本：全会话（或单消息）受账工具动作清单（四态 + 读/写标注）。

    只读视图，从 tool_invocations 聚合，不新开写链路。不限 side_effect（只读也返回）、
    不限 state（issued/completed/skipped/superseded 四态都返回），每行带 state 与
    side_effect，展示侧自行区分读/写、按状态呈现四色。按 created_at 升序，payload 含
    attempt 运行序号，前端据此按消息分组、按 attempt 分块渲染（regenerate 开新块 /
    continue 并入当前块）。
    """
    from server.db.models import OrmToolInvocation
    cond = [OrmToolInvocation.session_id == session_id]
    if message_id is not None:
        cond.append(OrmToolInvocation.message_id == message_id)
    return {
        "session_id": str(session_id),
        "effects": [
            _effect_orm_to_dict(r)
            for r in (await db.execute(
                select(OrmToolInvocation).where(*cond)
                .order_by(OrmToolInvocation.created_at)
            )).scalars()
        ],
    }


def _effect_orm_to_dict(orm) -> dict:
    """OrmToolInvocation → 工具动作账本对外 payload。

    result 读时归一（status → success）：改动前的历史行按旧客户端契约落库、不可回写，
    只有在这里补字段，前端才能把已存在的失败行如实标红。
    """
    return {
        "invocation_id": str(orm.invocation_id),
        "message_id": str(orm.message_id) if orm.message_id else None,
        "attempt": orm.attempt,
        "tool_name": orm.tool_name,
        "location": orm.location,
        "state": orm.state,
        "side_effect": bool(orm.side_effect),
        "idempotency": orm.idempotency,
        "input": orm.input or {},
        "result": normalize_client_result(orm.result),
        "error": orm.error,
        "created_at": orm.created_at.isoformat() if orm.created_at else None,
        "completed_at": orm.completed_at.isoformat() if orm.completed_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# 队列操作
# ═══════════════════════════════════════════════════════════════

@router.get("/queue")
async def get_queue(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")

    pending = await engine._message_repo.list_pending(session_id)
    current = None
    if engine._current_msg:
        current = {
            "message_id": str(engine._current_msg.id),
            "content_preview": engine._current_msg.content[:100],
            "started_at": engine._current_msg.started_at.isoformat() if engine._current_msg.started_at else None,
        }

    return {
        "session_id": str(session_id),
        "current_processing": current,
        "queue": [
            {
                "message_id": str(m.id),
                "content_preview": m.content[:100],
                "queue_position": m.queue_position,
                "status": "pending",
                "created_at": m.created_at.isoformat(),
            }
            for m in sorted(pending, key=lambda m: m.queue_position or 999)
        ],
    }


@router.delete("/queue/{msg_id}")
async def remove_from_queue(
    session_id: UUID, msg_id: UUID, engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")
    ok = await engine.remove_from_queue(msg_id)
    if not ok:
        raise HTTPException(404, "消息不存在或已在处理中")
    return {"success": True, "removed_message_id": str(msg_id)}


# ═══════════════════════════════════════════════════════════════
# Plan 模式端点
# ═══════════════════════════════════════════════════════════════

@router.post("/plan/confirm")
async def plan_confirm(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")
    engine.resolve_user_decision("confirmed")
    return {"status": "confirmed", "message_id": str(session_id)}


@router.post("/plan/edit")
async def plan_edit(
    session_id: UUID, body: dict, engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")
    plan_text = body.get("plan_text", "")
    if not plan_text:
        raise HTTPException(400, "plan_text 不能为空")
    engine.resolve_user_decision("edited")
    return {"status": "edited", "message_id": str(session_id)}


@router.post("/cancel")
async def session_cancel(
    session_id: UUID,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")
    # 方案 X：先置 _cancel_event（软取消）再仅唤醒当前消息的用户决策等待
    # （plan 确认 / plan_question，值 "rejected" 沿既有分支归入 cancelled）。
    # 不再 resolve_all 广播 abort —— 本地客户端工具不可撤，取消只停文本与后续工具：
    # 在途工具保持阻塞等待真实回投 → 正常落地（completed + 注入上下文），
    # 副作用如实交代、继续绝不重跑。
    await engine_mgr.cancel_tree(session_id)
    engine.resolve_user_decision("rejected")
    return {"status": "cancelled", "message_id": str(session_id)}


@router.post("/plan/answer")
async def plan_answer(
    session_id: UUID,
    body: dict,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")
    engine.resolve_user_decision(body.get("answer", ""))
    return {"status": "answered", "message_id": str(session_id)}


# ═══════════════════════════════════════════════════════════════
# Client 工具结果回传（含 Build 模式的 skip 结果）
# ═══════════════════════════════════════════════════════════════

@router.post("/tool-result/{request_id}")
async def tool_result(
    session_id: UUID, request_id: str, body: dict,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = engine_mgr.get(session_id)
    if engine is None:
        raise HTTPException(404, "无活跃引擎")
    return await engine.submit_client_tool_result(str(request_id), body)


# ═══════════════════════════════════════════════════════════════
# M4 · 显式重跑（regenerate 截断重生成 / continue 原地续跑）
# 复用同一条 Message 行，不新增消息行；输出走 NDJSON 流（复用 _chunk_queue 单消费者）。
# ═══════════════════════════════════════════════════════════════

async def _reprocess_stream(engine, message_id: UUID, mode: str, body: dict):
    """调度一次重跑并返回 NDJSON 流 / 拒绝 JSON。

    body 可带 client_message_id（客户端本地该条 user 消息 id，供服务端关联审计）。
    """
    res = await engine.reprocess(message_id, mode)
    status = res["status"]
    if status != "scheduled":
        if status == "not_found":
            raise HTTPException(404, "消息不存在或不属于该会话")
        if status == "busy":
            raise HTTPException(409, detail={
                "error": "busy", "message": "引擎正在处理中，请稍后再试",
            })
        # needs_confirm：返回清单交客户端提示（不自动放行写类工具尾巴）
        return {
            "status": "needs_confirm",
            "message_id": str(message_id),
            "mode": mode,
            "ambiguous_tools": res.get("ambiguous_tools", []),
        }

    async def chunk_generator():
        while True:
            chunk = await engine._chunk_queue.get()
            yield json.dumps(chunk, default=str) + "\n"
            if chunk.get("message_id") == str(message_id) and chunk.get("type") in (
                "message.complete", "message.error",
            ):
                break

    return StreamingResponse(
        chunk_generator(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{message_id}/regenerate")
async def regenerate_message(
    session_id: UUID, message_id: UUID, body: dict = None,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = await _get_engine(session_id, engine_mgr)
    return await _reprocess_stream(engine, message_id, "regenerate", body or {})


@router.post("/messages/{message_id}/continue")
async def continue_message(
    session_id: UUID, message_id: UUID, body: dict = None,
    engine_mgr=Depends(get_engine_manager),
    _: None = Depends(require_session_access),
):
    engine = await _get_engine(session_id, engine_mgr)
    return await _reprocess_stream(engine, message_id, "continue", body or {})


# ═══════════════════════════════════════════════════════════════
# Rules 管理 API — POST/DELETE /rules, GET /rules, GET /rules/{id}
# ═══════════════════════════════════════════════════════════════

router_rules = APIRouter(prefix="/rules")


@router_rules.get("")
async def list_rules(
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    from server.db.models import OrmRule
    rows = (await db.execute(
        OrmRule.__table__.select()
        .where(OrmRule.user_id == user_id)
        .order_by(OrmRule.priority.desc())
    )).mappings().all()
    return {
        "rules": [
            {
                "id": str(r["id"]), "name": r["name"],
                "description": r["description"], "content": r["content"],
                "priority": r["priority"],
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat(),
            }
            for r in rows
        ]
    }


@router_rules.get("/{rule_id}")
async def get_rule(
    rule_id: UUID,
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    from server.db.models import OrmRule
    row = (await db.execute(
        OrmRule.__table__.select()
        .where(OrmRule.id == rule_id, OrmRule.user_id == user_id)
    )).mappings().first()
    if row is None:
        raise HTTPException(404, "规则不存在")
    return {
        "id": str(row["id"]), "name": row["name"],
        "description": row["description"], "content": row["content"],
        "priority": row["priority"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router_rules.post("", status_code=201)
async def upsert_rule(
    body: dict,
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    from server.db.models import OrmRule
    from datetime import datetime, timezone
    import uuid as _uuid

    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "name 不能为空")

    existing = (await db.execute(
        OrmRule.__table__.select()
        .where(OrmRule.user_id == user_id, OrmRule.name == name)
    )).mappings().first()

    now = datetime.now(timezone.utc)
    if existing:
        await db.execute(
            OrmRule.__table__.update()
            .where(OrmRule.id == existing["id"])
            .values(
                description=body.get("description", existing["description"]),
                content=body.get("content", existing["content"]),
                priority=body.get("priority", existing["priority"]),
                updated_at=now,
            )
        )
        await db.commit()
        return {"id": str(existing["id"]), "name": name, "updated_at": now.isoformat()}
    else:
        rid = _uuid.uuid4()
        await db.execute(
            OrmRule.__table__.insert().values(
                id=rid, user_id=user_id, name=name,
                description=body.get("description", ""),
                content=body.get("content", ""),
                priority=body.get("priority", 0),
                created_at=now, updated_at=now,
            )
        )
        await db.commit()
        return {"id": str(rid), "name": name, "created_at": now.isoformat()}


@router_rules.delete("/{rule_id}")
async def delete_rule(
    rule_id: UUID,
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    from server.db.models import OrmRule
    result = await db.execute(
        OrmRule.__table__.delete()
        .where(OrmRule.id == rule_id, OrmRule.user_id == user_id)
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "规则不存在")
    return {"status": "deleted", "id": str(rule_id)}


# ═══════════════════════════════════════════════════════════════
# L1 原子记忆（docs/chapters/5-记忆模块）
# ═══════════════════════════════════════════════════════════════

router_l1 = APIRouter(prefix="/l1")


def _l1_row(row) -> dict:
    meta = row["metadata_json"] or {}
    return {
        "id": row["id"],
        "content": row["content"],
        "type": row["type"],
        "priority": row["priority"],
        "scene_name": row["scene_name"],
        "agent_id": row["agent_id"],
        "activity_start_time": meta.get("activity_start_time"),
        "activity_end_time": meta.get("activity_end_time"),
        "version": row["version"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router_l1.get("/memories")
async def list_l1_memories(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    type: str | None = Query(None, description="persona | episodic | instruction"),
    agent_id: str | None = Query(None),
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    """L1 原子记忆列表。只出 retrievable=true（被取代的旧版本不进列表）。"""
    from server.db.models import OrmL1Memory
    where = [OrmL1Memory.user_id == user_id, OrmL1Memory.retrievable.is_(True)]
    if type:
        where.append(OrmL1Memory.type == type)
    if agent_id is not None:
        where.append(OrmL1Memory.agent_id == agent_id)

    total = (await db.execute(
        select(func.count()).select_from(OrmL1Memory.__table__).where(*where)
    )).scalar_one()
    rows = (await db.execute(
        OrmL1Memory.__table__.select().where(*where)
        .order_by(OrmL1Memory.updated_at.desc()).limit(limit).offset(offset)
    )).mappings().all()
    return {"total": total, "memories": [_l1_row(r) for r in rows]}


@router_l1.delete("/memories/{memory_id}", status_code=204)
async def delete_l1_memory(
    memory_id: str,
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    """硬删。用户手删的语义是"这条记忆不该存在"，软删只会让它永远躺在库里。"""
    from server.db.models import OrmL1Memory
    result = await db.execute(
        OrmL1Memory.__table__.delete().where(
            OrmL1Memory.id == memory_id, OrmL1Memory.user_id == user_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "记忆不存在")


# ═══════════════════════════════════════════════════════════════
# L2 场景记忆（docs/chapters/5-记忆模块 第二部分）
# ═══════════════════════════════════════════════════════════════

router_l2 = APIRouter(prefix="/l2")


def _l2_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "summary": row["summary"],
        "content": row["content"],
        "heat": row["heat"],
        "version": row["version"],
        "agent_id": row["agent_id"],
        "source_memory_ids": row["source_memory_ids"] or [],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router_l2.get("/scenes")
async def list_l2_scenes(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    agent_id: str | None = Query(None),
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    """L2 场景列表。只出 retrievable=true（被 merge 取代的旧场景不进列表），按热度降序。"""
    from server.db.models import OrmL2Scene
    where = [OrmL2Scene.user_id == user_id, OrmL2Scene.retrievable.is_(True)]
    if agent_id is not None:
        where.append(OrmL2Scene.agent_id == agent_id)

    total = (await db.execute(
        select(func.count()).select_from(OrmL2Scene.__table__).where(*where)
    )).scalar_one()
    rows = (await db.execute(
        OrmL2Scene.__table__.select().where(*where)
        .order_by(OrmL2Scene.heat.desc(), OrmL2Scene.updated_at.desc())
        .limit(limit).offset(offset)
    )).mappings().all()
    return {"total": total, "scenes": [_l2_row(r) for r in rows]}


@router_l2.delete("/scenes/{scene_id}", status_code=204)
async def delete_l2_scene(
    scene_id: str,
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    """硬删。场景是自动产物，手改会和 merge / version 语义打架；删除只留作逃生口。"""
    from server.db.models import OrmL2Scene
    result = await db.execute(
        OrmL2Scene.__table__.delete().where(
            OrmL2Scene.id == scene_id, OrmL2Scene.user_id == user_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "场景不存在")


# ═══════════════════════════════════════════════════════════════
# L3 画像记忆（docs/chapters/5-记忆模块 第三部分）
# ═══════════════════════════════════════════════════════════════

router_l3 = APIRouter(prefix="/l3")


def _l3_row(row) -> dict:
    return {
        "agent_id": row["agent_id"],
        "content": row["content"],
        "version": row["version"],
        "memory_count_at_generation": row["memory_count_at_generation"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router_l3.get("/personas")
async def list_l3_personas(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    agent_id: str | None = Query(None),
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    """画像列表。一个作用域一行，按最后生成时间降序。"""
    from server.db.models import OrmL3Persona
    where = [OrmL3Persona.user_id == user_id]
    if agent_id is not None:
        where.append(OrmL3Persona.agent_id == agent_id)

    total = (await db.execute(
        select(func.count()).select_from(OrmL3Persona.__table__).where(*where)
    )).scalar_one()
    rows = (await db.execute(
        OrmL3Persona.__table__.select().where(*where)
        .order_by(OrmL3Persona.updated_at.desc()).limit(limit).offset(offset)
    )).mappings().all()
    return {"total": total, "personas": [_l3_row(r) for r in rows]}


@router_l3.delete("/personas", status_code=204)
async def delete_l3_persona(
    agent_id: str = Query("", description="作用域 agent 标识（顶层为空串）"),
    db=Depends(get_db),
    user_id: UUID = Depends(get_current_user),
):
    """硬删。主键是 (user_id, agent_id)、画像行没有自己的 id，所以用 query 参数定位作用域。
    删掉之后下次 L2 整合会按 P2 冷启动重新生成，这条路径只留作逃生口。"""
    from server.db.models import OrmL3Persona
    result = await db.execute(
        OrmL3Persona.__table__.delete().where(
            OrmL3Persona.user_id == user_id,
            OrmL3Persona.agent_id == agent_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "画像不存在")


# ═══════════════════════════════════════════════════════════════
# 会话回放 + 管理员审计 API（7.5.3 / 7.6.4 节）
# ═══════════════════════════════════════════════════════════════

@router.get("/replay")
async def replay_session(
    session_id: UUID,
    message_id: UUID | None = Query(None, description="按消息 ID 过滤"),
    since_seq: int | None = Query(None, description="从指定 seq 开始回放"),
    db=Depends(get_db),
    _: None = Depends(require_session_access),
):
    """会话回放：按 seq 顺序返回 NDJSON 流，格式与实时流一致。"""
    from server.db.models import OrmStreamEvent
    stmt = OrmStreamEvent.__table__.select().where(
        OrmStreamEvent.session_id == session_id,
    )
    if message_id:
        stmt = stmt.where(OrmStreamEvent.message_id == message_id)
    if since_seq is not None:
        stmt = stmt.where(OrmStreamEvent.seq > since_seq)
    stmt = stmt.order_by(OrmStreamEvent.seq.asc())

    rows = (await db.execute(stmt)).mappings().all()

    async def ndjson_stream():
        for r in rows:
            yield json.dumps(r["chunk"], ensure_ascii=False) + "\n"

    return StreamingResponse(ndjson_stream(), media_type="application/x-ndjson")


router_admin_audit = APIRouter(prefix="/admin")


@router_admin_audit.get("/audit")
async def list_audit_logs(
    user_id: UUID | None = Query(None),
    action: str | None = Query(None),
    session_id: UUID | None = Query(None),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
    caller_id: UUID = Depends(get_current_user),
    _: None = Depends(require_permission("system:audit:list")),
):
    """管理员审计查询：分页查询审计日志。

    审计按 doc 19-5.3 归入 admin 专属：`user_id` 以前是客户端随便传的查询参数，
    数据范围不是 `ALL` 的调用者一律**强制覆盖成自己** —— 只拦"能不能调"不够，
    还得拦"能看谁的"。
    """
    from server.db.models import OrmAuditLog
    from datetime import datetime

    if await load_user_scope(db, caller_id) != "ALL":
        user_id = caller_id

    stmt = OrmAuditLog.__table__.select()
    if user_id:
        stmt = stmt.where(OrmAuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(OrmAuditLog.action == action)
    if session_id:
        stmt = stmt.where(OrmAuditLog.session_id == session_id)
    if from_date:
        stmt = stmt.where(OrmAuditLog.created_at >= datetime.fromisoformat(from_date))
    if to_date:
        stmt = stmt.where(OrmAuditLog.created_at <= datetime.fromisoformat(to_date))
    stmt = stmt.order_by(OrmAuditLog.created_at.desc())

    # Count
    count_stmt = stmt.with_only_columns(
        OrmAuditLog.__table__.columns["id"],
    ).subquery()
    total = (await db.execute(
        count_stmt.select().with_only_columns(func.count())
    )).scalar()

    # Paginate
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).mappings().all()

    return {
        "items": [
            {
                "id": str(r["id"]),
                "created_at": r["created_at"].isoformat(),
                "user_id": str(r["user_id"]),
                "session_id": str(r["session_id"]) if r["session_id"] else None,
                "message_id": str(r["message_id"]) if r["message_id"] else None,
                "action": r["action"],
                "resource": r["resource"],
                "detail": r["detail"],
                "client_ip": r["client_ip"],
                "user_agent": r["user_agent"],
            }
            for r in rows
        ],
        "total": total or 0,
        "page": page,
        "page_size": page_size,
    }
