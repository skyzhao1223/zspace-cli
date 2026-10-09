"""Core SDK client for the ZSpace NAS API."""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from zspace_cli.auth import (
    Credentials,
    check_client_running,
    default_base_url,
    load_credentials,
)

# Network-level failures (connection refused/reset, timeout, 5xx, 429) can be
# transient — the desktop proxy occasionally hiccups. Business errors
# (ZSpaceError, HTTP 4xx) are not retried.
_RETRYABLE_EXC: tuple[type[BaseException], ...] = (
    httpx.TransportError,
    httpx.HTTPStatusError,
)

# Business error codes the desktop client treats as fatal during sliced
# uploads (no retry): permission / conflicting-dir / safe-box closed, etc.
_UPLOAD_FATAL_CODES = frozenset({"N001302", "N001331", "N001603", "N001397"})

# The desktop client's local proxy (openresty) rejects request bodies above a
# size limit on /v2/file/create with HTTP 413. Files larger than this
# threshold go straight through the sliced /v2/file/upload protocol instead;
# smaller files try /v2/file/create first and fall back to slices on 413.
DEFAULT_SLICE_THRESHOLD = 64 * 1024 * 1024

# Slice size for /v2/file/upload — matches the desktop client's remote-mode
# maximum (dynamicSplitSize caps at 2MB when not on LAN).
DEFAULT_SLICE_SIZE = 2 * 1024 * 1024

ProgressCallback = Callable[[int, int], None]


class _CountingReader:
    """Wrap a binary file handle to report upload progress (bytes sent/total)."""

    def __init__(self, fh: Any, total: int, callback: ProgressCallback | None):
        self._fh = fh
        self._total = total
        self._callback = callback
        self._done = 0

    def read(self, n: int = -1) -> bytes:
        chunk = self._fh.read(n)
        self._done += len(chunk)
        if self._callback is not None:
            self._callback(self._done, self._total)
        return chunk

    def __iter__(self):
        return iter(self._fh)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fh, name)


@dataclass
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modify_time: str = ""
    create_time: str = ""
    ext: str = ""

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> FileEntry:
        raw_size = d.get("size", 0)
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            size = 0
        return cls(
            name=d["name"],
            path=d["path"],
            is_dir=d.get("is_dir", "0") == "1",
            size=size,
            modify_time=d.get("modify_time", ""),
            create_time=d.get("create_time", ""),
            ext=d.get("ext", ""),
        )


# /v2/file/statistic reports both direct-child counts (``vnum``) and recursive
# totals (``tvnum``). Only the recursive totals describe a subtree's contents,
# so those are the ones surfaced as categories.
_STAT_CATEGORIES: dict[str, str] = {
    "视频": "tvnum",
    "音频": "tanum",
    "图片": "tinum",
    "文档": "tdocnum",
    "应用": "tappnum",
    "压缩文件": "tcomnum",
}

# Human-readable names for the /system/diskusage3 labels. Several of these are
# invisible to any file-level walk: Time Machine backups, the safe box, other
# users' spaces, and the pool's own system data all live outside
# ``/<pool>/my/data``, which is the only path a normal token can list.
USAGE_LABELS: dict[str, str] = {
    "my": "个人文件",
    "my_tm": "Time Machine 备份",
    "my_recycle": "个人回收站",
    "my_safe": "保险箱",
    "pub_recycle": "公共回收站",
    "sys_raid": "池/RAID 元数据",
    "sys_docker": "Docker",
    "sys_vm": "虚拟机",
    "sys_iscsi": "iSCSI",
    "sys_other": "其他系统数据",
}


def _as_int(value: Any) -> int:
    """Coerce an API numeric field, which may arrive as str, int, or None."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class DirStat:
    """Directory size/count statistics.

    Two sources populate this, and telling them apart matters:

    ``source="statistic"``
        Server-side computation via ``/v2/file/statistic``. Fast (one request)
        and exact for small-to-medium trees, but the NAS computes large trees
        asynchronously and reports ``state="running"`` with a **partial and
        fluctuating** snapshot. Measured on a 1.175 TiB / 448,687-file folder:
        fourteen consecutive calls all returned ``running`` with ~0.35 TiB and a
        file count jittering between 27,457 and 30,366 — never converging.
        ``/v2/file/statistictask``, which looks like the matching poll endpoint,
        returns an empty task and is useless for this.
    ``source="walk"``
        Client-side recursive walk of ``/v2/file/list``. Slow (~1 request per
        directory) but always complete, so it is the fallback for large trees.

    Never present a result as final without checking :attr:`complete`.
    """

    path: str
    size: int = 0
    files: int = 0
    dirs: int = 0
    state: str = ""
    source: str = "statistic"
    hidden_size: int = 0
    hidden_files: int = 0
    hidden_dirs: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    requests: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """True only when the numbers are final and safe to report."""
        return self.state == "done"

    @classmethod
    def from_api(cls, path: str, task: dict[str, Any]) -> DirStat:
        return cls(
            path=path,
            size=_as_int(task.get("size")),
            files=_as_int(task.get("tfnum")),
            dirs=_as_int(task.get("tdirnum")),
            state=str(task.get("state") or ""),
            hidden_size=_as_int(task.get("hidden_size")),
            hidden_files=_as_int(task.get("hidden_fnum")),
            hidden_dirs=_as_int(task.get("hidden_dirnum")),
            categories={
                label: _as_int(task.get(key))
                for label, key in _STAT_CATEGORIES.items()
                if _as_int(task.get(key))
            },
            raw=task,
        )


@dataclass
class RecycleEntry:
    """One item in a recycle bin.

    ``path`` is the item's location *inside* the bin
    (``/<pool>/.recycle/my/<name>``) and is what :meth:`ZSpaceClient.recycle_restore`
    needs; ``original_path`` is where it lived before deletion.
    """

    name: str
    path: str
    original_path: str = ""
    is_dir: bool = False
    size: int = 0
    modify_time: str = ""

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> RecycleEntry:
        return cls(
            name=d.get("name", ""),
            path=d.get("path", ""),
            original_path=d.get("original_path", "") or "",
            is_dir=d.get("is_dir") == "1",
            size=_as_int(d.get("size")),
            modify_time=str(d.get("modify_time", "") or ""),
        )


@dataclass
class LargeFile:
    """One match from a server-side large-file scan."""

    name: str
    path: str
    size: int
    modify_time: str = ""
    ftype: str = ""

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> LargeFile:
        return cls(
            name=d.get("name", "") or "",
            path=d.get("path", "") or "",
            size=_as_int(d.get("size")),
            modify_time=str(d.get("modify_time", "") or ""),
            ftype=str(d.get("ftype", "") or ""),
        )


@dataclass
class LargeFileScan:
    """State and results of a ``/v2/file/find/large`` task.

    ``state`` is 1 while scanning and 2 when done; there is a single shared
    task slot, so ``info`` takes no id. ``matched`` arrives as an int once the
    scan settles but the web client also guards for an array, so both shapes
    are accepted.
    """

    state: int = 0
    paths: list[str] = field(default_factory=list)
    min_size: int = 0
    max_size: int = 0
    topk: int = 0
    scanned: int = 0
    matched: int = 0
    files: list[LargeFile] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.state == 2

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> LargeFileScan:
        mc = d.get("match_count")
        matched = len(mc) if isinstance(mc, list) else _as_int(mc)
        return cls(
            state=_as_int(d.get("state")),
            paths=[str(p) for p in (d.get("paths") or [])],
            min_size=_as_int(d.get("min_size")),
            max_size=_as_int(d.get("max_size")),
            topk=_as_int(d.get("topk")),
            scanned=_as_int(d.get("scan_count")),
            matched=matched,
            files=[LargeFile.from_api(i) for i in (d.get("list") or [])],
            raw=d,
        )


@dataclass
class UsageEntry:
    """One row of the /system/diskusage3 breakdown."""

    kind: str      # "user" | "sys" | "public_recycle" | "public"
    label: str     # my / my_tm / sys_docker / ...
    size: int
    owner: str = ""

    @property
    def label_cn(self) -> str:
        return USAGE_LABELS.get(self.label, self.label)


@dataclass
class PoolUsage:
    """Physical usage of one storage pool, summed across its disks.

    ``/system/diskusage3`` returns one entry *per disk*, so a 3-disk pool
    arrives as three entries that must be summed to match ``/zspool/info``'s
    pool-level ``usage_size``.
    """

    pool: str
    entries: list[UsageEntry] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(e.size for e in self.entries)

    @property
    def user_total(self) -> int:
        return sum(e.size for e in self.entries if e.kind == "user")

    @property
    def system_total(self) -> int:
        return sum(e.size for e in self.entries if e.kind != "user")

    def by_owner(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            if e.kind == "user":
                out[e.owner] = out.get(e.owner, 0) + e.size
        return out


@dataclass
class DiskInfo:
    """One physical disk, with the health fields worth surfacing."""

    pool: str
    position: str
    model: str
    sn: str
    dev_type: str
    mount: str
    total_size: int
    free_size: int
    usage_size: int
    health: str = ""
    status: str = ""
    temp: int = 0
    power_on_hours: int = 0
    reallocated_sectors: int = 0
    fragment_pct: float = 0.0
    suspected_smr: bool = False

    @property
    def used_pct(self) -> float:
        return (self.usage_size / self.total_size * 100) if self.total_size else 0.0

    @classmethod
    def from_api(cls, pool: str, d: dict[str, Any]) -> DiskInfo:
        smart = d.get("simple_smart") or {}
        frag = d.get("fs_fragment") or {}
        try:
            frag_pct = float(frag.get("percentage") or 0.0)
        except (TypeError, ValueError):
            frag_pct = 0.0
        return cls(
            pool=pool,
            position=str(d.get("pos", "")),
            model=d.get("model", "") or "",
            sn=d.get("sn", "") or "",
            dev_type=d.get("dev_type", "") or "",
            mount=d.get("mnt", "") or "",
            total_size=_as_int(d.get("total_size")),
            free_size=_as_int(d.get("free_size")),
            usage_size=_as_int(d.get("usage_size")),
            health=d.get("health", "") or "",
            status=d.get("status", "") or "",
            temp=_as_int(d.get("temp")),
            power_on_hours=_as_int(smart.get("power_on_hours")),
            reallocated_sectors=_as_int(smart.get("reallocated_sector_count")),
            fragment_pct=frag_pct,
            suspected_smr=bool(_as_int(d.get("suspected_smr"))),
        )


class ZSpaceError(Exception):
    """Raised when the ZSpace API returns a non-200 code."""

    def __init__(self, code: str, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"[{code}] {msg}")

    @staticmethod
    def diagnose(code: str, msg: str = "") -> str | None:
        """Return an actionable hint for a known error code, or None."""
        low = msg.lower()
        if code in ("401", "403") or "无权限" in msg or "permission" in low:
            return "没有权限 — 检查该账号对目标路径/文件的访问权限"
        if code in ("404",) or "不存在" in msg or "not found" in low:
            return "路径不存在 — 确认路径拼写（可用 zs ls 上级目录）"
        if "已存在" in msg or "exists" in low:
            return "目标已存在 — 换个文件名或用 zs rename"
        if "参数" in msg or "invalid" in low:
            return "参数不合法 — 检查路径/名称是否含非法字符"
        if "busy" in low or "占用" in msg:
            return "文件被占用 — 关闭占用它的程序后重试"
        return None


class ZSpaceClient:
    """High-level client for ZSpace NAS file operations.

    Connects through the local desktop client proxy (127.0.0.1:13579).
    All operations are pure API calls — no SSH or WebDAV needed.
    """

    # NAS API version reported by the desktop client web layer.
    DEFAULT_API_VERSION = "2.3.2026042401"

    def __init__(
        self,
        base_url: str | None = None,
        credentials: Credentials | None = None,
        config_dir: Path | str | None = None,
        api_version: str = DEFAULT_API_VERSION,
        max_retries: int = 2,
        retry_delay: float = 0.25,
        slice_threshold: int = DEFAULT_SLICE_THRESHOLD,
        slice_size: int = DEFAULT_SLICE_SIZE,
    ):
        # base_url defaults to the desktop client proxy, overridable via the
        # ZS_BASE_URL env var (e.g. when running in a container against the host).
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self._creds = credentials or load_credentials(config_dir)
        self.api_version = api_version
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.slice_threshold = slice_threshold
        self.slice_size = slice_size
        # trust_env=False: macOS system HTTP proxy (e.g. Clash :7897) must not
        # intercept 127.0.0.1:13579, or all API calls return empty 502.
        # The download (GET) endpoint authenticates via a full cookie set
        # (token + zenithtoken + nas_id + device_id), so set them all here.
        self._http = httpx.Client(
            base_url=self.base_url,
            cookies={
                "token": self._creds.token,
                "zenithtoken": self._creds.token,
                "nas_id": self._creds.nas_id,
                "nasid": self._creds.nas_id,
                "device_id": self._creds.device_id,
            },
            timeout=60,
            trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ZSpaceClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── internal ──

    def _common_params(self) -> dict[str, str]:
        return {
            "token": self._creds.token,
            "nasid": self._creds.nas_id,
            "plat": "web",
            "version": self.api_version,
            "device_id": self._creds.device_id,
            "_l": "zh_cn",
        }

    def _url(self, endpoint: str) -> str:
        rnd = f"{int(time.time())}{random.randint(1000,9999)}_{random.randint(1000,9999)}"
        return f"{endpoint}?&rnd={rnd}&webagent=v2"

    def _post(self, endpoint: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self._common_params()
        if extra:
            data.update(extra)
        return self._send(
            "POST",
            self._url(endpoint),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def _post_with_array(
        self, endpoint: str, paths: list[str], extra: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """POST with paths[] array parameter (used by move/copy/delete)."""
        params = self._common_params()
        if extra:
            params.update(extra)
        parts = [f"{_urlencode(k)}={_urlencode(v)}" for k, v in params.items()]
        for p in paths:
            parts.append(f"paths%5B%5D={_urlencode(p)}")

        return self._send(
            "POST",
            self._url(endpoint),
            content="&".join(parts),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def _send(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Send a request, validating the response and retrying transient failures.

        Only network errors / 5xx / 429 are retried, with exponential backoff.
        Business errors (ZSpaceError) propagate immediately.
        """
        call = getattr(self._http, method.lower())
        for attempt in range(self.max_retries + 1):
            try:
                resp = call(url, **kwargs)
                return self._check_response(resp)
            except ZSpaceError:
                raise
            except _RETRYABLE_EXC as exc:
                if not self._is_retryable(exc) or attempt >= self.max_retries:
                    raise
                time.sleep(min(self.retry_delay * (2**attempt), 2.0))
        raise RuntimeError("unreachable")

    @staticmethod
    def _check_response(resp: httpx.Response) -> dict[str, Any]:
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != "200":
            raise ZSpaceError(body.get("code", "?"), body.get("msg", "unknown error"))
        return body

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            return status >= 500 or status == 429
        return isinstance(exc, httpx.TransportError)

    @staticmethod
    def _parse_entries(raw: list[Any]) -> list[FileEntry]:
        """Parse a raw API row list, skipping malformed rows."""
        entries: list[FileEntry] = []
        for item in raw:
            try:
                entries.append(FileEntry.from_api(item))
            except (KeyError, TypeError):
                continue
        return entries

    # ── public API ──

    def is_connected(self) -> bool:
        """Check if the ZSpace desktop client proxy is reachable."""
        return check_client_running(self.base_url)

    def client_status(self):
        """Return detailed status of the ZSpace desktop client proxy."""
        from zspace_cli.auth import client_status

        return client_status(self.base_url)

    def pool_info(self) -> dict[str, Any]:
        """Get storage pool information."""
        return self._post("/zspool/info")

    def disk_stats(self) -> dict[str, Any]:
        """Get disk statistics."""
        return self._post("/disk/statics")

    # ── storage introspection ──
    #
    # These answer "where did my space go?" without walking the tree. They are
    # the only way to see data that lives outside ``/<pool>/my/data``: a normal
    # token gets N001411 on ``/recycle``, ``/<pool>/my`` and every other user's
    # space, so a file-level walk systematically under-reports pool usage.

    #: Virtual root that lists the personal recycle bin across all pools.
    RECYCLE_MY = "/.recycle/my"
    #: Virtual root of the public (family/shared) recycle bin.
    RECYCLE_PUBLIC = "/.public_recycle"

    def disk_usage(self) -> dict[str, Any]:
        """Raw ``/system/diskusage3`` payload — physical usage per disk.

        Breaks each disk down by user and by label (``my``, ``my_tm`` for Time
        Machine, ``my_recycle``, ``my_safe``) plus system categories
        (``sys_docker``, ``sys_raid``, ``sys_vm``, ``sys_iscsi``, ``sys_other``).
        The figures are a periodically recomputed snapshot; see
        :meth:`disk_usage_status` and :meth:`disk_usage_refresh`.

        Only reachable from 127.0.0.1, i.e. through the desktop client proxy —
        which is exactly how this SDK talks to the NAS.
        """
        return self._post("/system/diskusage3")

    def disk_usage_status(self) -> dict[str, Any]:
        """``{is_running, updated_at}`` for the disk-usage snapshot."""
        return self._post("/system/diskusage/status")["data"]

    def disk_usage_refresh(self) -> dict[str, Any]:
        """Ask the NAS to recompute the disk-usage snapshot now.

        Asynchronous: ``disk_usage_status()["is_running"]`` flips to 1 and the
        new numbers appear once it finishes.
        """
        return self._post("/system/diskusage/runanyway")

    def usage_summary(self) -> list[PoolUsage]:
        """Per-pool physical usage, summed across disks and grouped by owner.

        ``/system/diskusage3`` reports one entry per *disk*, and each carries
        every user/label/sys row for that disk. Both levels are collapsed here:
        disks are summed into their pool, and rows sharing ``(kind, owner,
        label)`` are merged, so "my Time Machine in sata11" is one number
        rather than one per spindle. Summing reproduces ``/zspool/info``'s
        pool-level ``usage_size`` (verified to within 0.1%).
        """
        payload = self.disk_usage().get("data") or {}
        acc: dict[str, dict[tuple[str, str, str], int]] = {}
        for disk in payload.get("disk_usage") or []:
            name = disk.get("pool_name") or disk.get("pool_key") or "?"
            rows = acc.setdefault(name, {})

            def bump(kind: str, label: str, size: int, owner: str = "") -> None:
                if size:
                    key = (kind, owner, label)
                    rows[key] = rows.get(key, 0) + size

            usage = disk.get("usage_v2") or {}
            for user in usage.get("user") or []:
                owner = (
                    user.get("remark")
                    or user.get("nickname")
                    or user.get("username")
                    or "?"
                )
                if user.get("is_master"):
                    owner += " (主账号)"
                for item in user.get("list") or []:
                    bump("user", item.get("label", ""), _as_int(item.get("phy_size")), owner)
            pub = usage.get("public_recycle") or {}
            if isinstance(pub, dict):
                bump("public_recycle", pub.get("label", "pub_recycle"),
                     _as_int(pub.get("phy_size")))
            for item in usage.get("public") or []:
                bump("public", item.get("label", ""), _as_int(item.get("phy_size")))
            for item in usage.get("sys") or []:
                bump("sys", item.get("label", ""), _as_int(item.get("phy_size")))

        return [
            PoolUsage(
                pool=name,
                entries=sorted(
                    (UsageEntry(kind, label, size, owner)
                     for (kind, owner, label), size in rows.items()),
                    key=lambda e: -e.size,
                ),
            )
            for name, rows in acc.items()
        ]

    def statistic(self, path: str, show_hidden: bool = True) -> DirStat:
        """Server-side size/count of one directory. See :class:`DirStat`.

        Check ``result.complete`` before trusting the numbers: large trees come
        back as ``state="running"`` with a partial snapshot that never
        converges, and there is no working poll endpoint for it.
        """
        return self.statistic_many([path], show_hidden=show_hidden)

    def statistic_many(self, paths: list[str], show_hidden: bool = True) -> DirStat:
        """Server-side statistics for several directories in one request."""
        body = self._post_with_array(
            "/v2/file/statistic",
            list(paths),
            {"show_hidden": "1" if show_hidden else "0"},
        )
        task = (body.get("data") or {}).get("task") or {}
        return DirStat.from_api(", ".join(paths), task)

    def walk_stat(
        self,
        path: str,
        show_hidden: bool = True,
        workers: int = 8,
        max_requests: int = 0,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> DirStat:
        """Recursively sum a subtree via ``/v2/file/list``.

        The reliable fallback for trees too large for :meth:`statistic`. Costs
        about one request per directory — the endpoint hard-caps at 50 entries
        per page no matter what ``limit`` you pass (verified: ``limit=500`` still
        returns 50) — so file count, not byte count, drives the runtime. A
        448k-file / 89k-directory tree needed ~93k requests and 12 minutes at 16
        workers; a 2.1M-file sync folder needed ~285k.

        ``max_requests`` is a safety valve, not a budget to aim for: hitting it
        marks the result ``state="partial"`` so callers cannot mistake a
        truncated total for a real one.
        """
        import threading
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor

        lock = threading.Lock()
        state = {"size": 0, "files": 0, "dirs": 0, "requests": 0, "truncated": False}
        queue: deque[str] = deque([path])
        outstanding = [1]
        cv = threading.Condition()

        def list_dir(target: str) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            start = 0
            while True:
                with lock:
                    state["requests"] += 1
                    n = state["requests"]
                if max_requests and n > max_requests:
                    with lock:
                        state["truncated"] = True
                    return items
                body = self._post(
                    "/v2/file/list",
                    {"path": target, "start": start, "limit": 50,
                     "show_hidden": 1 if show_hidden else 0},
                )
                data = body.get("data") or {}
                page = data.get("list") or []
                items.extend(page)
                start += len(page)
                if not page or start >= _as_int(data.get("total")):
                    return items

        def worker() -> None:
            while True:
                with cv:
                    while not queue and outstanding[0] > 0:
                        cv.wait(0.2)
                    if not queue:
                        if outstanding[0] == 0:
                            return
                        continue
                    target = queue.popleft()
                try:
                    entries = list_dir(target)
                except ZSpaceError:
                    with cv:
                        outstanding[0] -= 1
                        cv.notify_all()
                    continue
                subdirs = []
                fbytes = fcount = 0
                for it in entries:
                    if it.get("is_dir") == "1":
                        subdirs.append(it.get("path") or "")
                        continue
                    fbytes += _as_int(it.get("size"))
                    fcount += 1
                with lock:
                    state["size"] += fbytes
                    state["files"] += fcount
                    state["dirs"] += 1
                if progress is not None:
                    progress(state["requests"], state["files"], state["size"])
                with cv:
                    outstanding[0] += len([s for s in subdirs if s])
                    for s in subdirs:
                        if s:
                            queue.append(s)
                    outstanding[0] -= 1
                    cv.notify_all()

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(lambda _: worker(), range(max(1, workers))))

        return DirStat(
            path=path,
            size=state["size"],
            files=state["files"],
            dirs=state["dirs"],
            state="partial" if state["truncated"] else "done",
            source="walk",
            requests=state["requests"],
        )

    # ── server-side large-file scan ──
    #
    # One shared task slot (info/delete take no id), so a stale task must be
    # cleared before creating a new one. N001307 means "no task exists", which
    # the web client also treats as an acceptable outcome of delete.

    #: Default lower bound for the large-file scan; matches the web client (50 MiB).
    LARGE_MIN_SIZE = 50 * 1024 * 1024
    #: Default upper bound; matches the web client (1 PiB, i.e. effectively none).
    LARGE_MAX_SIZE = 1024 ** 5

    def find_large_create(
        self,
        paths: list[str] | str,
        min_size: int = LARGE_MIN_SIZE,
        max_size: int = LARGE_MAX_SIZE,
        topk: int = 1000,
    ) -> LargeFileScan:
        """Start a server-side scan for large files. Non-blocking.

        All four parameters are required by the server -- omitting ``min_size``,
        ``max_size`` or ``topk`` yields ``N001212 参数有误`` rather than a
        default. ``paths`` is the array form (``paths[]``).
        """
        if isinstance(paths, str):
            paths = [paths]
        body = self._post_with_array(
            "/v2/file/find/large/create",
            list(paths),
            {"min_size": str(min_size), "max_size": str(max_size), "topk": str(topk)},
        )
        return LargeFileScan.from_api(body.get("data") or {})

    def find_large_info(self) -> LargeFileScan:
        """Current large-file scan task. Raises ``N001307`` when none exists."""
        return LargeFileScan.from_api(self._post("/v2/file/find/large/info").get("data") or {})

    def find_large_delete(self) -> dict[str, Any]:
        """Stop/clear the large-file scan task. ``N001307`` means already clear."""
        try:
            return self._post("/v2/file/find/large/delete")
        except ZSpaceError as e:
            if e.code == "N001307":
                return {}
            raise

    def find_large(
        self,
        paths: list[str] | str,
        min_size: int = LARGE_MIN_SIZE,
        max_size: int = LARGE_MAX_SIZE,
        topk: int = 1000,
        wait: bool = True,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> LargeFileScan:
        """Run a large-file scan to completion and return the matches.

        Clears any stale task first, creates a new one, then polls
        :meth:`find_large_info` until ``state != 1`` (1 = scanning, 2 = done).
        Server-side and fast: a 7k-file tree scanned in about 2 seconds, versus
        ~765 requests and ~10 seconds for the equivalent client-side walk.

        The task is left in place on success so callers can re-read it; use
        :meth:`find_large_delete` to clear it.
        """
        self.find_large_delete()
        scan = self.find_large_create(paths, min_size, max_size, topk)
        if not wait:
            return scan
        deadline = time.time() + timeout
        while scan.state == 1 and time.time() < deadline:
            time.sleep(poll_interval)
            scan = self.find_large_info()
        return scan

    # ── recycle bin ──
    #
    # Two independent bins with different endpoints, which is easy to get
    # wrong: emptying the public one does nothing to files deleted from a
    # personal space. Both are verified against a live NAS.

    def recycle_list(
        self,
        path: str | None = None,
        start: int = 0,
        num: int = 200,
        wait: bool = True,
        timeout: float = 120.0,
    ) -> tuple[list[RecycleEntry], int]:
        """List a recycle bin via ``/v2/file/nb/list``.

        The first call kicks off a server-side scan; until it finishes the
        response carries ``scan_info.state != "done"`` with an incomplete list.
        ``wait`` polls until it settles, so callers do not mistake a partial
        listing for an empty bin.

        Returns ``(entries, total)``.
        """
        target = path or self.RECYCLE_MY
        deadline = time.time() + timeout
        data: dict[str, Any] = {}
        while True:
            data = self._post(
                "/v2/file/nb/list", {"path": target, "start": start, "num": num}
            ).get("data") or {}
            scan = (data.get("scan_info") or {}).get("state")
            if not wait or scan in (None, "done") or time.time() >= deadline:
                break
            time.sleep(2)
        entries = [
            RecycleEntry.from_api(i) for i in (data.get("list") or [])
        ]
        return entries, _as_int(data.get("total"))

    def recycle_empty(self, public: bool = False) -> dict[str, Any]:
        """Permanently purge a recycle bin. **Irreversible.**

        ``public=False`` (default) empties the *personal* bin via
        ``/v2/file/rclean``, which takes no parameters and clears the bin of
        every pool at once. ``public=True`` empties the shared/family bin via
        ``/v2/public/recycle/clean`` — a different store; calling it when only
        personal deletions are pending succeeds with ``total_num: 0`` and frees
        nothing.

        Returns the task payload (``total_num``, ``succeed_num``, ``fail_num``,
        ``total_size``) so callers can confirm what was actually purged.
        """
        endpoint = "/v2/public/recycle/clean" if public else "/v2/file/rclean"
        return (self._post(endpoint).get("data") or {}).get("task") or {}

    def recycle_restore(self, paths: list[str]) -> tuple[int, list[str]]:
        """Restore items from the personal bin. Returns ``(restored, failed)``.

        ``paths`` must be the in-bin paths (``/<pool>/.recycle/my/<name>``) as
        reported by :meth:`recycle_list`, not the original locations.

        The endpoint takes a singular ``path`` parameter — ``paths[]`` is
        rejected with N001411 — so multiple items are restored one call each.
        ``/v2/file/mrestore`` exists for batches but its parameter shape is
        unverified, so it is deliberately not used here.
        """
        restored, failed = 0, []
        for p in paths:
            try:
                self._post("/v2/file/restore", {"path": p})
                restored += 1
            except ZSpaceError:
                failed.append(p)
        return restored, failed

    def recycle_purge(self, paths: list[str]) -> tuple[int, list[str]]:
        """Permanently delete *specific* items from the personal bin.

        Returns ``(purged, failed)``. ``paths`` must be in-bin paths
        (``/<pool>/.recycle/my/<name>``) as reported by :meth:`recycle_list`.

        This is the safe alternative to :meth:`recycle_empty`, which is
        all-or-nothing across every pool at once — worth knowing when the bin
        also holds files you did not put there. Mechanically it is a plain
        ``/v2/file/remove`` aimed at the in-bin path, which is what the web
        client does for items ticked inside the recycle bin; verified live to
        drop the target while leaving other items untouched.
        """
        purged, failed = 0, []
        for p in paths:
            try:
                self.remove([p])
                purged += 1
            except ZSpaceError:
                failed.append(p)
        return purged, failed

    def recycle_config(self) -> dict[str, Any]:
        """Retention policy: ``{my_cycle, public_cycle}``.

        ``-1`` means *never* auto-purge, so deleted data keeps occupying the
        pool indefinitely until :meth:`recycle_empty` runs.
        """
        return self._post("/v2/file/recycle/config/get").get("data") or {}

    def recycle_set_config(
        self, my_cycle: int | None = None, public_cycle: int | None = None
    ) -> dict[str, Any]:
        """Set retention (days; ``-1`` = never). Unset fields keep their value."""
        current = self.recycle_config()
        body = {
            "my_cycle": current.get("my_cycle", -1) if my_cycle is None else my_cycle,
            "public_cycle": (
                current.get("public_cycle", -1)
                if public_cycle is None
                else public_cycle
            ),
        }
        self._post("/v2/file/recycle/config/save", body)
        return body

    # ── hardware & health ──

    def disks(self) -> list[DiskInfo]:
        """Every physical disk in every pool, with health and usage."""
        out: list[DiskInfo] = []
        for pool in (self.pool_info().get("data") or {}).get("pool_list") or []:
            name = pool.get("name", "?")
            for d in pool.get("disk_list") or []:
                out.append(DiskInfo.from_api(name, d))
        return out

    def hardware(self) -> dict[str, int]:
        """Slot counts by bus, e.g. ``{"sata": 4, "nvme": 4, "esata": 1}``."""
        return (self._post("/zspool/hardware/info").get("data") or {}).get("slot") or {}

    def free_bays(self) -> dict[str, int]:
        """Empty slots per bus — how many drives can be added without pulling one."""
        slots = self.hardware()
        used: dict[str, int] = {}
        for pool in (self.pool_info().get("data") or {}).get("pool_list") or []:
            bus = "nvme" if pool.get("protocol") == "protnvme" else "sata"
            used[bus] = used.get(bus, 0) + len(pool.get("disk_list") or [])
        return {bus: max(0, total - used.get(bus, 0)) for bus, total in slots.items()}

    def smart(self, sn: str) -> dict[str, Any]:
        """Full SMART attribute report for one disk, addressed by serial number.

        ``/zspool/smart/report2`` keys on ``sn`` — passing ``pool_id`` or a
        ``/dev/*`` name returns N300403 参数错误. Serials come from
        :meth:`disks`.
        """
        return self._post("/zspool/smart/report2", {"sn": sn}).get("data") or {}

    def ls(
        self,
        path: str = "/sata11/my/data",
        show_hidden: bool = False,
        page_size: int = 50,
    ) -> list[FileEntry]:
        """List directory contents, paging through the API until exhausted.

        The ZSpace API returns at most 50 entries per request, so larger
        directories are fetched in a loop.
        """
        entries: list[FileEntry] = []
        start = 0
        while True:
            body = self._post("/v2/file/list", {
                "path": path,
                "show_hidden": "1" if show_hidden else "0",
                "start": str(start),
                "limit": str(page_size),
            })
            page = self._parse_entries(body.get("data", {}).get("list", []))
            entries.extend(page)
            if len(page) < page_size:
                break
            start += len(page)
        return entries

    def info(self, path: str) -> dict[str, Any]:
        """Get detailed file/directory info."""
        return self._post("/v2/file/info", {"path": path})["data"]

    def rename(self, path: str, new_name: str) -> FileEntry:
        """Rename a file or directory."""
        body = self._post("/v2/file/modify", {"path": path, "newname": new_name})
        return FileEntry.from_api(body["data"])

    def mkdir(self, parent: str, name: str) -> FileEntry:
        """Create a new directory."""
        body = self._post("/v2/file/newdir", {"parent": parent, "name": name, "rename": "0"})
        return FileEntry.from_api(body["data"])

    def move(self, paths: list[str] | str, to: str) -> dict[str, Any]:
        """Move files/directories to a destination directory."""
        if isinstance(paths, str):
            paths = [paths]
        return self._post_with_array("/v2/file/move", paths, {"to": to})

    def copy(self, paths: list[str] | str, to: str) -> dict[str, Any]:
        """Copy files/directories to a destination directory."""
        if isinstance(paths, str):
            paths = [paths]
        return self._post_with_array("/v2/file/copy", paths, {"to": to})

    def remove(self, paths: list[str] | str) -> dict[str, Any]:
        """Delete files/directories into the **personal** recycle bin.

        This does not free pool space: the data is moved to
        ``/<pool>/.recycle/my/`` and still counts toward ``usage_size`` until
        :meth:`recycle_empty` purges it. Verified live — deleting 43.6 GiB left
        the pool's used/free figures byte-for-byte unchanged.

        Retention is controlled by :meth:`recycle_config`; the factory default
        ``my_cycle = -1`` means *never* auto-purge, so space stays held
        indefinitely unless something empties the bin. Deleted items remain
        recoverable through :meth:`recycle_restore` until then.

        Note the public/family bin is a separate store — emptying it does not
        touch anything deleted through this method.
        """
        if isinstance(paths, str):
            paths = [paths]
        return self._post_with_array("/v2/file/remove", paths)

    def search(
        self,
        keyword: str,
        path: str = "/sata11/my/data",
        limit: int = 100,
    ) -> list[FileEntry]:
        """Search files across the NAS using the full-text search index.

        Unlike a single-directory client-side filter, this hits the NAS
        ``/file_search/file_search`` endpoint which searches the whole index.
        The ``path`` argument is accepted for CLI compatibility but the
        backend search is global; results are filtered to ``path`` when given.
        """
        body = self._post("/file_search/file_search", {"keyword": keyword})
        raw = body.get("data", {}).get("list", [])
        entries = self._parse_entries(raw)
        if path:
            base = path.rstrip("/")
            entries = [
                e for e in entries if e.path == base or e.path.startswith(base + "/")
            ]
        return entries[:limit]

    def glob(self, pattern: str) -> list[FileEntry]:
        """Match NAS paths against a glob pattern and return matching entries.

        Supports ``*``, ``?``, ``[...]`` and ``**`` (any depth), e.g.::

            client.glob("/sata11/my/data/影视/*.mkv")
            client.glob("/sata11/my/data/**/*.mp4")

        The directory holding the first wildcard is the scan root; the NAS
        directory tree is walked from there. Patterns must be absolute.
        """
        if not pattern.startswith("/"):
            raise ValueError(f"glob pattern must be absolute: {pattern!r}")
        if not any(ch in pattern for ch in "*?["):
            raise ValueError(f"no wildcard in glob pattern: {pattern!r}")

        root = "/"
        for i, ch in enumerate(pattern):
            if ch in "*?[":
                # The wildcard may sit mid-segment (``/dir/2026-1*.log``), so the
                # scan root is the *directory* of the static prefix — not the
                # prefix itself, which would be a non-existent partial path.
                root = pattern[:i].rsplit("/", 1)[0] or "/"
                break
        regex = _glob_to_regex(pattern)
        matches: list[FileEntry] = []

        def walk(path: str) -> None:
            try:
                entries = self.ls(path)
            except ZSpaceError:
                return
            for e in entries:
                if regex.match(e.path):
                    matches.append(e)
                if e.is_dir:
                    walk(e.path)

        walk(root)
        matches.sort(key=lambda e: e.path)
        return matches

    def upload(
        self,
        local_path: Path | str,
        remote_dir: str,
        new_name: str | None = None,
        progress: ProgressCallback | None = None,
        verify: bool = False,
        resume: bool = True,
    ) -> dict[str, Any]:
        """Upload a local file to a directory on the NAS.

        ``local_path``  — local file to upload.
        ``remote_dir`` — destination directory, e.g. ``/sata11/my/data/影视``.
        ``new_name``   — optional target filename (defaults to local basename).
        ``progress``   — optional callback ``(bytes_done, bytes_total)``.
        ``verify``     — download the uploaded file and compare its MD5 with the local file.
        ``resume``     — for sliced uploads, ask the NAS how many bytes it already
                         accepted for this session and skip them (see _tmpinfo_size).

        Small files go through ``/v2/file/create`` in a single request. Large
        files (> ``slice_threshold``) use the desktop client's sliced
        ``/v2/file/upload`` protocol, because the local proxy rejects
        oversized single-request bodies with HTTP 413; a 413 on the create
        path also falls back to sliced upload automatically.
        """
        local = Path(local_path)
        if not local.is_file():
            raise FileNotFoundError(f"本地文件不存在: {local}")
        target_name = new_name or local.name
        target = f"{remote_dir.rstrip('/')}/{target_name}"
        total = local.stat().st_size

        if total > self.slice_threshold:
            result = self._upload_sliced(local, target, total, progress, resume)
            return self._finish_upload(result, local, target, verify)

        last_exc: BaseException | None = None
        create_headers: dict[str, Any] = {
            "Content-Type": "application/octet-stream",
            # Header values must be ASCII; CJK paths crash httpx unless
            # encoded. The proxy accepts raw UTF-8 bytes.
            "path": target.encode("utf-8"),
        }
        with local.open("rb") as fh:
            for attempt in range(self.max_retries + 1):
                fh.seek(0)
                reader = _CountingReader(fh, total, progress)
                try:
                    resp = self._http.post(
                        self._url("/v2/file/create"),
                        content=reader,  # stream the file — don't buffer GBs into memory
                        headers=create_headers,
                    )
                    result = self._check_response(resp).get("data", {})
                    return self._finish_upload(result, local, target, verify)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 413:
                        # body too large for the local proxy — switch to slices
                        result = self._upload_sliced(local, target, total, progress, resume)
                        return self._finish_upload(result, local, target, verify)
                    if not self._is_retryable(exc) or attempt >= self.max_retries:
                        raise
                    last_exc = exc
                    time.sleep(min(self.retry_delay * (2**attempt), 2.0))
                except ZSpaceError:
                    raise
                except _RETRYABLE_EXC as exc:
                    if not self._is_retryable(exc) or attempt >= self.max_retries:
                        raise
                    last_exc = exc
                    time.sleep(min(self.retry_delay * (2**attempt), 2.0))
        raise last_exc  # type: ignore[misc]

    def _finish_upload(
        self,
        result: dict[str, Any],
        local: Path,
        target: str,
        verify: bool,
    ) -> dict[str, Any]:
        """Optionally verify an uploaded file before returning its result."""
        if verify:
            self._verify_upload(local, target)
        return result

    def _verify_upload(self, local: Path, target: str) -> None:
        """Round-trip an uploaded file and compare its MD5 with the local source."""
        import tempfile

        local_md5 = _file_md5(local)
        with tempfile.TemporaryDirectory(prefix="zspace-verify-") as temp_dir:
            downloaded = self.download(target, temp_dir)
            remote_md5 = _file_md5(downloaded)

        if remote_md5 != local_md5:
            raise ZSpaceError(
                "verify",
                f"上传校验失败: MD5 不匹配 (local={local_md5}, remote={remote_md5})",
            )

    def _tmpinfo_size(self, target: str, uuid: str, total: int) -> int:
        """Bytes the NAS already accepted for this upload session, else 0.

        ``GET /v2/file/tmpinfo?path=<target>&uuid=<uuid>`` returns the partial
        upload's temporary entry while a session is open. The desktop client
        reads ``data.size`` as ``finishedSize`` and resumes from it; the temp
        object is a hidden dotfile named ``.<name>.z<session id>`` beside the
        target. Measured against a real NAS: after three 2 MB slices the
        endpoint reports ``size == "6291456"``, and once the upload completes
        the session is consumed and the same query answers ``N001315``
        (文件不存在) — so "no session" and "already finished" are the same
        signal, and both mean start over.

        Any failure here must not fail the upload: resume is an optimisation,
        and re-sending from zero is accepted even when a partial session exists
        (measured: a slice at ``seek=0`` after one slice had landed returns 200
        and the reported size does not go backwards).
        """
        try:
            body = self._send(
                "GET", "/v2/file/tmpinfo", params={"path": target, "uuid": uuid}
            )
        except ZSpaceError:
            return 0
        raw = (body.get("data") or {}).get("size")
        if not isinstance(raw, (str, int)):
            return 0
        try:
            size = int(raw)
        except (TypeError, ValueError):
            return 0
        # Only trust a strict partial offset. size >= total would mean nothing
        # left to send, but the NAS assembles the target only when the final
        # slice lands, so skipping every slice could leave no file at all; fall
        # back to a full re-send, which the measurement above shows is safe.
        return size if 0 < size < total else 0

    def _upload_sliced(
        self,
        local: Path,
        target: str,
        total: int,
        progress: ProgressCallback | None = None,
        resume: bool = True,
    ) -> dict[str, Any]:
        """Sliced upload via /v2/file/upload — the desktop client's protocol.

        Each slice is an independent POST carrying ``uuid`` (upload session),
        ``seek`` (slice offset) and ``split=1``; the NAS assembles the slices
        into the target file once the last one lands. The session ``uuid`` is
        ``md5(mtime_ms + size + target_path)`` — the same formula the desktop
        client uses (JS adds the two numbers, then concatenates the path).
        """
        st = local.stat()
        mtime_ms = math.ceil(st.st_mtime * 1000)
        uuid = _upload_uuid(mtime_ms, total, target)
        modify_time = math.ceil(mtime_ms / 1000)
        sent = self._tmpinfo_size(target, uuid, total) if resume else 0
        sent_resume = sent
        with local.open("rb") as fh:
            if sent:
                fh.seek(sent)
                if progress is not None:
                    progress(sent, total)
            while sent < total:
                length = min(self.slice_size, total - sent)
                body = fh.read(length)
                if len(body) != length:
                    raise ZSpaceError("local", f"本地文件读取不完整: {local}")
                params: dict[str, Any] = {
                    "app": "file",
                    "path": _urlencode(target),
                    "size": total,
                    "uuid": uuid,
                    "seek": sent,
                    "crtime": "",
                    "modify_time": modify_time,
                    "rename": 0,
                    "token": _urlencode(self._creds.token),
                    "plat": "pc",
                    "nasid": self._creds.nas_id,
                    "version": self._creds.app_version,
                    "device_id": self._creds.device_id,
                    "device": _urlencode(self._creds.device),
                    "Content-Length": length,
                    "request-purpose": 4,
                    "remote-port": 8050,
                    "split": 1,
                }
                headers = {k: str(v) for k, v in params.items()}
                headers["Cookie"] = _upload_cookie(params)
                headers["Content-Type"] = "application/octet-stream"
                url = (
                    f"/v2/file/upload?remote_port=8050"
                    f"&drnd={int(time.time() * 1000)}&uuid={uuid}"
                )
                self._post_slice(url, body, headers)
                sent += length
                if progress is not None:
                    progress(sent, total)
        result: dict[str, Any] = {
            "path": target, "name": target.rsplit("/", 1)[-1], "size": total,
        }
        if sent_resume:
            # Only present when bytes were actually skipped, so the common path
            # keeps returning exactly the shape callers (and tests) already see.
            result["resumed_from"] = sent_resume
        return result

    def _post_slice(self, url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """POST one upload slice, retrying transient failures like _send().

        Unlike _send(), non-fatal business error codes are also retried —
        the desktop client re-runs any slice whose code is not in
        _UPLOAD_FATAL_CODES (up to its own retry budget).
        """
        last_exc: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._http.post(url, content=body, headers=headers)
                return self._check_response(resp)
            except ZSpaceError as exc:
                if exc.code in _UPLOAD_FATAL_CODES or attempt >= self.max_retries:
                    raise
                last_exc = exc
            except _RETRYABLE_EXC as exc:
                if not self._is_retryable(exc) or attempt >= self.max_retries:
                    raise
                last_exc = exc
            time.sleep(min(self.retry_delay * (2**attempt), 2.0))
        assert last_exc is not None
        raise last_exc

    def download(
        self,
        remote_path: str,
        local_dir: Path | str,
        local_name: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Download a file from the NAS to a local directory.

        ``progress`` — optional callback ``(bytes_done, bytes_total)``; the total
        is only known when the server sends a ``Content-Length`` header.
        """
        dest = Path(local_dir)
        dest.mkdir(parents=True, exist_ok=True)
        name = local_name or Path(remote_path).name
        out = dest / name
        url = self._url("/v2/file/download")
        params = {"path": remote_path, "remote_port": "8050"}

        last_exc: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._http.stream("GET", url, params=params) as resp:
                    resp.raise_for_status()
                    # ZSpace signals *logical* failures as HTTP 200 plus a JSON
                    # envelope — e.g. {"code":"N001533"} when the path is a
                    # directory, or N001315 when it does not exist. Real file
                    # bodies always come back as application/octet-stream with a
                    # Content-Disposition header, even for .json files, so a JSON
                    # content-type here unambiguously means "error, not content".
                    # Without this the error body gets written to disk as if it
                    # were the file, and download() reports success.
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if ctype.startswith("application/json"):
                        body = resp.read()
                        payload: Any = None
                        try:
                            payload = json.loads(body)
                        except ValueError:
                            pass
                        code = str(payload.get("code", "")) if isinstance(payload, dict) else ""
                        if code and code != "200":
                            msg = (
                                str(payload.get("msg") or "download failed")
                                if isinstance(payload, dict)
                                else "download failed"
                            )
                            raise ZSpaceError(code, msg)
                        # Defensive fallback: a genuine JSON payload with no error
                        # code is treated as the file content.
                        out.write_bytes(body)
                        if progress is not None:
                            progress(len(body), len(body))
                        return out
                    total = int(resp.headers.get("content-length") or 0)
                    downloaded = 0
                    with out.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if progress is not None:
                                progress(downloaded, total)
                return out
            except ZSpaceError:
                out.unlink(missing_ok=True)
                raise
            except _RETRYABLE_EXC as exc:
                out.unlink(missing_ok=True)
                if not self._is_retryable(exc) or attempt >= self.max_retries:
                    raise
                last_exc = exc
                time.sleep(min(self.retry_delay * (2**attempt), 2.0))
        raise last_exc  # type: ignore[misc]

    def tree(self, path: str = "/sata11/my/data", max_depth: int = 2) -> list[dict[str, Any]]:
        """Recursively list directory structure up to max_depth."""
        result: list[dict[str, Any]] = []
        self._tree_walk(path, 0, max_depth, result, set())
        return result

    def _tree_walk(
        self,
        path: str,
        depth: int,
        max_depth: int,
        acc: list[dict[str, Any]],
        seen: set[str],
    ) -> None:
        if depth >= max_depth:
            return
        # skip already-walked dirs (hardlinks / overlapping trees) — saves API calls
        if path in seen:
            return
        seen.add(path)
        try:
            entries = self.ls(path)
        except ZSpaceError:
            return
        for e in entries:
            node: dict[str, Any] = {
                "name": e.name,
                "path": e.path,
                "is_dir": e.is_dir,
                "depth": depth,
            }
            if not e.is_dir:
                node["size"] = e.size
            acc.append(node)
            if e.is_dir:
                self._tree_walk(e.path, depth + 1, max_depth, acc, seen)

    # ── Baidu NetDisk (百度网盘) — /znetdisk/* ──
    #
    # The NAS ships an official Baidu NetDisk module; the desktop proxy exposes
    # it under /znetdisk/* with the same auth (common params + cookie set) and
    # the same {code:"200", msg, data} envelope as the file API — measured
    # live on a real NAS (2026-10, issue #12) — so _post()/_check_response()
    # apply unchanged. Endpoint and parameter shapes come from the NAS-side
    # web app's API module (Vue chunk 64392, module 2934) and its call sites;
    # confidence notes live in skills/zspace-nas/api-reference.md.
    #
    # Deliberately NOT wrapped: the mutating and/or membership-gated endpoints
    # (share/transfer, share/transfer_result, file/download, file/upload,
    # file/newdir, sync/*, autobackup/*, membership/active, order/*,
    # auth/token, auth/logout). Transfer/download are server-gated behind the
    # paid 百度NAS会员 (non-members get code 15, and non-VIP file/download
    # tasks were observed stalled at 0 B/s), and none of them can be verified
    # without changing remote state — they are documented, not automated.

    def baidu_check(self) -> dict[str, Any]:
        """Whether a Baidu account is linked to the NAS's NetDisk module.

        ``POST /znetdisk/auth/check`` with no business params. Returns
        ``data`` — ``is_login`` (bool) and ``url`` (the Baidu OAuth page the
        web UI opens to start linking when ``is_login`` is false; the linking
        flow itself is browser-driven and not wrapped here).
        """
        return self._post("/znetdisk/auth/check")["data"]

    def baidu_userinfo(self) -> dict[str, Any]:
        """Linked Baidu account profile and quota.

        ``POST /znetdisk/auth/userinfo`` with no business params. Returns
        ``data``: ``user_info`` carries ``uk``/``baidu_name``/
        ``netdisk_name``/``avatar_url``/``vip_type`` (Baidu's own
        membership)/``iot_vip_type`` (百度NAS会员 — ``1`` unlocks share
        transfer; the server rejects non-members with code 15)/
        ``iot_vip_end_time``; ``quota`` carries ``used``/``total`` byte
        counts; ``iot_vip_cashier`` is the membership purchase URL. Field
        names measured live; meaningful only when baidu_check() reports
        ``is_login``.
        """
        return self._post("/znetdisk/auth/userinfo")["data"]

    def baidu_ls(self, path: str = "/", page_size: int = 50) -> list[dict[str, Any]]:
        """List a directory of the linked Baidu pan (through the NAS module).

        ``POST /znetdisk/file/list`` with ``{path, page, limit}`` — ``page``
        is 1-based and pages are fetched until a short one, the stop rule the
        web UI uses. Entries are raw Baidu-shaped dicts: ``fs_id``,
        ``server_filename``, ``path``, ``size``, ``isdir`` (int 0/1),
        ``category``, ``server_mtime``. Note the own-pan ``fs_id`` key —
        baidu_share_list() entries spell it ``fsid`` instead.
        """
        entries: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self._post("/znetdisk/file/list", {
                "path": path,
                "page": str(page),
                "limit": str(page_size),
            })
            rows = (body.get("data") or {}).get("list") or []
            entries.extend(rows)
            if len(rows) < page_size:
                break
            page += 1
        return entries

    def baidu_share_verify(self, short_url: str, pwd: str = "") -> str:
        """Verify a Baidu share link server-side; return the verified password.

        ``POST /znetdisk/share/verify`` with ``{short_url, pwd}`` — ``pwd``
        (提取码) travels plaintext; the web UI only trims surrounding
        whitespace before sending. Returns ``data.spwd``, the verified
        password that baidu_share_list() needs in place of the raw ``pwd``.
        """
        body = self._post(
            "/znetdisk/share/verify", {"short_url": short_url, "pwd": pwd}
        )
        return str((body.get("data") or {}).get("spwd", ""))

    def baidu_share_list(
        self,
        short_url: str,
        spwd: str = "",
        path: str = "/",
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """List entries inside a Baidu share link (no local Baidu cookie).

        ``POST /znetdisk/share/filelist`` with ``{short_url, spwd, path,
        page, limit}`` — the NAS verifies and reads the share server-side;
        ``path`` navigates inside the share (root ``/``). Entries carry
        ``fsid`` — NOT the ``fs_id`` key baidu_ls() returns — plus
        ``server_filename``, ``size``, ``md5``, ``isdir``. Pages are fetched
        until a short one.
        """
        entries: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self._post("/znetdisk/share/filelist", {
                "short_url": short_url,
                "spwd": spwd,
                "path": path,
                "page": str(page),
                "limit": str(page_size),
            })
            rows = (body.get("data") or {}).get("list") or []
            entries.extend(rows)
            if len(rows) < page_size:
                break
            page += 1
        return entries

    def baidu_tasks(self, state: str = "", page_size: int = 50) -> list[dict[str, Any]]:
        """List the NetDisk module's transfer tasks (Baidu pan ↔ NAS).

        ``POST /znetdisk/task/list`` with ``{page, limit, state}`` —
        ``state`` filters: ``""`` (all, default) / ``running`` / ``done`` /
        ``pause`` / ``fail``, matching the web UI's tabs. Tasks are raw
        dicts carrying ``task_id``, ``name``, ``down_state`` (1 downloading,
        2 paused, 4 done, 6 queued, 9/10 backup preparing; "retrying" is
        ``down_state == 1`` with ``retry_times > 0``),
        ``download_size``/``total_size``/``rate``, ``save_path``, and the
        failure fields ``fail_num``/``fail_reason``/``baidu_limit``/
        ``space_fulle``. Pages are fetched until a short one.
        """
        entries: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self._post("/znetdisk/task/list", {
                "state": state,
                "page": str(page),
                "limit": str(page_size),
            })
            rows = (body.get("data") or {}).get("list") or []
            entries.extend(rows)
            if len(rows) < page_size:
                break
            page += 1
        return entries

    def baidu_fail_list(
        self, task_id: str | None = None, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """List files that failed inside NetDisk transfer tasks.

        ``POST /znetdisk/fail/list`` with ``{page, limit}`` plus ``task_id``
        to scope to one task (omitted = every task's failures). Entries carry
        ``file_name``, ``file_path``, ``fail_reason``, ``baidu_fail_code``,
        ``advice``, ``retry_times``, ``save_path``, ``task_id``. Pages are
        fetched until a short one.
        """
        entries: list[dict[str, Any]] = []
        page = 1
        while True:
            extra: dict[str, Any] = {"page": str(page), "limit": str(page_size)}
            if task_id is not None:
                extra["task_id"] = task_id
            body = self._post("/znetdisk/fail/list", extra)
            rows = (body.get("data") or {}).get("list") or []
            entries.extend(rows)
            if len(rows) < page_size:
                break
            page += 1
        return entries

    def baidu_task_action(self, method: str, task_id: str | None = None) -> dict[str, Any]:
        """Act on NetDisk transfer tasks — mutates remote task state.

        ``POST /znetdisk/task/action`` with ``{method}`` plus ``task_id``
        for the per-task operations. Methods observed in the web UI:
        ``resume``/``pause``/``clean`` (single task, needs ``task_id``) and
        ``pause_all``/``resume_all``/``clean_all``/``clean_all_done``/
        ``resume_fail_all``/``clean_fail_all`` (bulk, ``task_id`` omitted).
        ``clean*`` deletes task records — no undo. Returns the full response
        body.
        """
        extra: dict[str, Any] = {"method": method}
        if task_id is not None:
            extra["task_id"] = task_id
        return self._post("/znetdisk/task/action", extra)


def _file_md5(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the MD5 digest of a file without loading it all into memory."""
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _urlencode(s: str) -> str:
    from urllib.parse import quote
    return quote(str(s), safe="")


def _upload_uuid(mtime_ms: int, size: int, target: str) -> str:
    """Session uuid for /v2/file/upload.

    The desktop client computes ``md5(file.lastModified + file.size + path)``
    in JavaScript — the two numbers are ADDED first, then the sum is
    string-concatenated with the target path.
    """
    return hashlib.md5(f"{mtime_ms + size}{target}".encode()).hexdigest()


def _upload_cookie(params: dict[str, Any]) -> str:
    """Build the Cookie header for /v2/file/upload from its param dict.

    Mirrors the desktop client's serializer: every param (percent-encoded)
    is duplicated into the Cookie, with ``nasid`` renamed to ``nas_id``.
    """
    parts = []
    for k, v in params.items():
        key = "nas_id" if k == "nasid" else k
        parts.append(f"{key}={_urlencode(str(v))}")
    return "; ".join(parts)


def _glob_to_regex(pattern: str) -> Any:
    """Translate a glob pattern into an anchored regex (POSIX-like).

    ``**`` matches any number of path segments (including none); ``*`` and ``?``
    match within a single segment; ``[...]`` is a character class.
    """
    import re

    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append("(?:.*/)?")
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = i + 1
            while j < n and pattern[j] != "]":
                j += 1
            if j < n:
                out.append("[" + pattern[i + 1 : j].replace("\\", "\\\\") + "]")
                i = j + 1
            else:
                out.append(r"\[")
                i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")
