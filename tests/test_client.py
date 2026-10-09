"""Tests for zspace_cli.client — API URL construction, paging, upload/download."""

from pathlib import Path
from unittest.mock import MagicMock, patch

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
    # the request must carry the full target path as a header — UTF-8 bytes,
    # so CJK paths don't crash httpx's ASCII header-value validation
    _, kwargs = client._http.post.call_args
    headers = kwargs["headers"]
    assert headers["path"] == b"/dst/hello.txt"
    # upload now streams the file object instead of buffering bytes
    assert hasattr(kwargs["content"], "read")


def test_upload_cjk_path_header_encodes_utf8(client, tmp_path):
    src = tmp_path / "视频.mp4"
    src.write_bytes(b"x" * 10)
    client._http.post.return_value = MagicMock(
        raise_for_status=lambda: None,
        json=lambda: _resp("200", {"path": "/影视/视频.mp4"}),
    )
    client.upload(src, "/影视")
    _, kwargs = client._http.post.call_args
    assert kwargs["headers"]["path"] == "/影视/视频.mp4".encode()


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


def test_glob_mid_segment_wildcard_scans_parent_dir(client):
    """A wildcard inside a path segment must not truncate the scan root.

    Regression: ``/d/2026-1*.log`` used to walk ``/d/2026-1`` — a non-existent
    partial path — because the root was the raw prefix before the first
    wildcard. ``ls`` then raised, ``walk`` swallowed it, and glob silently
    returned no matches while ``/d/*.log`` worked.
    """
    pages = {
        "/d": {
            "list": [
                _entry("2026-10-05.log", "/d/2026-10-05.log"),
                _entry("2026-09-30.log", "/d/2026-09-30.log"),
                _entry("note.txt", "/d/note.txt"),
            ]
        },
    }
    client._http.post.side_effect = _glob_post(pages)
    assert [e.path for e in client.glob("/d/2026-1*.log")] == ["/d/2026-10-05.log"]


def test_glob_mid_segment_wildcard_in_first_segment_scans_root(client):
    pages = {
        "/": {"list": [_entry("data", "/data", is_dir="1"),
                       _entry("etc", "/etc", is_dir="1")]}
    }
    client._http.post.side_effect = _glob_post(pages)
    assert [e.path for e in client.glob("/da*")] == ["/data"]


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


def test_upload_verify_round_trip_matches(client, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hello")

    client._http.post.return_value = _resp_mock("200", {"name": "hello.txt"})

    def fake_download(remote_path, local_dir):
        assert remote_path == "/dst/hello.txt"
        out = Path(local_dir) / "hello.txt"
        out.write_bytes(b"hello")
        return out

    with patch.object(client, "download", side_effect=fake_download) as download:
        result = client.upload(src, "/dst", verify=True)

    assert result["name"] == "hello.txt"
    download.assert_called_once()


def test_upload_verify_mismatch_raises(client, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hello")
    client._http.post.return_value = _resp_mock("200", {"name": "hello.txt"})

    def fake_download(_remote_path, local_dir):
        out = Path(local_dir) / "hello.txt"
        out.write_bytes(b"corrupted")
        return out

    with patch.object(client, "download", side_effect=fake_download):
        with pytest.raises(ZSpaceError, match="MD5 不匹配") as exc:
            client.upload(src, "/dst", verify=True)

    assert exc.value.code == "verify"


def test_upload_verify_is_opt_in(client, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hello")
    client._http.post.return_value = _resp_mock("200", {"name": "hello.txt"})

    with patch.object(client, "download") as download:
        client.upload(src, "/dst")

    download.assert_not_called()


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


# --- ZSpaceError.diagnose ---


def test_diagnose_permission():
    assert "权限" in ZSpaceError.diagnose("403", "无权限")
    assert "权限" in ZSpaceError.diagnose("401", "permission denied")


def test_diagnose_not_found():
    assert "路径不存在" in ZSpaceError.diagnose("404", "not found")


def test_diagnose_exists():
    assert "已存在" in ZSpaceError.diagnose("500", "文件已存在")


def test_diagnose_unknown_code():
    assert ZSpaceError.diagnose("500", "something weird") is None


# --- sliced upload (/v2/file/upload) ---


def test_upload_uuid_formula():
    import hashlib

    from zspace_cli.client import _upload_uuid

    # JS: md5(lastModified + size + path) — the two numbers ADD, then concat
    expected = hashlib.md5(b"1700000000579/dst/f.bin").hexdigest()
    assert _upload_uuid(1700000000123, 456, "/dst/f.bin") == expected


def test_upload_cookie_renames_nasid_and_encodes():
    from zspace_cli.client import _upload_cookie

    ck = _upload_cookie({"nasid": "N1", "path": "/a b/中.txt", "size": 3})
    assert ck == "nas_id=N1; path=%2Fa%20b%2F%E4%B8%AD.txt; size=3"


def test_upload_over_threshold_uses_slices(creds, tmp_path):
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")  # 10 bytes → slices of 4+4+2
    seen: list = []
    result = c.upload(src, "/dst", progress=lambda d, t: seen.append((d, t)))

    assert c._http.post.call_count == 3
    assert result == {"path": "/dst/f.bin", "name": "f.bin", "size": 10}
    assert seen == [(4, 10), (8, 10), (10, 10)]

    seeks, bodies = [], []
    for call in c._http.post.call_args_list:
        args, kwargs = call
        url = args[0]
        h = kwargs["headers"]
        assert url.startswith("/v2/file/upload?remote_port=8050")
        assert f"uuid={h['uuid']}" in url
        assert h["split"] == "1"
        assert h["size"] == "10"
        assert h["path"] == "%2Fdst%2Ff.bin"
        assert "nas_id=N1" in h["Cookie"]
        assert h["Content-Type"] == "application/octet-stream"
        seeks.append(int(h["seek"]))
        bodies.append(kwargs["content"])
    assert seeks == [0, 4, 8]
    assert b"".join(bodies) == b"0123456789"


def test_upload_resumes_from_tmpinfo(creds, tmp_path):
    """A partial session reported by tmpinfo skips the already-accepted bytes."""
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    c._http.get.return_value = _resp_mock("200", {"size": "4"})  # 4 of 10 already in
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    seen: list = []
    result = c.upload(src, "/dst", progress=lambda d, t: seen.append((d, t)))

    assert c._http.post.call_count == 2
    seeks = [int(x.kwargs["headers"]["seek"]) for x in c._http.post.call_args_list]
    bodies = [x.kwargs["content"] for x in c._http.post.call_args_list]
    assert seeks == [4, 8]
    assert b"".join(bodies) == b"456789"
    assert result["resumed_from"] == 4
    # progress must start at the resumed offset, not rewind to 0
    assert seen[0] == (4, 10)
    # queried with the target path and the deterministic session uuid
    args, kwargs = c._http.get.call_args
    assert args[0] == "/v2/file/tmpinfo"
    assert kwargs["params"]["path"] == "/dst/f.bin"
    assert len(kwargs["params"]["uuid"]) == 32


def test_upload_tmpinfo_unaligned_offset_is_honoured(creds, tmp_path):
    """A reported size that is not a slice multiple still resumes exactly there."""
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    c._http.get.return_value = _resp_mock("200", {"size": "3"})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    result = c.upload(src, "/dst")

    seeks = [int(x.kwargs["headers"]["seek"]) for x in c._http.post.call_args_list]
    bodies = [x.kwargs["content"] for x in c._http.post.call_args_list]
    assert seeks == [3, 7]
    assert b"".join(bodies) == b"3456789"
    assert result["resumed_from"] == 3


def test_upload_no_tmpinfo_session_starts_from_zero(creds, tmp_path):
    """N001315 (no temp entry) is the normal case and must not fail the upload."""
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    c._http.get.return_value = _resp_mock("N001315", None, msg="文件不存在")
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    result = c.upload(src, "/dst")

    assert c._http.post.call_count == 3
    assert [int(x.kwargs["headers"]["seek"]) for x in c._http.post.call_args_list] == [0, 4, 8]
    # unchanged shape: no resumed_from key when nothing was skipped
    assert result == {"path": "/dst/f.bin", "name": "f.bin", "size": 10}


def test_upload_resume_disabled_never_queries_tmpinfo(creds, tmp_path):
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    c._http.get.return_value = _resp_mock("200", {"size": "4"})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    result = c.upload(src, "/dst", resume=False)

    assert c._http.get.call_count == 0
    assert [int(x.kwargs["headers"]["seek"]) for x in c._http.post.call_args_list] == [0, 4, 8]
    assert "resumed_from" not in result


def test_upload_tmpinfo_size_at_total_resends_everything(creds, tmp_path):
    """size >= total must NOT skip: the NAS assembles only when the last slice lands."""
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    c._http.get.return_value = _resp_mock("200", {"size": "10"})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    result = c.upload(src, "/dst")

    assert c._http.post.call_count == 3
    assert [int(x.kwargs["headers"]["seek"]) for x in c._http.post.call_args_list] == [0, 4, 8]
    assert "resumed_from" not in result


def test_upload_tmpinfo_malformed_size_is_ignored(creds, tmp_path):
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    c._http.get.return_value = _resp_mock("200", {"size": "not-a-number"})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    result = c.upload(src, "/dst")

    assert c._http.post.call_count == 3
    assert "resumed_from" not in result


def test_upload_tmpinfo_null_size_is_ignored(creds, tmp_path):
    """data.size missing/null must not reach int() — pyright-narrowed, and tested."""
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    c._http.get.return_value = _resp_mock("200", {"size": None})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    result = c.upload(src, "/dst")

    assert c._http.post.call_count == 3
    assert "resumed_from" not in result


def test_upload_413_fallback_also_resumes(creds, tmp_path):
    """The 413 -> sliced fallback must honour resume too, not just the >threshold path."""
    import httpx as _httpx

    c = ZSpaceClient(credentials=creds, slice_threshold=1 << 40, slice_size=4)
    c._http = MagicMock()
    req = _httpx.Request("POST", "http://proxy/v2/file/create")
    err = _httpx.HTTPStatusError(
        "413", request=req, response=_httpx.Response(413, request=req)
    )
    c._http.post.side_effect = [err, _resp_mock("200", {}), _resp_mock("200", {})]
    c._http.get.return_value = _resp_mock("200", {"size": "4"})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    result = c.upload(src, "/dst")

    seeks = [int(x.kwargs["headers"]["seek"]) for x in c._http.post.call_args_list
             if isinstance(x.kwargs.get("headers"), dict) and "seek" in x.kwargs["headers"]]
    assert seeks == [4, 8]
    assert result["resumed_from"] == 4


def test_upload_uuid_stable_across_slices(creds, tmp_path):
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("200", {})
    src = tmp_path / "f.bin"
    src.write_bytes(b"0123456789")
    c.upload(src, "/dst")
    uuids = {call[1]["headers"]["uuid"] for call in c._http.post.call_args_list}
    assert len(uuids) == 1


def test_upload_413_falls_back_to_slices(creds, tmp_path):
    import httpx as _httpx

    c = ZSpaceClient(credentials=creds, slice_threshold=1 << 40, slice_size=8)
    c._http = MagicMock()
    req = _httpx.Request("POST", "http://proxy/v2/file/create")
    err = _httpx.HTTPStatusError(
        "413", request=req, response=_httpx.Response(413, request=req)
    )
    c._http.post.side_effect = [err, _resp_mock("200", {}), _resp_mock("200", {})]
    src = tmp_path / "big.bin"
    src.write_bytes(b"y" * 10)

    result = c.upload(src, "/dst")
    urls = [call[0][0] for call in c._http.post.call_args_list]
    assert "/v2/file/create" in urls[0]
    assert all("/v2/file/upload" in u for u in urls[1:])
    assert result["path"] == "/dst/big.bin"


def test_upload_slice_fatal_code_raises_without_retry(creds, tmp_path):
    c = ZSpaceClient(credentials=creds, slice_threshold=0, slice_size=4)
    c._http = MagicMock()
    c._http.post.return_value = _resp_mock("N001302", None, "无权限")
    src = tmp_path / "f.bin"
    src.write_bytes(b"abcd")
    with pytest.raises(ZSpaceError) as ei:
        c.upload(src, "/dst")
    assert ei.value.code == "N001302"
    assert c._http.post.call_count == 1


def test_upload_slice_transient_code_retries(creds, tmp_path):
    c = ZSpaceClient(
        credentials=creds, slice_threshold=0, slice_size=4,
        max_retries=2, retry_delay=0,
    )
    c._http = MagicMock()
    c._http.post.side_effect = [
        _resp_mock("N001500", None, "busy"),
        _resp_mock("200", {}),
    ]
    src = tmp_path / "f.bin"
    src.write_bytes(b"abcd")
    result = c.upload(src, "/dst")
    assert c._http.post.call_count == 2
    assert result["path"] == "/dst/f.bin"


# ── Baidu NetDisk (/znetdisk/*) ──


def _baidu_post_data(client, idx: int = -1) -> dict:
    """Form data of the nth (default last) mocked POST."""
    return client._http.post.call_args_list[idx].kwargs["data"]


def _baidu_url(client, idx: int = -1) -> str:
    return client._http.post.call_args_list[idx][0][0]


def test_baidu_check_returns_data_and_hits_endpoint(client):
    client._http.post.return_value = _resp_mock(
        "200", {"is_login": True, "url": "https://pan.baidu.com/oauth"}
    )
    data = client.baidu_check()
    assert data["is_login"] is True
    assert _baidu_url(client).startswith("/znetdisk/auth/check")
    # common auth params ride along exactly like the file API
    sent = _baidu_post_data(client)
    assert sent["token"] == "tok"
    assert sent["nasid"] == "N1"


def test_baidu_check_business_error_raises(client):
    """znetdisk uses the same envelope, so _check_response must raise on it."""
    client._http.post.return_value = _resp_mock("15", None, msg="需要NAS会员权限")
    with pytest.raises(ZSpaceError) as ei:
        client.baidu_check()
    assert ei.value.code == "15"


def test_baidu_userinfo_passes_through_data(client):
    payload = {
        "user_info": {"uk": 1, "vip_type": 0, "iot_vip_type": 0},
        "quota": {"used": 10, "total": 100},
        "iot_vip_cashier": "https://example.invalid/buy",
    }
    client._http.post.return_value = _resp_mock("200", payload)
    data = client.baidu_userinfo()
    assert data == payload
    assert _baidu_url(client).startswith("/znetdisk/auth/userinfo")


def test_baidu_ls_single_page(client):
    client._http.post.return_value = _resp_mock("200", {"current_page": 1, "list": [
        {"fs_id": 111, "server_filename": "a.mkv", "path": "/a.mkv",
         "size": 10, "isdir": 0},
    ]})
    entries = client.baidu_ls("/")
    assert len(entries) == 1
    assert entries[0]["fs_id"] == 111
    sent = _baidu_post_data(client)
    assert sent["path"] == "/"
    assert sent["page"] == "1"
    assert sent["limit"] == "50"


def test_baidu_ls_pages_until_short_page(client):
    """page is 1-based and increments; a full page must trigger another fetch."""
    def fake_post(url, **kwargs):
        page = int(kwargs["data"]["page"])
        n = 50 if page < 3 else 7
        rows = [{"fs_id": page * 100 + i} for i in range(n)]
        return _resp_mock("200", {"list": rows, "current_page": page})

    client._http.post.side_effect = fake_post
    entries = client.baidu_ls("/docs", page_size=50)
    assert len(entries) == 107
    assert client._http.post.call_count == 3
    assert [_baidu_post_data(client, i)["page"] for i in range(3)] == ["1", "2", "3"]
    assert all(_baidu_url(client, i).startswith("/znetdisk/file/list") for i in range(3))


def test_baidu_ls_missing_list_key_is_empty(client):
    client._http.post.return_value = _resp_mock("200", {"current_page": 1})
    assert client.baidu_ls("/") == []


def test_baidu_share_verify_returns_spwd(client):
    client._http.post.return_value = _resp_mock("200", {"spwd": "verified-pwd"})
    spwd = client.baidu_share_verify("1AbCdEf", pwd="ab12")
    assert spwd == "verified-pwd"
    assert _baidu_url(client).startswith("/znetdisk/share/verify")
    sent = _baidu_post_data(client)
    assert sent["short_url"] == "1AbCdEf"
    assert sent["pwd"] == "ab12"


def test_baidu_share_verify_missing_spwd_is_empty_string(client):
    client._http.post.return_value = _resp_mock("200", {})
    assert client.baidu_share_verify("1x") == ""


def test_baidu_share_list_sends_all_params(client):
    client._http.post.return_value = _resp_mock("200", {"count": 1, "list": [
        {"fsid": 9, "server_filename": "b.mp4", "size": 5, "md5": "m", "isdir": 0},
    ]})
    entries = client.baidu_share_list("1AbC", spwd="pw", path="/sub")
    assert entries[0]["fsid"] == 9
    sent = _baidu_post_data(client)
    assert sent["short_url"] == "1AbC"
    assert sent["spwd"] == "pw"
    assert sent["path"] == "/sub"
    assert sent["page"] == "1"
    assert _baidu_url(client).startswith("/znetdisk/share/filelist")


def test_baidu_share_list_pages(client):
    def fake_post(url, **kwargs):
        page = int(kwargs["data"]["page"])
        n = 10 if page < 2 else 3
        return _resp_mock("200", {"list": [{"fsid": i} for i in range(n)]})

    client._http.post.side_effect = fake_post
    entries = client.baidu_share_list("1AbC", page_size=10)
    assert len(entries) == 13
    assert client._http.post.call_count == 2


def test_baidu_tasks_default_state_is_all(client):
    client._http.post.return_value = _resp_mock("200", {"task_count": 1, "list": [
        {"task_id": "t1", "name": "f.mkv", "down_state": 6},
    ]})
    tasks = client.baidu_tasks()
    assert tasks[0]["task_id"] == "t1"
    sent = _baidu_post_data(client)
    assert sent["state"] == ""
    assert _baidu_url(client).startswith("/znetdisk/task/list")


def test_baidu_tasks_state_filter_passthrough(client):
    client._http.post.return_value = _resp_mock("200", {"list": []})
    client.baidu_tasks(state="fail")
    assert _baidu_post_data(client)["state"] == "fail"


def test_baidu_tasks_pages_until_short_page(client):
    def fake_post(url, **kwargs):
        page = int(kwargs["data"]["page"])
        n = 20 if page == 1 else 11
        return _resp_mock("200", {"list": [{"task_id": f"t{page}-{i}"} for i in range(n)]})

    client._http.post.side_effect = fake_post
    tasks = client.baidu_tasks(page_size=20)
    assert len(tasks) == 31
    assert client._http.post.call_count == 2


def test_baidu_fail_list_omits_task_id_when_none(client):
    client._http.post.return_value = _resp_mock("200", {"total": 0, "list": []})
    client.baidu_fail_list()
    assert "task_id" not in _baidu_post_data(client)
    assert _baidu_url(client).startswith("/znetdisk/fail/list")


def test_baidu_fail_list_scopes_to_task(client):
    client._http.post.return_value = _resp_mock("200", {"total": 1, "list": [
        {"file_name": "x.mkv", "fail_reason": "r", "baidu_fail_code": -1},
    ]})
    fails = client.baidu_fail_list(task_id="t9")
    assert fails[0]["file_name"] == "x.mkv"
    assert _baidu_post_data(client)["task_id"] == "t9"


def test_baidu_task_action_includes_method_and_task_id(client):
    client._http.post.return_value = _resp_mock("200", {})
    body = client.baidu_task_action("resume", task_id="t1")
    assert body["code"] == "200"
    sent = _baidu_post_data(client)
    assert sent["method"] == "resume"
    assert sent["task_id"] == "t1"
    assert _baidu_url(client).startswith("/znetdisk/task/action")


def test_baidu_task_action_bulk_omits_task_id(client):
    client._http.post.return_value = _resp_mock("200", {})
    client.baidu_task_action("resume_fail_all")
    sent = _baidu_post_data(client)
    assert sent["method"] == "resume_fail_all"
    assert "task_id" not in sent
