"""场景切分 + 记忆抽取（设计文档 L1-2.4）。

全流程两处 LLM 调用中的第一处。一次调用同时完成场景切分与记忆抽取，
输出结构化 JSON；上限截断与编号分配在工程侧做（L1-2.4 第 ④ 步）。

解析失败不抛给主链路：返回 None，调度侧据此**不推进游标**，下一轮重试。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from server.memory.l0 import L0Batch
from server.memory.prompts import EXTRACTION_SYSTEM_PROMPT, render_extraction_user_prompt
from server.memory.types import (
    MEMORY_TYPES, L1Memory, new_memory_id, passes_priority,
)
from server.observability.logging import get_logger

logger = get_logger("iwork.memory")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


async def call_json(llm, system: str, user: str) -> str:
    """非流式调用的等价物：把流 drain 成完整文本。tool_choice=none 禁掉工具。

    与 context_compressor._call_haiku_for_compression 同一路子 —— 仓库里的
    LLM 客户端只有流式接口，没有 response_format。
    """
    chunks: list[str] = []
    async for chunk in llm.stream(
        messages=[{"role": "user", "content": user}],
        system=system,
        tools=None,
        tool_choice="none",
    ):
        if chunk.type == "text" and chunk.delta:
            chunks.append(chunk.delta)
    return "".join(chunks)


async def call_text(llm, system: str, user: str) -> str:
    """`call_json` 的纯文本版：同样把流 drain 成完整文本、禁掉工具，但**不解析**。

    L3 画像生成用它 —— LLM 的返回本身就是最终正文（doc L3-2.4：不夹带思考过程、
    不包代码块），工程侧只做转义与空判定，没有结构化解析这一环。
    """
    chunks: list[str] = []
    async for chunk in llm.stream(
        messages=[{"role": "user", "content": user}],
        system=system,
        tools=None,
        tool_choice="none",
    ):
        if chunk.type == "text" and chunk.delta:
            chunks.append(chunk.delta)
    return "".join(chunks)


def parse_json_payload(text: str):
    """剥 ```json 围栏 → 定位首个 [ 或 { → raw_decode（自动忽略尾随解释文本）。

    返回 None 表示本批解析失败。
    """
    if not text:
        return None
    cleaned = text.strip()
    fence = _FENCE_RE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    starts = [i for i in (cleaned.find("["), cleaned.find("{")) if i >= 0]
    if not starts:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(cleaned[min(starts):])
        return payload
    except json.JSONDecodeError:
        return None


@dataclass
class ExtractResult:
    memories: list[L1Memory] = field(default_factory=list)
    scene_name: str = ""


class MemoryExtractor:
    def __init__(self, llm, *, max_per_run: int = 10):
        self._llm = llm
        self._max = max_per_run

    async def extract(
        self,
        batch: L0Batch,
        *,
        user_id: str,
        agent_id: str = "",
        session_id: str | None = None,
        previous_scene: str = "",
    ) -> ExtractResult | None:
        """抽取一批 L0 新消息。返回 None = 失败（调用方不要推进游标）。"""
        if not batch.has_new:
            return ExtractResult(memories=[], scene_name=previous_scene)

        user_prompt = render_extraction_user_prompt(
            previous_scene, batch.background, batch.new_messages,
        )
        raw = await call_json(self._llm, EXTRACTION_SYSTEM_PROMPT, user_prompt)
        scenes = parse_json_payload(raw)
        if not isinstance(scenes, list):
            logger.warning(
                "l1_extract_parse_failed",
                session_id=str(session_id),
                raw_head=raw[:200],
            )
            return None

        timestamps = {
            m.id: m.timestamp for m in batch.new_messages if m.timestamp
        }
        return self._to_memories(
            scenes,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            timestamps=timestamps,
            fallback_scene=previous_scene,
        )

    def _to_memories(
        self, scenes, *, user_id, agent_id, session_id, timestamps, fallback_scene,
    ) -> ExtractResult:
        memories: list[L1Memory] = []
        scene_name = fallback_scene or ""
        truncated = False

        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            name = str(scene.get("scene_name") or "").strip()
            if name:
                scene_name = name
            for item in scene.get("memories") or []:
                if not isinstance(item, dict):
                    continue
                if len(memories) >= self._max:
                    truncated = True
                    break
                memory = self._build(
                    item, scene_name=scene_name, user_id=user_id,
                    agent_id=agent_id, session_id=session_id,
                    timestamps=timestamps,
                )
                if memory is not None:
                    memories.append(memory)
            if truncated:
                break

        if truncated:
            logger.info("l1_extract_truncated", count=len(memories), limit=self._max)
        return ExtractResult(memories=memories, scene_name=scene_name)

    @staticmethod
    def _build(
        item: dict, *, scene_name, user_id, agent_id, session_id, timestamps,
    ) -> L1Memory | None:
        """一条候选 → L1Memory。质量门（类型 + priority 阈值）不合格返回 None。"""
        content = str(item.get("content") or "").strip()
        memory_type = str(item.get("type") or "").strip()
        if not content or memory_type not in MEMORY_TYPES:
            return None
        try:
            priority = int(item.get("priority"))
        except (TypeError, ValueError):
            return None
        if not passes_priority(memory_type, priority):
            return None

        metadata = item.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        source_ids = [
            str(s) for s in (item.get("source_message_ids") or []) if s
        ]
        # 时间轨迹：来源消息的时间戳并集去重排序（merge 时在此基础上再并）
        trace = sorted({timestamps[i] for i in source_ids if i in timestamps})
        for key in ("activity_start_time", "activity_end_time"):
            value = metadata.get(key)
            if isinstance(value, str) and value and value not in trace:
                trace.append(value)
        trace.sort()

        return L1Memory(
            id=new_memory_id(),
            user_id=user_id,
            content=content,
            type=memory_type,
            agent_id=agent_id or "",
            session_id=str(session_id) if session_id else None,
            priority=priority,
            scene_name=scene_name or "",
            source_message_ids=source_ids,
            metadata=metadata,
            timestamps=trace,
        )
