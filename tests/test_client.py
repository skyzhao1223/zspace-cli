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
    assert kwargs["content"] == b"hello"


def test_upload_missing_local_file(client, tmp_path):
    with pytest.raises(FileNotFoundError):
        client.upload(tmp_path / "nope.txt", "/dst")


def test_download_writes_file(client, tmp_path):
    client._http.get.return_value = MagicMock(
        raise_for_status=lambda: None,
        content=b"file-bytes",
    )
    out = client.download("/dst/hello.txt", tmp_path)
    assert out.exists()
    assert out.read_bytes() == b"file-bytes"
    # request carries path and remote_port params
    _, kwargs = client._http.get.call_args
    assert kwargs["params"]["path"] == "/dst/hello.txt"


def test_file_entry_from_api():
    e = FileEntry.from_api(
        {"name": "a", "path": "/a", "is_dir": "1", "size": "0"}
    )
    assert e.is_dir is True
    e2 = FileEntry.from_api({"name": "b", "path": "/b", "is_dir": "0", "size": 5})
    assert e2.is_dir is False
    assert e2.size == 5
