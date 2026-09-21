"""L3 增量读取与输入构造（设计文档 L3-2.2）。

**不重读全部历史**：每次只处理"上次画像之后变化的部分"。变化判据是场景的 `updated_at`
晚于画像行的 `updated_at`（画像行不在 → 全部场景算变化，配合 `l3_personas` 无行即首次）。

**一个重要的短路**：没有任何变化场景、且画像已存在 → 返回 None，本次直接跳过。所有场景
都在上次生成时分析过了，没有必要重复生成（P1 主动请求也不例外 —— 调用方跳过时不会清信号，
下轮场景一变就会再触发）。

日期不可解析时按"变化"处理（保守，宁多不少，doc L3-2.2）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from server.memory.l3.types import L3Persona
from server.observability.logging import get_logger
from server.storage.base import (
    L1MemoryRepository, L2SceneRepository, L3PersonaRepository,
)

logger = get_logger("iwork.memory")


def _aware(value: datetime | None) -> datetime | None:
    """tz-naive 一律按 UTC 解释，避免与 now(tz-aware) 相减时炸（同 l2/scheduler.py）。"""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_changed(scene_updated_at: datetime | None, since: datetime | None) -> bool:
    """场景是否算"上次画像之后变化过"。since 为 None（还没有画像）时全部算变化。"""
    if since is None:
        return True
    scene_at = _aware(scene_updated_at)
    since_at = _aware(since)
    if scene_at is None or since_at is None:
        return True   # 日期不可解析：按变化处理（doc L3-2.2）
    return scene_at > since_at


@dataclass
class L3Input:
    """一次 L3 生成的完整输入。"""

    persona: L3Persona | None          # 现有画像行（None = 首次）
    changed_scenes: list[tuple[str, str, str]] = field(default_factory=list)
    scene_count: int = 0               # 作用域内场景总数（含未变化的）
    memory_count: int = 0              # 当前 L1 记忆总数（写进快照供 P4 算增量）
    trigger: str = ""
    mode: str = ""

    @property
    def existing_content(self) -> str:
        return self.persona.content if self.persona else ""

    @property
    def is_first(self) -> bool:
        """首次 / 增量的唯一判据：有没有画像正文（doc L3-2.3，不是配置项）。"""
        return not self.existing_content.strip()


class L3Reader:
    """把"画像行 + 场景索引 + 记忆总数"读成 L3Input。"""

    def __init__(
        self,
        memory_repo: L1MemoryRepository,
        scene_repo: L2SceneRepository,
        persona_repo: L3PersonaRepository,
    ):
        self._memory = memory_repo
        self._scenes = scene_repo
        self._personas = persona_repo

    async def read(
        self, user_id: str, agent_id: str, *, trigger: str, mode: str,
    ) -> L3Input | None:
        """返回 None = 本次不生成（无变化场景且已有画像）。"""
        persona = await self._personas.get(user_id, agent_id)
        since = persona.updated_at if persona is not None else None

        scenes = await self._scenes.list_by_scope(user_id, agent_id)
        changed = [s for s in scenes if is_changed(s.updated_at, since)]
        if not changed and persona is not None:
            logger.info("l3_no_change", user_id=user_id, agent_id=agent_id)
            return None

        return L3Input(
            persona=persona,
            changed_scenes=[self._render_scene(s) for s in changed],
            scene_count=len(scenes),
            memory_count=await self._memory.count_by_scope(user_id, agent_id),
            trigger=trigger,
            mode=mode,
        )

    @staticmethod
    def _render_scene(scene) -> tuple[str, str, str]:
        """场景 → (名字, 元信息行, 正文)。元信息进提示词是因为 doc L3-2.2 要求
        "含元信息与正文"原样读出来（热度与更新时间本身就是画像该看重的信号）。"""
        updated = scene.updated_at.isoformat() if scene.updated_at else "未知"
        meta = f"热度: {scene.heat} | 更新: {updated} | 摘要: {scene.summary}"
        return scene.name, meta, scene.content
