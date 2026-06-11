"""Authentication helpers — reads credentials from the ZSpace desktop client."""

from __future__ import annotations

import json
import os
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
    import httpx

    try:
        r = httpx.get(f"{base_url}/home/", timeout=3)
        return r.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException):
        return False
