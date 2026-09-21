"""单轮工具调度的读 / 写判定与分段（doc 14 §1–§2）。

纯函数测试，不起引擎：判定只回答"能不能并发"，判不准一律归写。
"""
from server.llm.client import LLMChunk
from server.tools.scheduling import (
    READ, WRITE, command_is_read_only, is_read_only, path_of, segment_tool_calls,
)


# ── bash 只读白名单 ──────────────────────────────────────────

READ_COMMANDS = [
    "cat a.rs",
    "head -n 20 a.rs",
    "wc -l a.rs",
    "grep -n foo a.rs",
    "rg pattern src/",
    "ls -la",
    "tree src",
    "stat a.rs",
    "pwd",
    "git status",
    "git diff HEAD",
    "git log --oneline -5",
    "git branch",
    "git branch -a",
    "git remote -v",
    "sed -n '1,5p' a.rs",
    "sort a.txt",
    "find . -name '*.py'",
    "cat ./sub/dir/x.rs",
    "CAT.EXE a.rs",
]

WRITE_COMMANDS = [
    # 重定向 / 管道 / 命令链 / 命令替换 / heredoc
    "cat a > b",
    "cat a >> b",
    "echo hi > f",
    "cat a < b",
    "cat a | grep x",
    "cd src && cargo test",
    "a; b",
    "cat $(ls)",
    "cat `ls`",
    "cat <<EOF",
    "cat a\nls",
    # 非白名单程序
    "cargo build",
    "rm -rf target",
    "mv a b",
    "tee out.txt",
    "xargs rm",
    # 白名单程序 + 写 flag
    "sed -i 's/a/b/' a.rs",
    "sed --in-place=.bak s/a/b/ a.rs",
    "sort -o out.txt a.txt",
    "find . -delete",
    "find . -exec rm {} +",
    "curl -o out.html https://x",
    "curl -d @body https://x",
    "curl -X POST https://x",
    "awk 'BEGIN{system(\"rm a\")}'",
    # git：写形式与不解析的全局 flag
    "git branch -D foo",
    "git branch new-name",
    "git remote add origin x",
    "git config user.name a",
    "git tag -d v1",
    "git -C dir status",
    "git push",
    # 空 / 空白
    "",
    "   ",
]


def test_read_only_commands_pass_whitelist():
    for cmd in READ_COMMANDS:
        assert command_is_read_only(cmd) is True, cmd


def test_non_read_only_commands_fall_back_to_write():
    for cmd in WRITE_COMMANDS:
        assert command_is_read_only(cmd) is False, cmd


# ── 工具名判定 ──────────────────────────────────────────────

def test_read_tools_are_read():
    for name in ("read_file", "glob", "grep", "skill", "recall", "memory_search"):
        assert is_read_only(name) is True, name


def test_write_and_unknown_tools_are_write():
    # 未知名字（MCP 工具、模型幻觉出的名字）一律判写：判错成写的代价只是慢
    for name in ("write_file", "edit_file", "apply_patch", "task",
                 "mcp__baidu__search", "some_unknown_tool", ""):
        assert is_read_only(name) is False, name


def test_bash_follows_command_judgement():
    assert is_read_only("bash", {"command": "cat a.rs"}) is True
    assert is_read_only("bash", {"command": "cargo build"}) is False
    assert is_read_only("bash", {}) is False
    assert is_read_only("exec_command", {"command": "ls -la"}) is True


# ── 分段 ────────────────────────────────────────────────────

def _call(name: str, **tool_input) -> LLMChunk:
    return LLMChunk(type="tool_use", tool_name=name, tool_call_id=name, tool_input=tool_input)


def test_segments_group_consecutive_same_kind():
    calls = [
        _call("read_file", path="a.rs"),
        _call("glob", pattern="**/*.py"),
        _call("write_file", path="b.rs"),
        _call("read_file", path="a.rs"),
        _call("edit_file", path="c.rs"),
        _call("edit_file", path="c.rs"),
    ]
    segments = segment_tool_calls(calls)
    assert [s.kind for s in segments] == [READ, WRITE, READ, WRITE]
    assert [len(s.calls) for s in segments] == [2, 1, 1, 2]
    # 段内保持模型发出顺序，不重排
    assert [c.tool_name for c in segments[0].calls] == ["read_file", "glob"]
    assert [c.tool_name for c in segments[3].calls] == ["edit_file", "edit_file"]


def test_segments_from_bash_commands():
    calls = [
        _call("bash", command="cat a.rs"),
        _call("bash", command="ls -la"),
        _call("bash", command="cargo build"),
        _call("bash", command="cat b.rs"),
    ]
    assert [s.kind for s in segment_tool_calls(calls)] == [READ, WRITE, READ]


def test_task_and_mcp_are_write_segments():
    calls = [_call("read_file", path="a.rs"), _call("task", agent_name="dev"),
             _call("mcp__x__search", q="a")]
    segments = segment_tool_calls(calls)
    assert [s.kind for s in segments] == [READ, WRITE]
    assert len(segments[1].calls) == 2


def test_segment_empty_input():
    assert segment_tool_calls([]) == []


# ── 写类目标的路径（预留）────────────────────────────────────

def test_path_of_only_for_file_writes():
    assert path_of("write_file", {"path": "a.rs"}) == "a.rs"
    assert path_of("edit_file", {"path": "a.rs"}) == "a.rs"
    assert path_of("write_file", {}) is None
    assert path_of("bash", {"command": "cat a"}) is None
