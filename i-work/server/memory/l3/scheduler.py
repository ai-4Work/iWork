"""L3 触发判定与落地（设计文档 L3-1.2 / L3-2.6 / L3-2.7）。

**没有 run_forever，没有独立定时任务** —— L2 整合完成后级联调用 `maybe_generate`（doc L3-1.2
"L3 不是独立定时任务，而是由 L2 级联触发的"），因此 L2 被跳过的那一轮 L3 也不评估。

**没有 l3_checkpoints**：触发判据全部由 DB 现算 ——
- "有没有画像" = `l3_personas` 里有没有这一行（P2 冷启动、首次/增量）
- "上次画像生成时间" = 行的 `updated_at`（筛变化场景）
- "自上次画像以来新增多少记忆" = 当前 L1 记忆总数 − 行的 `memory_count_at_generation`（P4）

四优先级按序评估，命中任一即生成（doc L3-1.2 的表格）：
P1 主动请求 → P2 冷启动 → P3 首个场景 → P4 阈值。

失败语义（doc L3-2.6）：LLM 返回空 → 不写库、**不清 P1 信号**，跟随 L2 的扫描节奏下轮重试。
因此 `maybe_generate` 自己吞掉异常 —— 画像生成失败不能把 L2 的整轮 sweep 判成失败。
"""
from __future__ import annotations

from server.memory.l3.generator import PersonaGenerator
from server.memory.l3.reader import L3Reader
from server.memory.l3.types import (
    DEFAULT_MEMORY_THRESHOLD, MODE_CHAT, TRIGGER_COLD_START, TRIGGER_FIRST_SCENE,
    TRIGGER_REQUEST, TRIGGER_THRESHOLD, L3Persona,
)
from server.observability.logging import get_logger
from server.storage.base import (
    L1MemoryRepository, L2SceneRepository, L3PersonaRepository,
)

logger = get_logger("iwork.memory")


class L3Scheduler:
    def __init__(
        self,
        *,
        persona_repo: L3PersonaRepository,
        scene_repo: L2SceneRepository,
        memory_repo: L1MemoryRepository,
        reader: L3Reader,
        generator: PersonaGenerator,
        threshold: int = DEFAULT_MEMORY_THRESHOLD,
        mode: str = MODE_CHAT,
    ):
        self._personas = persona_repo
        self._scenes = scene_repo
        self._memory = memory_repo
        self._reader = reader
        self._generator = generator
        self._threshold = threshold
        self._mode = mode

    async def maybe_generate(
        self, user_id: str, agent_id: str, *, request: str = "",
    ) -> bool:
        """返回是否真的生成并落库了一份画像。任何异常都吞掉（L2 的 sweep 不能被拖垮）。"""
        try:
            return await self._generate(user_id, agent_id, request or "")
        except Exception as exc:
            logger.warning(
                "l3_generate_failed", user_id=user_id, agent_id=agent_id,
                error=str(exc),
            )
            return False

    # ── 单作用域 ────────────────────────────────────────────────

    async def _generate(self, user_id: str, agent_id: str, request: str) -> bool:
        trigger = await self._trigger_for(user_id, agent_id, request)
        if trigger is None:
            return False

        scope_label = f"{user_id}/{agent_id}"
        data = await self._reader.read(
            user_id, agent_id, trigger=trigger, mode=self._mode,
        )
        if data is None:      # 无变化场景且已有画像：跳过，**不动 P1 信号**
            return False

        content = await self._generator.generate(data, scope_label=scope_label)
        if content is None:   # 生成失败：不写行、不清信号，下轮重试
            return False

        await self._personas.upsert(L3Persona(
            user_id=user_id,
            agent_id=agent_id or "",
            content=content,
            memory_count_at_generation=data.memory_count,
        ))
        await self._consume_request(user_id, agent_id)

        logger.info(
            "l3_generate_done",
            user_id=user_id,
            agent_id=agent_id,
            trigger=trigger,
            mode=data.mode,
            first=data.is_first,
            scenes=len(data.changed_scenes),
            memories=data.memory_count,
            chars=len(content),
        )
        return True

    async def _trigger_for(
        self, user_id: str, agent_id: str, request: str,
    ) -> str | None:
        """四优先级依次判定，命中即返回触发原因。"""
        if request.strip():
            return TRIGGER_REQUEST

        checkpoint = await self._scenes.get_checkpoint(user_id, agent_id)
        processed = checkpoint.processing_count if checkpoint else 0
        persona = await self._personas.get(user_id, agent_id)

        # P2 冷启动：已抽过场景、但还没有画像行
        if processed >= 1 and persona is None:
            return TRIGGER_COLD_START

        # P3 首个场景：只完成过 1 次场景抽取、且画像之后有新记忆。
        # DB 口径下 P2 已覆盖了"没有画像行"的那一半，这条实际只在画像行存在、
        # 而 L2 的 processing_count 仍是 1 的窗口里成立（doc L3-1.2 原表如此，保留）。
        if processed == 1 and persona is not None:
            if await self._has_new_memory(user_id, agent_id, persona):
                return TRIGGER_FIRST_SCENE

        # P4 阈值：自上次画像以来新增的记忆数达到阈值
        if persona is not None:
            current = await self._memory.count_by_scope(user_id, agent_id)
            if current - (persona.memory_count_at_generation or 0) >= self._threshold:
                return TRIGGER_THRESHOLD

        return None

    async def _has_new_memory(
        self, user_id: str, agent_id: str, persona: L3Persona,
    ) -> bool:
        rows = await self._memory.list_since(
            user_id, agent_id, since=persona.updated_at, limit=1,
        )
        return bool(rows)

    async def _consume_request(self, user_id: str, agent_id: str) -> None:
        """清掉 P1 信号：这次插队已经被消费（doc L3-2.7）。"""
        checkpoint = await self._scenes.get_checkpoint(user_id, agent_id)
        if checkpoint is None or not checkpoint.persona_update_request:
            return
        checkpoint.persona_update_request = ""
        await self._scenes.upsert_checkpoint(checkpoint)
