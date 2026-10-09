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


@_safe_tool
@server.tool(
    title="Storage profile by pool, user and category",
    annotations=_READ_ONLY,
)
async def zspace_usage(
    refresh: Annotated[
        bool, Field(description="Ask the NAS to recompute the snapshot first (slower)")
    ] = False,
) -> dict[str, Any]:
    """Report where physical disk space actually goes on the ZSpace NAS.

    Use this FIRST for any "is my NAS full?" or "what is using my space?"
    question. It is the only view that includes Time Machine backups, other
    users' spaces, the safe box, Docker, RAID metadata and the recycle bins --
    every one of those is outside /<pool>/my/data and returns N001411 to file
    listing, so walking the file tree systematically under-reports pool usage
    (measured: a full 57-minute walk of 2.69M files accounted for only 4.84 TiB
    of a 7.97 TiB pool).

    Returns per pool: total/used/free bytes, used_pct, and entries of
    {kind, owner, label, label_cn, bytes} sorted largest first, where label is
    one of my / my_tm (Time Machine) / my_recycle / my_safe / sys_docker /
    sys_raid / sys_vm / sys_iscsi / sys_other. Read-only.

    For the size of one specific directory use zspace_du instead; for per-disk
    health use zspace_disks.
    """
    with ZSpaceClient() as c:
        if refresh:
            c.disk_usage_refresh()
        status = c.disk_usage_status()
        pools = c.usage_summary()
        info = {
            p["name"]: p
            for p in (c.pool_info().get("data") or {}).get("pool_list") or []
        }
        out = []
        for p in pools:
            meta = info.get(p.pool) or {}
            total = int(meta.get("total_size") or 0)
            used = int(meta.get("usage_size") or 0)
            out.append({
                "pool": p.pool,
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": int(meta.get("free_size") or 0),
                "used_pct": round(used / total * 100, 2) if total else None,
                "breakdown_bytes": p.total,
                "entries": [
                    {"kind": e.kind, "owner": e.owner, "label": e.label,
                     "label_cn": e.label_cn, "bytes": e.size}
                    for e in p.entries
                ],
            })
        return _ok({"snapshot": status, "pools": out})


@_safe_tool
@server.tool(
    title="Directory size and file count",
    annotations=_READ_ONLY,
)
async def zspace_du(
    path: Annotated[
        str, Field(description="Absolute directory path, e.g. /sata11/my/data/影视")
    ],
    walk: Annotated[
        bool,
        Field(description="Force an exact client-side traversal (slow, but the only "
                          "reliable option for very large trees)"),
    ] = False,
) -> dict[str, Any]:
    """Get the size, file count and directory count of one NAS directory.

    Returns {path, bytes, files, dirs, state, complete, source, categories}.

    **Always check `complete` before reporting the numbers.** The server-side
    statistic is instant and exact for small-to-medium trees, but on large ones
    the NAS returns state="running" with a partial snapshot that never
    converges -- measured on a 1.175 TiB / 448k-file folder, fourteen
    consecutive calls all reported ~0.35 TiB with the file count jittering
    between 27k and 30k. There is no poll endpoint that works, so when
    `complete` is false either pass walk=true or tell the user the figure is a
    lower bound. Never present a partial number as the answer.

    walk=true traverses via /v2/file/list at roughly one request per directory;
    a 448k-file tree needs ~93k requests and ~12 minutes. Read-only.

    For whole-pool accounting use zspace_usage; to list individual large files
    use zspace_bigfiles.
    """
    with ZSpaceClient() as c:
        st = c.walk_stat(path) if walk else c.statistic(path)
        return _ok({
            "path": st.path, "bytes": st.size, "files": st.files, "dirs": st.dirs,
            "state": st.state, "complete": st.complete, "source": st.source,
            "requests": st.requests, "hidden_bytes": st.hidden_size,
            "hidden_files": st.hidden_files, "categories": st.categories,
        })


@_safe_tool
@server.tool(
    title="Find the largest files (server-side scan)",
    annotations=_READ_ONLY,
)
async def zspace_bigfiles(
    paths: Annotated[
        list[str],
        Field(description="Directories to scan, e.g. [\"/sata11/my/data\"]"),
    ],
    min_size_mib: Annotated[
        int, Field(description="Only report files at least this many MiB (default 50)")
    ] = 50,
    top: Annotated[int, Field(description="How many largest matches to return")] = 30,
) -> dict[str, Any]:
    """List the largest files under the given directories, biggest first.

    Use to find cleanup candidates. The NAS scans server-side, which is far
    faster than walking: a 7k-file tree returns in ~2 seconds versus ~765
    requests and ~10 seconds client-side. Returns {complete, scanned, matched,
    files: [{name, path, bytes, modify_time}]}.

    There is a single shared task slot, so this call clears any stale task
    before starting one; that is harmless but means two concurrent bigfiles
    scans would interfere. Check `complete` -- if the timeout elapsed the
    results are partial. Read-only.

    For a directory's total size use zspace_du; for pool-level accounting use
    zspace_usage.
    """
    with ZSpaceClient() as c:
        scan = c.find_large(paths, min_size=min_size_mib * 1024 * 1024,
                            topk=max(top, 1000))
        return _ok({
            "complete": scan.complete, "state": scan.state, "paths": scan.paths,
            "min_size": scan.min_size, "scanned": scan.scanned,
            "matched": scan.matched,
            "files": [{"name": f.name, "path": f.path, "bytes": f.size,
                       "modify_time": f.modify_time} for f in scan.files[:top]],
        })


@_safe_tool
@server.tool(
    title="Per-disk capacity, health and free bays",
    annotations=_READ_ONLY,
)
async def zspace_disks() -> dict[str, Any]:
    """Report every physical disk: free space, temperature, health, SMART
    counters, fragmentation and power-on hours, plus how many bays are empty.

    Use before recommending a drive purchase or when a pool is nearly full --
    pool-level free space can be misleading, since in a ZDR pool the space may
    all sit on one spindle while another is effectively full (observed: a pool
    reporting 476 GiB free had one disk with only 59.8 GiB left).

    Returns {free_bays: {sata, nvme, esata}, disks: [{pool, position, model, sn,
    dev_type, total_bytes, free_bytes, used_pct, temp_c, health,
    power_on_hours, reallocated_sectors, fragment_pct, suspected_smr}]}.
    Read-only.

    Tip: power_on_hours greater than the hours since the disk was installed
    means the drive was previously used elsewhere. For full SMART attributes use
    zspace_smart; for pool totals use zspace_usage.
    """
    with ZSpaceClient() as c:
        return _ok({
            "free_bays": c.free_bays(),
            "disks": [{
                "pool": d.pool, "position": d.position, "model": d.model,
                "sn": d.sn, "dev_type": d.dev_type, "mount": d.mount,
                "total_bytes": d.total_size, "free_bytes": d.free_size,
                "used_bytes": d.usage_size, "used_pct": round(d.used_pct, 1),
                "temp_c": d.temp, "health": d.health, "status": d.status,
                "power_on_hours": d.power_on_hours,
                "reallocated_sectors": d.reallocated_sectors,
                "fragment_pct": d.fragment_pct, "suspected_smr": d.suspected_smr,
            } for d in c.disks()],
        })


@_safe_tool
@server.tool(
    title="SMART attribute report for one disk",
    annotations=_READ_ONLY,
)
async def zspace_smart(
    sn: Annotated[
        str, Field(description="Disk serial number, as reported by zspace_disks")
    ],
) -> dict[str, Any]:
    """Return the full SMART attribute table for one disk.

    The endpoint is keyed on serial number -- passing a pool id or a /dev name
    returns N300403. Get serials from zspace_disks first. Returns {health,
    attributes: [{id, attr_name, en_attr_name, now_value, worst, thresh,
    str_value, health}]}; the server also supplies warning/fault text per
    attribute. NVMe drives report no classic attributes. Read-only.

    Focus on ids 5 (reallocated), 187 (reported uncorrectable), 197 (current
    pending), 198 (offline uncorrectable) and 199 (UDMA CRC) -- those are the
    ones that predict failure. For a quick health overview use zspace_disks.
    """
    with ZSpaceClient() as c:
        rep = c.smart(sn)
        return _ok({
            "sn": sn,
            "health": rep.get("health"),
            "attributes": [
                {"id": a.get("id"), "attr_name": a.get("attr_name"),
                 "en_attr_name": a.get("en_attr_name"),
                 "now_value": a.get("now_value"), "worst": a.get("worst"),
                 "thresh": a.get("thresh"), "str_value": a.get("str_value"),
                 "health": a.get("health")}
                for a in (rep.get("attributes") or [])
            ],
        })


@_safe_tool
@server.tool(
    title="List recycle bin contents",
    annotations=_READ_ONLY,
)
async def zspace_recycle_list(
    public: Annotated[
        bool, Field(description="True for the shared/family bin, False for the personal one")
    ] = False,
    limit: Annotated[int, Field(description="Maximum items to return")] = 200,
) -> dict[str, Any]:
    """List what is currently in a recycle bin, including each item's original
    location.

    **Always call this before zspace_recycle_empty** -- emptying purges every
    item in the bin, including files deleted earlier by other means, and is
    irreversible. The personal and public bins are separate stores; files
    deleted from /<pool>/my/data land in the personal one.

    The listing triggers a server-side scan and this call waits for it to
    settle, so an in-progress scan is not mistaken for an empty bin. Returns
    {bin, total, items: [{name, path, original_path, is_dir, bytes}]}. The
    `path` is the in-bin location and is what zspace_recycle_restore and
    zspace_recycle_purge need. Read-only.
    """
    with ZSpaceClient() as c:
        target = c.RECYCLE_PUBLIC if public else c.RECYCLE_MY
        entries, total = c.recycle_list(target, num=limit)
        return _ok({
            "bin": target, "total": total,
            "items": [{"name": e.name, "path": e.path,
                       "original_path": e.original_path, "is_dir": e.is_dir,
                       "bytes": e.size} for e in entries],
        })


@_safe_tool
@server.tool(
    title="Recycle bin retention policy",
    annotations=_READ_ONLY,
)
async def zspace_recycle_config() -> dict[str, Any]:
    """Read the recycle bin retention policy: {my_cycle, public_cycle} in days.

    -1 means never auto-purge, which is the factory default -- so deleted data
    keeps occupying pool space indefinitely until something empties the bin. If
    a user reports that deleting files did not free space, check this first.
    Read-only; there is deliberately no MCP tool for changing it.
    """
    with ZSpaceClient() as c:
        cfg = c.recycle_config()
        return _ok({
            **cfg,
            "my_cycle_meaning": "never auto-purge" if int(cfg.get("my_cycle", 0)) == -1
                                else f"{cfg.get('my_cycle')} days",
        })


@_safe_tool
@server.tool(
    title="Restore files from the recycle bin",
    annotations=_OPEN_WORLD,
)
async def zspace_recycle_restore(
    paths: Annotated[
        list[str],
        Field(description="In-bin paths from zspace_recycle_list, e.g. "
                          "[\"/sata11/.recycle/my/photo.jpg\"]"),
    ],
) -> dict[str, Any]:
    """Restore items from the personal recycle bin to their original locations.

    Paths must be the in-bin paths reported by zspace_recycle_list, not the
    original locations. Returns {restored, failed}. Restores data rather than
    removing it, so this is the recovery path after an accidental
    zspace_remove -- but it only works while the bin has not been emptied.
    """
    with ZSpaceClient() as c:
        ok, failed = c.recycle_restore(paths)
        return _ok({"restored": ok, "failed": failed})


@_safe_tool
@server.tool(
    title="Permanently delete specific recycle bin items",
    annotations=_DESTRUCTIVE,
)
async def zspace_recycle_purge(
    paths: Annotated[
        list[str],
        Field(description="In-bin paths from zspace_recycle_list"),
    ],
    confirm: Annotated[
        bool, Field(description="Must be explicitly true; the call is refused otherwise")
    ] = False,
) -> dict[str, Any]:
    """Permanently delete only the named items from the personal recycle bin,
    leaving everything else untouched. **Irreversible.**

    Prefer this over zspace_recycle_empty whenever the goal is to free the space
    held by specific files: the bin may also contain items the user did not put
    there. Call zspace_recycle_list first and show the user what will be
    destroyed. Requires confirm=true; without it the call is refused and
    nothing happens. Returns {purged, failed}.
    """
    if not confirm:
        # Raised rather than returned so _safe_tool converts it to the same
        # {"error": ...} shape every other failure uses.
        raise ValueError(
            "refused: pass confirm=true after listing the bin with "
            "zspace_recycle_list and confirming with the user"
        )
    with ZSpaceClient() as c:
        ok, failed = c.recycle_purge(paths)
        return _ok({"purged": ok, "failed": failed})


@_safe_tool
@server.tool(
    title="Empty an entire recycle bin",
    annotations=_DESTRUCTIVE,
)
async def zspace_recycle_empty(
    confirm: Annotated[
        bool, Field(description="Must be explicitly true; the call is refused otherwise")
    ] = False,
    public: Annotated[
        bool, Field(description="True empties the shared/family bin, False the personal one")
    ] = False,
) -> dict[str, Any]:
    """Permanently purge an entire recycle bin. **Irreversible, and this is the
    only way deleted files actually stop occupying pool space.**

    Two things make this easy to get wrong. First, the personal and public bins
    are separate stores: emptying the public bin after deleting from a personal
    space succeeds with total_num=0 and frees nothing. Second, it purges
    EVERYTHING in that bin, not just what the current task deleted.

    Always call zspace_recycle_list first, show the user the contents, and get
    their agreement. Requires confirm=true. Returns the server task summary
    {total_num, succeed_num, fail_num, total_size} -- verify total_num is
    non-zero before claiming space was freed. Use zspace_recycle_purge to
    remove specific items instead.
    """
    if not confirm:
        # Raised rather than returned so _safe_tool converts it to the same
        # {"error": ...} shape every other failure uses.
        raise ValueError(
            "refused: pass confirm=true after listing the bin with "
            "zspace_recycle_list and confirming with the user"
        )
    with ZSpaceClient() as c:
        task = c.recycle_empty(public=public)
        return _ok({"bin": "public" if public else "personal", "task": task})


def main() -> None:
    """Entry point for the MCP server (used by zs-mcp console script)."""
    import asyncio

    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
