import json
from pathlib import Path

import pytest

from zspace_cli.auth import load_base_url


def test_load_base_url_reads_local_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "zspace"
    config_dir.mkdir()
    (config_dir / "vuex.json").write_text(
        json.dumps({"state": {"app": {"localPort": 13580}}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("ZSPACE_BASE_URL", raising=False)
    assert load_base_url(config_dir) == "http://127.0.0.1:13580"


def test_load_base_url_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "zspace"
    config_dir.mkdir()
    (config_dir / "vuex.json").write_text(
        json.dumps({"state": {"app": {"localPort": 13580}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZSPACE_BASE_URL", "http://127.0.0.1:19999")
    assert load_base_url(config_dir) == "http://127.0.0.1:19999"


def test_load_base_url_default_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "zspace"
    config_dir.mkdir()
    (config_dir / "vuex.json").write_text(json.dumps({"state": {"app": {}}}), encoding="utf-8")
    monkeypatch.delenv("ZSPACE_BASE_URL", raising=False)
    assert load_base_url(config_dir) == "http://127.0.0.1:13579"
