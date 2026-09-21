"""L2 场景记忆的领域模型与常量（设计文档第二部分）。

**与设计文档的偏离**：文档假定场景是磁盘上的 `.md` 文件（`技术研究-Billing超时排查.md`），
LLM 用 read / write / edit 三个文件工具在沙箱里自主增删改。iWork 把场景存进 `l2_scenes`
表，LLM 改为**一次调用输出动作数组**（create / update / merge），工程侧单事务落地。

于是文档里只服务于文件系统的那些机制不再存在：备份与整体还原（事务取代）、
`[DELETED]` 标记（动作的 sources 列表取代）、全量重建索引（表本身就是索引）、
画像文件追导航（导航直接进提示词）、记忆库镜像（场景本来就在库里）、
文件头 `-----META-START-----`（summary / heat / 时间戳变成列）。
**文件名规范化保留**，退化成纯函数 `normalize_name()` —— 名字仍是导航里对外的键。

与存储介质无关的内容规则逐条照抄文档：动作模型与优先级、热度算法、场景正文的章节骨架。

作用域：文档是 teamId + agentId，teamId 缺省退化为 userId；本仓库没有 teams 表，因此直接用
user_id + agent_id（与 L1 同款降级，见 memory/types.py:6-7）。
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

# 动作模型（doc L2-2.5）。文档里的"删除（软删）"没有独立动作：merge 会连带软删被并的场景，
# 这是工程侧按 sources 列表做的确定性动作，不需要 LLM 单独声明。
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_MERGE = "merge"
ACTIONS = (ACTION_CREATE, ACTION_UPDATE, ACTION_MERGE)

# 场景正文的字符上限（doc L2-4.1：每个 md 控制在 1500 字符内）
SCENE_MAX_CHARS = 1500
# 场景名长度上限，与 OrmL2Scene.name 的 String(200) 对齐
NAME_MAX_CHARS = 200


def new_scene_id() -> str:
    """s_<epoch_ms>_<hex8>，形状对齐 L1 的 m_<epoch_ms>_<hex8>（memory/types.py:31）。"""
    return f"s_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def heat_flames(heat: int) -> int:
    """热度分档（doc L2-3.3）：>=1000 五火 / >=500 四火 / >=200 三火 / >=100 双火 / >=50 单火。"""
    for threshold, flames in ((1000, 5), (500, 4), (200, 3), (100, 2), (50, 1)):
        if heat >= threshold:
            return flames
    return 0


# ── 名字规范化（doc L2-2.6 第 2 步：文件名规范化） ──────────────────────
# 允许：字母 / 数字 / CJK / 下划线（\w 在 unicode 下涵盖）、短横线、点号。
# 空白（含全角空格）→ 短横线；其余标点一律删除。
_WS_RE = re.compile(r"[\s　]+")
_INVALID_RE = re.compile(r"[^\w\-.]")
_DASH_RUN_RE = re.compile(r"-{2,}")
_MD_SUFFIX_RE = re.compile(r"\.md\.?$", re.IGNORECASE)


def normalize_name(raw: str) -> str:
    """场景名规范化。空结果用 `scene-<hex6>` 兜底，保证名字永远是可用作导航键的非空串。

    文档要求"必须以 .md 结尾"，落库后场景不再是文件，后缀统一剥掉；LLM 若照旧带上
    也会在这里被去掉，不会污染名字。
    """
    name = _MD_SUFFIX_RE.sub("", (raw or "").strip())
    name = _WS_RE.sub("-", name)
    name = _INVALID_RE.sub("", name)
    name = _DASH_RUN_RE.sub("-", name).strip("-.")
    name = name[:NAME_MAX_CHARS].strip("-.")
    return name or f"scene-{uuid.uuid4().hex[:6]}"


@dataclass
class L2Scene:
    """一个场景。id / heat / version / created_at 由落地层维护，整合侧不填。"""

    id: str
    user_id: str
    agent_id: str = ""
    name: str = ""
    summary: str = ""
    content: str = ""
    heat: int = 1
    version: int = 1
    source_memory_ids: list[str] = field(default_factory=list)
    retrievable: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        """前端/路由的序列化形状。"""
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "content": self.content,
            "heat": self.heat,
            "version": self.version,
            "source_memory_ids": list(self.source_memory_ids),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class L2Checkpoint:
    """整合游标：每个 (user_id, agent_id) 作用域一条。

    与 l1_checkpoints 的维度不同 —— L1 是 per 会话，L2 是 per 作用域（doc L2-1.1：刻意忽略
    用户、会话、任务维度，跨会话累积）。落库而不是放内存，sweep 重启后不丢断点。
    """

    user_id: str
    agent_id: str = ""
    last_memory_at: datetime | None = None
    last_run_at: datetime | None = None
    processing_count: int = 0
    # doc L2-2.6 第 4 步：解析 LLM 输出的 [PERSONA_UPDATE_REQUEST]，供 L3 画像刷新优先触发。
    # 落信号后由 L3 生成侧消费并清空（doc L3-1.2 触发 P1）。
    persona_update_request: str = ""


@dataclass
class SceneAction:
    """LLM 给出的一条场景整合动作。heat / version / created_at 由 store 按文档规则算，不听 LLM 的。"""

    action: str
    name: str = ""
    sources: list[str] = field(default_factory=list)  # 仅 merge：被合并的旧场景名
    summary: str = ""
    content: str = ""
    source_memory_ids: list[str] = field(default_factory=list)


@dataclass
class ConsolidateResult:
    """一次 L2 整合的产物。"""

    actions: list[SceneAction] = field(default_factory=list)
    persona_update_request: str = ""
