"""L2 整合：一次 LLM 调用 → 动作数组（设计文档 L2-2.5 的动作模型）。

与 L1 的 `memory/extractor.py` 同一路子：`call_json` + `parse_json_payload`，解析失败返回
None，调度侧据此**不推进游标**，下一轮重试。

动作不合法时降级而不是丢弃 —— 游标照常越过本批 L1 记忆，丢掉动作等于永久丢掉那批信息。
降级方向统一为 `create`（对应 L1 的"降级为 store"，dedup.py:117-120）：未知 action /
update 目标解析不到 / merge 的 sources 全是非法名，都退化为新建。
**唯一真正丢弃的是 content 为空的动作** —— merge 若正文为空却照做，等于删掉旧场景不留替代。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from server.memory.extractor import call_json, parse_json_payload
from server.memory.l2.prompts import (
    consolidation_system_prompt, render_consolidation_user_prompt,
)
from server.memory.l2.reader import L2Input
from server.memory.l2.types import (
    ACTION_CREATE, ACTION_MERGE, ACTION_UPDATE, ACTIONS, SCENE_MAX_CHARS,
    ConsolidateResult, SceneAction, normalize_name,
)
from server.observability.logging import get_logger

logger = get_logger("iwork.memory")

SUMMARY_MAX_CHARS = 500  # 与 OrmL2Scene.summary 的 String(500) 对齐

# 文档 L2-2.6 第 4 步的标记；JSON 字段之外再兜一层（模型可能把标记写进正文）
_PERSONA_RE = re.compile(
    r"\[PERSONA_UPDATE_REQUEST\](.*?)\[/PERSONA_UPDATE_REQUEST\]", re.DOTALL,
)


class MemoryConsolidator:
    def __init__(self, llm, *, max_scenes: int, scene_max_chars: int = SCENE_MAX_CHARS):
        self._llm = llm
        self._max_scenes = max_scenes
        self._scene_max_chars = scene_max_chars

    async def consolidate(
        self, data: L2Input, *, scope_label: str = "",
    ) -> ConsolidateResult | None:
        """把一批新记忆整合进场景。返回 None = 本批失败（调用方不要推进游标）。"""
        user_prompt = render_consolidation_user_prompt(
            memories_json=self._render_memories(data),
            scene_summaries=self._render_scene_summaries(data),
            candidates=data.candidates,
            current_time=datetime.now(timezone.utc).isoformat(),
            count=len(data.scenes),
            max_scenes=self._max_scenes,
        )
        raw = await call_json(
            self._llm, consolidation_system_prompt(self._max_scenes), user_prompt,
        )
        payload = parse_json_payload(raw)
        if payload is None:
            logger.warning(
                "l2_consolidate_parse_failed", scope=scope_label, raw_head=raw[:200],
            )
            return None

        raw_actions, persona = self._unwrap(payload, raw)
        known = {s.name for s in data.scenes}
        batch_ids = data.memory_ids
        actions = [
            a for a in (
                self._to_action(item, known=known, batch_ids=batch_ids)
                for item in raw_actions
                if isinstance(item, dict)
            ) if a is not None
        ]
        if len(actions) < len(raw_actions):
            logger.info(
                "l2_actions_dropped", scope=scope_label,
                raw=len(raw_actions), kept=len(actions),
            )
        return ConsolidateResult(actions=actions, persona_update_request=persona)

    # ── 内部 ────────────────────────────────────────────────────

    @staticmethod
    def _render_memories(data: L2Input) -> str:
        """只送 content / created_at / id（doc L2-2.2：不携带 type / priority）。"""
        return json.dumps(
            [
                {
                    "id": m.id,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in data.memories
            ],
            ensure_ascii=False, indent=2,
        )

    @staticmethod
    def _render_scene_summaries(data: L2Input) -> str:
        if not data.scenes:
            return "（当前无已有场景）"
        rows = []
        for s in data.scenes:
            updated = s.updated_at.isoformat() if s.updated_at else "?"
            rows.append(
                f"- **{s.name}** | 热度: {s.heat} | 更新: {updated}\n  Summary: {s.summary}"
            )
        return "\n".join(rows)

    @staticmethod
    def _unwrap(payload, raw: str) -> tuple[list, str]:
        """兼容 `{"actions": [...]}` 与裸数组两种形状；persona 标记再兜一层正则。"""
        if isinstance(payload, list):
            actions, persona = payload, ""
        elif isinstance(payload, dict):
            actions = payload.get("actions")
            actions = actions if isinstance(actions, list) else []
            persona = str(payload.get("persona_update_request") or "").strip()
        else:
            actions, persona = [], ""
        if not persona:
            match = _PERSONA_RE.search(raw or "")
            if match:
                persona = match.group(1).strip()
        return actions, persona

    def _to_action(
        self, item: dict, *, known: set[str], batch_ids: list[str],
    ) -> SceneAction | None:
        """一条候选动作 → SceneAction。返回 None 表示这条该丢（正文为空）。"""
        content = str(item.get("content") or "").strip()
        if not content:
            return None

        action = str(item.get("action") or "").strip().lower()
        name = normalize_name(str(item.get("name") or ""))
        raw_name_given = bool(str(item.get("name") or "").strip())
        sources = [
            normalize_name(str(s)) for s in (item.get("sources") or []) if str(s).strip()
        ]
        sources = [s for s in sources if s in known]

        # 降级规则：未知动作 / 更新目标不在作用域内 / 合并无可解析的旧场景 → create
        if action not in ACTIONS:
            action = ACTION_CREATE
        if action == ACTION_UPDATE and name not in known:
            action = ACTION_CREATE
        if action == ACTION_MERGE and not sources:
            action = ACTION_CREATE
        if action in (ACTION_CREATE, ACTION_MERGE) and not raw_name_given:
            return None  # 新建/合并却没给名字，无兜底价值

        ids = [str(i) for i in (item.get("source_memory_ids") or []) if str(i).strip()]
        ids = [i for i in ids if i in set(batch_ids)] or list(batch_ids)

        return SceneAction(
            action=action,
            name=name,
            sources=sources,
            summary=str(item.get("summary") or "").strip()[:SUMMARY_MAX_CHARS],
            content=content[:self._scene_max_chars],
            source_memory_ids=ids,
        )
