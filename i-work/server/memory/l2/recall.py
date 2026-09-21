"""L2 场景召回（设计文档 L2-3）。

与 L1 的召回刻意相反的一点：**导航全量注入、不过滤、按热度降序**（doc L2-3.3），
而 L1 是"按相似度检索 top-N 注入"。理由是场景数量本来就有上限（默认 15 个），
导航给的是"有哪些历史情境"的索引，模型需要一眼看全才能判断该读哪个。

只注入名字 / 热度 / 摘要（渐进式披露，doc L2-3.5）；要看细节由模型主动调
`scene_read`，这是导航与工具的分工。

挂点与 L1 一致：**每条用户消息算一次**，整条消息的工具循环复用。导航进 system 末尾
（稳定半边，命中提示词缓存），不进用户消息前缀 —— 用户前缀只有 L1 的动态召回块。

非关键路径：任何失败只记日志、返回空串，对话照常。
"""
from __future__ import annotations

import asyncio

from server.memory.l2.prompts import SCENE_TOOLS_GUIDE, render_scene_navigation
from server.memory.l2.types import L2Scene, heat_flames, normalize_name
from server.observability.logging import get_logger
from server.storage.base import L2SceneRepository

logger = get_logger("iwork.memory")

FLAME = "🔥"
COLD_MARK = "·"   # heat < 50 的档位（doc L2-3.3 表里没有火）


def format_entry(scene: L2Scene) -> str:
    """一个场景 → 导航条目。热度用火苗让模型一眼看出权重。"""
    marks = FLAME * heat_flames(scene.heat) or COLD_MARK
    updated = scene.updated_at.isoformat() if scene.updated_at else "?"
    return (
        f"{marks} Scene: {scene.name}\n"
        f"热度: {scene.heat} | 更新: {updated}\n"
        f"Summary: {scene.summary}"
    )


class L2RecallService:
    """场景导航与按名读取。引擎持有一个实例。"""

    def __init__(
        self,
        repo: L2SceneRepository,
        *,
        nav_max_chars: int = 4000,
        timeout_seconds: float = 5.0,
    ):
        self._repo = repo
        self._nav_max_chars = nav_max_chars
        self._timeout = timeout_seconds

    @property
    def guide_xml(self) -> str:
        """稳定部分：注入 system 末尾的场景工具使用指南。"""
        return SCENE_TOOLS_GUIDE

    async def navigation_xml(self, *, user_id: str, agent_id: str) -> str:
        """取本轮要注入 system 末尾的 <scene-navigation> 块。空串 = 不注入。"""
        try:
            scenes = await asyncio.wait_for(
                self._repo.list_by_scope(user_id, agent_id or ""),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("l2_nav_timeout", user_id=user_id, agent_id=agent_id)
            return ""
        except Exception as exc:  # 非关键路径：失败不阻塞对话
            logger.warning("l2_nav_failed", error=str(exc))
            return ""

        entries: list[str] = []
        used = 0
        for scene in scenes:       # list_by_scope 已是热度降序
            entry = format_entry(scene)
            if used + len(entry) > self._nav_max_chars:
                # 文档说"全量注入不过滤"，但无上限地涨 system 提示词比部分导航更糟；
                # 按热度降序丢尾部（15 个场景的正常量级下这条永远不会触发）
                logger.info("l2_nav_budget_dropped", kept=len(entries), total=len(scenes))
                break
            entries.append(entry)
            used += len(entry)
        return render_scene_navigation(entries)

    async def read_scene(
        self, *, user_id: str, agent_id: str, name: str,
    ) -> L2Scene | None:
        """按名字取场景正文（scene_read 工具）。未命中返回 None，由调用方给可用名单。"""
        if not name:
            return None
        scope = agent_id or ""
        try:
            scene = await self._repo.get_by_name(user_id, scope, name)
            if scene is None:
                # 模型常带上空格 / .md 后缀，规范化后重试一次
                normalized = normalize_name(name)
                if normalized != name:
                    scene = await self._repo.get_by_name(user_id, scope, normalized)
            return scene
        except Exception as exc:
            logger.warning("l2_scene_read_failed", name=name, error=str(exc))
            return None

    async def names(self, *, user_id: str, agent_id: str) -> list[str]:
        """可用场景名清单（读取未命中时回给模型，等价于文档"只能读清单里的文件"）。"""
        try:
            scenes = await self._repo.list_by_scope(user_id, agent_id or "")
        except Exception as exc:
            logger.warning("l2_scene_names_failed", error=str(exc))
            return []
        return [s.name for s in scenes]
