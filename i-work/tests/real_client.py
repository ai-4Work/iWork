"""真执行器（fidelity L2）：扮演客户端，真跑内置工具，回投**真跑出来的**结果。

为什么需要它
------------
服务端不执行 shell——`bash` / `read_file` / `write_file` / `edit_file` / `glob` /
`grep` 六个内置工具全部归 `ToolLocation.CLIENT`（`tools/dispatcher.py` 兜底），
服务端只下发 `client.tool_request` 再 `sync_waiter.wait` 等回投。

既有测试用 fake 把应答换成事先写好的失败——那是"造结果"，①段（错误文本是否可判）
是编的。本模块把应答换成**真执行**产出：按 `tool_name` 在 workspace 里真跑，
真异常（文件不存在、依赖没装、匹配不唯一）自己冒出来。控制的是**条件**，不是**结果**。

L2 的边界（写在用例里，别夸大）
------------------------------
真执行器复刻的是**执行语义**，不是 Electron 客户端的错误格式化——同一类失败，
真客户端给的文本形状可能不同。③段（模型据此换方案）在 L2 仍靠 FakeLLM 脚本化，
所以它证明的是"错误能流到下一轮且可判"，不是"模型真会自愈"。

回投契约
--------
与 `1-Query Loop 引擎.md` 的工具结果形状一致：

    成功 {"status": "success", "output": str, "exit_code": 0, "duration_ms": int}
    失败 {"status": "error",   "error": str, "exit_code": int, "duration_ms": int}

`submit_client_tool_result` 内部 `normalize_client_result` 会把 `status` 归一成
账本读的 `success`（`models/tool_invocation.py:26`）。

用法
----
    ex = RealToolExecutor(engine, workspace=tmp_path)
    await ex.start()
    ...  enqueue 一条消息 ...
    await ex.wait_idle(engine)
    await ex.stop()

    ex.requests   # 收到的 client.tool_request（按序）
    ex.results    # (request_id, body) —— ①段真文本在这里
    ex.chunks     # 被消费掉的展示 chunk（执行器独占 stream_buffer，测试读这里）
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

POLL_INTERVAL = 0.01  # 轮询 stream_buffer 的间隔


def _shell_argv(command: str) -> list[str]:
    """按平台选 shell。与内置工具集成文档一致：win32 → Windows PowerShell。"""
    if sys.platform == "win32":
        return [
            "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", command,
        ]
    return ["bash", "-lc", command]


def _child_env() -> dict[str, str]:
    """子进程环境：强制 UTF-8 输出，免得中文 locale 下 stderr 用 GBK 编不出来。

    只钉编码，不碰错误本身——失败是环境和代码真跑出来的。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class RealToolExecutor:
    """等 `client.tool_request` → 真执行 → 回投真实结果。"""

    def __init__(
        self,
        engine,
        workspace: str | Path,
        *,
        timeout: float = 60.0,
        delay: float = 0.0,
        drop_n: int = 0,
    ):
        self.engine = engine
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        # 可编排时序（大类 #11「Client 回传异常」）：延迟 N 秒再回投 / 吞掉前 N 次回投
        self.delay = delay
        self.drop_n = drop_n

        self.chunks: list[dict] = []
        self.requests: list[dict] = []
        self.results: list[tuple[str, dict]] = []
        self.dropped: list[str] = []

        self._task: asyncio.Task | None = None
        self._stopping = False
        self._last_seq = 0

    # ── 生命周期 ──────────────────────────────────────────────
    async def start(self) -> "RealToolExecutor":
        self._task = asyncio.create_task(self._serve())
        return self

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def __aenter__(self) -> "RealToolExecutor":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def wait_idle(self, loops: int = 600) -> bool:
        for _ in range(loops):
            await asyncio.sleep(POLL_INTERVAL)
            if self.engine.state == "IDLE":
                return True
        return False

    async def wait_requests(self, n: int, loops: int = 600) -> bool:
        for _ in range(loops):
            await asyncio.sleep(POLL_INTERVAL)
            if len(self.requests) >= n:
                return True
        return False

    async def wait_results(self, n: int, loops: int = 600) -> bool:
        for _ in range(loops):
            await asyncio.sleep(POLL_INTERVAL)
            if len(self.results) >= n:
                return True
        return False

    # ── 主循环 ────────────────────────────────────────────────
    async def _serve(self) -> None:
        # stream_buffer.drain() 是**重放**语义（不清空，只按 seq 过滤），不是消费。
        # 必须自己记住水位，否则同一条 client.tool_request 会被反复回投。
        while not self._stopping:
            for chunk in self.engine.stream_buffer.drain(since_seq=self._last_seq):
                self._last_seq = chunk.get("seq", self._last_seq)
                self.chunks.append(chunk)
                if chunk.get("type") == "client.tool_request":
                    await self._handle(chunk)
            await asyncio.sleep(POLL_INTERVAL)

    async def _handle(self, chunk: dict) -> None:
        request_id = chunk["request_id"]
        self.requests.append(chunk)

        if self.drop_n > 0:
            self.drop_n -= 1
            self.dropped.append(request_id)
            return  # 不回投：让引擎走到等待超时 / 对账窗

        if self.delay:
            await asyncio.sleep(self.delay)

        started = time.monotonic()
        body = await self.execute(chunk.get("tool_name", ""), chunk.get("input") or {})
        body.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
        self.results.append((request_id, body))
        await self.engine.submit_client_tool_result(request_id, body)

    # ── 工具实现 ──────────────────────────────────────────────
    async def execute(self, tool_name: str, args: dict) -> dict:
        """真执行一次工具。任何异常都按客户端契约回投失败——真客户端也是这么兜的。"""
        handler = getattr(self, f"_do_{tool_name}", None)
        if handler is None:
            return self._error(f"未知工具: {tool_name}", None)
        try:
            return await handler(args)
        except Exception as e:
            return self._error(f"{type(e).__name__}: {e}", None)

    @staticmethod
    def _ok(output: str, **extra) -> dict:
        body = {"status": "success", "output": output, "exit_code": 0}
        body.update(extra)
        return body

    # 注意：调用方别再传 output= —— `_ok(out, output=x)` 会 TypeError，
    # 而 execute() 会把 TypeError 兜成失败回投，"真失败"用例反而照样绿。

    @staticmethod
    def _error(error: str, exit_code: int | None, **extra) -> dict:
        body: dict[str, Any] = {"status": "error", "error": error}
        if exit_code is not None:
            body["exit_code"] = exit_code
        body.update(extra)
        return body

    def _resolve(self, path: str | None) -> Path:
        if not path:
            raise ValueError("缺少参数: path")
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        return p

    async def _do_bash(self, args: dict) -> dict:
        command = args.get("command") or args.get("cmd")
        if not command:
            return self._error("缺少参数: command", None)

        proc = await asyncio.create_subprocess_exec(
            *_shell_argv(command),
            cwd=str(self.workspace),
            env=_child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return self._error(f"命令超时（{self.timeout:.0f}s）: {command}", None)

        out_s = out.decode("utf-8", errors="replace")
        err_s = err.decode("utf-8", errors="replace")
        if proc.returncode == 0:
            return self._ok(out_s or "(no output)")
        # 真失败：stderr 原样回投（这就是①段要判的证据）
        return self._error(err_s or out_s, proc.returncode, output=out_s)

    async def _do_read_file(self, args: dict) -> dict:
        path = self._resolve(args.get("path"))
        text = path.read_text(encoding="utf-8")
        return self._ok(text)

    async def _do_write_file(self, args: dict) -> dict:
        path = self._resolve(args.get("path"))
        content = args.get("content", "")
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self._ok(
            f"已{'覆盖' if existed else '写入'} {path.name}（{len(content)} 字节）",
            files=[{
                "path": str(path),
                "content": content,
                "action": "modified" if existed else "created",
            }],
        )

    async def _do_edit_file(self, args: dict) -> dict:
        path = self._resolve(args.get("path"))
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        # 匹配不唯一是真失败（大类 #4 编辑类失败）：与真客户端同一条判据
        if count == 0:
            return self._error(f"old_string 在 {path.name} 中未找到", 1)
        if count > 1:
            return self._error(f"old_string 在 {path.name} 中匹配到 {count} 处，需唯一", 1)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return self._ok(f"已编辑 {path.name}", files=[
            {"path": str(path), "content": new, "action": "modified"},
        ])

    async def _do_glob(self, args: dict) -> dict:
        pattern = args.get("pattern") or args.get("glob")
        if not pattern:
            return self._error("缺少参数: pattern", None)
        matches = sorted(
            str(p.relative_to(self.workspace)).replace("\\", "/")
            for p in self.workspace.glob(pattern) if p.is_file()
        )
        if not matches:
            return self._error(f"没有文件匹配 pattern: {pattern}", 1)
        return self._ok("\n".join(matches))

    async def _do_grep(self, args: dict) -> dict:
        pattern = args.get("pattern")
        if not pattern:
            return self._error("缺少参数: pattern", None)
        root = self._resolve(args.get("path") or ".") if args.get("path") else self.workspace
        rx = re.compile(pattern)
        hits: list[str] = []
        targets = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for f in targets:
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if rx.search(line):
                        rel = str(f.relative_to(self.workspace)).replace("\\", "/")
                        hits.append(f"{rel}:{i}: {line}")
            except (OSError, UnicodeDecodeError):
                continue
        if not hits:
            return self._error(f"没有匹配: {pattern}", 1)
        return self._ok("\n".join(hits))
