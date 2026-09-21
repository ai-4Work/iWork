"""L1 检索抽象。

设计文档的首选是向量余弦，三级降级为「向量 → 关键词 → 跳过」。本仓库没有
pgvector / embedding 服务，因此当前落在**关键词检索**档：复用 engine 的
TFIDFRetriever（CJK 一元+二元、DF 加权 idf、L2 归一化、余弦）。

L1Retriever 是接口，将来接上向量服务只需换实现，召回侧与去重侧都不动。

注意量纲：TF-IDF 余弦分与向量余弦分不同分布，因此 l1_recall_min_score 默认
0.3（照抄文档）只是起点，首轮跑完按实际分布调。
"""
from __future__ import annotations

from typing import Protocol

from server.engine.tfidf import TFIDFRetriever
from server.memory.types import L1Memory


class L1Retriever(Protocol):
    """检索接口：查询进，相关记忆 + 分数出。"""

    def search(self, query: str, top_k: int = 5) -> list[tuple[L1Memory, float]]:
        """按相关度降序返回 top_k，分数低于调用方阈值由调用方过滤。"""
        ...

    def by_ids(self, ids: list[str]) -> list[L1Memory]:
        """按 id 取回记忆（去重侧要把候选 id 还原成完整记录）。"""
        ...


class _Doc:
    """TFIDFRetriever 认 .block_id / .content，这里把 L1Memory 适配过去。"""

    __slots__ = ("block_id", "content")

    def __init__(self, memory: L1Memory):
        self.block_id = memory.id
        self.content = memory.content


class TFIDFMemoryRetriever:
    """TF-IDF 实现。索引一次全量构建（作用域内记忆条数不大）。"""

    def __init__(self, memories: list[L1Memory] | None = None):
        self._by_id: dict[str, L1Memory] = {}
        self._tfidf = TFIDFRetriever()
        if memories:
            self.build(memories)

    def build(self, memories: list[L1Memory]) -> None:
        self._by_id = {m.id: m for m in memories}
        self._tfidf.build([_Doc(m) for m in memories])

    def search(self, query: str, top_k: int = 5) -> list[tuple[L1Memory, float]]:
        if not query or not self._by_id:
            return []
        hits = self._tfidf.search(query, top_k)
        return [
            (self._by_id[bid], score) for bid, score in hits if bid in self._by_id
        ]

    def by_ids(self, ids: list[str]) -> list[L1Memory]:
        return [self._by_id[i] for i in ids if i in self._by_id]

    def __len__(self) -> int:
        return len(self._by_id)


def build_retriever(memories: list[L1Memory]) -> TFIDFMemoryRetriever:
    return TFIDFMemoryRetriever(memories)
