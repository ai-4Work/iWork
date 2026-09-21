"""L3 画像生成：全流程唯一一次 LLM 调用 + 工程侧确定性后处理（doc L3-2.4 / L3-2.5）。

LLM **不给工具、也拿不到任何路径** —— 它只返回一份画像正文，输入的读取与结果的落库全部由
工程侧负责（doc L3-2.4）。因此这里没有"剥场景导航"这道工序：导航是 L2 自己的表字段、
由 L2 侧渲染，从不进画像。

后处理两道（doc L3-2.5）：① 转义注入边界标签 ② 空内容判定。转义后为空 → 返回 None，
调度侧据此**不写库**、下轮重试。
"""
from __future__ import annotations

from datetime import datetime, timezone

from server.memory.extractor import call_text
from server.memory.l3.prompts import (
    MODE_LABEL_FIRST, MODE_LABEL_ITERATE, ITERATION_GUIDE,
    escape_boundary_tags, render_changed_scenes, render_existing_persona,
    render_trigger_section, render_user_prompt, system_prompt_for,
)
from server.memory.l3.reader import L3Input
from server.observability.logging import get_logger

logger = get_logger("iwork.memory")


class PersonaGenerator:
    def __init__(self, llm):
        self._llm = llm

    async def generate(self, data: L3Input, *, scope_label: str = "") -> str | None:
        """返回后处理之后的画像正文；None = 本次生成失败（调用方不写库）。"""
        user_prompt = render_user_prompt(
            current_time=datetime.now(timezone.utc).isoformat(),
            mode_label=MODE_LABEL_FIRST if data.is_first else MODE_LABEL_ITERATE,
            trigger_section=render_trigger_section(data.trigger),
            total_processed=data.memory_count,
            scene_count=data.scene_count,
            changed_scene_count=len(data.changed_scenes),
            changed_scenes_content=render_changed_scenes(data.changed_scenes),
            existing_persona_section=render_existing_persona(data.existing_content),
            iteration_guide="" if data.is_first else ITERATION_GUIDE,
        )
        raw = await call_text(
            self._llm, system_prompt_for(data.mode), user_prompt,
        )

        content = escape_boundary_tags(raw).strip()
        if not content:
            logger.warning(
                "l3_generate_empty", scope=scope_label,
                mode=data.mode, raw_len=len(raw or ""),
            )
            return None
        return content
