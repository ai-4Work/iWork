from __future__ import annotations
import asyncio


def session_prefix(session_id) -> str:
    """会话前缀（组合键的公共前缀，取消时广播用）。"""
    return f"{session_id}:"


def user_wait_key(session_id, message_id) -> str:
    """Plan/Build 用户决策等待键：同轮同消息至多一个决策等待点。"""
    return f"{session_id}:user:{message_id}"


def tool_wait_key(session_id, invocation_id) -> str:
    """客户端工具结果等待键：invocation_id == 下发 request_id，结果按 key 精确回投。"""
    return f"{session_id}:tool:{invocation_id}"


class SyncWaiter:
    """键控同步等待器（阶段 C-1 泛化）。

    从"per-session 单 Event"改为"按字符串键等待/回投"，消除串扰：
      - 客户端工具结果：tool_wait_key(session, request_id) —— 过期/重复回投只落在自己的
        key 上，不会唤醒后续无关等待（如 Plan 决策等待或下一轮工具等待）。
      - Plan/Build 用户决策：user_wait_key(session, message_id)。
      - 取消：resolve_all(session, value) 广播该会话所有在等 key。
    """

    def __init__(self, default_timeout: float = 300.0):
        self._events: dict[str, asyncio.Event] = {}
        self._results: dict[str, object] = {}
        self._timeout = default_timeout

    async def wait(self, key: str, timeout: float | None = None) -> object | None:
        """阻塞等待，直到 resolve(key) 或超时。
        返回 resolve 传入的值；超时返回 None。
        """
        if timeout is None:
            timeout = self._timeout
        # 清掉无等待者时 resolve 写入的残留结果，避免污染本次等待
        self._results.pop(key, None)
        event = asyncio.Event()
        self._events[key] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._results.pop(key, None)
        except asyncio.TimeoutError:
            return None
        finally:
            self._events.pop(key, None)

    def has_waiter(self, key: str) -> bool:
        """该 key 当前是否有在等协程（Part B：无等待者 = 该轮已被终止，回投改直接收口）。"""
        return key in self._events

    def resolve(self, key: str, value: object) -> None:
        """HTTP handler / 引擎调用：按 key 唤醒对应等待协程。"""
        self._results[key] = value
        event = self._events.get(key)
        if event:
            event.set()

    def resolve_all(self, session_id, value: object) -> None:
        """广播：唤醒该会话下所有在等 key（取消用）。"""
        prefix = session_prefix(session_id)
        for key in list(self._events):
            if key.startswith(prefix):
                self.resolve(key, value)
