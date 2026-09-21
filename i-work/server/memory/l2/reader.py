"""L2 整合的输入构造（设计文档 L2-2.2）。

一次整合的三类输入：
1. **本批新 L1 记忆** —— `updated_at > 游标` 的 retrievable 行，升序取前 N 条。
   只送 content / created_at / id：文档明说不携带 type / priority，L2 要自由整合，
   不按类型机械搬运。
2. **现有场景摘要清单** —— 全部可检索场景的名字 / 热度 / 更新时间 / 摘要。
3. **候选场景正文** —— 按 TF-IDF 相似度取 top-k 个场景，连带正文注入。

第 3 条是文档"LLM 用 read 工具按需读场景"在单次调用下的等价物：上限 15 个场景 × 1500 字符
本来就装得下，不需要工具循环。作用域边界与 L1 一致：永不跨 (user_id, agent_id)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from server.engine.tfidf import TFIDFRetriever
from server.memory.l2.types import L2Scene
from server.memory.types import L1Memory
from server.storage.base import L1MemoryRepository, L2SceneRepository


@dataclass
class L2Input:
    """一次整合读入的全部素材。"""

    memories: list[L1Memory] = field(default_factory=list)
    scenes: list[L2Scene] = field(default_factory=list)
    candidates: list[tuple[str, str]] = field(default_factory=list)  # (name, content)
    # 本批最新一条 L1 记忆的 updated_at —— 级联触发的判据（scheduler 用，不再查一次库）
    newest_at: datetime | None = None

    @property
    def memory_ids(self) -> list[str]:
        return [m.id for m in self.memories]


class _SceneDoc:
    """TFIDFRetriever 认 .block_id / .content（同 memory/retriever.py:32 的 _Doc）。"""

    __slots__ = ("block_id", "content")

    def __init__(self, scene: L2Scene):
        self.block_id = scene.id
        # 场景名本身就是主题（"技术研究-Billing超时排查"），与摘要一起进索引，
        # 只索引正文会让相似度偏向"正文写作风格"而不是"讲的是同一件事"。
        self.content = f"{scene.name} {scene.summary} {scene.content}"


class L2Reader:
    def __init__(self, l1_repo: L1MemoryRepository, scene_repo: L2SceneRepository):
        self._l1 = l1_repo
        self._scenes = scene_repo

    async def read(
        self,
        user_id: str,
        agent_id: str,
        *,
        since: datetime | None,
        batch_memories: int,
        candidate_scenes: int,
        candidate_chars: int,
        max_scenes: int,
    ) -> L2Input | None:
        """读入一个作用域的待整合素材。没有新记忆返回 None（调用方直接结束本轮）。"""
        memories = await self._l1.list_since(
            user_id, agent_id, since, limit=batch_memories,
        )
        if not memories:
            return None
        scenes = await self._scenes.list_by_scope(user_id, agent_id)
        return L2Input(
            memories=memories,
            scenes=scenes,
            candidates=self._select_candidates(
                memories, scenes, candidate_scenes, candidate_chars, max_scenes,
            ),
            # list_since 按 updated_at 升序，故末条即最新
            newest_at=memories[-1].updated_at,
        )

    @staticmethod
    def _select_candidates(
        memories: list[L1Memory],
        scenes: list[L2Scene],
        candidate_scenes: int,
        candidate_chars: int,
        max_scenes: int,
    ) -> list[tuple[str, str]]:
        """按相似度挑候选场景正文。累计字符超预算即止，按相似度降序（先来先得）。"""
        if not scenes:
            return []
        # 红色预警（场景数达上限）下 LLM 必须 MERGE，而它无法重写看不见正文的场景 ——
        # 此时把全部场景都当候选，靠下面的字符预算截断兜底。
        top_k = len(scenes) if len(scenes) >= max_scenes else min(candidate_scenes, len(scenes))
        retriever = TFIDFRetriever([_SceneDoc(s) for s in scenes])
        query = "\n".join(m.content for m in memories)
        by_id = {s.id: s for s in scenes}

        picked: list[tuple[str, str]] = []
        used = 0
        for scene_id, _score in retriever.search(query, top_k):
            scene = by_id.get(scene_id)
            if scene is None or scene.name in {n for n, _ in picked}:
                continue
            body = scene.content or ""
            # used 为 0 时无条件收下第一条：宁可超预算，也不给一个空正文清单
            if used and used + len(body) > candidate_chars:
                break
            picked.append((scene.name, body))
            used += len(body)

        if not picked:
            # 相似度全零（查询侧无有效词元）时退化为热度序前 k 个，list_by_scope 已是热度降序
            picked = [(s.name, s.content or "") for s in scenes[:candidate_scenes]]
        return picked
