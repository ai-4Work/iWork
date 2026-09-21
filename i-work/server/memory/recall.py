"""召回侧：记忆怎么回到对话里（设计文档 L1-3）。

- 时机：**每条新用户消息**触发一次（不是每个工具循环 turn）。调用方（引擎）
  在消息开始时算一次并缓存整个 turn 循环复用。
- 查询：当前用户消息先清洗（剥注入标签 / 网关元数据 / base64），不足 2 字符跳过。
- 口径：top_k=5、阈值 0.15、整体超时 5 秒。文档给的阈值 0.3 是按**向量余弦**
  标定的，当前 TF-IDF 实现量纲不同（实测相关≈0.28、无关≈0.10），照抄 0.3 会把
  命中全部滤掉；阈值因此走 l1_recall_min_score 配置项，换向量后回调到 0.3。
- 非关键路径：失败或超时只记日志、返回空串，对话照常。
- 注入：动态部分（这一块）拼到本轮用户消息前缀；稳定部分（工具指南）由
  调用方拼到 system 提示词末尾 —— 拆开是为了让 system 段命中提示词缓存。

索引每次召回全量重建（TF-IDF，作用域内条数不大）。量级上去后换成向量服务
或加索引缓存，只动 L1Retriever 的实现，这里不变。
"""
from __future__ import annotations

import asyncio

from server.memory.l0 import clean_text
from server.memory.prompts import MEMORY_TOOLS_GUIDE, render_relevant_memories
from server.memory.retriever import TFIDFMemoryRetriever
from server.memory.types import EPISODIC, L1Memory
from server.observability.logging import get_logger
from server.storage.base import L1MemoryRepository

logger = get_logger("iwork.memory")

TRUNCATED_SUFFIX = "…已截断"


def clean_query(text: str | None) -> str:
    """查询清洗：复用 L0 的文本清洗（剥注入标签、行首时间戳、base64 等）。"""
    return clean_text(text or "", "user").strip()


def format_entry(memory: L1Memory, per_memory_chars: int) -> str:
    """一条记忆 → 注入行。episodic 附活动时间（doc L1-3.4 的例子）。"""
    content = memory.content
    if per_memory_chars > 0 and len(content) > per_memory_chars:
        content = content[:per_memory_chars] + TRUNCATED_SUFFIX

    label = memory.type
    if memory.scene_name:
        label += f"|{memory.scene_name}"
    line = f"[{label}] {content}"

    if memory.type == EPISODIC:
        start = memory.metadata.get("activity_start_time")
        end = memory.metadata.get("activity_end_time")
        if start and end and start != end:
            line += f" (活动时间: {start} ~ {end})"
        elif start or end:
            line += f" (活动时间: {start or end})"
    return line


class L1RecallService:
    """L1 记忆的召回与主动检索。引擎持有一个实例。"""

    def __init__(
        self,
        repo: L1MemoryRepository,
        *,
        top_k: int = 5,
        min_score: float = 0.3,
        timeout_seconds: float = 5.0,
        max_chars: int = 2000,
        per_memory_chars: int = 500,
    ):
        self._repo = repo
        self._top_k = top_k
        self._min_score = min_score
        self._timeout = timeout_seconds
        self._max_chars = max_chars
        self._per_memory_chars = per_memory_chars

    @property
    def guide_xml(self) -> str:
        """稳定部分：注入 system 提示词末尾的记忆工具使用指南。"""
        return MEMORY_TOOLS_GUIDE

    async def recall_block(
        self, *, user_id: str, agent_id: str, query: str,
    ) -> str:
        """取本轮要注入用户消息前缀的 <relevant-memories> 块。空串 = 不注入。"""
        cleaned = clean_query(query)
        if len(cleaned) < 2:
            return ""
        try:
            memories = await asyncio.wait_for(
                self._search(user_id, agent_id, cleaned), timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("l1_recall_timeout", user_id=user_id, agent_id=agent_id)
            return ""
        except Exception as exc:  # 非关键路径：任何失败都不阻塞对话
            logger.warning("l1_recall_failed", error=str(exc))
            return ""
        return self.format_block(memories)

    async def search(
        self, *, user_id: str, agent_id: str, query: str, top_k: int | None = None,
    ) -> list[L1Memory]:
        """主动检索（memory_search 工具）。调用方负责限次。"""
        cleaned = clean_query(query)
        if len(cleaned) < 2:
            return []
        return await self._search(user_id, agent_id, cleaned, top_k=top_k)

    async def _search(
        self, user_id: str, agent_id: str, query: str, *, top_k: int | None = None,
    ) -> list[L1Memory]:
        rows = await self._repo.list_by_scope(user_id, agent_id or "")
        if not rows:
            return []
        hits = TFIDFMemoryRetriever(rows).search(query, top_k or self._top_k)
        return [memory for memory, score in hits if score >= self._min_score]

    def format_block(self, memories: list[L1Memory]) -> str:
        """按总预算拼块：单条超长截断，整体超预算丢弃（doc L1-3.5）。"""
        entries: list[str] = []
        used = 0
        for memory in memories:
            line = format_entry(memory, self._per_memory_chars)
            if used + len(line) > self._max_chars:
                logger.info("l1_recall_budget_dropped", kept=len(entries))
                break
            entries.append(line)
            used += len(line)
        return render_relevant_memories(entries)
