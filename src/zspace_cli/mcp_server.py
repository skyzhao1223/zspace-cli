"""MCP Server for ZSpace NAS — expose file operations as MCP tools.

Usage:
    python -m zspace_cli.mcp_server
    # or via the CLI:
    zs-mcp
"""

from __future__ import annotations

import importlib.metadata as _md
from typing import Any

from mcp.server import MCPServer

from zspace_cli.client import ZSpaceClient, ZSpaceError

_VENDOR_VERSION = _md.version("zspace-cli")

server = MCPServer("zspace-nas", version=_VENDOR_VERSION)


def _ok(data: Any) -> dict[str, Any]:
    return {"result": data}


def _err(e: Exception) -> dict[str, str]:
    return {"error": str(e)}


@server.tool()
async def zspace_check() -> dict[str, Any]:
    """检查极空间 NAS 连接状态和存储信息 / Check ZSpace NAS connection status and storage info"""
    with ZSpaceClient() as c:
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


@server.tool()
async def zspace_ls(path: str = "/sata11/my/data", show_hidden: bool = False) -> dict[str, Any]:
    """列出极空间 NAS 目录内容 / List directory contents on ZSpace NAS"""
    with ZSpaceClient() as c:
        entries = c.ls(path, show_hidden=show_hidden)
        return _ok(
            [
                {
                    "name": e.name,
                    "path": e.path,
                    "is_dir": e.is_dir,
                    "size": e.size,
                }
                for e in entries
            ]
        )


@server.tool()
async def zspace_info(path: str) -> dict[str, Any]:
    """查看文件或目录详细信息 / Get detailed file/directory info"""
    with ZSpaceClient() as c:
        return _ok(c.info(path))


@server.tool()
async def zspace_rename(path: str, new_name: str) -> dict[str, Any]:
    """重命名文件或目录 / Rename a file or directory"""
    with ZSpaceClient() as c:
        result = c.rename(path, new_name)
        return _ok({"name": result.name, "path": result.path})


@server.tool()
async def zspace_mkdir(parent: str, name: str) -> dict[str, Any]:
    """在极空间 NAS 上创建新目录 / Create a new directory"""
    with ZSpaceClient() as c:
        result = c.mkdir(parent, name)
        return _ok({"name": result.name, "path": result.path})


@server.tool()
async def zspace_move(paths: str | list[str], to: str) -> dict[str, Any]:
    """移动文件或目录 / Move files or directories"""
    with ZSpaceClient() as c:
        if isinstance(paths, str):
            paths = [paths]
        c.move(paths, to)
        return _ok({"status": "moved", "paths": paths, "to": to})


@server.tool()
async def zspace_copy(paths: str | list[str], to: str) -> dict[str, Any]:
    """复制文件或目录 / Copy files or directories"""
    with ZSpaceClient() as c:
        if isinstance(paths, str):
            paths = [paths]
        c.copy(paths, to)
        return _ok({"status": "copied", "paths": paths, "to": to})


@server.tool()
async def zspace_remove(paths: str | list[str]) -> dict[str, Any]:
    """删除文件或目录 / Delete files or directories"""
    with ZSpaceClient() as c:
        if isinstance(paths, str):
            paths = [paths]
        c.remove(paths)
        return _ok({"status": "removed", "paths": paths})


@server.tool()
async def zspace_search(keyword: str, path: str = "/sata11/my/data") -> dict[str, Any]:
    """按文件名搜索 / Search files by name"""
    with ZSpaceClient() as c:
        results = c.search(keyword, path)
        return _ok(
            [
                {"name": e.name, "path": e.path, "is_dir": e.is_dir}
                for e in results
            ]
        )


@server.tool()
async def zspace_tree(path: str = "/sata11/my/data", depth: int = 2) -> dict[str, Any]:
    """树形展示目录结构 / Show directory tree"""
    with ZSpaceClient() as c:
        return _ok(c.tree(path, max_depth=depth))


@server.tool()
async def zspace_upload(
    local_path: str, remote_dir: str, new_name: str | None = None
) -> dict[str, Any]:
    """上传本地文件到 NAS / Upload a local file to the NAS"""
    with ZSpaceClient() as c:
        result = c.upload(local_path, remote_dir, new_name=new_name)
        return _ok(result)


@server.tool()
async def zspace_download(remote_path: str, local_dir: str = ".") -> dict[str, Any]:
    """从 NAS 下载文件到本地 / Download a file from the NAS"""
    with ZSpaceClient() as c:
        out = c.download(remote_path, local_dir)
        return _ok({"saved_to": str(out)})


def main() -> None:
    """Entry point for the MCP server (used by zs-mcp console script)."""
    import asyncio

    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
