"""Authentication helpers — reads credentials from the ZSpace desktop client."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    token: str
    nas_id: str
    device_id: str
    username: str = ""


# Env var to override the config directory (helpful when auto-detection
# misses an installation location on Windows/Linux).
CONFIG_DIR_ENV = "ZS_CONFIG_DIR"
_VUEX_FILENAME = "vuex.json"

# Cache loaded credentials keyed by (mtime_ns, size) of vuex.json so that
# long-lived processes (e.g. the MCP server, which builds a ZSpaceClient per
# tool call) don't re-read + re-parse the file on every operation.
_CRED_CACHE: dict[Path, tuple[int, int, Credentials]] = {}


def _candidate_dirs() -> list[Path]:
    """Candidate config directories for the ZSpace desktop client, most likely first.

    The desktop client stores its login state (``vuex.json``) in a
    platform-specific location; on macOS it's
    ``~/Library/Application Support/zspace``. Windows/Linux paths are best-effort
    guesses — override with ``ZS_CONFIG_DIR`` when needed.
    """
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        local = os.environ.get("LOCALAPPDATA")
        userprofile = os.environ.get("USERPROFILE")
        dirs: list[Path] = []
        for base in (appdata, local):
            if base:
                dirs.append(Path(base) / "zspace")
        if userprofile:
            dirs.append(Path(userprofile) / "zspace")
            dirs.append(Path(userprofile) / "ZSpace")
        return dirs or [home / "zspace"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "zspace"]
    # linux / other: flatpak, XDG, and home-dir installs
    return [
        home / ".zspace",
        home / ".config" / "zspace",
        home / "zspace",
    ]


def _cache_stamp(vuex_path: Path) -> tuple[int, int] | None:
    try:
        st = vuex_path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def locate_config(config_dir: Path | str | None = None) -> Path:
    """Return the path to vuex.json, raising FileNotFoundError if missing.

    Resolution order: explicit ``config_dir`` argument, then the
    ``ZS_CONFIG_DIR`` env var, then platform-specific default locations.
    """
    if config_dir:
        dirs = [Path(config_dir)]
    else:
        env = os.environ.get(CONFIG_DIR_ENV)
        dirs = [Path(env)] if env else _candidate_dirs()
    for d in dirs:
        vuex = d / _VUEX_FILENAME
        if vuex.exists():
            return vuex
    tried = "\n".join(f"  - {d / _VUEX_FILENAME}" for d in dirs)
    raise FileNotFoundError(
        f"极空间客户端配置未找到，已尝试:\n{tried}\n"
        "请确认已安装并登录极空间桌面客户端；"
        f"或用环境变量 {CONFIG_DIR_ENV} 指定配置目录。"
    )


def load_credentials(config_dir: Path | str | None = None) -> Credentials:
    """Load auth credentials from the ZSpace desktop client config.

    Results are cached per file and invalidated when vuex.json changes
    (re-login, token refresh), avoiding a disk read on every call.
    """
    vuex_path = locate_config(config_dir)
    stamp = _cache_stamp(vuex_path)
    if stamp is not None:
        cached = _CRED_CACHE.get(vuex_path)
        if cached is not None and cached[:2] == stamp:
            return cached[2]

    data = json.loads(vuex_path.read_text(encoding="utf-8"))

    state = data.get("state", data)
    user = state["user"]
    nas = state["nas"]
    app = state.get("app", {})

    creds = Credentials(
        token=user["token"],
        nas_id=nas["nasId"],
        device_id=app.get("deviceId", ""),
        username=user.get("username", ""),
    )

    if stamp is not None:
        _CRED_CACHE[vuex_path] = (stamp[0], stamp[1], creds)
    return creds


def check_client_running(base_url: str = "http://127.0.0.1:13579") -> bool:
    """Quick check if the ZSpace desktop client proxy is reachable."""
    return client_status(base_url).ok


@dataclass(frozen=True)
class ClientStatus:
    """Detailed status of the ZSpace desktop client proxy."""

    ok: bool
    reason: str = ""

    def __str__(self) -> str:
        return f"{'ok' if self.ok else 'not-ok'}: {self.reason}"


def client_status(base_url: str = "http://127.0.0.1:13579") -> ClientStatus:
    """Probe the local desktop client proxy and explain failures.

    Distinguishes "client not running" (connection refused) from "port in
    use by something else" (connection succeeded but not the ZSpace proxy).
    """
    import httpx

    try:
        # trust_env=False: match ZSpaceClient — the macOS system proxy must not
        # intercept the local desktop client port (127.0.0.1:13579).
        r = httpx.get(f"{base_url}/home/", timeout=3, trust_env=False)
    except httpx.ConnectError:
        return ClientStatus(
            False,
            f"无法连接 {base_url}（极空间桌面客户端可能未运行）",
        )
    except httpx.TimeoutException:
        return ClientStatus(False, f"{base_url} 连接超时（客户端可能卡住）")
    if r.status_code < 500:
        return ClientStatus(True)
    return ClientStatus(False, f"{base_url} 返回 HTTP {r.status_code}（端口可能被其他程序占用）")
