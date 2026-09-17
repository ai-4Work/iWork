from __future__ import annotations
from uuid import UUID, uuid4
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# 消息状态枚举 —— 一条消息从入队到完成的生命周期
# pending → processing → completed / error / cancelled
# ═══════════════════════════════════════════════════════════════

class MessageStatus(str, Enum):
    PENDING = "pending"          # 排队中，等待引擎取走处理
    PROCESSING = "processing"    # 引擎正在执行 per-message loop
    COMPLETED = "completed"      # 正常完成（end_turn / ask 单轮结束）
    ERROR = "error"              # 异常终止（超时 / 死循环 / API 认证失败等）
    CANCELLED = "cancelled"      # 用户从队列中手动移除（仅限 pending 状态）


# ═══════════════════════════════════════════════════════════════
# 输入增强 —— 消息中携带的 @文件引用 和 /Skill调用
# ═══════════════════════════════════════════════════════════════

class MCPServerConfig(BaseModel):
    """本消息启用的 MCP 服务（消息级覆盖会话默认值）"""
    server_id: str               # MCP 服务唯一标识
    server_name: str             # MCP 服务显示名称
    enabled_tools: list[str] = Field(default_factory=list)  # 白名单工具，空=全部启用


class MCPHubEntry(BaseModel):
    """MCP Hub 中的一条可安装服务（含连接信息）"""
    server_id: str
    server_name: str
    description: str
    icon: str
    category: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class MCPInstallRequest(BaseModel):
    """安装 MCP 请求"""
    server_id: str


class MCPCustomCreate(BaseModel):
    """创建自定义 MCP 请求（server_id 自动生成）"""
    server_name: str
    description: str = ""
    icon: str = "🔌"
    category: str = "自定义"
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# Skill 模型 —— Hub 浏览、安装/卸载、自定义 Skill 管理
# ═══════════════════════════════════════════════════════════════

class SkillHubEntry(BaseModel):
    """Skill Hub 中的一条可安装 Skill（不含 prompt，仅元数据）"""
    skill_id: str
    skill_name: str
    description: str
    version: str = "1.0.0"
    category: str = "通用"
    icon: str = "⚡"
    author: str = ""
    tags: list[str] = Field(default_factory=list)


class SkillInstallRequest(BaseModel):
    """安装 Skill 请求"""
    skill_id: str


class SkillInvocation(BaseModel):
    """客户端通过 / 显式调用的 Skill 条目"""
    skill_id: str                # Skill 唯一标识（如 "sk1"）
    skill_name: str              # Skill 名称（如 "code-review"）


class SkillCustomCreate(BaseModel):
    """创建自定义 Skill 请求（skill_id 自动生成 cs 前缀）"""
    skill_name: str
    description: str = ""
    version: str = "1.0.0"
    category: str = "自定义"
    icon: str = "⚡"
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    folder_path: str = ""


class SkillCustomUpdate(BaseModel):
    """更新自定义 Skill 请求"""
    skill_name: str | None = None
    description: str | None = None
    version: str | None = None
    category: str | None = None
    icon: str | None = None
    tags: list[str] | None = None
    folder_path: str | None = None


# ═══════════════════════════════════════════════════════════════
# MessageCreate —— 前端 POST /sessions/{id}/messages 的请求体
# 纯输入模型，字段对应设计文档 1.9.2 节 Request Body schema
# ═══════════════════════════════════════════════════════════════

class MessageCreate(BaseModel):
    """前端发送消息的请求体，每条消息独立携带运行配置。"""
    # ── 必填 ──
    content: str                                          # 用户输入文本
    scene_mode: str                                       # 工作场景: "office" | "code"
    workspace: str                                        # 工作空间根目录绝对路径（沙箱边界）
    model: str                                            # 模型标识符
    mode: str                                             # "ask" | "plan" | "build"

    # ── 可选 ──
    agent_id: str | None = None                           # 目标 agent id
    agent_type: str | None = None                         # "expert" | "team"
    skill_invocations: list["SkillInvocation"] = Field(default_factory=list)  # / 调用的 Skill 列表
    files: list[str] = Field(default_factory=list)       # @ 引用的文件绝对路径列表
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    client_message_id: str | None = None                  # 客户端幂等键：同一次发送重试复用同一值，服务端据此去重

    # ── 邮箱信封（§9.11.7，客户端不走这些字段，由 MailRouter 填）──
    sender_agent_id: str | None = None                    # 发件人 agent_path（用户消息为空）
    recipient_agent_id: str | None = None                 # 收件人 agent_path
    msg_type: str = "user"                                # user | task | followup | result | status
    cid: str | None = None                                # 关联 id：result 对回 task


# ═══════════════════════════════════════════════════════════════
# Message —— 服务端完整消息模型
# 入队时由 MessageCreate 扩展，增加服务端管理字段。
# 状态流转: pending → processing → completed / error / cancelled
# ═══════════════════════════════════════════════════════════════

class Message(BaseModel):
    # ── 请求携带的字段 ──
    id: UUID = Field(default_factory=uuid4)              # 服务端生成
    session_id: UUID                                      # 归属会话 ID
    user_id: str                                          # 发送者用户 ID
    content: str                                          # 用户输入文本
    scene_mode: str                                       # 工作场景
    workspace: str                                        # 工作空间路径
    model: str                                            # 模型标识符
    mode: str                                             # 使用模式
    agent_id: str | None = None                           # 目标 agent id
    agent_type: str | None = None                         # "expert" | "team"
    files: list[str] = Field(default_factory=list)       # @ 引用的文件
    skill_invocations: list["SkillInvocation"] = Field(default_factory=list)  # / 调用的 Skill 列表
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    client_message_id: str | None = None                  # 客户端幂等键（来自 MessageCreate）

    # ── 邮箱信封（§9.11.7）──
    sender_agent_id: str | None = None                    # 发件人 agent_path（用户消息为空）
    recipient_agent_id: str | None = None                 # 收件人 agent_path
    msg_type: str = "user"                                # user=用户输入；其余见 server/models/mail.py
    cid: str | None = None                                # 关联 id：把 result 对回它那条 task

    # ── 队列状态 ──
    status: MessageStatus = MessageStatus.PENDING        # 当前状态
    queue_position: int | None = None                     # 队列排位（1-based），出队后置 None

    # ── 执行统计（处理完成后回填）──
    turn_count: int = 0                                   # LLM turn 数
    tokens_in: int = 0                                    # 输入 token 总量
    tokens_out: int = 0                                   # 输出 token 总量
    duration_ms: int = 0                                  # 总耗时（毫秒）
    tool_calls_count: int = 0                             # 工具调用次数
    error_message: str | None = None                      # 异常信息

    # ── 时间戳 ──
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))   # 入队时间
    started_at: datetime | None = None                               # 引擎开始处理时间
    completed_at: datetime | None = None                             # 处理完成时间


# ═══════════════════════════════════════════════════════════════
# QueueItem —— 队列快照的精简视图，仅暴露前端渲染需要的信息
# ═══════════════════════════════════════════════════════════════

class QueueItem(BaseModel):
    message_id: UUID
    content_preview: str       # 内容预览（前 100 字符）
    queue_position: int
    status: str
    created_at: datetime
