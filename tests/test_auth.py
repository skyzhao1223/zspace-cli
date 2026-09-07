"""Tests for zspace_cli.auth — credential loading and proxy probing."""

import json
from pathlib import Path

import pytest

from zspace_cli.auth import (
    CONFIG_DIR_ENV,
    Credentials,
    _candidate_dirs,
    client_status,
    load_credentials,
)


def _write_vuex(tmp_path: Path) -> Path:
    payload = {
        "state": {
            "user": {"token": "tok-123", "username": "skyzhao1223"},
            "nas": {"nasId": "Z04A01012A0ZB"},
            "app": {"deviceId": "dev-abc", "version": "1.0"},
        }
    }
    vuex = tmp_path / "vuex.json"
    vuex.write_text(json.dumps(payload), encoding="utf-8")
    return vuex


def test_load_credentials(tmp_path):
    vuex = _write_vuex(tmp_path)
    creds = load_credentials(tmp_path)
    assert isinstance(creds, Credentials)
    assert creds.token == "tok-123"
    assert creds.nas_id == "Z04A01012A0ZB"
    assert creds.device_id == "dev-abc"
    assert creds.username == "skyzhao1223"
    assert vuex.exists()


def test_locate_config_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_credentials(tmp_path / "does-not-exist")


def test_load_credentials_tolerates_no_app(tmp_path):
    payload = {
        "state": {
            "user": {"token": "t"},
            "nas": {"nasId": "N"},
        }
    }
    (tmp_path / "vuex.json").write_text(json.dumps(payload), encoding="utf-8")
    creds = load_credentials(tmp_path)
    assert creds.device_id == ""
    assert creds.username == ""


def test_load_credentials_caches_then_invalidates(tmp_path):
    import time

    from zspace_cli.auth import _CRED_CACHE

    vuex = _write_vuex(tmp_path)
    first = load_credentials(tmp_path)
    assert first.token == "tok-123"
    assert _CRED_CACHE.get(vuex) is not None

    # re-login rewrites vuex.json with a new token -> mtime changes -> cache miss
    time.sleep(0.01)
    payload = json.loads(vuex.read_text(encoding="utf-8"))
    payload["state"]["user"]["token"] = "tok-NEW"
    vuex.write_text(json.dumps(payload), encoding="utf-8")

    second = load_credentials(tmp_path)
    assert second.token == "tok-NEW"
    assert _CRED_CACHE[vuex][2].token == "tok-NEW"


# --- platform-aware config discovery ---


def test_env_var_overrides_default_dir(monkeypatch, tmp_path):
    vuex = _write_vuex(tmp_path)
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    creds = load_credentials()
    assert creds.token == "tok-123"
    assert vuex.exists()


def test_env_var_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        load_credentials()


def test_explicit_config_dir_beats_env(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _write_vuex(tmp_path)
    (other / "vuex.json").write_text(
        json.dumps({"state": {"user": {"token": "tok-B"}, "nas": {"nasId": "N"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_DIR_ENV, str(other))
    creds = load_credentials(tmp_path)
    assert creds.token == "tok-123"


def test_candidate_dirs_windows(monkeypatch):
    monkeypatch.setattr("zspace_cli.auth.sys.platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\u")
    dirs = _candidate_dirs()
    expected = [
        str(Path(r"C:\Users\u\AppData\Roaming") / "zspace"),
        str(Path(r"C:\Users\u\AppData\Local") / "zspace"),
        str(Path(r"C:\Users\u") / "zspace"),
        str(Path(r"C:\Users\u") / "ZSpace"),
    ]
    assert [str(d) for d in dirs] == expected


def test_candidate_dirs_darwin(monkeypatch):
    monkeypatch.setattr("zspace_cli.auth.sys.platform", "darwin")
    dirs = _candidate_dirs()
    assert len(dirs) == 1
    assert dirs[0].name == "zspace"
    assert "Application Support" in str(dirs[0])


def test_locate_config_tries_candidates(monkeypatch, tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    _write_vuex(good)
    monkeypatch.setattr(
        "zspace_cli.auth._candidate_dirs",
        lambda: [tmp_path / "miss-a", good, tmp_path / "miss-b"],
    )
    found = load_credentials()
    assert found.token == "tok-123"


def test_load_credentials_tolerates_utf8_bom(tmp_path):
    # Windows tooling often writes JSON with a UTF-8 BOM
    payload = json.dumps({
        "state": {
            "user": {"token": "tok-bom"},
            "nas": {"nasId": "N"},
            "app": {"deviceId": "d"},
        }
    })
    (tmp_path / "vuex.json").write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    creds = load_credentials(tmp_path)
    assert creds.token == "tok-bom"


def test_client_status_ok(monkeypatch):
    class _Resp:
        status_code = 200

    def fake_get(url, timeout=3, trust_env=False):  # noqa: ARG001
        return _Resp()

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is True


def test_client_status_connect_error(monkeypatch):
    import httpx

    def fake_get(url, timeout=3, trust_env=False):  # noqa: ARG001
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is False
    assert "未运行" in status.reason or "无法连接" in status.reason


def test_client_status_timeout(monkeypatch):
    import httpx

    def fake_get(url, timeout=3, trust_env=False):  # noqa: ARG001
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is False
    assert "超时" in status.reason


def test_client_status_other_status(monkeypatch):
    class _Resp:
        status_code = 502

    def fake_get(url, timeout=3, trust_env=False):  # noqa: ARG001
        return _Resp()

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is False
