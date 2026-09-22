"""MCP Server for ZSpace NAS — expose file operations as MCP tools.

Usage:
    python -m zspace_cli.mcp_server
    # or via the CLI:
    zs-mcp
"""

from __future__ import annotations

import importlib.metadata as _md
import sys
from typing import Annotated, Any

try:
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
    from pydantic import Field
except ImportError as _exc:  # pragma: no cover - only on unsupported setups
    # The `mcp` extra is gated on python_version >= '3.10', while the package
    # itself still supports 3.9 (CI tests both, installing .[dev] on 3.9 and
    # .[mcp,dev] elsewhere). So `pip install "zspace-cli[mcp]"` on 3.9 quietly
    # resolves to no mcp and no pydantic, and the console script then dies with
    # a bare ImportError traceback that never mentions the Python version.
    raise SystemExit(
        "zs-mcp needs the 'mcp' extra, which requires Python 3.10 or newer.\n"
        f"  running on        : Python {sys.version.split()[0]}\n"
        f"  import that failed: {_exc}\n"
        "\n"
        "Install it on Python 3.10+ with:\n"
        "  pip install 'zspace-cli[mcp]'\n"
        "\n"
        "The plain CLI (zs) still supports Python 3.9+; only the MCP server\n"
        "needs 3.10+, because the upstream `mcp` package requires it."
    ) from None

from zspace_cli.client import ZSpaceClient, ZSpaceError

try:
    _VENDOR_VERSION = _md.version("zspace-cli")
except _md.PackageNotFoundError:
    _VENDOR_VERSION = "0.0.0"  # running from source without install

server = MCPServer("zspace-nas", version=_VENDOR_VERSION)

# Every tool below operates on the real (external) ZSpace NAS through the
# desktop client proxy, never on the MCP server's own resources, so the
# open-world hint applies across the board.
_OPEN_WORLD = ToolAnnotations(open_world_hint=True)
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_DESTRUCTIVE = ToolAnnotations(destructive_hint=True, open_world_hint=True)


def _ok(data: Any) -> dict[str, Any]:
    return {"result": data}


def _err(e: Exception) -> dict[str, str]:
    return {"error": str(e)}


def _safe_tool(fn):
    """Wrap an MCP tool so ZSpace/business errors come back as a structured
    {"error": ...} result instead of an unhandled exception over the wire.
    """
    import functools

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except ZSpaceError as e:
            return _err(e)
        except (FileNotFoundError, ValueError) as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001 - report anything to the agent
            return _err(e)

    return wrapper


@_safe_tool
@server.tool(
    title="Check ZSpace NAS connection",
    annotations=_READ_ONLY,
)
async def zspace_check() -> dict[str, Any]:
    """Check whether the ZSpace NAS is reachable through the local desktop
    client proxy and, if so, return a summary of storage pools.

    Call this first to verify the proxy at 127.0.0.1:13579 is online before
    running other file operations. Returns {"connected": true, "pools":
    [{name, total_tb, free_tb}]} when the NAS responds, or {"connected":
    false} when it does not. Never modifies NAS state.

    Unlike zspace_pool_info, this is a connectivity probe that happens to
    include a pool summary; use pool_info when you only need capacity.
    """
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


@_safe_tool
@server.tool(
    title="Get storage pool info",
    annotations=_READ_ONLY,
)
async def zspace_pool_info() -> dict[str, Any]:
    """List every storage pool on the ZSpace NAS with its name and capacity.

    Use to inspect free space before an upload or to report total capacity.
    Returns a list of {name, total_tb, free_tb} objects, one per pool.
    Read-only; never modifies NAS state.

    Prefer this over zspace_disk_stats for capacity questions: it returns
    normalized per-pool numbers, whereas disk_stats returns the NAS's raw
    diagnostics object. Use zspace_check instead if you need to know whether
    the NAS is reachable at all.
    """
    with ZSpaceClient() as c:
        pool = c.pool_info()
        return _ok([
            {
                "name": p["name"],
                "total_tb": round(p["total_size"] / (1024**4), 1),
                "free_tb": round(p["free_size"] / (1024**4), 1),
            }
            for p in pool["data"]["pool_list"]
        ])


@_safe_tool
@server.tool(
    title="Get disk statistics",
    annotations=_READ_ONLY,
)
async def zspace_disk_stats() -> dict[str, Any]:
    """Return raw disk statistics reported by the ZSpace NAS.

    Use for diagnostics and health monitoring (I/O, capacity, SMART-style
    counters as exposed by the NAS API). Returns the raw stats object.
    Read-only; never modifies NAS state.

    Unlike zspace_pool_info, the shape is whatever the NAS API exposes and is
    not normalized — choose pool_info for "how much free space", this one for
    "is a disk unhealthy".
    """
    with ZSpaceClient() as c:
        return _ok(c.disk_stats())


@_safe_tool
@server.tool(
    title="List directory contents",
    annotations=_READ_ONLY,
)
async def zspace_ls(
    path: Annotated[
        str, Field(description="Absolute path on the NAS, e.g. /sata11/my/data")
    ] = "/sata11/my/data",
    show_hidden: Annotated[bool, Field(description="Include hidden files and directories")] = False,
) -> dict[str, Any]:
    """List the contents of a directory on the ZSpace NAS at the given path.

    Use to browse files, locate items, or pick targets for later move/copy
    operations. Large directories are paginated automatically (the NAS API
    returns at most 50 entries per call). Returns a list of {name, path,
    is_dir, size} objects. Read-only; pass show_hidden=True to include hidden
    entries.

    Flat and one level deep: use zspace_tree for nested structure,
    zspace_search when you know a name but not the location, and
    zspace_info for full metadata on one path you already have.
    """
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


@_safe_tool
@server.tool(
    title="Get file or directory info",
    annotations=_READ_ONLY,
)
async def zspace_info(
    path: Annotated[
        str,
        Field(
            description="Absolute path of the file or directory to inspect, "
            "e.g. /sata11/my/data/影视"
        ),
    ],
) -> dict[str, Any]:
    """Return detailed metadata for one file or directory on the ZSpace NAS.

    Use when you need more than the ls summary exposes (permissions,
    timestamps, exact size, type details). Returns the raw info object from
    the NAS API. Read-only; the path must already exist.

    One path only — use zspace_ls to enumerate a directory, or zspace_search
    to locate an item first.
    """
    with ZSpaceClient() as c:
        return _ok(c.info(path))


@_safe_tool
@server.tool(
    title="Rename file or directory",
    annotations=_OPEN_WORLD,
)
async def zspace_rename(
    path: Annotated[
        str,
        Field(
            description="Absolute path of the file or directory to rename, "
            "e.g. /sata11/my/data/old"
        ),
    ],
    new_name: Annotated[str, Field(description="New name (basename only), e.g. newname.mkv")],
) -> dict[str, Any]:
    """Rename one file or directory on the ZSpace NAS, keeping it in place.

    Use to fix a name without changing location; the parent directory stays
    the same. new_name is the bare basename, not a path. Returns the updated
    {name, path}. This mutates NAS state; it cannot be undone.

    Same-directory only — use zspace_move to relocate to a different parent
    (move also accepts several paths at once, rename does not).
    """
    with ZSpaceClient() as c:
        result = c.rename(path, new_name)
        return _ok({"name": result.name, "path": result.path})


@_safe_tool
@server.tool(
    title="Create directory",
    annotations=_OPEN_WORLD,
)
async def zspace_mkdir(
    parent: Annotated[
        str, Field(description="Absolute path of the parent directory, e.g. /sata11/my/data")
    ],
    name: Annotated[str, Field(description="Name of the new directory (basename only)")],
) -> dict[str, Any]:
    """Create a new directory on the ZSpace NAS under the given parent.

    Use to prepare a destination before moving files into it. name is the
    bare basename, not a path. Fails if a directory of that name already
    exists. Returns the created {name, path}. Mutates NAS state.

    zspace_move and zspace_copy both require the destination to exist
    already, so call this first when reorganizing into a new folder.
    """
    with ZSpaceClient() as c:
        result = c.mkdir(parent, name)
        return _ok({"name": result.name, "path": result.path})


@_safe_tool
@server.tool(
    title="Move files or directories",
    annotations=_OPEN_WORLD,
)
async def zspace_move(
    paths: Annotated[
        str | list[str],
        Field(description="One absolute path or a list of absolute paths to move"),
    ],
    to: Annotated[str, Field(description="Destination directory (absolute path)")],
) -> dict[str, Any]:
    """Move one or more files/directories on the NAS into a destination folder.

    Use to reorganize files, e.g. sort media into per-genre folders. Accepts
    a single path or a list; the destination must already exist (create it
    with zspace_mkdir). Returns {status: "moved", paths, to}. Mutates NAS
    state; sources are removed from their original location.

    Choose move vs copy by whether the originals should survive: copy keeps
    them, move does not. To change only the name within the same directory,
    use zspace_rename instead.
    """
    with ZSpaceClient() as c:
        if isinstance(paths, str):
            paths = [paths]
        c.move(paths, to)
        return _ok({"status": "moved", "paths": paths, "to": to})


@_safe_tool
@server.tool(
    title="Copy files or directories",
    annotations=_OPEN_WORLD,
)
async def zspace_copy(
    paths: Annotated[
        str | list[str],
        Field(description="One absolute path or a list of absolute paths to copy"),
    ],
    to: Annotated[str, Field(description="Destination directory (absolute path)")],
) -> dict[str, Any]:
    """Copy one or more files/directories on the NAS into a destination folder.

    Use to duplicate media or create backups without removing the originals.
    Accepts a single path or a list; the destination must already exist
    (create it with zspace_mkdir). Returns {status: "copied", paths, to}.
    Mutates NAS state but leaves the sources untouched.

    The non-destructive counterpart of zspace_move — pick copy when the
    original must stay where it is, move when it should not. Copies consume
    additional capacity; check zspace_pool_info first for large trees.
    """
    with ZSpaceClient() as c:
        if isinstance(paths, str):
            paths = [paths]
        c.copy(paths, to)
        return _ok({"status": "copied", "paths": paths, "to": to})


@_safe_tool
@server.tool(
    title="Delete files or directories",
    annotations=_DESTRUCTIVE,
)
async def zspace_remove(
    paths: Annotated[
        str | list[str],
        Field(description="One absolute path or a list of absolute paths to delete"),
    ],
) -> dict[str, Any]:
    """Permanently delete one or more files/directories from the ZSpace NAS.

    Use only when you are sure the items are no longer needed — deletion is
    immediate and NOT recoverable. Accepts a single path or a list. Returns
    {status: "removed", paths}. Marked destructive: confirm intent before
    calling.

    There is no trash or undo: zspace_move is the reversible alternative when
    the goal is only to get items out of the way. Confirm the exact paths with
    zspace_ls or zspace_search before calling, and never pass a pool root.
    """
    with ZSpaceClient() as c:
        if isinstance(paths, str):
            paths = [paths]
        c.remove(paths)
        return _ok({"status": "removed", "paths": paths})


@_safe_tool
@server.tool(
    title="Search files by name",
    annotations=_READ_ONLY,
)
async def zspace_search(
    keyword: Annotated[str, Field(description="Keyword to match against file names")],
    path: Annotated[
        str, Field(description="Directory to search under, e.g. /sata11/my/data")
    ] = "/sata11/my/data",
) -> dict[str, Any]:
    """Search for files/directories on the NAS whose names contain the keyword.

    Use to find a file without knowing its exact location. Returns a list of
    {name, path, is_dir} matches, capped at 100. Read-only; never modifies
    NAS state.

    The NAS full-text index is searched GLOBALLY, then results are filtered
    client-side to `path`. Because `path` defaults to /sata11/my/data, a match
    living in another pool is silently dropped and you get an empty list —
    pass the pool root (or the widest directory you mean) before concluding a
    file does not exist. Unlike zspace_ls / zspace_tree this matches by name
    across the index rather than walking a directory.
    """
    with ZSpaceClient() as c:
        results = c.search(keyword, path)
        return _ok(
            [
                {"name": e.name, "path": e.path, "is_dir": e.is_dir}
                for e in results
            ]
        )


@_safe_tool
@server.tool(
    title="Show directory tree",
    annotations=_READ_ONLY,
)
async def zspace_tree(
    path: Annotated[
        str, Field(description="Root directory to show as a tree, e.g. /sata11/my/data")
    ] = "/sata11/my/data",
    depth: Annotated[
        int, Field(description="Maximum recursion depth (1 = current level only)")
    ] = 2,
) -> dict[str, Any]:
    """Return a nested tree view of a directory on the ZSpace NAS.

    Use to understand overall folder structure before deep operations. depth
    caps recursion (default 2). Returns the nested tree structure as exposed
    by the NAS API. Read-only.

    Nested rather than flat: use zspace_ls for a single level with sizes, or
    zspace_search when you are looking for a name rather than a layout.
    """
    with ZSpaceClient() as c:
        return _ok(c.tree(path, max_depth=depth))


@_safe_tool
@server.tool(
    title="Upload local file to NAS",
    annotations=_OPEN_WORLD,
)
async def zspace_upload(
    local_path: Annotated[str, Field(description="Local filesystem path of the file to upload")],
    remote_dir: Annotated[
        str, Field(description="Destination directory on the NAS (absolute path)")
    ],
    new_name: Annotated[
        str | None,
        Field(description="Optional remote file name; defaults to the local basename"),
    ] = None,
) -> dict[str, Any]:
    """Upload a file from the local machine to a directory on the ZSpace NAS.

    Use to push media or documents to the NAS. remote_dir must already exist
    (create it with zspace_mkdir); pass new_name to rename it on arrival.
    Returns the upload result from the NAS API. Mutates NAS state; a file with
    the same name is overwritten.

    Local -> NAS, the inverse of zspace_download. Files above 64 MB go through
    the desktop client's sliced upload protocol (2 MB slices) automatically,
    because the local proxy rejects oversized single-request bodies with
    HTTP 413. Check zspace_pool_info for free space before large uploads.
    """
    with ZSpaceClient() as c:
        result = c.upload(local_path, remote_dir, new_name=new_name)
        return _ok(result)


@_safe_tool
@server.tool(
    title="Download file from NAS",
    annotations=_READ_ONLY,
)
async def zspace_download(
    remote_path: Annotated[
        str, Field(description="Absolute path of the file on the NAS to download")
    ],
    local_dir: Annotated[
        str, Field(description="Local directory to save the file into (defaults to current dir)")
    ] = ".",
) -> dict[str, Any]:
    """Download a file from the ZSpace NAS to a local directory.

    Use to pull media or documents off the NAS. The local_dir must already
    exist. Returns {saved_to: "<absolute local path>"}. Does not modify NAS
    state.

    NAS -> local, the inverse of zspace_upload. Unlike upload this never
    changes anything on the NAS, so it is safe to retry; an existing local file
    of the same name is overwritten.
    """
    with ZSpaceClient() as c:
        out = c.download(remote_path, local_dir)
        return _ok({"saved_to": str(out)})


def main() -> None:
    """Entry point for the MCP server (used by zs-mcp console script)."""
    import asyncio

    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
