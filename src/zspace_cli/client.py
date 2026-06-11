"""Core SDK client for the ZSpace NAS API."""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from zspace_cli.auth import Credentials, check_client_running, load_credentials


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
        return cls(
            name=d["name"],
            path=d["path"],
            is_dir=d.get("is_dir", "0") == "1",
            size=int(d.get("size", 0)),
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


class ZSpaceClient:
    """High-level client for ZSpace NAS file operations.

    Connects through the local desktop client proxy (127.0.0.1:13579).
    All operations are pure API calls — no SSH or WebDAV needed.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:13579",
        credentials: Credentials | None = None,
        config_dir: Path | str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._creds = credentials or load_credentials(config_dir)
        self._http = httpx.Client(
            base_url=self.base_url,
            cookies={"token": self._creds.token},
            timeout=30,
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
            "version": "2.3.2026042401",
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
        resp = self._http.post(
            self._url(endpoint),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != "200":
            raise ZSpaceError(body.get("code", "?"), body.get("msg", "unknown error"))
        return body

    def _post_with_array(
        self, endpoint: str, paths: list[str], extra: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """POST with paths[] array parameter (used by move/copy/delete)."""
        params = self._common_params()
        if extra:
            params.update(extra)
        parts: list[str] = []
        for k, v in params.items():
            parts.append(f"{_urlencode(k)}={_urlencode(v)}")
        for p in paths:
            parts.append(f"paths%5B%5D={_urlencode(p)}")
        body_str = "&".join(parts)

        resp = self._http.post(
            self._url(endpoint),
            content=body_str,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != "200":
            raise ZSpaceError(body.get("code", "?"), body.get("msg", "unknown error"))
        return body

    # ── public API ──

    def is_connected(self) -> bool:
        """Check if the ZSpace desktop client proxy is reachable."""
        return check_client_running(self.base_url)

    def pool_info(self) -> dict[str, Any]:
        """Get storage pool information."""
        return self._post("/zspool/info")

    def disk_stats(self) -> dict[str, Any]:
        """Get disk statistics."""
        return self._post("/disk/statics")

    def ls(self, path: str = "/sata11/my/data", show_hidden: bool = False) -> list[FileEntry]:
        """List directory contents."""
        body = self._post("/v2/file/list", {
            "path": path,
            "show_hidden": "1" if show_hidden else "0",
        })
        return [FileEntry.from_api(f) for f in body["data"]["list"]]

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

    def search(self, keyword: str, path: str = "/sata11/my/data") -> list[FileEntry]:
        """Search for files by name within a directory (client-side filtering)."""
        entries = self.ls(path, show_hidden=True)
        kw = keyword.lower()
        return [e for e in entries if kw in e.name.lower()]

    def tree(self, path: str = "/sata11/my/data", max_depth: int = 2) -> list[dict[str, Any]]:
        """Recursively list directory structure up to max_depth."""
        result: list[dict[str, Any]] = []
        self._tree_walk(path, 0, max_depth, result)
        return result

    def _tree_walk(
        self, path: str, depth: int, max_depth: int, acc: list[dict[str, Any]]
    ) -> None:
        if depth >= max_depth:
            return
        try:
            entries = self.ls(path)
        except ZSpaceError:
            return
        for e in entries:
            node: dict[str, Any] = {"name": e.name, "path": e.path, "is_dir": e.is_dir, "depth": depth}
            if not e.is_dir:
                node["size"] = e.size
            acc.append(node)
            if e.is_dir:
                self._tree_walk(e.path, depth + 1, max_depth, acc)


def _urlencode(s: str) -> str:
    from urllib.parse import quote
    return quote(str(s), safe="")
