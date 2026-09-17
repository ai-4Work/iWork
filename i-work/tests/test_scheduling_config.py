"""调度名单来自 server/scheduling.yaml，不是代码里的硬编码副本（doc 14 §1.2③）。

判定行为的正确性由 test_tool_scheduling.py 卡住（它跑的是随仓库发布的那份 YAML）；
这里只证明四件事：
  1. 配置一改，判定就跟着变（且默认名单不会偷偷生效）；
  2. 单个键缺失 → 保守退化为"全判写"，不报错；
  3. 文件不存在 → 报错（引擎启动即失败，不静默降级）；
  4. 护栏字符（> | $( 等）留在代码里，改配置也绕不过去。
"""
import tempfile
from pathlib import Path

import pytest

from server.llm.client import LLMChunk
from server.tools.scheduling import READ, WRITE, SchedulingPolicy

# 一份"故意和默认名单完全不重叠"的配置：默认名单里的 cat / read_file / rg 都失效
CUSTOM_CONFIG = """
read_tools: [peek]
command_tools: [shell]
read_only_commands: [foo]
write_flags:
  foo: ["-x"]
read_only_git_subcommands: [status]
git_query_flags: ["-v"]
write_file_tools: [save_file]
"""

_serial = 0


def _policy(config_text: str) -> SchedulingPolicy:
    """把内联 YAML 落到临时文件后建策略（照 test_permission.py 的写法）。"""
    global _serial
    _serial += 1
    path = Path(tempfile.gettempdir()) / f"iwork_scheduling_{_serial}.yaml"
    path.write_text(config_text, encoding="utf-8")
    return SchedulingPolicy(config_path=path)


def _call(name: str, **tool_input) -> LLMChunk:
    return LLMChunk(type="tool_use", tool_name=name, tool_call_id=name, tool_input=tool_input)


# ── 1. 配置真的驱动判定 ────────────────────────────────────────

def test_custom_command_whitelist_replaces_default():
    policy = _policy(CUSTOM_CONFIG)
    assert policy.command_is_read_only("foo bar") is True
    # 默认名单里的命令此刻不算数（证明没在读代码里的副本）
    assert policy.command_is_read_only("rg pattern") is False
    assert policy.command_is_read_only("cat a.rs") is False
    assert policy.command_is_read_only("ls -la") is False


def test_custom_write_flags_apply():
    policy = _policy(CUSTOM_CONFIG)
    assert policy.command_is_read_only("foo bar") is True
    assert policy.command_is_read_only("foo -x bar") is False
    # 前缀匹配：-xabc 也算命中
    assert policy.command_is_read_only("foo -xabc bar") is False


def test_custom_tool_names_replace_default():
    policy = _policy(CUSTOM_CONFIG)
    assert policy.is_read_only("peek") is True
    assert policy.is_read_only("read_file") is False
    # command_tools 也换了：bash 不再是命令类，直接判写
    assert policy.is_read_only("shell", {"command": "foo bar"}) is True
    assert policy.is_read_only("bash", {"command": "foo bar"}) is False


def test_custom_write_file_tools_drive_path_of():
    policy = _policy(CUSTOM_CONFIG)
    assert policy.path_of("save_file", {"path": "a.rs"}) == "a.rs"
    assert policy.path_of("write_file", {"path": "a.rs"}) is None


def test_segments_use_policy():
    policy = _policy(CUSTOM_CONFIG)
    calls = [_call("peek"), _call("save_file", path="a.rs"), _call("peek"), _call("peek")]
    assert [s.kind for s in policy.segments(calls)] == [READ, WRITE, READ]
    assert [len(s.calls) for s in policy.segments(calls)] == [1, 1, 2]


def test_git_and_guard_chars_still_apply():
    policy = _policy(CUSTOM_CONFIG)
    assert policy.command_is_read_only("git status") is True
    assert policy.command_is_read_only("git push") is False
    # 护栏是机制（留代码里），改配置绕不过去
    assert policy.command_is_read_only("foo a > b") is False
    assert policy.command_is_read_only("foo a | bar") is False
    assert policy.command_is_read_only("foo $(bar)") is False


# ── 2. 缺键 → 保守（全判写），不报错 ───────────────────────────

def test_missing_keys_degrade_to_serial():
    policy = _policy("read_tools: [read_file]\n")
    assert policy.is_read_only("read_file") is True
    # 命令名单整段缺失 → 没有命令算只读
    assert policy.command_is_read_only("cat a.rs") is False
    assert policy.is_read_only("bash", {"command": "cat a.rs"}) is False
    # command_tools 缺失 → bash 不走命令判定，也不是 read_tools → 判写
    assert policy.is_read_only("bash") is False


def test_empty_config_is_conservative():
    policy = _policy("")
    assert policy.is_read_only("read_file") is False
    assert policy.command_is_read_only("cat a.rs") is False
    assert policy.segments([_call("read_file", path="a.rs"), _call("glob")])[0].kind == WRITE


def test_non_mapping_config_is_conservative():
    policy = _policy("just a string\n")
    assert policy.is_read_only("read_file") is False
    assert policy.command_is_read_only("cat a.rs") is False


# ── 3. 文件不存在 → 报错 ───────────────────────────────────────

def test_missing_config_file_raises():
    missing = Path(tempfile.gettempdir()) / "iwork_scheduling_does_not_exist.yaml"
    missing.unlink(missing_ok=True)
    with pytest.raises(FileNotFoundError, match="调度配置不存在"):
        SchedulingPolicy(config_path=missing)


def test_default_config_path_points_at_server_yaml():
    from server.tools.scheduling import DEFAULT_CONFIG_PATH
    assert DEFAULT_CONFIG_PATH.name == "scheduling.yaml"
    assert DEFAULT_CONFIG_PATH.is_file()
