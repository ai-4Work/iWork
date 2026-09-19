"""第3层（韧性）首批用例：**造环境，不造结果**。

与既有用例的根本差别：这里不写死一段失败文本喂进去，而是把 workspace / 环境摆成
必然失败的形状，让真代码路径真失败，再断言三段链：

    ① 工具产出可判的错误  →  ② 该错误原样进入下一轮上下文  →  ③ 下一轮动作变

本文件覆盖第3层 R1–R4（B 类下界，工具 / skill 边界的四条真实杠杆），
外加 R7（A 类，真 429 端点）。编号与 `第3层-韧性测试用例.xlsx` 对齐：

    test_skill_script_missing_dependency   R1 skill 缺依赖（缺/坏依赖族）
    test_skill_network_unreachable         R2 skill 网络断开（换外部端点族·死地址）
    test_bash_missing_file_then_switch     R3 目标文件缺失（缺/坏文件族）
    test_edit_file_ambiguous_match         R4 编辑目标不唯一（制造不一致族·静态版）
    test_llm_429_retry_exhausted           R7 LLM 端点持续 429 → 重试耗尽

L2 的边界（别夸大）
-------------------
③段在 L2 仍靠 FakeLLM 脚本化：它证明的是"错误能流到下一轮、且模型有足够信息可判"，
不是"真模型真会自愈"。真自愈只有真 LLM 能测（`slow` 档）。真异常 + 可自愈 = 用例
不可重复，本文件里 FakeLLM 不会真的装回依赖，所以天然可重复；换真 LLM 时必须让整轮
跑在可回滚的 venv 里。
"""
import http.server
import threading
import time
from pathlib import Path

import httpx
import pytest

from server.config import settings
from server.engine.query_loop import EngineManager
from server.llm.client import DeepSeekLLMClient, LLMChunk
from server.models.message import MessageCreate
from server.models.session import ClientTool, Session

from .real_client import RealToolExecutor

# 真实环境里确定没装的模块名。真跑时解释器真去 sys.path 找、真抛 ModuleNotFoundError——
# 在有 smart-charts 的真流程里这个位置就是 `import pandas`。
ABSENT_DEP = "iwork_probe_absent_dep"


def _tool(name: str, props: dict) -> ClientTool:
    return ClientTool(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": props, "required": list(props)},
    )


CLIENT_TOOLS = [
    _tool("bash", {"command": {"type": "string"}}),
    _tool("read_file", {"path": {"type": "string"}}),
    _tool("write_file", {"path": {"type": "string"}, "content": {"type": "string"}}),
    _tool("edit_file", {
        "path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
    }),
    _tool("glob", {"pattern": {"type": "string"}}),
    _tool("grep", {"pattern": {"type": "string"}}),
]


async def _build(session_repo, message_repo, fake_llm, workspace):
    """建会话 + 引擎 + 真执行器。会话 workspace 指向一次性 tmp 目录。"""
    session = Session(
        user_id="test-user", mode="build", workspace=str(workspace),
        model="claude-sonnet-4-6", client_tools=CLIENT_TOOLS,
    )
    await session_repo.create(session)
    manager = EngineManager(session_repo, message_repo, fake_llm)
    engine = await manager.get_or_create(session)
    executor = RealToolExecutor(engine, workspace=workspace)
    await executor.start()
    return session, engine, executor


def _tool_history(history: list[dict]) -> list[str]:
    return [h["content"] for h in history if h.get("role") == "tool"]


async def _enqueue(engine, content):
    return await engine.enqueue("test-user", MessageCreate(
        content=content, scene_mode="code", workspace=str(engine.session.workspace),
        model="claude-sonnet-4-6", mode="build",
    ))


# ══════════════════════════════════════════════════════════════════
# 基线：真执行器的**成功**路径
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_executor_success_baseline(session_repo, message_repo, tmp_path, fake_llm):
    """真执行器跑得通才算数。

    下面几条用例断言的是"失败了"——执行器要是坏的，它也照样"失败"，全绿但什么都没测到。
    这条钉住成功路径：命令真跑通、真 stdout 回投、真进上下文、文件真落盘。
    """
    (tmp_path / "ok.py").write_text("print('hello-from-script')\n", encoding="utf-8")

    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc1",
                  tool_input={"command": "python ok.py"})],
        [LLMChunk(type="tool_use", tool_name="write_file", tool_call_id="tc2",
                  tool_input={"path": "out.txt", "content": "written"})],
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    session, engine, executor = await _build(session_repo, message_repo, fake_llm, tmp_path)
    try:
        await _enqueue(engine, "跑 ok.py 再写个文件")
        assert await executor.wait_idle(), f"未回到 IDLE，state={engine.state}"

        bash_body = executor.results[0][1]
        assert bash_body["status"] == "success", f"bash 未成功: {bash_body}"
        assert "hello-from-script" in bash_body["output"]
        assert bash_body["exit_code"] == 0

        assert executor.results[1][1]["status"] == "success"
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "written"

        tool_rows = _tool_history(await message_repo.get_history(session.id))
        assert any("hello-from-script" in c for c in tool_rows)
    finally:
        await executor.stop()


# ══════════════════════════════════════════════════════════════════
# R1 Skill 脚本失败：缺依赖（零制造成本，环境当下就不满足）
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_skill_script_missing_dependency(session_repo, message_repo, tmp_path, fake_llm):
    """跑 skill 脚本 → 真 ModuleNotFoundError → 进上下文 → 换路读脚本。

    ① 真解释器真去 import，真抛（不是我们编的字符串）
    ② stderr 原样落在 tool 历史行里，未被截断 / 摘要
    ③ 下一轮换成 read_file 去看脚本依赖什么（不是原样重跑）
    """
    script_dir = tmp_path / "skills" / "smart-charts" / "scripts"
    script_dir.mkdir(parents=True)
    script = script_dir / "render_chart.py"
    script.write_text(f"import {ABSENT_DEP}\nprint('never reached')\n", encoding="utf-8")

    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc1",
                  tool_input={"command": "python skills/smart-charts/scripts/render_chart.py"})],
        [LLMChunk(type="tool_use", tool_name="read_file", tool_call_id="tc2",
                  tool_input={"path": "skills/smart-charts/scripts/render_chart.py"})],
        [LLMChunk(type="text", delta="脚本依赖未安装，已定位。"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    session, engine, executor = await _build(session_repo, message_repo, fake_llm, tmp_path)
    try:
        await _enqueue(engine, "用 render_chart.py 出张图")
        assert await executor.wait_idle(), f"未回到 IDLE，state={engine.state}"

        # ① 真失败，且证据可判（第 1 条调用的回投）
        assert [r["tool_name"] for r in executor.requests] == ["bash", "read_file"]
        body = executor.results[0][1]
        assert body["status"] == "error"
        assert body["exit_code"] != 0
        assert f"ModuleNotFoundError: No module named '{ABSENT_DEP}'" in body["error"]

        # ② 原样进下一轮上下文（context.py 是 json.dumps 直通，无包装文本）
        tool_rows = _tool_history(await message_repo.get_history(session.id))
        assert any(ABSENT_DEP in c and "ModuleNotFoundError" in c for c in tool_rows)

        # ③ 下一轮动作与出错那次不同
        assert executor.requests[0]["tool_name"] == "bash"
        assert executor.requests[1]["tool_name"] == "read_file"
    finally:
        await executor.stop()


# ══════════════════════════════════════════════════════════════════
# R2 Skill 脚本失败：联网端点不可达（换外部端点族·死地址）
# ══════════════════════════════════════════════════════════════════

# 没人监听的端口：连它立刻 Connection refused。死地址是**被动**失败——不用起 stub、
# 不用真断网、不用改 .env 重启服务，这是它比 R5–R7 好造的地方。
DEAD_ENDPOINT = "http://127.0.0.1:9/v1/ocr"

# 跑的是**真** skill 脚本，不是替身：它自己读 PADDLEOCR_API_URL、自己发请求、自己 exit(1)。
SKILL_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "server" / "skills" / "definitions" / "paddleocr-doc-parsing"
    / "scripts" / "parse_document.py"
)


@pytest.mark.asyncio
async def test_skill_network_unreachable(session_repo, message_repo, tmp_path, fake_llm, monkeypatch):
    """跑真 skill 脚本 → 联网端点死地址 → 真连接错误 → 进上下文 → 换路。

    与 R1 的分界：故障不在依赖也不在文件——脚本在、requests 也装着，只有**端点不可达**。
    断言取证据片段（`Max retries exceeded` / `Failed to establish a new connection`），
    不锁原文：真文本含 WinError 与本地化系统提示，随机器变。
    """
    monkeypatch.setenv("PADDLEOCR_API_URL", DEAD_ENDPOINT)
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "dummy")
    (tmp_path / "scans").mkdir()
    (tmp_path / "scans" / "receipt.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc1",
                  tool_input={"command": f'python "{SKILL_SCRIPT}" scans/receipt.png'})],
        [LLMChunk(type="tool_use", tool_name="read_file", tool_call_id="tc2",
                  tool_input={"path": "scans/receipt.png"})],
        [LLMChunk(type="text", delta="OCR 端点不可达，改走本地手段。"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    session, engine, executor = await _build(session_repo, message_repo, fake_llm, tmp_path)
    try:
        await _enqueue(engine, "把 scans/receipt.png 里的文字提取出来")
        assert await executor.wait_idle(), f"未回到 IDLE，state={engine.state}"

        # ① 真连接失败，证据可判
        body = executor.results[0][1]
        assert body["status"] == "error"
        assert body["exit_code"] == 1
        assert "Max retries exceeded" in body["error"]
        assert "Failed to establish a new connection" in body["error"]

        # ② 原样进下一轮上下文，未被吞
        tool_rows = _tool_history(await message_repo.get_history(session.id))
        assert any("Max retries exceeded" in c for c in tool_rows)

        # ③ 换路：不再跑同一条 bash，改走别的工具；且不谎报 OCR 成功
        assert [r["tool_name"] for r in executor.requests] == ["bash", "read_file"]
    finally:
        await executor.stop()


# ══════════════════════════════════════════════════════════════════
# R3 内置工具失败：文件真的不存在
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bash_missing_file_then_switch(session_repo, message_repo, tmp_path, fake_llm):
    """bash 跑不存在的脚本 → 真 [Errno 2] → 进上下文 → 换只读工具取数 → 出报告。"""
    data_dir = tmp_path / "data" / "2026-09"
    data_dir.mkdir(parents=True)
    (data_dir / "sales.csv").write_text("date,amount\n2026-09-01,100\n", encoding="utf-8")
    # 刻意不建 scripts/rollup.py —— 这正是注入

    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc1",
                  tool_input={"command": "python scripts/rollup.py data/2026-09/sales.csv"})],
        [LLMChunk(type="tool_use", tool_name="read_file", tool_call_id="tc2",
                  tool_input={"path": "data/2026-09/sales.csv"})],
        [LLMChunk(type="tool_use", tool_name="write_file", tool_call_id="tc3",
                  tool_input={"path": "report.md", "content": "# 汇总\n合计 100\n"})],
        [LLMChunk(type="text", delta="已写入 report.md。"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    session, engine, executor = await _build(session_repo, message_repo, fake_llm, tmp_path)
    try:
        await _enqueue(engine, "把 sales.csv 按月汇总，写到 report.md")
        assert await executor.wait_idle(), f"未回到 IDLE，state={engine.state}"

        # ① 真报错：判据取证据片段，不锁原文（真文本含解释器全路径与盘符，随机器变）
        body = executor.results[0][1]
        assert body["status"] == "error"
        assert body["exit_code"] != 0
        assert "[Errno 2]" in body["error"]
        assert "No such file or directory" in body["error"]

        # ② 该片段原样进上下文
        tool_rows = _tool_history(await message_repo.get_history(session.id))
        assert any("[Errno 2]" in c for c in tool_rows)

        # ③ 换路：不再重跑同一条 bash，改走 read_file 取数，最后产出 report.md
        assert [r["tool_name"] for r in executor.requests] == ["bash", "read_file", "write_file"]
        assert (tmp_path / "report.md").exists()
    finally:
        await executor.stop()


# ══════════════════════════════════════════════════════════════════
# R4 编辑类失败：old_string 真的匹配到多处
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_edit_file_ambiguous_match(session_repo, message_repo, tmp_path, fake_llm):
    """edit_file 的 old_string 在文件里真出现多次 → 真失败 → 进上下文 → 收窄入参。"""
    target = tmp_path / "conf.yaml"
    target.write_text("port: 80\nport: 8080\n", encoding="utf-8")

    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="edit_file", tool_call_id="tc1",
                  tool_input={"path": "conf.yaml", "old_string": "port: 80", "new_string": "port: 9000"})],
        [LLMChunk(type="tool_use", tool_name="read_file", tool_call_id="tc2",
                  tool_input={"path": "conf.yaml"})],
        [LLMChunk(type="text", delta="匹配不唯一，先看原文。"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    session, engine, executor = await _build(session_repo, message_repo, fake_llm, tmp_path)
    try:
        await _enqueue(engine, "把 conf.yaml 的端口改成 9000")
        assert await executor.wait_idle(), f"未回到 IDLE，state={engine.state}"

        body = executor.results[0][1]
        assert body["status"] == "error"
        assert "匹配到 2 处" in body["error"]

        tool_rows = _tool_history(await message_repo.get_history(session.id))
        assert any("匹配到 2 处" in c for c in tool_rows)

        # 换路：先去读原文，且文件未被改动（失败的编辑不留半写）
        assert executor.requests[1]["tool_name"] == "read_file"
        assert target.read_text(encoding="utf-8") == "port: 80\nport: 8080\n"
    finally:
        await executor.stop()


# ══════════════════════════════════════════════════════════════════
# R7 LLM 端点持续 429：重试耗尽（A 类，确定性，可进 CI 门禁）
# ══════════════════════════════════════════════════════════════════

class _Always429(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_POST(self):  # noqa: N802
        type(self).hits += 1
        body = b'{"error":{"message":"rate limited","type":"rate_limit_error"}}'
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


@pytest.mark.asyncio
async def test_llm_429_retry_exhausted(monkeypatch):
    """真起一个只返 429 的 HTTP 服务，让 `client.py` 里那段真重试循环真跑。

    用 FakeLLM 回放 retry chunk 测不出"恰好几次"——必须让真 httpx + 真
    asyncio.sleep 跑起来。退避在 `DeepSeekLLMClient.stream` 内部（max_retries=3）：
    首次 + 重试 2 次 = 共 3 次尝试，退避 1s、2s（`2 ** attempt`，attempt=0/1），
    第 3 次失败即终止——**没有第 4 次，也没有第 3 段退避**。
    """
    _Always429.hits = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Always429)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    monkeypatch.setattr(settings, "deepseek_base_url", f"http://127.0.0.1:{port}")
    client = DeepSeekLLMClient(api_key="test-key", model="deepseek-chat")

    chunks: list[LLMChunk] = []
    started = time.monotonic()
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            async for c in client.stream([{"role": "user", "content": "hi"}], system="sys"):
                chunks.append(c)
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert exc.value.response.status_code == 429

    # 恰好 3 次尝试，服务端被打 3 次（不多不少）
    assert _Always429.hits == 3

    retries = [c for c in chunks if c.type == "retry"]
    assert [c.retry_attempt for c in retries] == [1, 2]
    assert {c.retry_code for c in retries} == {"llm_rate_limited"}

    # 退避证据：1s + 2s = 3s 下界（第 3 次失败直接抛，不再睡）
    assert elapsed >= 2.9, f"退避时长不足，elapsed={elapsed:.2f}s"
