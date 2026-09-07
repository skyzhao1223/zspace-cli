"""Core SDK client for the ZSpace NAS API."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
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
    ):
        # base_url defaults to the desktop client proxy, overridable via the
        # ZS_BASE_URL env var (e.g. when running in a container against the host).
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self._creds = credentials or load_credentials(config_dir)
        self.api_version = api_version
        self.max_retries = max_retries
        self.retry_delay = retry_delay
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
        """Delete files/directories (moves to trash)."""
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

        The static prefix before the first wildcard is the scan root; the NAS
        directory tree is walked from there. Patterns must be absolute.
        """
        if not pattern.startswith("/"):
            raise ValueError(f"glob pattern must be absolute: {pattern!r}")
        if not any(ch in pattern for ch in "*?["):
            raise ValueError(f"no wildcard in glob pattern: {pattern!r}")

        root = pattern
        for i, ch in enumerate(pattern):
            if ch in "*?[":
                root = pattern[:i].rstrip("/") or "/"
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
    ) -> dict[str, Any]:
        """Upload a local file to a directory on the NAS.

        ``local_path``  — local file to upload.
        ``remote_dir`` — destination directory, e.g. ``/sata11/my/data/影视``.
        ``new_name``   — optional target filename (defaults to local basename).
        ``progress``   — optional callback ``(bytes_done, bytes_total)``.
        """
        local = Path(local_path)
        if not local.is_file():
            raise FileNotFoundError(f"本地文件不存在: {local}")
        target_name = new_name or local.name
        target = f"{remote_dir.rstrip('/')}/{target_name}"
        total = local.stat().st_size

        last_exc: BaseException | None = None
        with local.open("rb") as fh:
            for attempt in range(self.max_retries + 1):
                fh.seek(0)
                reader = _CountingReader(fh, total, progress)
                try:
                    resp = self._http.post(
                        self._url("/v2/file/create"),
                        content=reader,  # stream the file — don't buffer GBs into memory
                        headers={
                            "Content-Type": "application/octet-stream",
                            "path": target,
                        },
                    )
                    return self._check_response(resp).get("data", {})
                except ZSpaceError:
                    raise
                except _RETRYABLE_EXC as exc:
                    if not self._is_retryable(exc) or attempt >= self.max_retries:
                        raise
                    last_exc = exc
                    time.sleep(min(self.retry_delay * (2**attempt), 2.0))
        raise last_exc  # type: ignore[misc]

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
                raise
            except _RETRYABLE_EXC as exc:
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


def _urlencode(s: str) -> str:
    from urllib.parse import quote
    return quote(str(s), safe="")


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
