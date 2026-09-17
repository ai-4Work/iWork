"""iWork 工具权限三态判定引擎。

参考 OpenAI Codex 的权限设计（详见 docs/chapters/权限控制-codex.md），按工具分类判定：

- 文件类工具（write/edit/read/glob/grep）逐路径比对 filesystem 策略
- 命令类工具（bash/exec_command）只查命令规则 + 危险命令启发式，不解析路径
- 网络类工具（web_fetch/web_search）不做域名静态判定：域名 allow/deny 由客户端代理在 CONNECT 时判定，服务端一律放行
- 其它工具走默认兜底

判定产出三态 Verdict：skip（直接执行）/ needs_approval（要确认）/ forbidden（硬停）。
配置来源 server/permissions.yaml。
"""
from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, Literal

import yaml

VerdictValue = Literal["skip", "needs_approval", "forbidden"]
Decision = Literal["allow", "prompt", "forbidden"]

# 可写根 / deny 规则中的特殊标记，运行时按会话上下文解析
WORKSPACE_MARK = "@WORKSPACE@"
TMPDIR_MARK = "@TMPDIR@"

BUILTIN_READ_ONLY = ":read-only"
BUILTIN_WORKSPACE = ":workspace"
BUILTIN_FULL = ":full"

# 工具分类（客户端工具，由 client.tool_request 执行）
FILE_WRITE_TOOLS = {"write_file", "edit_file", "apply_patch"}
FILE_READ_TOOLS = {"read_file", "glob", "grep"}
COMMAND_TOOLS = {"bash", "exec_command"}
NETWORK_TOOLS = {"web_fetch", "web_search"}

# 危险命令启发式（对标 hooks/deny-rm.sh 的 BLOCKED 防御面）
DANGEROUS_PATTERNS = (
    "rm -rf /", "rm -fr /", "rm -rf *",
    "mkfs.", "dd if=", "> /dev/sda",
    ":(){ :|:& };:", "chmod 777 /", "chmod 777 .",
)
# PowerShell 默认危险子串：Windows 客户端实际产出 PowerShell 语法（沙箱与 MSYS/bash 不兼容，见
# agent-client sandbox.ts），故按会话 shell 自动补充。仅收无歧义的盘符级清/删/格式化，
# 避免误伤常规递归清理（Remove-Item -Recurse -Force .\build 之类交给 operator tool_rules 精确 forbid）。
DANGEROUS_PATTERNS_POWERSHELL = (
    "Clear-Disk",
    "Remove-Partition",
    "Format-Volume",
    "format c:",
    "format C:",
)

_glob_re = re.compile(r"[*?\[\]]")


class PermissionDenied(Exception):
    """工具调用被权限系统拒绝（保留向后兼容）。"""

    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"工具 '{tool_name}' 权限被拒绝: {reason}")


@dataclass
class Verdict:
    verdict: VerdictValue
    reason: str | None = None
    matched_rule: str | None = None


@dataclass
class FileSystemPolicy:
    writable_roots: list[str] = field(default_factory=list)   # 含 @WORKSPACE@ / @TMPDIR@ / 绝对路径
    denied_abs: list[str] = field(default_factory=list)       # 绝对 / 家目录 / glob deny
    denied_ws: list[str] = field(default_factory=list)        # 相对 workspace 的 deny 规则（可含 glob）


@dataclass
class NetworkPolicy:
    enabled: bool = False
    domains: dict[str, str] = field(default_factory=dict)     # host 模式 -> allow | deny
    unknown_domain: str = "ask"                               # ask | deny


@dataclass
class Profile:
    name: str
    disabled: bool = False
    filesystem: FileSystemPolicy = field(default_factory=FileSystemPolicy)
    network: NetworkPolicy = field(default_factory=NetworkPolicy)


def classify_tool(tool_name: str) -> str:
    """返回工具类别：file | command | network | other。"""
    if tool_name in COMMAND_TOOLS:
        return "command"
    if tool_name in NETWORK_TOOLS:
        return "network"
    if tool_name in FILE_WRITE_TOOLS or tool_name in FILE_READ_TOOLS:
        return "file"
    return "other"


def _has_glob(text: str) -> bool:
    return bool(_glob_re.search(text))


_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _is_win_drive(p: str) -> bool:
    """带盘符的 Windows 绝对路径（G:\\... 或 G:/...）。"""
    return bool(_WIN_DRIVE_RE.match(p))


def _norm_slashes(p) -> str:
    """把反斜杠统一成斜杠：服务端可能在 Linux 上评估 Windows 客户端路径。"""
    return str(p).replace("\\", "/")


def _within(target: str, root: str) -> bool:
    """target 是否等于或在 root 目录内（已归一化斜杠的字符串级判断，带路径边界）。"""
    if not root:
        return False
    root = root.rstrip("/")
    if root == "/":
        return target.startswith("/")
    return target == root or target.startswith(root + "/")


def _relative_to(target: str, root: str) -> str | None:
    """返回 target 相对 root 的路径串；不在 root 下返回 None。"""
    if not root:
        return None
    root = root.rstrip("/")
    if root == "/":
        return target.lstrip("/") if target.startswith("/") else None
    if target == root:
        return ""
    if target.startswith(root + "/"):
        return target[len(root) + 1:]
    return None


def _path_match(target: PurePath, rule: str) -> bool:
    """判断 target 是否命中 rule。rule 可含 glob（*.? ** []）；无 glob 时为目录包含语义。

    Windows 盘符路径在 POSIX 下是单个组件、is_relative_to 恒失败，统一归一化为斜杠后
    按 Windows 路径形态比对；纯 POSIX 路径走原逻辑不变。
    """
    target_str = str(target)
    if _is_win_drive(target_str) or _is_win_drive(rule):
        target = PureWindowsPath(_norm_slashes(target_str))
        if not _has_glob(rule):
            rule_path = PureWindowsPath(_norm_slashes(rule))
            return target == rule_path or target.is_relative_to(rule_path)
        return target.match(_norm_slashes(rule))
    if not _has_glob(rule):
        rule_path = PurePath(rule)
        return target == rule_path or target.is_relative_to(rule_path)
    return target.match(rule)


def _is_denied(path: Path, policy: FileSystemPolicy, workspace: str | None) -> bool:
    for rule in policy.denied_abs:
        if _path_match(path, rule):
            return True
    if workspace and policy.denied_ws:
        rel = _relative_to(_norm_slashes(path), _norm_slashes(workspace))
        if rel is not None:
            for rule in policy.denied_ws:
                if _path_match(PurePath(rel), rule):
                    return True
    return False


class PermissionEngine:
    """三态判定引擎：加载 permissions.yaml，按工具分类判 skip/needs_approval/forbidden。"""

    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path) if config_path else Path(__file__).parent.parent / "permissions.yaml"
        self._config = self._load()
        self._approval_policy = self._parse_approval_policy(self._config.get("approval_policy", "on-request"))
        self._default_profile = str(self._config.get("default_permissions", ":workspace"))
        self._tool_rules = self._config.get("tool_rules", []) or []
        self._network_rules = self._parse_network_rules(self._config.get("network_rules") or [])
        self._danger_heuristics = bool(self._config.get("dangerous_command_heuristics", True))
        # yaml 写了 dangerous_patterns → 全量替换内置默认（对所有 shell）；未写 → None，
        # 命中时按会话 shell 现算默认集（POSIX / +PowerShell，见 _dangerous_patterns_for）
        self._dangerous_patterns_cfg = self._config.get("dangerous_patterns")
        self._file_path_check = bool((self._config.get("file_tools", {}) or {}).get("path_check", True))
        # 沙箱豁免名单：命中首个 token 的可执行文件走裸机执行路径（客户端消费，见 fileOps.ts）
        self._sandbox_exempt = [
            str(x).strip().lower()
            for x in ((self._config.get("sandbox", {}) or {}).get("exempt_commands") or [])
            if str(x).strip()
        ]
        self._profiles = self._compile_profiles(self._config.get("permissions", {}) or {})

    # ── 配置加载 / 编译 ─────────────────────────────────────────────

    def _load(self) -> dict:
        with open(self._config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _dangerous_patterns_for(self, shell_env: str) -> tuple[str, ...]:
        """按会话 shell 解析有效危险子串集。

        - yaml 配置了 dangerous_patterns：全量替换内置默认，对所有 shell 生效（替换语义，可加可删）。
        - 未配置：POSIX 内置默认；PowerShell 会话额外并集 PS 盘符级破坏性子串。
        """
        if self._dangerous_patterns_cfg is not None:
            return tuple(str(p) for p in self._dangerous_patterns_cfg if p)
        if shell_env == "powershell":
            return DANGEROUS_PATTERNS + DANGEROUS_PATTERNS_POWERSHELL
        return DANGEROUS_PATTERNS

    @staticmethod
    def _parse_approval_policy(raw: Any) -> Any:
        if isinstance(raw, dict):
            return raw.get("granular", {})
        return str(raw) if raw else "on-request"

    def _compile_profiles(self, raw: dict) -> dict[str, Profile]:
        profiles: dict[str, Profile] = {
            BUILTIN_READ_ONLY: Profile(
                name=BUILTIN_READ_ONLY,
                filesystem=FileSystemPolicy(writable_roots=[TMPDIR_MARK]),
            ),
            BUILTIN_WORKSPACE: Profile(
                name=BUILTIN_WORKSPACE,
                filesystem=FileSystemPolicy(
                    writable_roots=[TMPDIR_MARK, WORKSPACE_MARK],
                    denied_ws=[".git/**", ".env"],
                ),
            ),
            BUILTIN_FULL: Profile(name=BUILTIN_FULL, disabled=True),
        }
        for name, cfg in (raw or {}).items():
            key = name if name.startswith(":") else ":" + name
            if not isinstance(cfg, dict):
                continue
            if key == BUILTIN_FULL:
                continue  # full 内置不可覆盖，保持 disabled
            if key in (BUILTIN_READ_ONLY, BUILTIN_WORKSPACE):
                # 内置画像：filesystem 保持内置，network 段按配置文件生效（不配置时回落默认 enabled=false）
                net = cfg.get("network", {}) or {}
                profiles[key].network = self._build_network(net)
                continue
            profiles[key] = self._compile_custom_profile(name, cfg, profiles)
        return profiles

    def _compile_custom_profile(self, name: str, cfg: dict, profiles: dict[str, Profile]) -> Profile:
        base = FileSystemPolicy()
        if cfg.get("extends"):
            parent = profiles.get(str(cfg["extends"]))
            if parent:
                base = FileSystemPolicy(
                    writable_roots=list(parent.filesystem.writable_roots),
                    denied_abs=list(parent.filesystem.denied_abs),
                    denied_ws=list(parent.filesystem.denied_ws),
                )
        fs = cfg.get("filesystem", {}) or {}
        fs_policy = self._build_filesystem(fs, base)
        net_policy = self._build_network(cfg.get("network", {}) or {})
        return Profile(name=":" + name, filesystem=fs_policy, network=net_policy)

    def _build_filesystem(self, fs: dict, base: FileSystemPolicy) -> FileSystemPolicy:
        writable = list(base.writable_roots)
        denied_abs = list(base.denied_abs)
        denied_ws = list(base.denied_ws)
        for key, value in fs.items():
            if key == ":root":
                continue  # 全盘只读是默认打底，无需存储
            elif key in (":workspace_roots", ":tmpdir"):
                if value == "write":
                    writable.append(WORKSPACE_MARK if key == ":workspace_roots" else TMPDIR_MARK)
            elif key == ":deny_workspace":
                if isinstance(value, list):
                    denied_ws.extend(str(v) for v in value)
            elif key.startswith(":"):
                continue
            else:
                # 绝对 / 家目录路径条目
                if value == "deny":
                    denied_abs.append(self._expand_path(key))
                elif value == "write":
                    writable.append(self._expand_path(key))
        return FileSystemPolicy(writable_roots=writable, denied_abs=denied_abs, denied_ws=denied_ws)

    @staticmethod
    def _expand_path(p: str) -> str:
        if p.startswith("~"):
            return str(Path.home() / p[1:].lstrip("/\\"))
        return p

    def _build_network(self, net: dict) -> NetworkPolicy:
        domains: dict[str, str] = {}
        raw_domains = net.get("domains", {}) or {}
        for pat, decision in raw_domains.items():
            if decision in ("allow", "deny"):
                domains[str(pat)] = str(decision)
        return NetworkPolicy(
            enabled=bool(net.get("enabled", False)),
            domains=domains,
            unknown_domain=str(net.get("unknown_domain", "ask")),
        )

    @staticmethod
    def _parse_network_rules(raw: Any) -> list[dict]:
        """编译顶层 network_rules（host+protocol 精确规则，维度3），供客户端代理消费。"""
        rules: list[dict] = []
        for r in raw or []:
            if not isinstance(r, dict):
                continue
            host = r.get("host")
            protocol = r.get("protocol")
            decision = r.get("decision")
            if host and protocol and decision in ("allow", "prompt", "forbidden"):
                rules.append({"host": str(host), "protocol": str(protocol), "decision": decision})
        return rules

    # ── 判定入口 ───────────────────────────────────────────────────

    def evaluate(self, tool_name: str, tool_input: dict, ctx: dict | None = None) -> Verdict:
        """三态判定。ctx 可含 workspace（会话工作区绝对路径）。"""
        ctx = ctx or {}
        workspace = str(ctx.get("workspace") or "") or None
        shell_env = str(ctx.get("shell_env") or "")
        profile = self._profiles.get(self._default_profile, self._profiles[BUILTIN_READ_ONLY])

        if profile.disabled:
            return Verdict("skip")

        kind = classify_tool(tool_name)
        if kind == "file":
            decision, reason, matched = self._evaluate_file(tool_name, tool_input, profile, workspace)
        elif kind == "command":
            decision, reason, matched = self._evaluate_command(tool_input, profile, shell_env)
        else:
            # 网络类不做域名静态判定：域名 allow/deny 由客户端代理在 CONNECT 时判定（§4.4），服务端一律放行
            decision, reason, matched = self._evaluate_other(profile)
        return self._translate(decision, reason, matched, kind)

    def build_policy(self, ctx: dict | None = None) -> dict:
        """构建策略包（阶段1：随 client.tool_request 下发，供客户端执行/预检）。

        与 evaluate() 用同一画像选择逻辑，保证判定与下发一致。
        """
        ctx = ctx or {}
        workspace = str(ctx.get("workspace") or "") or None
        profile = self._profiles.get(self._default_profile, self._profiles[BUILTIN_READ_ONLY])
        if profile.disabled:  # :full，关掉整套权限体系，不建沙箱
            return {
                "filesystem": {"profile": profile.name},
                "network": {"enabled": False},
                "sandbox": {"required": False, "exempt_commands": []},
            }
        fs = profile.filesystem
        deny = self._resolve_deny(fs, workspace)
        net = profile.network
        return {
            "filesystem": {
                "profile": profile.name,
                "allow_write": self._resolve_roots_for_emit(fs.writable_roots, workspace),
                # 引擎当前 deny 同时拦读与写，两个列表同源
                "deny_write": deny,
                "deny_read": deny,
            },
            "network": {
                "enabled": net.enabled,
                "allow_domains": [d for d, dec in net.domains.items() if dec == "allow"],
                "deny_domains": [d for d, dec in net.domains.items() if dec == "deny"],
                "unknown_domain": net.unknown_domain,
                # 全局审批开关：客户端在未命中白名单时按此总闸 + unknown_domain 决定 Ask 去向
                # （never → 拒；granular 且 network:false → 拒；否则再按 unknown_domain 细化）
                "approval_policy": self._approval_policy,
                "network_rules": self._network_rules,
            },
            "sandbox": {"required": True, "exempt_commands": self._sandbox_exempt},
        }

    @staticmethod
    def _resolve_deny(policy: FileSystemPolicy, workspace: str | None) -> list[str]:
        denied: list[str] = []
        for rule in policy.denied_abs:
            if rule not in denied:
                denied.append(rule)
        for rule in policy.denied_ws:
            anchored = os.path.join(workspace, rule) if workspace else rule
            if anchored not in denied:
                denied.append(anchored)
        return denied

    # ── 各类工具判定基调（allow / prompt / forbidden）───────────────

    def _evaluate_file(
        self, tool_name: str, tool_input: dict, profile: Profile, workspace: str | None,
    ) -> tuple[Decision, str | None, str | None]:
        if not self._file_path_check:
            return "allow", None, None
        path = tool_input.get("path")
        if not path:
            return "allow", None, None  # 无路径参数，靠沙箱兜底
        try:
            target = Path(str(path)).expanduser()
        except Exception:
            return "allow", None, None
        policy = profile.filesystem
        if _is_denied(target, policy, workspace):
            return "forbidden", f"路径 '{path}' 命中 deny 规则", "deny"
        if tool_name in FILE_WRITE_TOOLS:
            roots = self._resolve_roots(policy.writable_roots, workspace)
            if any(_within(_norm_slashes(target), _norm_slashes(root)) for root in roots):
                return "allow", None, None
            return "prompt", f"越界写入：'{path}' 不在可写根内", "filesystem:write"
        return "allow", None, None  # 读操作：仅 deny 拦截

    def _evaluate_command(self, tool_input: dict, profile: Profile, shell_env: str = "") -> tuple[Decision, str | None, str | None]:
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        if not command.strip():
            return "allow", None, None
        # 会话 shell 语法适配：PowerShell → Windows 分词（保留 C:\ 反斜杠）+ 大小写不敏感；
        # bash/zsh/未上报 → 维持 POSIX 分词 + 大小写敏感（向后兼容）
        is_ps = shell_env == "powershell"
        # ① 命令规则（前缀匹配）
        try:
            tokens = shlex.split(command, posix=not is_ps)
        except ValueError:
            tokens = command.split()
        matched = self._match_command_rule(tokens, case_sensitive=not is_ps, shell_env=shell_env)
        if matched is not None:
            decision, rule_name = matched
            if decision == "forbidden":
                return "forbidden", f"命令 '{command}' 命中禁止规则 {rule_name}", rule_name
            if decision == "allow":
                return "allow", None, rule_name
            return "prompt", f"命令 '{command}' 命中确认规则 {rule_name}", rule_name
        # ② 危险命令启发式（子串 contains，默认集按会话 shell 选）
        if self._danger_heuristics:
            folded_cmd = command.casefold() if is_ps else command
            for pat in self._dangerous_patterns_for(shell_env):
                needle = pat.casefold() if is_ps else pat
                if needle in folded_cmd:
                    return "prompt", f"命令 '{command}' 命中危险命令启发式", "dangerous"
        return "allow", None, None

    def _match_command_rule(
        self, tokens: list[str], case_sensitive: bool = True, shell_env: str = "",
    ) -> tuple[Decision, str] | None:
        # 规则 shell 归属：可选 powershell | posix | all（缺省 all）；会话按 shell_env 归类
        session_shell = "powershell" if shell_env == "powershell" else "posix"
        for rule in self._tool_rules:
            obj = rule.get("object", "")
            if not obj.startswith("tool:"):
                continue
            rule_shell = rule.get("shell", "all")
            if rule_shell not in ("all", "powershell", "posix"):
                rule_shell = "all"  # 未知值按 all 处理，宽松不打破配置
            if rule_shell != "all" and rule_shell != session_shell:
                continue
            rule_tokens = [str(t) for t in rule.get("rule", [])]
            if len(rule_tokens) == 0 or len(tokens) < len(rule_tokens):
                continue
            head = tokens[:len(rule_tokens)]
            if case_sensitive:
                hit = tuple(head) == tuple(rule_tokens)
            else:
                # PowerShell/Windows 命令与参数大小写不敏感
                hit = tuple(t.casefold() for t in head) == tuple(t.casefold() for t in rule_tokens)
            if hit:
                decision = rule.get("decision", "prompt")
                if decision in ("allow", "forbidden", "prompt"):
                    return decision, obj.split(":", 1)[-1] + " " + " ".join(rule_tokens)
        return None

    def _evaluate_other(self, profile: Profile) -> tuple[Decision, str | None, str | None]:
        # 默认兜底：文件系统受限（非 full 即受限）时问人；阶段0 先不误伤服务端/记忆工具
        return "allow", None, None

    # ── 三态翻译 ───────────────────────────────────────────────────

    def _translate(self, decision: Decision, reason: str | None, matched: str | None, kind: str) -> Verdict:
        if decision == "allow":
            return Verdict("skip", None, matched)
        if decision == "forbidden":
            return Verdict("forbidden", reason, matched)
        # prompt → 由全局审批开关定最终三态
        ap = self._approval_policy
        if ap == "never":
            return Verdict("forbidden", reason, matched)
        if isinstance(ap, dict):  # granular
            key = {"command": "shell", "network": "network"}.get(kind, "rules")
            if not ap.get(key, True):
                return Verdict("forbidden", reason, matched)
        return Verdict("needs_approval", reason, matched)

    # ── 工具函数 ───────────────────────────────────────────────────

    @staticmethod
    def _resolve_roots(marks: list[str], workspace: str | None) -> list[Path]:
        roots: list[Path] = []
        for mark in marks:
            if mark == WORKSPACE_MARK:
                if workspace:
                    roots.append(Path(workspace))
            elif mark == TMPDIR_MARK:
                roots.append(Path(tempfile.gettempdir()))
            else:
                roots.append(Path(mark))
        return roots

    @staticmethod
    def _resolve_roots_for_emit(marks: list[str], workspace: str | None) -> list[str]:
        """下发策略包时的可写根：@WORKSPACE@ 解析为客户端上报的工作区；
        @TMPDIR@ 保持标记原样下发，由客户端按本机 os.tmpdir() 解析（服务端无法得知
        Windows 客户端用户名，且跨机时服务端 tempfile.gettempdir() 并非客户端临时目录）。
        保序去重：画像既 extends 内置又显式重写同一条目时（如 dev），不重复下发同一路径。"""
        out: list[str] = []
        for mark in marks:
            value = workspace if mark == WORKSPACE_MARK else mark
            if value and value not in out:
                out.append(value)
        return out
