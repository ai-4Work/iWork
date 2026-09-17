"""Artifact extraction and normalization.

`artifact` labels which document a tool_call operates on; it is the grouping key
for granularity-2 compression and the source of the offload index label.

Only file tools (read/write/edit) and `bash -f <file>` produce an artifact.
Everything else (bare SQL, URL, web search, glob/grep patterns) is a non-document
edge case and yields an empty artifact.
"""

from __future__ import annotations

import re as _re

_FILE_PARAM: dict[str, str] = {
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
}


def extract_artifact(tool_name: str | None, tool_input: dict | None) -> str:
    """Return the document basename (with extension) a tool_call operates on.

    Returns "" when no document artifact can be extracted.
    """
    if not tool_name or not tool_input:
        return ""

    if tool_name in _FILE_PARAM:
        path = tool_input.get(_FILE_PARAM[tool_name], "")
    elif tool_name == "bash":
        path = _bash_target_file(tool_input.get("command", ""))
    else:
        return ""

    if not isinstance(path, str) or not path:
        return ""
    return _basename(path)


def _bash_target_file(command: str) -> str:
    """Extract the file argument following `-f` in a bash command, else ""."""
    if not isinstance(command, str):
        return ""
    tokens = command.split()
    for i, tok in enumerate(tokens):
        if tok == "-f" and i + 1 < len(tokens):
            return tokens[i + 1]
    return ""


def _basename(path: str) -> str:
    path = path.replace("\\", "/")
    return path.rstrip("/").split("/")[-1]


def normalize_artifact(artifact: str) -> str:
    """Normalize a document basename to a bare name for grouping/labelling.

    "top50_v3.sql" → "top50", "schema.sql" → "schema".
    """
    if not artifact:
        return artifact
    name = _basename(artifact)
    name = _re.sub(r"\.[^.]+$", "", name)      # strip extension
    name = _re.sub(r"_v\d+$", "", name)         # strip version suffix (_v1, _v2...)
    return name.lower()
