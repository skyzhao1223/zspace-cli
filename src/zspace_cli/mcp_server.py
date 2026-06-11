"""MCP Server for ZSpace NAS — expose file operations as MCP tools.

Usage:
    python -m zspace_cli.mcp_server
    # or via the CLI:
    zs-mcp
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from zspace_cli.client import ZSpaceClient, ZSpaceError

server = Server("zspace-nas")

TOOLS = [
    Tool(
        name="zspace_check",
        description="检查极空间 NAS 连接状态和存储信息 / Check ZSpace NAS connection status and storage info",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="zspace_ls",
        description="列出极空间 NAS 目录内容 / List directory contents on ZSpace NAS",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "目录路径，如 /sata11/my/data",
                    "default": "/sata11/my/data",
                },
                "show_hidden": {"type": "boolean", "default": False},
            },
        },
    ),
    Tool(
        name="zspace_info",
        description="查看文件或目录详细信息 / Get detailed file/directory info",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件或目录路径"},
            },
            "required": ["path"],
        },
    ),
    Tool(
        name="zspace_rename",
        description="重命名文件或目录 / Rename a file or directory",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件/目录路径"},
                "new_name": {"type": "string", "description": "新名称"},
            },
            "required": ["path", "new_name"],
        },
    ),
    Tool(
        name="zspace_mkdir",
        description="在极空间 NAS 上创建新目录 / Create a new directory",
        inputSchema={
            "type": "object",
            "properties": {
                "parent": {"type": "string", "description": "父目录路径"},
                "name": {"type": "string", "description": "新目录名"},
            },
            "required": ["parent", "name"],
        },
    ),
    Tool(
        name="zspace_move",
        description="移动文件或目录 / Move files or directories",
        inputSchema={
            "type": "object",
            "properties": {
                "paths": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "源路径（单个字符串或数组）",
                },
                "to": {"type": "string", "description": "目标目录"},
            },
            "required": ["paths", "to"],
        },
    ),
    Tool(
        name="zspace_copy",
        description="复制文件或目录 / Copy files or directories",
        inputSchema={
            "type": "object",
            "properties": {
                "paths": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "源路径（单个字符串或数组）",
                },
                "to": {"type": "string", "description": "目标目录"},
            },
            "required": ["paths", "to"],
        },
    ),
    Tool(
        name="zspace_remove",
        description="删除文件或目录 / Delete files or directories",
        inputSchema={
            "type": "object",
            "properties": {
                "paths": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "要删除的路径（单个字符串或数组）",
                },
            },
            "required": ["paths"],
        },
    ),
    Tool(
        name="zspace_search",
        description="按文件名搜索 / Search files by name",
        inputSchema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "path": {
                    "type": "string",
                    "description": "搜索目录",
                    "default": "/sata11/my/data",
                },
            },
            "required": ["keyword"],
        },
    ),
    Tool(
        name="zspace_tree",
        description="树形展示目录结构 / Show directory tree",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "根目录",
                    "default": "/sata11/my/data",
                },
                "depth": {"type": "integer", "default": 2, "description": "递归深度"},
            },
        },
    ),
]


def _ok(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


def _err(e: Exception) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {e}")]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        with ZSpaceClient() as c:
            if name == "zspace_check":
                connected = c.is_connected()
                result: dict[str, Any] = {"connected": connected}
                if connected:
                    try:
                        pool = c.pool_info()
                        result["pools"] = [
                            {
                                "name": p["name"],
                                "total_tb": round(p["total_size"] / (1024**4), 1),
                                "free_tb": round(p["free_size"] / (1024**4), 1),
                            }
                            for p in pool["data"]["pool_list"]
                        ]
                    except ZSpaceError:
                        pass
                return _ok(result)

            elif name == "zspace_ls":
                entries = c.ls(
                    arguments.get("path", "/sata11/my/data"),
                    show_hidden=arguments.get("show_hidden", False),
                )
                return _ok([
                    {
                        "name": e.name,
                        "path": e.path,
                        "is_dir": e.is_dir,
                        "size": e.size,
                    }
                    for e in entries
                ])

            elif name == "zspace_info":
                data = c.info(arguments["path"])
                return _ok(data)

            elif name == "zspace_rename":
                result = c.rename(arguments["path"], arguments["new_name"])
                return _ok({"name": result.name, "path": result.path})

            elif name == "zspace_mkdir":
                result = c.mkdir(arguments["parent"], arguments["name"])
                return _ok({"name": result.name, "path": result.path})

            elif name == "zspace_move":
                paths = arguments["paths"]
                if isinstance(paths, str):
                    paths = [paths]
                c.move(paths, arguments["to"])
                return _ok({"status": "moved", "paths": paths, "to": arguments["to"]})

            elif name == "zspace_copy":
                paths = arguments["paths"]
                if isinstance(paths, str):
                    paths = [paths]
                c.copy(paths, arguments["to"])
                return _ok({"status": "copied", "paths": paths, "to": arguments["to"]})

            elif name == "zspace_remove":
                paths = arguments["paths"]
                if isinstance(paths, str):
                    paths = [paths]
                c.remove(paths)
                return _ok({"status": "removed", "paths": paths})

            elif name == "zspace_search":
                results = c.search(
                    arguments["keyword"],
                    arguments.get("path", "/sata11/my/data"),
                )
                return _ok([
                    {"name": e.name, "path": e.path, "is_dir": e.is_dir}
                    for e in results
                ])

            elif name == "zspace_tree":
                nodes = c.tree(
                    arguments.get("path", "/sata11/my/data"),
                    max_depth=arguments.get("depth", 2),
                )
                return _ok(nodes)

            else:
                return _err(ValueError(f"Unknown tool: {name}"))

    except ZSpaceError as e:
        return _err(e)
    except Exception as e:
        return _err(e)


async def _async_main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Entry point for the MCP server (used by zs-mcp console script)."""
    import asyncio
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
