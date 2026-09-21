from enum import Enum


class ToolLocation(str, Enum):
    CLIENT = "client"    # Electron 前端本地执行 / 客户端 MCP 工具
    SERVER = "server"    # 服务端直执行：记忆检索/召回工具（直接读写 DB）


# 服务端直执行的内置工具
SERVER_BUILTIN_TOOLS = {"recall", "memory_search", "scene_read"}


class ToolDispatcher:
    """将 LLM 的工具调用按执行位置分流。客户端工具通过 client.tool_request 委派给前端。"""

    def classify(self, tool_name: str) -> ToolLocation:
        """判断工具应在客户端还是服务端执行。"""
        if tool_name in SERVER_BUILTIN_TOOLS:
            return ToolLocation.SERVER
        # 兜底一律发客户端：客户端 MCP / 内置工具 / LLM 幻觉出的工具名都由前端处理
        return ToolLocation.CLIENT
