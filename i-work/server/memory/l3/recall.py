"""L3 画像召回（设计文档 L3-3）。

与 L1 / L2 都不同的一点：**画像不按当前输入检索、也不做长度裁剪**（doc L3-3.3）。
整份读出、整份注入 —— 它的"有界"由生成侧保证（提示词里的 2000 字符 / 1200 字上限），
因此这里没有 top-N、没有字符预算、没有相似度阈值。

挂点与 L1 / L2 一致：**每条用户消息算一次**，整条消息的工具循环复用。进 system 末尾的
稳定区（命中提示词缓存），不进用户消息前缀 —— 用户前缀只有 L1 的动态召回块。

非关键路径：任何失败只记日志、返回空串，对话照常。
"""
from __future__ import annotations

import asyncio

from server.memory.l3.prompts import render_persona_xml
from server.observability.logging import get_logger
from server.storage.base import L3PersonaRepository

logger = get_logger("iwork.memory")


class L3RecallService:
    """画像读取与注入。引擎持有一个实例。"""

    def __init__(self, repo: L3PersonaRepository, *, timeout_seconds: float = 5.0):
        self._repo = repo
        self._timeout = timeout_seconds

    async def persona_xml(self, *, user_id: str, agent_id: str) -> str:
        """取本轮要注入 system 末尾的 <user-persona> 块。空串 = 不注入。"""
        try:
            persona = await asyncio.wait_for(
                self._repo.get(user_id, agent_id or ""), timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("l3_recall_timeout", user_id=user_id, agent_id=agent_id)
            return ""
        except Exception as exc:  # 非关键路径：失败不阻塞对话
            logger.warning("l3_recall_failed", error=str(exc))
            return ""
        return render_persona_xml(persona.content if persona else "")
