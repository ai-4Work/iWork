"""
Hook 配置加载与校验。
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from server.hooks.chain import HookConfig

VALID_HOOK_POINTS = {
    "message.before", "message.after",
    "llm.before", "llm.after",
    "tool.before", "tool.after",
}


def load_hooks_config(config_path: str = "hooks.json", script_dir: str = "./hooks") -> list[dict]:
    """从 JSON 文件加载 hook 配置列表，校验后返回。"""
    if not os.path.isfile(config_path):
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    hooks = raw if isinstance(raw, list) else raw.get("hooks", [])
    script_dir_abs = os.path.abspath(script_dir)

    validated: list[dict] = []
    for i, h in enumerate(hooks):
        name = h.get("name", f"#entry_{i}")
        _check_required(h, name)
        _check_hook_point(h["on"], name)
        _check_script_path(h["script"], script_dir_abs, name)
        h.setdefault("description", "")
        h.setdefault("order", 0)
        h.setdefault("timeout_ms", 5000)
        h.setdefault("enabled", True)
        if "hook_id" not in h:
            h["hook_id"] = h["name"]
        validated.append(h)

    return validated


def _check_required(h: dict, name: str) -> None:
    for field in ("name", "on", "script"):
        if not h.get(field):
            raise ValueError(f"Hook '{name}': 缺少必填字段 '{field}'")


def _check_hook_point(on: str, name: str) -> None:
    if on not in VALID_HOOK_POINTS:
        raise ValueError(
            f"Hook '{name}': 无效拦截点 '{on}'，合法值: {', '.join(sorted(VALID_HOOK_POINTS))}"
        )


def _check_script_path(script: str, script_dir_abs: str, name: str) -> None:
    if ".." in script:
        raise ValueError(f"Hook '{name}': script 路径禁止包含 '..': {script}")
    # script 字段可能包含解释器前缀（如 "python3 ./hooks/cost-tracker.py"）
    parts = script.split(maxsplit=1)
    script_path = parts[-1] if len(parts) > 1 and os.path.isfile(parts[-1]) else script
    if not os.path.isfile(script_path):
        # 宽松模式：文件不存在时警告而非报错（可能使用绝对路径或系统 PATH 中的命令）
        import logging
        logging.getLogger("iwork.hooks").warning(
            "hook_script_not_found  hook=%s  path=%s", name, script_path
        )
