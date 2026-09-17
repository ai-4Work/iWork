"""单轮多工具调度的"读 / 写"判定与分段（docs/chapters/14 §1–§2）。

只回答一个问题：这个工具调用能不能跟同轮的其它调用同时跑。不做权限决策（见
permission.py），也不做重试安全判定（见 idempotency.py）。

名单 / 策略数据在 server/scheduling.yaml（加载形状对齐 permissions.yaml）：
read_tools / command_tools / read_only_commands / write_flags /
read_only_git_subcommands / git_query_flags / write_file_tools。
代码里只留**机制**：护栏字符与命令解析。

代价刻意不对称：把写判成读，代价是两个写同时改同一个文件（丢数据）；把读判成写，
代价只是多等一次串行（慢）。所以**认不出来的形态一律归"写"**——名单不需要精确，
只需要"有把握不乱动用户文件"。配置文件缺失即启动报错，代码里不留兜底副本。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

import yaml

if TYPE_CHECKING:
    from server.llm.client import LLMChunk

READ = "read"
WRITE = "write"

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "scheduling.yaml"

# 出现即判写：重定向 / 管道 / 命令链 / 命令替换 / heredoc，以及多行脚本。
# 这是安全底线（机制，不是名单）：一旦放开，"cat a > b" 会被当成只读，两个写可能
# 同时改同一个文件。故意不放进配置文件。
_GUARD_CHARS = (">", "<", "|", "&", ";", "`", "\n", "\r")
_GUARD_SUBSTRINGS = ("$(", "<<")


class SchedulingPolicy:
    """读 / 写判定与分段；名单来自 server/scheduling.yaml。

    引擎在 __init__ 里建一个实例（缺文件即报错）；单测可以直接用模块级包装函数，
    或用 config_path 指到临时 YAML 验证配置真的驱动判定。
    """

    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._config = self._load()
        # 缺项 → 空集 → 该类别全判写（保守，不报错）；只有文件缺失才抛错
        self._read_tools = _as_frozenset(self._config.get("read_tools"))
        self._command_tools = _as_frozenset(self._config.get("command_tools"))
        self._read_only_commands = _as_frozenset(self._config.get("read_only_commands"))
        self._git_read_only = _as_frozenset(self._config.get("read_only_git_subcommands"))
        self._git_query_flags = _as_frozenset(self._config.get("git_query_flags"))
        self._write_file_tools = _as_frozenset(self._config.get("write_file_tools"))
        self._write_flags = _as_write_flags(self._config.get("write_flags"))

    # ── 配置加载 / 编译 ─────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._config_path.is_file():
            raise FileNotFoundError(f"调度配置不存在: {self._config_path}")
        with open(self._config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        return loaded if isinstance(loaded, dict) else {}

    # ── 对外判定 ───────────────────────────────────────────────────

    def is_read_only(self, tool_name: str, tool_input: dict | None = None) -> bool:
        """该工具调用能否与同轮其它调用并发（true = 进读段）。"""
        name = _text(tool_name)
        if name in self._command_tools:
            return self.command_is_read_only(str((tool_input or {}).get("command") or ""))
        return name in self._read_tools

    def command_is_read_only(self, cmd: str) -> bool:
        """一条 bash / PowerShell 命令是否"只是看看"。

        两道关：整条命令是**单条**普通命令（护栏），且程序在只读名单里（必要时再排写 flag）。
        """
        text = _text(cmd)
        if not text:
            return False
        if any(ch in text for ch in _GUARD_CHARS) or any(s in text for s in _GUARD_SUBSTRINGS):
            return False

        tokens = text.split()
        program = _program_name(tokens[0])
        args = tokens[1:]

        if program == "git":
            return self._git_is_read_only(args)
        if program not in self._read_only_commands:
            return False
        if any(self._is_write_flag(program, a) for a in args):
            return False
        if program == "awk":
            # awk 程序体能起子进程，不解析，一律判写
            return not any("system(" in a or "getline" in a for a in args)
        return True

    def path_of(self, tool_name: str, tool_input: dict | None = None) -> str | None:
        """写类工具的目标路径（write_file / edit_file 参数自带 path）。

        预留给 doc §2.2"写段内相邻异文件可并行"的判定；当前写段一律串行，尚未使用。
        """
        if _text(tool_name) not in self._write_file_tools:
            return None
        path = (tool_input or {}).get("path")
        return str(path) if path else None

    def segments(self, calls: Sequence["LLMChunk"]) -> list[Segment]:
        """按模型发出顺序把调用扫成连续段（doc §2.2）：连续读成读段、连续写成写段。"""
        segments: list[Segment] = []
        for call in calls:
            kind = READ if self.is_read_only(call.tool_name or "", call.tool_input or {}) else WRITE
            if segments and segments[-1].kind == kind:
                segments[-1].calls.append(call)
            else:
                segments.append(Segment(kind=kind, calls=[call]))
        return segments

    # ── 内部判定（机制）────────────────────────────────────────────

    def _is_write_flag(self, program: str, arg: str) -> bool:
        a = _strip_quotes(arg)
        for flag in self._write_flags.get(program, ()):
            if a == flag:
                return True
            if flag.startswith("--") and a.startswith(flag + "="):
                return True
            if not flag.startswith("--") and a.startswith(flag):
                return True
        return False

    def _git_is_read_only(self, args: list[str]) -> bool:
        """git 只读形状：`git <只读子命令>`；branch / remote 只放行裸形式或纯查询 flag。

        全局 flag（`git -C dir status`）不做解析，一律判写——保守优先。
        """
        if not args or args[0].startswith("-"):
            return False
        sub, rest = args[0].lower(), args[1:]
        if sub in self._git_read_only:
            return not any(a == "--output" or a.startswith("--output=") for a in rest)
        if sub in ("branch", "remote"):
            return all(_strip_quotes(a) in self._git_query_flags for a in rest)
        return False


@dataclass
class Segment:
    """一段连续的同类调用：kind = READ（段内并发）/ WRITE（段内串行）。"""
    kind: str
    calls: list[Any] = field(default_factory=list)


# ── 模块级包装：默认策略（懒加载 + 缓存）─────────────────────────
# 单测与既有调用点用这一套；引擎走显式实例（路径可注入，启动即报错）。
# 不提供 setter：要自定义配置请自己 SchedulingPolicy(config_path=...)。

_default: SchedulingPolicy | None = None


def default_policy() -> SchedulingPolicy:
    global _default
    if _default is None:
        _default = SchedulingPolicy()
    return _default


def is_read_only(tool_name: str, tool_input: dict | None = None) -> bool:
    return default_policy().is_read_only(tool_name, tool_input)


def command_is_read_only(cmd: str) -> bool:
    return default_policy().command_is_read_only(cmd)


def path_of(tool_name: str, tool_input: dict | None = None) -> str | None:
    return default_policy().path_of(tool_name, tool_input)


def segment_tool_calls(
    calls: Sequence["LLMChunk"], policy: SchedulingPolicy | None = None,
) -> list[Segment]:
    return (policy or default_policy()).segments(calls)


# ── 配置编译 ───────────────────────────────────────────────────


def _as_frozenset(raw: Any) -> frozenset[str]:
    """列表 → frozen 集合；非列表（含缺项）→ 空集。值与代码里的旧名单逐字一致，
    不做大小写变换（工具名 / flag 是大小写敏感的，`curl -O` 和 `-o` 不同）。"""
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(x) for x in raw if str(x).strip())


def _as_write_flags(raw: Any) -> dict[str, tuple[str, ...]]:
    """write_flags 段 → {程序名(小写): (flag, ...)}。程序名比对时已小写（见 _program_name），
    所以键统一小写；flag 值保持原样（`-O` 与 `-o` 是两条不同的规则）。"""
    if not isinstance(raw, dict):
        return {}
    flags: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if isinstance(value, (list, tuple, set, frozenset)):
            flags[str(key).strip().lower()] = tuple(str(v) for v in value if str(v).strip())
    return flags


def _text(value: Any) -> str:
    return str(value or "").strip()


def _program_name(token: str) -> str:
    """取程序名：去引号、去路径（两种分隔符）、去 .exe/.cmd/.bat/.ps1、转小写。"""
    name = _strip_quotes(token)
    for sep in ("/", "\\"):
        if sep in name:
            name = name.rsplit(sep, 1)[1]
    name = name.lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    return name


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token
