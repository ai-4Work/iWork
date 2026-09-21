"""L3 画像的领域模型与常量（设计文档 L3-1 / L3-2）。

落库形状见 alembic 018。dataclass 是仓储接口的交换格式，不依赖 ORM —— 内存替身与 Pg 实现
互不感知（与 L1 / L2 同款）。

与 L1 / L2 的一个结构差异：**没有 Checkpoint**。画像行本身既是产物也是游标 ——
`updated_at` 就是"上次画像生成时间"，行的存在与否就是"有没有画像"。触发判据全部由 DB 现算。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# ── 提示词模式（doc L3-4 开头的"两套提示词的来源与选择"）──

MODE_CHAT = "chat"   # 个人画像：Persona Architect，四层深度扫描，上限 2000 字符
MODE_CODE = "code"   # 团队 Operating Doctrine：可复用工作原则，上限 1200 字
MODES = (MODE_CHAT, MODE_CODE)

# ── 触发原因（doc L3-1.2 的四优先级）──

TRIGGER_REQUEST = "P1 主动请求"
TRIGGER_COLD_START = "P2 冷启动"
TRIGGER_FIRST_SCENE = "P3 首个场景"
TRIGGER_THRESHOLD = "P4 阈值"

# 引擎默认值（overridden by config.py）
DEFAULT_MEMORY_THRESHOLD = 50


@dataclass
class L3Persona:
    """一份画像。user_id + agent_id 是主键，其余由落地层维护。"""

    user_id: str
    agent_id: str = ""
    content: str = ""
    version: int = 1
    memory_count_at_generation: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        """路由/客户端的序列化形状。"""
        return {
            "agent_id": self.agent_id,
            "content": self.content,
            "version": self.version,
            "memory_count_at_generation": self.memory_count_at_generation,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
