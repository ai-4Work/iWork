from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentMarkdown:
    """Parsed agent .md file with YAML frontmatter."""
    name: str
    description: str = ""
    max_turn: int = 15
    max_tokens: int = 50000
    timeout_seconds: int = 180
    system_prompt: str = ""
    # 父→子上下文继承档位（§9.11.5）：none | all | "<正整数>"；空 = 走全局默认
    fork_turns: str = ""


@dataclass
class PluginConfig:
    """Parsed plugin.json for a team plugin."""
    name: str = ""
    version: str = "1.0.0"
    lead_agent_id: str = ""
    agents: dict[str, str] = field(default_factory=dict)
    members: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mcp: list[str] = field(default_factory=list)
    permission: dict = field(default_factory=dict)


_FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n(.*)', re.DOTALL)


class PluginLoader:
    """Reads agent .md files, plugin.json, and discovers skills from plugin directories."""

    def load_agent_md(self, plugin_path: str, agent_id: str) -> AgentMarkdown:
        """Read {plugin_path}/agents/{agent_id}.md, parse YAML frontmatter, return AgentMarkdown."""
        md_path = Path(plugin_path) / "agents" / f"{agent_id}.md"
        if not md_path.exists():
            raise FileNotFoundError(f"Agent markdown not found: {md_path}")

        content = md_path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(content)
        if not m:
            return AgentMarkdown(name=agent_id, system_prompt=content.strip())

        frontmatter_str = m.group(1)
        body = m.group(2).strip()
        frontmatter = _parse_simple_yaml(frontmatter_str)

        return AgentMarkdown(
            name=frontmatter.get("name", agent_id),
            description=frontmatter.get("description", ""),
            max_turn=int(frontmatter.get("max_turn", 15)),
            max_tokens=int(frontmatter.get("max_tokens", 50000)),
            timeout_seconds=int(frontmatter.get("timeout_seconds", 180)),
            system_prompt=body,
            fork_turns=frontmatter.get("fork_turns", ""),
        )

    def load_plugin_json(self, plugin_path: str) -> PluginConfig:
        """Read {plugin_path}/plugin.json, return PluginConfig."""
        json_path = Path(plugin_path) / "plugin.json"
        if not json_path.exists():
            raise FileNotFoundError(f"plugin.json not found: {json_path}")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        return PluginConfig(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            lead_agent_id=data.get("leadAgent", data.get("lead_agent_id", "")),
            agents=data.get("agents", {}),
            members=data.get("members", []),
            skills=data.get("skills", []),
            mcp=data.get("mcp", []),
            permission=data.get("permission", {}),
        )

    def discover_skills(self, plugin_path: str) -> list[dict]:
        """Scan {plugin_path}/skills/ for SKILL.md files.
        Returns list of {skill_name, skill_md_path, content} dicts."""
        skills_dir = Path(plugin_path) / "skills"
        if not skills_dir.exists():
            return []

        found: list[dict] = []
        for skill_md in skills_dir.rglob("SKILL.md"):
            rel = skill_md.relative_to(skills_dir)
            skill_name = str(rel.parent) if str(rel.parent) != "." else "default"
            found.append({
                "skill_name": skill_name,
                "skill_md_path": str(skill_md),
                "content": skill_md.read_text(encoding="utf-8"),
            })
        return found


def _parse_simple_yaml(text: str) -> dict[str, str]:
    """Parse flat key: value YAML without pulling in pyyaml."""
    result: dict[str, str] = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result
