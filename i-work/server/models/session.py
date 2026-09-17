from __future__ import annotations
from uuid import UUID, uuid4
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


# ═══════════════════════════════════════════════════════════════
# 客户端工具定义 —— 在 POST /sessions 时由 Client 上报
# ═══════════════════════════════════════════════════════════════

class ClientTool(BaseModel):
    """客户端本地工具定义。name + description 给 LLM 看，input_schema 定义参数。"""
    name: str                                   # e.g. "bash", "read_file", "write_file", "edit_file"
    description: str                            # 工具功能描述
    input_schema: dict = Field(default_factory=dict)  # JSON Schema 参数定义


# ═══════════════════════════════════════════════════════════════
# AgentConfig —— 多 Agent 配置
# ═══════════════════════════════════════════════════════════════

class AgentConfig(BaseModel):
    """多 Agent 会话中的单个 Agent 配置。"""
    agent_id: str                                   # 对应 expert.id 或 team.id
    agent_type: str = "expert"                      # "expert" | "team"
    role: str = "member"                            # "lead" | "member"
    model: str = ""                                 # 该 agent 的模型，空 = 继承 session.model
    mode: str = "build"                             # ask | plan | build
    workspace: str = ""                             # 空 = 继承 session.workspace
    # 子 agent 继承父多少轮上下文（§9.11.5）：none | all | "<正整数>"。
    # 空 = 用 settings.fork_turns_default；调研型 agent 配 all，工具型配 none。
    fork_turns: str = ""


# ═══════════════════════════════════════════════════════════════
# Session —— 服务端完整会话模型
# ═══════════════════════════════════════════════════════════════

class Session(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: str
    title: str = "新建任务"
    status: SessionStatus = SessionStatus.ACTIVE
    mode: str = "build"                         # ask | plan | build
    scene_mode: str = "office"                  # office | code
    model: str = ""                             # 模型标识符，e.g. "claude-opus-4-7"
    workspace: str = ""
    shell_env: str = ""                         # 客户端执行环境：powershell | zsh | bash（空 = 未上报，不注入 Shell 提示词）
    client_tools: list[ClientTool] = Field(default_factory=list)  # 创建时注册的客户端工具清单
    client_mcp_tools: list[dict] = Field(default_factory=list)  # 客户端上报的 MCP 工具清单
    parent_id: UUID | None = None               # 父会话 ID（子 agent 会话）
    # agent 树寻址（§9.11.4）：顶层会话 /root，子会话 /root/{member_id}。
    # 与 parent_id 的区别：parent_id 是单跳边，agent_path 是稳定地址——将来树变深
    # 或需要按路径寻址（派发的 task 只带路径，不带 session id）时用它。
    agent_path: str = "/root"
    # agent 树顶层会话 ID。lead 就跑在团队会话里，所以「子的 root = 团队会话 id」，
    # 于是子的 parent_id ≠ root_session_id。刻意不加 FK：会话只归档不硬删（§9.11.12 #7）。
    root_session_id: UUID | None = None
    agents: list[AgentConfig] = Field(default_factory=list)  # 多 Agent 配置
    current_message_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════
# SessionCreate —— POST /sessions 请求体
# ═══════════════════════════════════════════════════════════════

class SessionCreate(BaseModel):
    """创建会话请求。客户端生成 id，上报 client_tools 及初始配置。"""
    id: UUID = Field(default_factory=uuid4)     # 客户端生成的 UUID v4，服务端幂等校验
    scene_mode: str = "office"                  # office | code
    workspace: str = ""                         # 工作空间根目录绝对路径
    shell_env: str = ""                         # 客户端执行环境：powershell | zsh | bash（空 = 未上报）
    model: str = ""                             # 模型标识符
    mode: str = "build"                         # ask | plan | build
    client_tools: list[ClientTool] = Field(default_factory=list)
    agents: list[AgentConfig] = Field(default_factory=list)  # 多 Agent 配置，空 = 单人聊天


# ═══════════════════════════════════════════════════════════════
# SessionUpdate —— PATCH /sessions/{id} 请求体
# ═══════════════════════════════════════════════════════════════

class SessionUpdate(BaseModel):
    """更新会话配置。所有字段可选，传哪些更新哪些。"""
    workspace: str | None = None
    model: str | None = None
    mode: str | None = None                     # ask | plan | build
    scene_mode: str | None = None               # office | code


# ═══════════════════════════════════════════════════════════════
# 响应模型
# ═══════════════════════════════════════════════════════════════

class SessionListItem(BaseModel):
    """GET /sessions 列表中的单项。"""
    id: UUID
    title: str
    mode: str
    scene_mode: str
    model: str
    workspace: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    status: str


class SessionDetail(BaseModel):
    """GET /sessions/{id} 返回的完整会话详情。"""
    id: UUID
    title: str
    mode: str
    scene_mode: str
    model: str
    workspace: str
    status: str
    client_tools: list[ClientTool]
    agents: list[AgentConfig] = Field(default_factory=list)
    current_processing: dict | None = None
    queue_size: int = 0
    message_count: int = 0
    created_at: datetime
    updated_at: datetime
