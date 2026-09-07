"""Tests for zspace_cli.cli — Typer commands via CliRunner with a mocked client."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from zspace_cli.cli import _build_rich_tree, _size_str, app
from zspace_cli.client import FileEntry


def test_size_str():
    assert _size_str(0) == "0 B"
    assert _size_str(512) == "512 B"
    assert _size_str(2048) == "2.0 KB"
    assert _size_str(5 * 1024 * 1024) == "5.0 MB"
    assert _size_str(2 * 1024**3) == "2.0 GB"


def test_size_str_boundary():
    assert _size_str(1024) == "1.0 KB"
    assert _size_str(1024 * 1024) == "1.0 MB"
    assert _size_str(1024**3) == "1.0 GB"


def _entry(name, path, is_dir=False, size=10):
    return FileEntry(name=name, path=path, is_dir=is_dir, size=size)


def _run_cmd(*args, client_overrides=None):
    """Invoke a CLI command capturing Rich console output into a StringIO."""
    c = MagicMock()
    c.__enter__.return_value = c
    c.client_status.return_value = type("S", (), {"ok": True, "reason": ""})()
    c.ls.return_value = [_entry("a.txt", "/d/a.txt"), _entry("sub", "/d/sub", is_dir=True)]
    c.info.return_value = {
        "name": "a.txt", "path": "/d/a.txt", "is_dir": "0",
        "size": "1234", "modify_time": "2026-01-01 00:00:00",
    }
    c.rename.return_value = _entry("b.txt", "/d/b.txt")
    c.mkdir.return_value = _entry("new", "/d/new", is_dir=True)
    c.search.return_value = [_entry("hit.mkv", "/d/hit.mkv")]
    c.tree.return_value = [
        {"name": "d", "path": "/d", "is_dir": True, "depth": 0, "size": 0},
        {"name": "a.txt", "path": "/d/a.txt", "is_dir": False, "depth": 1, "size": 10},
    ]
    c.upload.return_value = {"path": "/d/remote/a.txt"}
    c.download.return_value = "/tmp/a.txt"
    if client_overrides:
        for k, v in client_overrides.items():
            parts = k.split(".")
            obj = c
            for part in parts[:-1]:
                obj = getattr(obj, part)
            setattr(obj, parts[-1], v)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    with patch("zspace_cli.cli.console", console), \
            patch("zspace_cli.cli._client", return_value=c):
        r = CliRunner().invoke(app, list(args))
    return r, buf.getvalue(), c


def test_cli_check_ok():
    pool = {"data": {"pool_list": [
        {"name": "池1", "total_size": 8 * 1024**4, "free_size": 4 * 1024**4}
    ]}}
    r, out, _ = _run_cmd("check", client_overrides={"pool_info.return_value": pool})
    assert r.exit_code == 0
    assert "已连接" in out


def test_cli_check_not_connected():
    fake = type("S", (), {"ok": False, "reason": "not logged in"})
    r, out, _ = _run_cmd("check", client_overrides={"client_status.return_value": fake()})
    assert r.exit_code == 1
    assert "not logged in" in out


def test_cli_check_pool_error_ignored():
    from zspace_cli.client import ZSpaceError

    r, out, _ = _run_cmd(
        "check", client_overrides={"pool_info.side_effect": ZSpaceError("500", "pool failed")}
    )
    assert r.exit_code == 0
    assert "已连接" in out


def test_cli_ls():
    r, out, c = _run_cmd("ls", "/d")
    assert r.exit_code == 0
    assert "a.txt" in out
    assert "sub" in out
    assert "共 2 项" in out
    c.ls.assert_called_once_with("/d", show_hidden=False)


def test_cli_ls_hidden():
    r, out, c = _run_cmd("ls", "/d", "--hidden")
    assert r.exit_code == 0
    c.ls.assert_called_once_with("/d", show_hidden=True)


def test_cli_ls_long():
    r, out, _ = _run_cmd("ls", "/d", "--long")
    assert r.exit_code == 0
    assert "类型" in out
    assert "大小" in out


def test_cli_ls_error():
    from zspace_cli.client import ZSpaceError

    r, out, _ = _run_cmd(
        "ls", "/d", client_overrides={"ls.side_effect": ZSpaceError("500", "ls failed")}
    )
    assert r.exit_code == 1
    assert "ls failed" in out


def test_cli_info():
    r, out, _ = _run_cmd("info", "/d/a.txt")
    assert r.exit_code == 0
    assert "/d/a.txt" in out
    assert "文件" in out


def test_cli_info_dir():
    r, out, _ = _run_cmd(
        "info", "/d/sub",
        client_overrides={
            "info.return_value": {"name": "sub", "path": "/d/sub", "is_dir": "1"}
        },
    )
    assert r.exit_code == 0
    assert "目录" in out


def test_cli_rename():
    r, out, _ = _run_cmd("rename", "/d/a.txt", "b.txt")
    assert r.exit_code == 0
    assert "b.txt" in out


def test_cli_mv():
    r, _, c = _run_cmd("mv", "/d/a.txt", "/d/other")
    assert r.exit_code == 0
    c.move.assert_called_once_with(["/d/a.txt"], "/d/other")


def test_cli_cp():
    r, _, c = _run_cmd("cp", "/d/a.txt", "/d/other")
    assert r.exit_code == 0
    c.copy.assert_called_once_with(["/d/a.txt"], "/d/other")


def test_cli_mkdir():
    r, out, _ = _run_cmd("mkdir", "/d", "new")
    assert r.exit_code == 0
    assert "已创建" in out


def test_cli_rm_force():
    r, _, c = _run_cmd("rm", "/d/a.txt", "--force")
    assert r.exit_code == 0
    c.remove.assert_called_once_with(["/d/a.txt"])


def test_cli_find_hits():
    r, out, _ = _run_cmd("find", "hit")
    assert r.exit_code == 0
    assert "hit.mkv" in out
    assert "找到 1 项" in out


def test_cli_find_no_results():
    r, out, _ = _run_cmd("find", "nope", client_overrides={"search.return_value": []})
    assert r.exit_code == 0
    assert "未找到匹配" in out


def test_cli_tree():
    r, out, _ = _run_cmd("tree", "/d", "--depth", "2")
    assert r.exit_code == 0
    assert "d/" in out


def test_cli_up():
    local = Path("/tmp/some-local-file.txt")
    r, out, _ = _run_cmd("up", str(local), "/d")
    assert r.exit_code == 0
    assert "已上传" in out


def test_cli_down():
    r, out, _ = _run_cmd("down", "/d/a.txt", "/tmp")
    assert r.exit_code == 0
    assert "已下载" in out


def test_cli_skill(tmp_path, monkeypatch):
    import zspace_cli.cli as cli

    fake_data = tmp_path / "skills"
    (fake_data / "zspace-nas").mkdir(parents=True)
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "cli.py"))
    target = tmp_path / "proj" / "skills"
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    with patch("zspace_cli.cli.console", console), \
            patch("zspace_cli.cli._client", return_value=MagicMock()):
        r = CliRunner().invoke(app, ["skill", str(target)])
    assert r.exit_code == 0
    assert "已复制 1 个 skill" in buf.getvalue()
    assert (target / "zspace-nas").is_dir()


def test_build_rich_tree():
    from rich.tree import Tree

    root = Tree("root")
    nodes = [
        {"name": "a", "is_dir": True, "depth": 0, "size": 0},
        {"name": "b.txt", "is_dir": False, "depth": 1, "size": 2048},
    ]
    _build_rich_tree(root, nodes, 0)
    labels = [str(child.label) for child in root.children]
    assert any("a/" in label for label in labels)
    nested = list(root.children)[0]
    assert any("b.txt" in str(child.label) for child in nested.children)


@pytest.mark.parametrize("args,method,excmsg", [
    (["info", "/x"], "info.side_effect", "info failed"),
    (["rename", "/a", "b"], "rename.side_effect", "rename failed"),
    (["mv", "/a", "/d"], "move.side_effect", "move failed"),
    (["cp", "/a", "/d"], "copy.side_effect", "copy failed"),
    (["mkdir", "/d", "n"], "mkdir.side_effect", "mkdir failed"),
    (["rm", "/a", "--force"], "remove.side_effect", "remove failed"),
    (["find", "kw"], "search.side_effect", "search failed"),
    (["tree", "/d"], "tree.side_effect", "tree failed"),
    (["down", "/a", "/tmp"], "download.side_effect", "down failed"),
])
def test_cli_error_branches(args, method, excmsg):
    from zspace_cli.client import ZSpaceError

    r, out, _ = _run_cmd(*args, client_overrides={method: ZSpaceError("500", excmsg)})
    assert r.exit_code == 1
    assert excmsg in out


def test_cli_up_missing_file():

    r, out, _ = _run_cmd(
        "up", "/nonexistent/x.txt", "/d",
        client_overrides={"upload.side_effect": FileNotFoundError("本地文件不存在")},
    )
    assert r.exit_code == 1
    assert "不存在" in out


# --- glob expansion in rm/mv/cp/down ---


def _mkv_entries():
    return [
        FileEntry("a.mkv", "/d/a.mkv", False),
        FileEntry("b.mkv", "/d/b.mkv", False),
    ]


def test_cli_rm_glob_expands():
    r, _, c = _run_cmd(
        "rm", "/d/*.mkv", "--force",
        client_overrides={"glob.return_value": _mkv_entries()},
    )
    assert r.exit_code == 0
    c.remove.assert_called_once_with(["/d/a.mkv", "/d/b.mkv"])


def test_cli_mv_glob_expands():
    r, _, c = _run_cmd(
        "mv", "/d/*.mkv", "/d/other",
        client_overrides={"glob.return_value": _mkv_entries()},
    )
    assert r.exit_code == 0
    c.move.assert_called_once_with(["/d/a.mkv", "/d/b.mkv"], "/d/other")


def test_cli_cp_glob_expands():
    r, _, c = _run_cmd(
        "cp", "/d/*.mkv", "/d/other",
        client_overrides={"glob.return_value": _mkv_entries()},
    )
    assert r.exit_code == 0
    c.copy.assert_called_once_with(["/d/a.mkv", "/d/b.mkv"], "/d/other")


def test_cli_down_glob_expands(tmp_path):
    r, out, c = _run_cmd(
        "down", "/d/*.mkv", str(tmp_path),
        client_overrides={
            "glob.return_value": [FileEntry("a.mkv", "/d/a.mkv", False)],
            "download.return_value": tmp_path / "a.mkv",
        },
    )
    assert r.exit_code == 0
    c.download.assert_called_once()
    assert "已下载" in out


def test_cli_rm_glob_no_match():
    r, out, c = _run_cmd(
        "rm", "/d/*.mkv", "--force",
        client_overrides={"glob.return_value": []},
    )
    assert r.exit_code == 0
    assert "没有匹配" in out
    c.remove.assert_not_called()


def test_cli_glob_error_branch():
    from zspace_cli.client import ZSpaceError

    r, out, _ = _run_cmd(
        "mv", "/d/*.mkv", "/d/other",
        client_overrides={"glob.side_effect": ZSpaceError("500", "glob failed")},
    )
    assert r.exit_code == 1
    assert "glob failed" in out


# --- --config-dir / ZS_CONFIG_DIR ---


def _fake_client_factory(captured):
    def _fake(*, config_dir=None):
        captured["config_dir"] = config_dir
        c = MagicMock()
        c.__enter__.return_value = c
        c.ls.return_value = []
        return c

    return _fake


def test_cli_config_dir_flag(tmp_path):
    import zspace_cli.cli as cli

    captured = {}
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    with patch("zspace_cli.cli.console", console), patch(
        "zspace_cli.cli.ZSpaceClient",
        side_effect=_fake_client_factory(captured),
    ):
        r = CliRunner().invoke(cli.app, ["--config-dir", str(tmp_path), "ls", "/d"])
    assert r.exit_code == 0
    assert captured["config_dir"] == str(tmp_path)


def test_cli_config_dir_env_var(tmp_path):
    import zspace_cli.cli as cli

    captured = {}
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    with patch("zspace_cli.cli.console", console), patch(
        "zspace_cli.cli.ZSpaceClient",
        side_effect=_fake_client_factory(captured),
    ):
        r = CliRunner().invoke(
            cli.app,
            ["ls", "/d"],
            env={"ZS_CONFIG_DIR": str(tmp_path)},
        )
    assert r.exit_code == 0
    assert captured["config_dir"] == str(tmp_path)


def test_cli_config_dir_missing_shows_tried_paths(tmp_path):
    import zspace_cli.cli as cli

    nope = tmp_path / "nope"
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=300)
    with patch("zspace_cli.cli.console", console):
        r = CliRunner().invoke(cli.app, ["--config-dir", str(nope), "check"])
    assert r.exit_code == 1
    assert "配置未找到" in buf.getvalue()
    assert str(nope) in buf.getvalue()


def test_cli_ls_json():
    import json as _json

    r, out, _ = _run_cmd("ls", "/d", "--json")
    assert r.exit_code == 0
    data = _json.loads(out)
    assert data[0]["name"] == "a.txt"
    assert data[0]["is_dir"] is False


def test_cli_check_json_ok():
    import json as _json

    pool = {"data": {"pool_list": [
        {"name": "池1", "total_size": 8 * 1024**4, "free_size": 4 * 1024**4}
    ]}}
    r, out, _ = _run_cmd("check", "--json", client_overrides={"pool_info.return_value": pool})
    assert r.exit_code == 0
    data = _json.loads(out)
    assert data["ok"] is True
    assert data["pools"][0]["name"] == "池1"


def test_cli_find_json():
    import json as _json

    r, out, _ = _run_cmd("find", "hit", "--json")
    assert r.exit_code == 0
    data = _json.loads(out)
    assert data[0]["name"] == "hit.mkv"


def test_cli_tree_json():
    import json as _json

    r, out, _ = _run_cmd("tree", "/d", "--json")
    assert r.exit_code == 0
    data = _json.loads(out)
    assert data[0]["name"] == "d"


def test_cli_info_json():
    import json as _json

    r, out, _ = _run_cmd("info", "/d/a.txt", "--json")
    assert r.exit_code == 0
    data = _json.loads(out)
    assert data["path"] == "/d/a.txt"
