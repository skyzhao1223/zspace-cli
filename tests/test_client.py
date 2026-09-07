"""Tests for zspace_cli.client — API URL construction, paging, upload/download."""

from unittest.mock import MagicMock

import pytest

from zspace_cli.auth import Credentials
from zspace_cli.client import FileEntry, ZSpaceClient, ZSpaceError


@pytest.fixture
def creds() -> Credentials:
    return Credentials(token="tok", nas_id="N1", device_id="D1", username="u")


@pytest.fixture
def client(creds) -> ZSpaceClient:
    c = ZSpaceClient(credentials=creds)
    # swap out the real HTTP transport for a mock
    c._http = MagicMock()
    return c


def _resp(code: str, data, msg: str = "ok"):
    return {"code": code, "msg": msg, "data": data}


def _resp_mock(code: str, data, msg: str = "ok"):
    """An httpx-like response mock with raise_for_status/json/content."""
    from unittest.mock import MagicMock

    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = _resp(code, data, msg)
    r.content = b"payload"
    return r


def _entry(name: str, path: str, is_dir: str = "0", size: int = 10) -> dict:
    return {"name": name, "path": path, "is_dir": is_dir, "size": size}


def test_common_params(client):
    p = client._common_params()
    assert p["token"] == "tok"
    assert p["nasid"] == "N1"
    assert p["device_id"] == "D1"
    assert p["plat"] == "web"
    assert p["version"] == ZSpaceClient.DEFAULT_API_VERSION
    assert p["_l"] == "zh_cn"


def test_client_sets_full_cookie_set(creds):
    c = ZSpaceClient(credentials=creds)
    ck = dict(c._http.cookies)
    assert ck["token"] == "tok"
    assert ck["zenithtoken"] == "tok"
    assert ck["nas_id"] == "N1"
    assert ck["nasid"] == "N1"
    assert ck["device_id"] == "D1"
    c.close()


def test_url_has_webagent_and_rnd(client):
    url = client._url("/v2/file/list")
    assert url.startswith("/v2/file/list")
    assert "webagent=v2" in url
    assert "rnd=" in url


def test_post_ok(client):
    client._http.post.return_value = MagicMock(
        raise_for_status=lambda: None,
        json=lambda: _resp("200", {"list": []}),
    )
    body = client._post("/v2/file/list", {"path": "/"})
    assert body["code"] == "200"
    client._http.post.assert_called_once()


def test_post_error_raises(client):
    client._http.post.return_value = MagicMock(
        raise_for_status=lambda: None,
        json=lambda: _resp("N001411", None, msg="无权限"),
    )
    with pytest.raises(ZSpaceError) as ei:
        client._post("/v2/file/list", {"path": "/"})
    assert "无权限" in str(ei.value)


def test_ls_single_page(client):
    client._http.post.return_value = MagicMock(
        raise_for_status=lambda: None,
        json=lambda: _resp("200", {"list": [_entry("a.txt", "/a.txt")]}),
    )
    entries = client.ls("/dir")
    assert len(entries) == 1
    assert entries[0].name == "a.txt"


def test_ls_pages_until_exhausted(client):
    page_sizes = [50, 50, 30]
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        data = kwargs.get("data", {})
        start = int(data.get("start", 0))
        idx = min(start // 50, len(page_sizes) - 1)
        size = page_sizes[idx]
        page = [_entry(f"f{i}.txt", f"/d/f{i}.txt") for i in range(start, start + size)]
        calls["n"] += 1
        return MagicMock(
            raise_for_status=lambda: None,
            json=lambda: _resp("200", {"list": page}),
        )

    client._http.post.side_effect = fake_post
    entries = client.ls("/d")
    assert len(entries) == 130
    assert calls["n"] == 3  # 3 requests: 50 + 50 + 30


def test_search_uses_file_search_endpoint(client):
    client._http.post.return_value = MagicMock(
        raise_for_status=lambda: None,
        json=lambda: _resp(
            "200",
            {
                "list": [
                    _entry("readme.md", "/sata11/my/data/readme.md"),
                    _entry("other.txt", "/sata11/my/data/other.txt"),
                ]
            },
        ),
    )
    results = client.search("readme", path="/sata11/my/data")
    # check it hit the full-text search endpoint with keyword
    _, kwargs = client._http.post.call_args
    data = kwargs["data"]
    assert data["keyword"] == "readme"
    assert len(results) == 2


def test_upload_posts_binary_with_path_header(client, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hello")
    client._http.post.return_value = MagicMock(
        raise_for_status=lambda: None,
        json=lambda: _resp("200", {"name": "hello.txt", "path": "/dst/hello.txt"}),
    )
    result = client.upload(src, "/dst")
    assert result["path"] == "/dst/hello.txt"
    # the request must carry the full target path as a header
    _, kwargs = client._http.post.call_args
    headers = kwargs["headers"]
    assert headers["path"] == "/dst/hello.txt"
    # upload now streams the file object instead of buffering bytes
    assert hasattr(kwargs["content"], "read")


def test_upload_missing_local_file(client, tmp_path):
    with pytest.raises(FileNotFoundError):
        client.upload(tmp_path / "nope.txt", "/dst")


def test_download_writes_file(client, tmp_path):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.iter_bytes.return_value = iter([b"file-", b"bytes"])
    client._http.stream.return_value.__enter__.return_value = resp
    out = client.download("/dst/hello.txt", tmp_path)
    assert out.exists()
    assert out.read_bytes() == b"file-bytes"
    # request carries path and remote_port params
    _, kwargs = client._http.stream.call_args
    assert kwargs["params"]["path"] == "/dst/hello.txt"


def test_file_entry_from_api():
    e = FileEntry.from_api(
        {"name": "a", "path": "/a", "is_dir": "1", "size": "0"}
    )
    assert e.is_dir is True
    e2 = FileEntry.from_api({"name": "b", "path": "/b", "is_dir": "0", "size": 5})
    assert e2.is_dir is False
    assert e2.size == 5


# --- _post_with_array + move/copy/remove ---


def test_post_with_array_builds_paths(client):
    client._http.post.return_value = _resp_mock("200", {"ok": True})
    body = client._post_with_array("/v2/file/move", ["/a", "/b"], {"to": "/d"})
    assert body["data"]["ok"] is True
    args, kwargs = client._http.post.call_args
    assert "paths%5B%5D=%2Fa" in kwargs["content"]
    assert "paths%5B%5D=%2Fb" in kwargs["content"]
    assert "to=%2Fd" in kwargs["content"]


def test_post_with_array_error_raises(client):
    client._http.post.return_value = _resp_mock("500", {}, msg="move failed")
    import pytest as _p

    with _p.raises(ZSpaceError):
        client._post_with_array("/v2/file/move", ["/a"], {"to": "/d"})


def test_move_str_and_list(client):
    client._http.post.return_value = _resp_mock("200", {})
    client.move("/a", "/d")
    client.move(["/b", "/c"], "/d")
    assert client._http.post.call_count == 2


def test_copy(client):
    client._http.post.return_value = _resp_mock("200", {})
    client.copy("/a", "/d")
    args, _ = client._http.post.call_args
    assert "/v2/file/copy" in args[0]


def test_remove(client):
    client._http.post.return_value = _resp_mock("200", {})
    client.remove("/a")
    args, _ = client._http.post.call_args
    assert "/v2/file/remove" in args[0]


def test_disk_stats(client):
    client._http.post.return_value = _resp_mock("200", {"x": 1})
    assert client.disk_stats()["data"]["x"] == 1


def test_rename_and_mkdir(client):
    client._http.post.return_value = _resp_mock("200", _entry("b", "/a/b"))
    e = client.rename("/a", "b")
    assert e.name == "b"
    client._http.post.return_value = _resp_mock("200", _entry("new", "/d/new", is_dir="1"))
    d = client.mkdir("/d", "new")
    assert d.is_dir is True


# --- search path filtering + bad rows ---


def test_search_filters_by_path(client):
    client._http.post.return_value = _resp_mock("200", {"list": [
        _entry("x", "/sata11/my/data/影视/x.mkv"),
        _entry("y", "/other/y.mkv"),
    ]})
    res = client.search("x", path="/sata11/my/data")
    assert len(res) == 1
    assert res[0].path.startswith("/sata11/my/data")


def test_search_path_filter_is_segment_aware(client):
    # /sata11/my/data must NOT match /sata11/my/data2 or /sata11/my/dataset
    client._http.post.return_value = _resp_mock("200", {"list": [
        _entry("a", "/sata11/my/data/影视/a.mkv"),
        _entry("b", "/sata11/my/data2/b.mkv"),
        _entry("c", "/sata11/my/dataset/c.mkv"),
        _entry("exact", "/sata11/my/data"),
    ]})
    res = client.search("a", path="/sata11/my/data")
    paths = {e.path for e in res}
    assert "/sata11/my/data/影视/a.mkv" in paths
    assert "/sata11/my/data" in paths
    assert "/sata11/my/data2/b.mkv" not in paths
    assert "/sata11/my/dataset/c.mkv" not in paths


def test_ls_skips_bad_rows(client):
    client._http.post.return_value = _resp_mock("200", {"list": [
        {"no": "name"},
        _entry("ok", "/d/ok.mkv"),
    ]})
    res = client.ls("/d")
    assert len(res) == 1
    assert res[0].name == "ok"


def test_search_skips_bad_rows(client):
    client._http.post.return_value = _resp_mock("200", {"list": [
        {"no": "name"},
        _entry("ok", "/a/ok.mkv"),
    ]})
    res = client.search("ok", path="")
    assert len(res) == 1
    assert res[0].name == "ok"


# --- glob ---


def _glob_post(pages):
    def fake_post(*args, **kwargs):
        data = kwargs.get("data", {})
        return _resp_mock("200", pages.get(data.get("path", ""), {"list": []}))

    return fake_post


def test_glob_double_star_matches_across_dirs(client):
    pages = {
        "/d": {
            "list": [
                _entry("movies", "/d/movies", is_dir="1"),
                _entry("a.mp4", "/d/a.mp4"),
            ]
        },
        "/d/movies": {
            "list": [_entry("b.mp4", "/d/movies/b.mp4"), _entry("c.txt", "/d/movies/c.txt")]
        },
    }
    client._http.post.side_effect = _glob_post(pages)
    res = client.glob("/d/**/*.mp4")
    assert {e.path for e in res} == {"/d/a.mp4", "/d/movies/b.mp4"}


def test_glob_single_star_is_shallow(client):
    pages = {
        "/d": {"list": [_entry("x.mkv", "/d/x.mkv"), _entry("sub", "/d/sub", is_dir="1")]},
        "/d/sub": {"list": [_entry("y.mkv", "/d/sub/y.mkv")]},
    }
    client._http.post.side_effect = _glob_post(pages)
    res = client.glob("/d/*.mkv")
    assert [e.path for e in res] == ["/d/x.mkv"]


def test_glob_sorts_by_path(client):
    pages = {"/d": {"list": [_entry("b", "/d/b.mkv"), _entry("a", "/d/a.mkv")]}}
    client._http.post.side_effect = _glob_post(pages)
    res = client.glob("/d/*.mkv")
    assert [e.path for e in res] == ["/d/a.mkv", "/d/b.mkv"]


def test_glob_rejects_relative_or_wildcardless(client):
    with pytest.raises(ValueError):
        client.glob("relative/*.mp4")
    with pytest.raises(ValueError):
        client.glob("/d/no-wildcard")


def test_client_default_base_url_from_env(monkeypatch, creds):
    monkeypatch.setenv("ZS_BASE_URL", "http://nas:13579")
    c = ZSpaceClient(credentials=creds)
    assert c.base_url == "http://nas:13579"
    c.close()


# --- tree / _tree_walk ---


def test_tree_walk_respects_depth(client):
    client._http.post.return_value = _resp_mock("200", {"list": [
        _entry("sub", "/d/sub", is_dir="1"),
        _entry("f.txt", "/d/f.txt", size=5),
    ]})
    nodes = client.tree("/d", max_depth=1)
    assert any(n["name"] == "sub" and n["depth"] == 0 for n in nodes)
    assert any(n["name"] == "f.txt" and "size" in n for n in nodes)


def test_tree_walk_stops_at_depth(client):
    client._http.post.return_value = _resp_mock("200", {"list": [
        _entry("sub", "/d/sub", is_dir="1"),
    ]})
    client.tree("/d", max_depth=0)
    assert client._http.post.call_count == 0  # never called at depth 0


def test_tree_walk_ignores_ls_error(client):
    client._http.post.return_value = _resp_mock("500", {}, msg="boom")
    assert client.tree("/d", max_depth=2) == []


# --- upload/download error paths ---


def test_upload_api_error(client, tmp_path):
    local = tmp_path / "x.txt"
    local.write_text("hi")
    client._http.post.return_value = _resp_mock("500", {}, msg="upload failed")
    import pytest as _p

    with _p.raises(ZSpaceError):
        client.upload(local, "/d")


def test_download_http_error(client, tmp_path):
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.raise_for_status.side_effect = Exception("network down")
    client._http.stream.return_value.__enter__.return_value = resp
    import pytest as _p

    with _p.raises(Exception):
        client.download("/a", tmp_path)


def test_is_connected(client):
    from unittest.mock import patch as _p

    from zspace_cli import client as _c

    with _p.object(_c, "check_client_running", return_value=True):
        assert client.is_connected() is True


# --- retry on transient failures ---


def test_post_retries_then_succeeds(client):
    import httpx

    calls = {"n": 0}

    def flaky_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return _resp_mock("200", {"ok": True})

    client._http.post.side_effect = flaky_post
    body = client._post("/v2/file/list", {"path": "/"})
    assert body["data"]["ok"] is True
    assert calls["n"] == 2


def test_post_retries_http_500(client):
    import httpx

    calls = {"n": 0}

    def flaky_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            resp = MagicMock()
            resp.status_code = 500
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "server error", request=MagicMock(), response=resp
            )
            return resp
        return _resp_mock("200", {"ok": True})

    client._http.post.side_effect = flaky_post
    client._post("/v2/file/list", {"path": "/"})
    assert calls["n"] == 3


def test_post_does_not_retry_http_404(client):
    import httpx

    calls = {"n": 0}

    def bad_post(*args, **kwargs):
        calls["n"] += 1
        resp = MagicMock()
        resp.status_code = 404
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found", request=MagicMock(), response=resp
        )
        return resp

    client._http.post.side_effect = bad_post
    with pytest.raises(httpx.HTTPStatusError):
        client._post("/v2/file/list", {"path": "/"})
    assert calls["n"] == 1


def test_post_does_not_retry_business_error(client):
    calls = {"n": 0}

    def bad_post(*args, **kwargs):
        calls["n"] += 1
        return _resp_mock("N001411", None, msg="无权限")

    client._http.post.side_effect = bad_post
    with pytest.raises(ZSpaceError):
        client._post("/v2/file/list", {"path": "/"})
    assert calls["n"] == 1


def test_post_gives_up_after_max_retries(client):
    import httpx

    client._http.post.side_effect = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        client._post("/v2/file/list", {"path": "/"})
    assert client._http.post.call_count == client.max_retries + 1


# --- progress callbacks ---


def test_upload_reports_progress(client, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hello")

    def fake_post(*args, **kwargs):
        content = kwargs.get("content")
        while content.read(1024 * 1024):
            pass  # simulate httpx consuming the stream
        return _resp_mock("200", {"name": "hello.txt"})

    client._http.post.side_effect = fake_post
    seen = []
    client.upload(src, "/dst", progress=lambda done, total: seen.append((done, total)))
    assert seen
    assert seen[-1] == (5, 5)


def test_download_reports_progress(client, tmp_path):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.headers = {"content-length": "10"}
    resp.iter_bytes.return_value = iter([b"file-", b"bytes"])
    client._http.stream.return_value.__enter__.return_value = resp
    seen = []
    out = client.download("/dst/hello.txt", tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert out.read_bytes() == b"file-bytes"
    assert seen[-1] == (10, 10)


def test_download_progress_without_content_length(client, tmp_path):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.headers = {}
    resp.iter_bytes.return_value = iter([b"abc"])
    client._http.stream.return_value.__enter__.return_value = resp
    seen = []
    client.download("/dst/a.txt", tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (3, 0)
