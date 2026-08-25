"""Tests for zspace_cli.auth — credential loading and proxy probing."""

import json
from pathlib import Path

import pytest

from zspace_cli.auth import (
    Credentials,
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


def test_client_status_ok(monkeypatch):
    class _Resp:
        status_code = 200

    def fake_get(url, timeout=3):  # noqa: ARG001
        return _Resp()

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is True


def test_client_status_connect_error(monkeypatch):
    import httpx

    def fake_get(url, timeout=3):  # noqa: ARG001
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is False
    assert "未运行" in status.reason or "无法连接" in status.reason


def test_client_status_timeout(monkeypatch):
    import httpx

    def fake_get(url, timeout=3):  # noqa: ARG001
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is False
    assert "超时" in status.reason


def test_client_status_other_status(monkeypatch):
    class _Resp:
        status_code = 502

    def fake_get(url, timeout=3):  # noqa: ARG001
        return _Resp()

    monkeypatch.setattr("httpx.get", fake_get)
    status = client_status()
    assert status.ok is False
