"""Tests for zspace_cli.mcp_server — MCP tool functions with a mocked client."""

import asyncio
from unittest.mock import patch

from zspace_cli.client import FileEntry
from zspace_cli.mcp_server import _err, _ok


def _mock_client(**overrides):
    """A MagicMock ZSpaceClient context manager with sensible defaults."""
    from unittest.mock import MagicMock

    c = MagicMock()
    c.__enter__.return_value = c
    c.is_connected.return_value = True
    c.pool_info.return_value = {
        "data": {"pool_list": [{
            "name": "池1", "total_size": 8 * 1024**4, "free_size": 4 * 1024**4
        }]}
    }
    c.ls.return_value = [FileEntry("a.txt", "/d/a.txt", False, 10)]
    c.info.return_value = {"name": "a.txt", "path": "/d/a.txt", "is_dir": "0"}
    c.rename.return_value = FileEntry("b.txt", "/d/b.txt", False)
    c.mkdir.return_value = FileEntry("new", "/d/new", True)
    c.search.return_value = [FileEntry("hit.mkv", "/d/hit.mkv", False)]
    c.tree.return_value = [{"name": "d", "depth": 0, "is_dir": True, "size": 0}]
    c.upload.return_value = {"path": "/d/r/a.txt"}
    c.download.return_value = "/tmp/a.txt"
    for k, v in overrides.items():
        parts = k.split(".")
        obj = c
        for part in parts[:-1]:
            obj = getattr(obj, part)
        if len(parts) == 1:
            # plain method name → configure its return value
            getattr(obj, parts[-1]).return_value = v
        else:
            setattr(obj, parts[-1], v)
    return c


def _run(coro):
    return asyncio.run(coro)


def test_ok_err_helpers():
    assert _ok({"a": 1}) == {"result": {"a": 1}}
    assert _err(ValueError("boom")) == {"error": "boom"}


def test_zspace_check_connected():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_check())
    assert r["result"]["connected"] is True
    assert r["result"]["pools"][0]["name"] == "池1"
    assert r["result"]["pools"][0]["total_tb"] == 8.0


def test_zspace_check_not_connected():
    from zspace_cli import mcp_server as m

    c = _mock_client(is_connected=False)
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_check())
    assert r["result"]["connected"] is False
    assert "pools" not in r["result"]


def test_zspace_check_pool_error_ignored():
    from zspace_cli import mcp_server as m
    from zspace_cli.client import ZSpaceError

    c = _mock_client()
    c.pool_info.side_effect = ZSpaceError("500", "pool err")
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_check())
    assert r["result"]["connected"] is True
    assert "pools" not in r["result"]


def test_zspace_ls():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_ls("/d", show_hidden=True))
    assert r["result"][0]["name"] == "a.txt"
    c.ls.assert_called_once_with("/d", show_hidden=True)


def test_zspace_info():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_info("/d/a.txt"))
    assert r["result"]["path"] == "/d/a.txt"


def test_zspace_rename():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_rename("/d/a.txt", "b.txt"))
    assert r["result"] == {"name": "b.txt", "path": "/d/b.txt"}


def test_zspace_mkdir():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_mkdir("/d", "new"))
    assert r["result"]["name"] == "new"


def test_zspace_move_str():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_move("/d/a.txt", "/d/x"))
    assert r["result"]["status"] == "moved"
    c.move.assert_called_once_with(["/d/a.txt"], "/d/x")


def test_zspace_move_list():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_move(["/d/a.txt", "/d/b.txt"], "/d/x"))
    assert r["result"]["paths"] == ["/d/a.txt", "/d/b.txt"]


def test_zspace_copy():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_copy("/d/a.txt", "/d/x"))
    assert r["result"]["status"] == "copied"
    c.copy.assert_called_once_with(["/d/a.txt"], "/d/x")


def test_zspace_remove():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_remove("/d/a.txt"))
    assert r["result"]["status"] == "removed"
    c.remove.assert_called_once_with(["/d/a.txt"])


def test_zspace_search():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_search("hit"))
    assert r["result"][0]["name"] == "hit.mkv"


def test_zspace_tree():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        r = _run(m.zspace_tree("/d", depth=3))
    assert r["result"][0]["name"] == "d"


def test_zspace_upload():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        _run(m.zspace_upload("/tmp/x.mp4", "/d", new_name="y.mp4"))
    c.upload.assert_called_once_with("/tmp/x.mp4", "/d", new_name="y.mp4")


def test_zspace_download():
    from zspace_cli import mcp_server as m

    c = _mock_client()
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        result = _run(m.zspace_download("/d/a.txt", "/tmp"))
    assert result["result"]["saved_to"] == "/tmp/a.txt"
