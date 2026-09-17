"""
Hook 责任链执行模型。

Hook 按拦截点分组，同组内按 order 升序串联执行。
每个 hook 以子进程方式运行脚本，stdin 传入 JSON，stdout 返回结果。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("iwork.hooks")


class HookAction(str, Enum):
    CONTINUE = "CONTINUE"
    MODIFY = "MODIFY"
    STOP = "STOP"


@dataclass
class HookConfig:
    hook_id: str
    name: str
    description: str = ""
    on: str = ""
    script: str = ""
    order: int = 0
    timeout_ms: int = 5000
    enabled: bool = True

    def __post_init__(self):
        if not self.hook_id:
            raise ValueError("hook_id 不能为空")
        if not self.name:
            raise ValueError("name 不能为空")
        if not self.on:
            raise ValueError("on (拦截点) 不能为空")
        if not self.script:
            raise ValueError("script 路径不能为空")


@dataclass
class HookResult:
    action: HookAction
    modified_input: dict[str, Any] | None = None
    reason: str | None = None


class HookManager:
    """按拦截点缓存已排序的 hook 列表，引擎调用 run() 时按序执行。"""

    def __init__(self, hooks_config: list[dict]):
        self._hooks: dict[str, list[HookConfig]] = {}
        for h in sorted(hooks_config, key=lambda x: x.get("order", 0)):
            if not h.get("enabled", True):
                continue
            cfg = HookConfig(**h)
            self._hooks.setdefault(cfg.on, []).append(cfg)

    @property
    def hook_count(self) -> int:
        return sum(len(v) for v in self._hooks.values())

    def hooks_for(self, hook_point: str) -> list[HookConfig]:
        return self._hooks.get(hook_point, [])

    async def run(
        self, hook_point: str, input_data: dict,
        session_id: str = "", user_id: str = "",
        message_id: str = "", turn: int = 0,
    ) -> HookResult:
        hooks = self._hooks.get(hook_point, [])
        for hook in hooks:
            try:
                result = await asyncio.wait_for(
                    self._execute(hook, hook_point, input_data,
                                  session_id, user_id, message_id, turn),
                    timeout=hook.timeout_ms / 1000,
                )
            except asyncio.TimeoutError:
                logger.warning("hook_timeout  hook=%s  on=%s", hook.name, hook_point)
                continue
            except Exception:
                logger.exception("hook_error  hook=%s  on=%s", hook.name, hook_point)
                continue

            if result.action == HookAction.STOP:
                logger.info("hook_stopped  hook=%s  reason=%s", hook.name, result.reason)
                return result
            if result.action == HookAction.MODIFY and result.modified_input is not None:
                input_data = result.modified_input

        return HookResult(action=HookAction.CONTINUE, modified_input=input_data)

    async def _execute(
        self, hook: HookConfig, hook_point: str, input_data: dict,
        session_id: str, user_id: str, message_id: str, turn: int,
    ) -> HookResult:
        hook_input = {
            "hook_point": hook_point,
            "session_id": session_id,
            "user_id": user_id,
            "message_id": message_id,
            "turn": turn,
            "input": input_data,
        }

        # 安全：不传敏感环境变量，只传 IWORK_HOOK_INPUT
        safe_env = {
            k: v for k, v in os.environ.items()
            if not any(s in k.upper() for s in ("API_KEY", "PASSWORD", "SECRET", "TOKEN", "DATABASE_URL"))
        }

        proc = await asyncio.create_subprocess_exec(
            hook.script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
            cwd=os.path.dirname(os.path.abspath(hook.script)) or ".",
        )
        stdout, stderr = await proc.communicate(json.dumps(hook_input).encode())

        if proc.returncode != 0:
            reason = stderr.decode()[:200] if stderr else f"exit code {proc.returncode}"
            return HookResult(action=HookAction.STOP, reason=reason)

        try:
            return HookResult(**json.loads(stdout))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("hook_bad_json  hook=%s  error=%s  stdout=%.200s",
                           hook.name, e, stdout.decode(errors="replace"))
            return HookResult(action=HookAction.CONTINUE)
