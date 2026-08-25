"""Authentication helpers — reads credentials from the ZSpace desktop client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    token: str
    nas_id: str
    device_id: str
    username: str = ""


_DEFAULT_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "zspace"
_VUEX_FILENAME = "vuex.json"


def locate_config(config_dir: Path | str | None = None) -> Path:
    """Return the path to vuex.json, raising FileNotFoundError if missing."""
    d = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    vuex = d / _VUEX_FILENAME
    if not vuex.exists():
        raise FileNotFoundError(
            f"极空间客户端配置未找到: {vuex}\n"
            "请确认已安装并登录极空间桌面客户端。"
        )
    return vuex


def load_credentials(config_dir: Path | str | None = None) -> Credentials:
    """Load auth credentials from the ZSpace desktop client config."""
    vuex_path = locate_config(config_dir)
    data = json.loads(vuex_path.read_text(encoding="utf-8"))

    state = data.get("state", data)
    user = state["user"]
    nas = state["nas"]
    app = state.get("app", {})

    return Credentials(
        token=user["token"],
        nas_id=nas["nasId"],
        device_id=app.get("deviceId", ""),
        username=user.get("username", ""),
    )


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
        r = httpx.get(f"{base_url}/home/", timeout=3)
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
