"""L1 原子记忆的领域模型与常量。

落库形状见 alembic 016。这里的 dataclass 是仓储接口的交换格式，不依赖 ORM ——
内存替身与 Pg 实现互不感知，测试用前者、线上用后者。

作用域：设计文档是 teamId + agentId，teamId 缺省退化为 userId；本仓库没有
teams 表，因此直接按 user_id + agent_id（文档的降级分支）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

PERSONA = "persona"
EPISODIC = "episodic"
INSTRUCTION = "instruction"
MEMORY_TYPES = (PERSONA, EPISODIC, INSTRUCTION)

# 各类型的最低 priority，低于此值的候选直接丢弃（doc 493-507）。
# instruction 另有一个 -1 逃逸值：极其严格的全局死命令。
PRIORITY_FLOOR: dict[str, int] = {
    PERSONA: 50,
    EPISODIC: 60,
    INSTRUCTION: 70,
}
INSTRUCTION_HARD = -1


def new_memory_id() -> str:
    """m_<epoch_ms>_<hex8>（doc 761）。"""
    return f"m_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def passes_priority(memory_type: str, priority: int) -> bool:
    if memory_type == INSTRUCTION and priority == INSTRUCTION_HARD:
        return True
    return priority >= PRIORITY_FLOOR.get(memory_type, 60)


@dataclass
class L1Memory:
    """一条原子记忆。id / timestamps / version 由落地层维护，抽取侧不填。"""

    id: str
    user_id: str
    content: str
    type: str = EPISODIC
    agent_id: str = ""
    session_id: str | None = None
    priority: int = 0
    scene_name: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    timestamps: list[str] = field(default_factory=list)
    version: int = 1
    retrievable: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        """前端/路由的序列化形状（字段名对齐设计文档的 JSON 记录）。"""
        return {
            "id": self.id,
            "content": self.content,
            "type": self.type,
            "priority": self.priority,
            "scene_name": self.scene_name,
            "activity_start_time": self.metadata.get("activity_start_time"),
            "activity_end_time": self.metadata.get("activity_end_time"),
            "timestamps": list(self.timestamps),
            "version": self.version,
            "source_message_ids": list(self.source_message_ids),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class L1Checkpoint:
    """抽取游标：每条会话一条。落库而不是放内存，sweep 重启后不丢断点。"""

    session_id: str
    user_id: str
    agent_id: str = ""
    last_cursor: int = 0
    last_scene_name: str = ""
    last_extracted_at: datetime | None = None
