"""PermissionEngine 三态判定单测。

注意：本文件不依赖 tests/conftest.py 的 fixtures，可直接
`python -m pytest tests/test_permission.py` 运行。
"""
import tempfile
from pathlib import Path

from server.tools.permission import PermissionEngine, classify_tool

WORKSPACE = str(Path(tempfile.gettempdir()) / "iwork-ws-test")

WS_CONFIG = """version: 1
approval_policy: on-request
default_permissions: ":workspace"
permissions:
  read-only:
    filesystem:
      ":root": read
    network:
      enabled: false
  workspace:
    filesystem:
      ":root": read
      ":workspace_roots": write
      ":tmpdir": write
      ":deny_workspace":
        - ".git/**"
        - ".env"
    network:
      enabled: false
  full:
    disabled: true
tool_rules:
  - object: "tool:bash"
    rule: ["git", "status"]
    decision: allow
  - object: "tool:bash"
    rule: ["rm", "-rf"]
    decision: forbidden
dangerous_command_heuristics: true
file_tools:
  path_check: true
"""

NET_CONFIG = WS_CONFIG.replace(
    'default_permissions: ":workspace"',
    'default_permissions: ":dev"',
).replace(
    'tool_rules:',
    '''permissions:
  dev:
    extends: ":workspace"
    network:
      enabled: true
      domains:
        "registry.npmjs.org": allow
        "evil.example.com": deny
      unknown_domain: ask
tool_rules:''',
)


# ── 会话 shell 语法适配（powershell / posix）───────────────────────────
# 用 WS_CONFIG 的 tool_rules 段替换为：git push(prompt, 跨 shell) +
# Remove-Item -Recurse -Force(forbidden, shell: powershell) +
# Remove-Item <盘符路径>(forbidden, 不标 shell = all，验证分词器按会话走)

PS_CFG = WS_CONFIG.replace(
    "tool_rules:\n"
    '  - object: "tool:bash"\n'
    '    rule: ["git", "status"]\n'
    '    decision: allow\n'
    '  - object: "tool:bash"\n'
    '    rule: ["rm", "-rf"]\n'
    '    decision: forbidden\n',
    "tool_rules:\n"
    '  - object: "tool:bash"\n'
    '    rule: ["git", "push"]\n'
    '    decision: prompt\n'
    '  - object: "tool:bash"\n'
    '    rule: ["Remove-Item", "-Recurse", "-Force"]\n'
    '    decision: forbidden\n'
    '    shell: powershell\n'
    '  - object: "tool:bash"\n'
    '    rule: ["Remove-Item", "C:\\\\x\\\\y"]\n'
    '    decision: forbidden\n',
)


def _engine(config_text: str, name: str) -> PermissionEngine:
    p = Path(tempfile.gettempdir()) / name
    p.write_text(config_text, encoding="utf-8")
    return PermissionEngine(config_path=p)


def test_classify_tool():
    assert classify_tool("write_file") == "file"
    assert classify_tool("edit_file") == "file"
    assert classify_tool("bash") == "command"
    assert classify_tool("exec_command") == "command"
    assert classify_tool("web_fetch") == "network"
    assert classify_tool("memory_search") == "other"


def test_file_write_inside_workspace_skip():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    v = e.evaluate("write_file", {"path": WORKSPACE + "/a.txt"}, {"workspace": WORKSPACE})
    assert v.verdict == "skip"


def test_file_write_outside_workspace_needs_approval():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    v = e.evaluate("write_file", {"path": "/outside/a.txt"}, {"workspace": WORKSPACE})
    assert v.verdict == "needs_approval"


def test_file_write_deny_env_forbidden():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("write_file", {"path": WORKSPACE + "/.env"}, {"workspace": WORKSPACE}).verdict == "forbidden"


def test_file_write_deny_git_forbidden():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("write_file", {"path": WORKSPACE + "/.git/config"}, {"workspace": WORKSPACE}).verdict == "forbidden"


# ── Windows 盘符路径（服务端跑在 Linux 时反斜杠是普通字符，需归一化后判定）──────

WIN_WS = "G:\\论文2"
WIN_PATH = "G:\\论文2\\parse_baidu.ps1"


def test_file_write_windows_path_inside_workspace_skip():
    e = _engine(WS_CONFIG, "perm-win.yaml")
    assert e.evaluate("write_file", {"path": WIN_PATH}, {"workspace": WIN_WS}).verdict == "skip"


def test_file_write_windows_path_outside_workspace_needs_approval():
    e = _engine(WS_CONFIG, "perm-win.yaml")
    assert e.evaluate("write_file", {"path": "G:\\其他\\a.txt"}, {"workspace": WIN_WS}).verdict == "needs_approval"


def test_file_write_windows_path_deny_env_forbidden():
    e = _engine(WS_CONFIG, "perm-win.yaml")
    assert e.evaluate("write_file", {"path": WIN_WS + "\\.env"}, {"workspace": WIN_WS}).verdict == "forbidden"


def test_file_write_windows_path_deny_git_forbidden():
    e = _engine(WS_CONFIG, "perm-win.yaml")
    assert e.evaluate("write_file", {"path": WIN_WS + "\\.git\\config"}, {"workspace": WIN_WS}).verdict == "forbidden"


def test_file_read_deny_forbidden():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("read_file", {"path": WORKSPACE + "/.env"}, {"workspace": WORKSPACE}).verdict == "forbidden"


def test_command_rule_allow_skip():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("bash", {"command": "git status"}, {}).verdict == "skip"


def test_command_rule_forbidden():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("bash", {"command": "rm -rf /"}, {}).verdict == "forbidden"
    assert e.evaluate("bash", {"command": "rm -rf build"}, {}).verdict == "forbidden"


def test_command_dangerous_heuristic_needs_approval():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("bash", {"command": "mkfs.ext4 /dev/sda"}, {}).verdict == "needs_approval"


def test_command_dangerous_patterns_replace_defaults():
    """dangerous_patterns 替换语义：配置了列表后内置默认不再生效，仅自定义列表命中。"""
    cfg = WS_CONFIG.replace(
        "dangerous_command_heuristics: true",
        'dangerous_command_heuristics: true\n'
        'dangerous_patterns:\n'
        '  - "custom-danger"',
    )
    e = _engine(cfg, "perm-patterns.yaml")
    # 内置默认 "mkfs." 已被替换，不再命中
    assert e.evaluate("bash", {"command": "mkfs.ext4 /dev/sda"}, {}).verdict == "skip"
    # 自定义列表命中
    assert e.evaluate("bash", {"command": "echo custom-danger"}, {}).verdict == "needs_approval"


def test_command_plain_skip():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("bash", {"command": "ls -la"}, {}).verdict == "skip"


# ── 会话 shell 语法适配（Windows=PowerShell / Linux/mac=posix）────────────

def test_command_powershell_forbid_rule_hits():
    """PowerShell 会话：shell: powershell 的递归强删 forbid 规则命中。"""
    e = _engine(PS_CFG, "perm-ps.yaml")
    v = e.evaluate(
        "bash",
        {"command": "Remove-Item -Recurse -Force C:\\x -ErrorAction Stop"},
        {"shell_env": "powershell"},
    )
    assert v.verdict == "forbidden"
    assert v.matched_rule and "Remove-Item" in v.matched_rule


def test_command_powershell_rule_skipped_on_posix_shell():
    """shell: powershell 规则在 bash/空 ctx 会话被跳过（语法隔离），命令默认放行。"""
    e = _engine(PS_CFG, "perm-ps.yaml")
    cmd = "Remove-Item -Recurse -Force C:\\x"
    assert e.evaluate("bash", {"command": cmd}, {"shell_env": "bash"}).verdict == "skip"
    assert e.evaluate("bash", {"command": cmd}, {}).verdict == "skip"


def test_command_case_insensitive_on_powershell_only():
    """PowerShell 命令/参数大小写不敏感：GIT Push 命中 git push(prompt)；
    bash/空 ctx 会话大小写敏感 → 不命中 → 默认放行。"""
    e = _engine(PS_CFG, "perm-ps.yaml")
    cmd = "GIT Push origin main"
    assert e.evaluate("bash", {"command": cmd}, {"shell_env": "powershell"}).verdict == "needs_approval"
    assert e.evaluate("bash", {"command": cmd}, {"shell_env": "bash"}).verdict == "skip"
    assert e.evaluate("bash", {"command": cmd}, {}).verdict == "skip"


def test_command_backslash_path_preserved_on_powershell():
    """PowerShell 走 Windows 分词，C:\\x\\y 反斜杠保留 → 路径 forbid 规则命中；
    bash 走 POSIX 分词吞反斜杠 → 规则不命中。规则不标 shell（all）以隔离分词差异。"""
    e = _engine(PS_CFG, "perm-ps.yaml")
    cmd = "Remove-Item C:\\x\\y -Force"
    assert e.evaluate("bash", {"command": cmd}, {"shell_env": "powershell"}).verdict == "forbidden"
    assert e.evaluate("bash", {"command": cmd}, {"shell_env": "bash"}).verdict == "skip"


def test_dangerous_defaults_per_shell():
    """未配置 dangerous_patterns：PowerShell 会话并集 PS 盘符破坏性子串（含大小写不敏感），bash 不含。"""
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("bash", {"command": "Clear-Disk -Number 0 -RemoveData"}, {"shell_env": "powershell"}).verdict == "needs_approval"
    # 小写 clear-disk 同样命中（PS 大小写不敏感）
    assert e.evaluate("bash", {"command": "clear-disk -Number 0"}, {"shell_env": "powershell"}).verdict == "needs_approval"
    assert e.evaluate("bash", {"command": "Clear-Disk -Number 0"}, {"shell_env": "bash"}).verdict == "skip"
    assert e.evaluate("bash", {"command": "Clear-Disk -Number 0"}, {}).verdict == "skip"


def test_network_tools_server_always_skip():
    """服务端不做域名静态判定：域名 allow/deny 由客户端代理在 CONNECT 时判定（§4.4），服务端一律放行。"""
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    assert e.evaluate("web_fetch", {"url": "https://evil.example.com/x"}, {}).verdict == "skip"
    assert e.evaluate("web_fetch", {"url": "https://registry.npmjs.org/pkg"}, {}).verdict == "skip"
    assert e.evaluate("web_search", {"query": "hello"}, {}).verdict == "skip"


def test_builtin_workspace_network_from_yaml():
    """内置 workspace 的 network 段按配置文件生效（不要代码写死）。"""
    cfg = WS_CONFIG.replace(
        '    network:\n      enabled: false\n  full:',
        '    network:\n      enabled: true\n      unknown_domain: ask\n  full:',
    )
    e = _engine(cfg, "perm-builtin-net.yaml")
    assert e._profiles[":workspace"].network.enabled is True
    assert e.build_policy({"workspace": WORKSPACE})["network"]["enabled"] is True


def test_network_policy_compiled_in_profile():
    """服务端仍解析 network 配置（阶段1 下发给客户端代理消费），但不据此做域名静态判定。"""
    e = _engine(NET_CONFIG, "perm-net.yaml")
    prof = e._profiles.get(":dev")
    assert prof is not None
    assert prof.network.enabled is True
    assert prof.network.domains.get("evil.example.com") == "deny"
    assert prof.network.unknown_domain == "ask"


def test_full_profile_skip_everything():
    cfg = WS_CONFIG.replace('default_permissions: ":workspace"', 'default_permissions: ":full"')
    e = _engine(cfg, "perm-full.yaml")
    assert e.evaluate("write_file", {"path": "/any/where"}, {}).verdict == "skip"
    assert e.evaluate("bash", {"command": "rm -rf /"}, {}).verdict == "skip"


def test_approval_never_forbids_prompt():
    cfg = WS_CONFIG.replace("approval_policy: on-request", "approval_policy: never")
    e = _engine(cfg, "perm-never.yaml")
    assert e.evaluate("write_file", {"path": "/outside/a.txt"}, {"workspace": WORKSPACE}).verdict == "forbidden"
    assert e.evaluate("bash", {"command": "mkfs.ext4 /dev/sda"}, {}).verdict == "forbidden"


# ── 阶段1：build_policy 策略包 ────────────────────────────────────────

def test_build_policy_workspace():
    e = _engine(WS_CONFIG, "perm-ws.yaml")
    pol = e.build_policy({"workspace": WORKSPACE})
    fs = pol["filesystem"]
    assert fs["profile"] == ":workspace"
    assert WORKSPACE in fs["allow_write"]
    assert any(r.endswith(".git/**") and r.startswith(WORKSPACE) for r in fs["deny_write"])
    assert any(r.endswith(".env") and r.startswith(WORKSPACE) for r in fs["deny_read"])
    assert pol["network"]["enabled"] is False
    assert pol["sandbox"]["required"] is True


def test_build_policy_tmpdir_is_marker_not_server_path():
    e = _engine(WS_CONFIG, "perm-tmpdir.yaml")
    allow = e.build_policy({"workspace": WORKSPACE})["filesystem"]["allow_write"]
    assert "@TMPDIR@" in allow
    assert tempfile.gettempdir() not in allow
    assert WORKSPACE in allow


DUP_CONFIG = WS_CONFIG.replace(
    "  full:\n    disabled: true\n",
    '  full:\n    disabled: true\n'
    '  dup:\n'
    '    extends: ":workspace"\n'
    '    filesystem:\n'
    '      ":workspace_roots": write\n'
    '      ":tmpdir": write\n',
).replace('default_permissions: ":workspace"', 'default_permissions: ":dup"')


def test_build_policy_allow_write_deduped():
    """画像既 extends 内置又显式重写同一条目时，allow_write 不出现重复路径。"""
    e = _engine(DUP_CONFIG, "perm-dup.yaml")
    allow = e.build_policy({"workspace": WORKSPACE})["filesystem"]["allow_write"]
    assert allow.count("@TMPDIR@") == 1
    assert allow.count(WORKSPACE) == 1


def test_build_policy_full_profile():
    cfg = WS_CONFIG.replace('default_permissions: ":workspace"', 'default_permissions: ":full"')
    e = _engine(cfg, "perm-full.yaml")
    pol = e.build_policy({"workspace": WORKSPACE})
    assert pol["sandbox"]["required"] is False


EXEMPT_CONFIG = WS_CONFIG + '\nsandbox:\n  exempt_commands: ["bsk", "BSK.EXE"]\n'


def test_build_policy_sandbox_exempt_commands():
    """sandbox.exempt_commands 原样（小写归一）下发，供客户端识别裸机执行。"""
    e = _engine(EXEMPT_CONFIG, "perm-exempt.yaml")
    pol = e.build_policy({"workspace": WORKSPACE})
    assert pol["sandbox"]["required"] is True
    assert pol["sandbox"]["exempt_commands"] == ["bsk", "bsk.exe"]


def test_build_policy_sandbox_exempt_default_empty():
    """未配置 sandbox 段时豁免名单为空。"""
    e = _engine(WS_CONFIG, "perm-noexempt.yaml")
    assert e.build_policy({"workspace": WORKSPACE})["sandbox"]["exempt_commands"] == []


def test_build_policy_network_domains():
    e = _engine(NET_CONFIG, "perm-net.yaml")
    pol = e.build_policy({"workspace": WORKSPACE})
    net = pol["network"]
    assert net["enabled"] is True
    assert "registry.npmjs.org" in net["allow_domains"]
    assert "evil.example.com" in net["deny_domains"]
    assert net["unknown_domain"] == "ask"


NW_RULES_CONFIG = WS_CONFIG.replace(
    "tool_rules:",
    '''network_rules:
  - host: "registry.npmjs.org"
    protocol: "https"
    decision: allow
  - host: "git.example.com"
    protocol: "https"
    decision: prompt
  - host: "bad.example.com"
    protocol: "http"
    decision: invalid          # 非法 decision，应被过滤
  - protocol: "https"          # 缺 host，应被过滤
    decision: allow
tool_rules:''',
)


def test_build_policy_network_rules():
    """阶段2：顶层 network_rules 编译进 policy 包；非法条目（缺 host / decision 非法）被过滤。"""
    e = _engine(NW_RULES_CONFIG, "perm-nwrules.yaml")
    rules = e.build_policy({"workspace": WORKSPACE})["network"]["network_rules"]
    assert {"host": "registry.npmjs.org", "protocol": "https", "decision": "allow"} in rules
    assert {"host": "git.example.com", "protocol": "https", "decision": "prompt"} in rules
    assert not any(r["host"] == "bad.example.com" for r in rules)
    assert len(rules) == 2


def test_build_policy_network_approval_policy():
    """阶段1.5：network.approval_policy 下发全局审批开关语义，客户端未命中白名单时按此 + unknown_domain 决定 Ask。"""
    # on-request（默认）：非 never，未命中再由 unknown_domain 细化
    e = _engine(WS_CONFIG, "perm-ap.yaml")
    pol = e.build_policy({"workspace": WORKSPACE})["network"]
    assert pol["approval_policy"] == "on-request"
    assert pol["unknown_domain"] == "ask"

    # never：全局总闸，未命中一律拒
    e = _engine(WS_CONFIG.replace("approval_policy: on-request", "approval_policy: never"), "perm-ap-never.yaml")
    assert e.build_policy({"workspace": WORKSPACE})["network"]["approval_policy"] == "never"

    # granular 且 network:false：网络审批关闭，未命中拒
    cfg = WS_CONFIG.replace(
        "approval_policy: on-request",
        "approval_policy:\n  granular:\n    network: false",
    )
    e = _engine(cfg, "perm-ap-gran.yaml")
    assert e.build_policy({"workspace": WORKSPACE})["network"]["approval_policy"].get("network") is False

    # granular 且 network:true（或不写，默认 true）：网络审批开启
    cfg = WS_CONFIG.replace(
        "approval_policy: on-request",
        "approval_policy:\n  granular:\n    network: true",
    )
    e = _engine(cfg, "perm-ap-gran2.yaml")
    assert e.build_policy({"workspace": WORKSPACE})["network"]["approval_policy"].get("network") is True
