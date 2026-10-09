"""Tests for the storage-introspection surface added to ZSpaceClient.

Covers /system/diskusage3, /v2/file/statistic (including the non-converging
large-directory case), the client-side walk fallback, both recycle bins, and
disk/SMART reporting.

Each test encodes behaviour that was verified against a live NAS, so the
assertions pin the exact parameter shapes the server accepts -- notably that
/v2/file/restore takes a singular ``path`` and rejects ``paths[]``.
"""

from unittest.mock import MagicMock

import pytest

from zspace_cli.auth import Credentials
from zspace_cli.client import (
    DirStat,
    DiskInfo,
    RecycleEntry,
    ZSpaceClient,
    ZSpaceError,
)


@pytest.fixture
def creds() -> Credentials:
    return Credentials(token="tok", nas_id="N1", device_id="D1", username="u")


@pytest.fixture
def client(creds) -> ZSpaceClient:
    c = ZSpaceClient(credentials=creds)
    c._http = MagicMock()
    return c


def _mock(code: str, data, msg: str = "ok"):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": code, "msg": msg, "data": data}
    r.content = b"payload"
    return r


def _route(mapping):
    """Side effect that picks a canned response by the request's ``path``."""

    def side(url, **kwargs):
        data = kwargs.get("data") or {}
        return _mock("200", mapping[data.get("path")])

    return side


def _lst(items, total=None):
    return {"total": len(items) if total is None else total, "list": items}


def _f(name, path, size):
    return {"name": name, "path": path, "is_dir": "0", "size": str(size)}


def _d(name, path):
    return {"name": name, "path": path, "is_dir": "1", "size": "0"}


# ── DirStat parsing ────────────────────────────────────────────────────────


def test_dirstat_parses_string_numerics_and_categories():
    task = {
        "state": "done", "size": "1073741824", "tfnum": "5", "tdirnum": "2",
        "hidden_size": "10", "hidden_fnum": "1", "hidden_dirnum": "0",
        "tvnum": "3", "tanum": "1", "tinum": "0", "tdocnum": "1",
        "tappnum": "0", "tcomnum": "0",
    }
    st = DirStat.from_api("/a", task)
    assert st.complete is True
    assert st.size == 1073741824
    assert st.files == 5 and st.dirs == 2
    assert st.hidden_size == 10 and st.hidden_files == 1
    # zero-valued categories are dropped so the CLI does not print empty rows
    assert st.categories == {"视频": 3, "音频": 1, "文档": 1}


def test_dirstat_running_is_not_complete():
    """The trap this API sets: a partial snapshot that looks like a result."""
    st = DirStat.from_api("/big", {"state": "running", "size": "1", "tfnum": "2"})
    assert st.state == "running"
    assert st.complete is False


def test_dirstat_tolerates_missing_fields():
    st = DirStat.from_api("/x", {})
    assert st.complete is False
    assert (st.size, st.files, st.dirs) == (0, 0, 0)
    assert st.categories == {}


# ── statistic ──────────────────────────────────────────────────────────────


def test_statistic_posts_paths_array_and_show_hidden(client):
    client._http.post.return_value = _mock(
        "200", {"task": {"state": "done", "size": "42", "tfnum": "1", "tdirnum": "0"}}
    )
    st = client.statistic("/a/b")
    assert st.complete and st.size == 42
    args, kwargs = client._http.post.call_args
    assert "/v2/file/statistic" in args[0]
    # _post_with_array form-encodes manually, so params land in `content`
    assert "paths%5B%5D=" in kwargs["content"]
    assert "show_hidden=1" in kwargs["content"]


def test_statistic_many_joins_paths(client):
    client._http.post.return_value = _mock("200", {"task": {"state": "done", "size": "0"}})
    st = client.statistic_many(["/a", "/b"])
    assert st.path == "/a, /b"
    assert client._http.post.call_args.kwargs["content"].count("paths%5B%5D=") == 2


def test_statistic_surfaces_running_state(client):
    client._http.post.return_value = _mock(
        "200", {"task": {"state": "running", "size": "387972300000", "tfnum": "30261"}}
    )
    st = client.statistic("/huge")
    assert st.complete is False
    assert st.size == 387972300000  # partial, and must not be reported as final


# ── walk_stat fallback ─────────────────────────────────────────────────────


def test_walk_stat_sums_subtree(client):
    client._http.post.side_effect = _route({
        "/a": _lst([_f("f1", "/a/f1", 10), _d("b", "/a/b")]),
        "/a/b": _lst([_f("f2", "/a/b/f2", 5), _d("c", "/a/b/c")]),
        "/a/b/c": _lst([]),
    })
    st = client.walk_stat("/a", workers=3)
    assert st.source == "walk"
    assert st.complete is True
    assert st.size == 15
    assert st.files == 2
    assert st.dirs == 3  # directories listed, including the root
    assert st.requests == 3


def test_walk_stat_pages_past_the_50_entry_cap(client):
    """The endpoint returns at most 50 rows per page regardless of `limit`."""
    page1 = [_f(f"f{i}", f"/a/f{i}", 1) for i in range(50)]
    page2 = [_f(f"f{i}", f"/a/f{i}", 1) for i in range(50, 70)]
    calls = {"n": 0}

    def side(url, **kwargs):
        data = kwargs.get("data") or {}
        if data.get("path") != "/a":
            return _mock("200", _lst([]))
        calls["n"] += 1
        page = page1 if data.get("start", 0) == 0 else page2
        return _mock("200", _lst(page, total=70))

    client._http.post.side_effect = side
    st = client.walk_stat("/a", workers=1)
    assert st.files == 70 and st.size == 70
    assert calls["n"] == 2


def test_walk_stat_marks_partial_when_budget_trips(client):
    """A truncated walk must never masquerade as a complete one."""
    client._http.post.side_effect = _route({
        "/a": _lst([_d("b", "/a/b")]),
        "/a/b": _lst([_f("x", "/a/b/x", 99)]),
    })
    st = client.walk_stat("/a", workers=1, max_requests=1)
    assert st.state == "partial"
    assert st.complete is False


def test_walk_stat_survives_error_dirs(client):
    def side(url, **kwargs):
        data = kwargs.get("data") or {}
        if data.get("path") == "/a/bad":
            return _mock("N001411", {}, "无权限进行此操作")
        return _mock("200", _lst([_d("bad", "/a/bad"), _f("ok", "/a/ok", 7)]))

    client._http.post.side_effect = side
    st = client.walk_stat("/a", workers=1)
    assert st.size == 7 and st.files == 1


# ── recycle bin ────────────────────────────────────────────────────────────


def test_recycle_list_polls_until_scan_done(client, monkeypatch):
    monkeypatch.setattr("zspace_cli.client.time.sleep", lambda s: None)
    item = {"name": "a.zip", "path": "/sata11/.recycle/my/a.zip",
            "original_path": "/sata11/my/data/a.zip", "is_dir": "0", "size": "10"}
    client._http.post.side_effect = [
        _mock("200", {"scan_info": {"state": "running"}, "list": [], "total": 0}),
        _mock("200", {"scan_info": {"state": "done"}, "list": [item], "total": 1}),
    ]
    entries, total = client.recycle_list()
    assert total == 1 and len(entries) == 1
    assert entries[0].original_path == "/sata11/my/data/a.zip"
    assert client._http.post.call_count == 2
    assert client._http.post.call_args.kwargs["data"]["path"] == "/.recycle/my"


def test_recycle_list_no_wait_returns_first_page(client):
    client._http.post.return_value = _mock(
        "200", {"scan_info": {"state": "running"}, "list": [], "total": 0}
    )
    entries, total = client.recycle_list(wait=False)
    assert entries == [] and total == 0
    assert client._http.post.call_count == 1


def test_recycle_list_public_bin_uses_public_root(client):
    client._http.post.return_value = _mock("200", {"list": [], "total": 0})
    client.recycle_list(client.RECYCLE_PUBLIC)
    assert client._http.post.call_args.kwargs["data"]["path"] == "/.public_recycle"


def test_recycle_empty_personal_hits_rclean(client):
    client._http.post.return_value = _mock(
        "200", {"task": {"opt": "rclean", "total_num": "72", "fail_num": "0"}}
    )
    task = client.recycle_empty()
    assert "/v2/file/rclean" in client._http.post.call_args.args[0]
    assert task["total_num"] == "72"


def test_recycle_empty_public_hits_public_endpoint(client):
    client._http.post.return_value = _mock("200", {"task": {"total_num": "0"}})
    task = client.recycle_empty(public=True)
    assert "/v2/public/recycle/clean" in client._http.post.call_args.args[0]
    assert task["total_num"] == "0"


def test_recycle_restore_uses_singular_path_param(client):
    """/v2/file/restore rejects paths[] with N001411 -- pin the working shape."""
    client._http.post.return_value = _mock("200", {})
    ok, failed = client.recycle_restore(["/sata11/.recycle/my/a", "/sata11/.recycle/my/b"])
    assert (ok, failed) == (2, [])
    assert client._http.post.call_count == 2
    data = client._http.post.call_args.kwargs["data"]
    assert data["path"] == "/sata11/.recycle/my/b"
    # the array form must not be used: the server rejects it with N001411
    assert "paths[]" not in data


def test_recycle_restore_collects_failures(client):
    client._http.post.side_effect = [
        _mock("200", {}),
        _mock("N001411", {}, "无权限进行此操作"),
    ]
    ok, failed = client.recycle_restore(["/r/a", "/r/b"])
    assert ok == 1 and failed == ["/r/b"]


def test_recycle_purge_removes_only_the_named_items(client):
    """Selective purge must not touch the rest of the bin."""
    client._http.post.return_value = _mock("200", {})
    ok, failed = client.recycle_purge(["/sata11/.recycle/my/probe"])
    assert (ok, failed) == (1, [])
    assert "/v2/file/remove" in client._http.post.call_args.args[0]
    assert client._http.post.call_count == 1


def test_recycle_purge_collects_failures(client):
    client._http.post.side_effect = [
        _mock("200", {}),
        _mock("N001411", {}, "无权限进行此操作"),
    ]
    ok, failed = client.recycle_purge(["/r/a", "/r/b"])
    assert ok == 1 and failed == ["/r/b"]


def test_recycle_config_get(client):
    client._http.post.return_value = _mock("200", {"my_cycle": -1, "public_cycle": -1})
    assert client.recycle_config() == {"my_cycle": -1, "public_cycle": -1}


def test_recycle_set_config_preserves_unset_field(client):
    client._http.post.side_effect = [
        _mock("200", {"my_cycle": -1, "public_cycle": 30}),
        _mock("200", {}),
    ]
    body = client.recycle_set_config(my_cycle=7)
    assert body == {"my_cycle": 7, "public_cycle": 30}
    assert "/v2/file/recycle/config/save" in client._http.post.call_args_list[1].args[0]


# ── disks / SMART / bays ───────────────────────────────────────────────────


def _pool_info():
    return {
        "pool_list": [
            {
                "name": "sata11", "protocol": "protsata",
                "disk_list": [
                    {
                        "pos": 1, "model": "ST8000NM017B", "sn": "SN1",
                        "dev_type": "SATA", "mnt": "/data_s001",
                        "total_size": 8001563222016, "free_size": 375339454464,
                        "usage_size": 7626223767552, "health": "ok", "status": "ok",
                        "temp": 40, "suspected_smr": 0,
                        "fs_fragment": {"percentage": 0.0198},
                        "simple_smart": {"power_on_hours": 18025,
                                         "reallocated_sector_count": 0},
                    },
                    {
                        "pos": 3, "model": "ST1000DM003", "sn": "SN3",
                        "dev_type": "SATA", "mnt": "/data_s003",
                        "total_size": 1000204886016, "free_size": 71679639552,
                        "usage_size": 928525246464, "health": "ok", "status": "ok",
                        "temp": 34, "suspected_smr": 0,
                        "simple_smart": {"power_on_hours": 18463,
                                         "reallocated_sector_count": 3},
                    },
                ],
            },
            {
                "name": "nvme12", "protocol": "protnvme",
                "disk_list": [
                    {
                        "pos": 1, "model": "SOLIDIGM", "sn": "NV1",
                        "dev_type": "NVME", "mnt": "/data_n002",
                        "total_size": 2048408248320, "free_size": 1756067991552,
                        "usage_size": 292340256768, "health": "ok", "status": "ok",
                        "temp": 39,
                    },
                ],
            },
        ]
    }


def test_disks_parses_health_and_smart(client):
    client._http.post.return_value = _mock("200", _pool_info())
    ds = client.disks()
    assert [d.sn for d in ds] == ["SN1", "SN3", "NV1"]
    assert [d.pool for d in ds] == ["sata11", "sata11", "nvme12"]
    d0 = ds[0]
    assert d0.power_on_hours == 18025
    assert d0.fragment_pct == pytest.approx(0.0198)
    assert d0.used_pct == pytest.approx(95.3, abs=0.1)
    # SMART fields default safely when the disk reports none (NVMe)
    assert ds[2].power_on_hours == 0 and ds[2].fragment_pct == 0.0
    assert ds[1].reallocated_sectors == 3


def test_diskinfo_from_api_tolerates_missing_blocks():
    d = DiskInfo.from_api("p", {"pos": 2, "model": "M", "sn": "S"})
    assert d.total_size == 0 and d.used_pct == 0.0
    assert d.position == "2" and d.health == ""


def test_free_bays_subtracts_occupied_slots(client):
    client._http.post.side_effect = [
        _mock("200", {"slot": {"sata": 4, "nvme": 4, "esata": 1}}),
        _mock("200", _pool_info()),
    ]
    assert client.free_bays() == {"sata": 2, "nvme": 3, "esata": 1}


def test_smart_queries_by_serial_number(client):
    """The endpoint keys on sn; pool_id / /dev names return N300403."""
    client._http.post.return_value = _mock(
        "200", {"health": "ok", "attributes": [{"id": 5, "attr_name": "重映射扇区计数",
                                                "now_value": 100, "worst": 100,
                                                "thresh": 10, "str_value": "0",
                                                "health": "ok"}]}
    )
    rep = client.smart("SN1")
    args, kwargs = client._http.post.call_args
    assert "/zspool/smart/report2" in args[0]
    assert kwargs["data"]["sn"] == "SN1"
    assert rep["health"] == "ok" and len(rep["attributes"]) == 1


def test_smart_raises_on_wrong_key(client):
    client._http.post.return_value = _mock("N300403", {}, "参数错误")
    with pytest.raises(ZSpaceError) as ei:
        client.smart("not-a-serial")
    assert ei.value.code == "N300403"


# ── diskusage3 ─────────────────────────────────────────────────────────────


def _diskusage():
    return {
        "ctime": 1791530738,
        "disk_usage": [
            {
                "pool_name": "sata11", "mount_point": "/data_s001", "position": "1",
                "usage_v2": {
                    "user": [
                        {"id": 1, "is_master": 1, "username": "13426031783",
                         "nickname": "13426031783", "remark": "13426031783",
                         "list": [{"label": "my", "phy_size": 4587160039424},
                                  {"label": "my_tm", "phy_size": 149629927424},
                                  {"label": "my_recycle", "phy_size": 0}]},
                        {"id": 2, "is_master": 0, "username": "13204600005",
                         "remark": "孙嘉阳",
                         "list": [{"label": "my", "phy_size": 82113613824}]},
                    ],
                    "public_recycle": {"label": "pub_recycle", "phy_size": 0},
                    "public": [],
                    "sys": [{"label": "sys_raid", "phy_size": 20940668928},
                            {"label": "sys_docker", "phy_size": 125865766912}],
                },
            },
            {
                "pool_name": "sata11", "mount_point": "/data_s004", "position": "2",
                "usage_v2": {
                    "user": [{"id": 1, "is_master": 1, "username": "13426031783",
                              "remark": "13426031783",
                              "list": [{"label": "my", "phy_size": 1000000000000}]}],
                    "public_recycle": {"label": "pub_recycle", "phy_size": 0},
                    "public": [],
                    "sys": [{"label": "sys_other", "phy_size": 500000000000}],
                },
            },
        ],
    }


def test_usage_summary_sums_disks_per_pool(client):
    """/system/diskusage3 reports per DISK; a pool spans several entries."""
    client._http.post.return_value = _mock("200", _diskusage())
    pools = client.usage_summary()
    assert len(pools) == 1
    p = pools[0]
    assert p.pool == "sata11"
    assert p.total == (4587160039424 + 149629927424 + 82113613824
                       + 20940668928 + 125865766912 + 1000000000000 + 500000000000)
    by_owner = p.by_owner()
    assert by_owner["13426031783 (主账号)"] == 4587160039424 + 149629927424 + 1000000000000
    assert by_owner["孙嘉阳"] == 82113613824
    assert p.system_total == 20940668928 + 125865766912 + 500000000000


def test_usage_summary_merges_same_label_across_disks(client):
    """A pool spans several disks; each row must appear once, not once per disk."""
    client._http.post.return_value = _mock("200", _diskusage())
    p = client.usage_summary()[0]
    mine = [e for e in p.entries if e.owner.startswith("13426031783") and e.label == "my"]
    assert len(mine) == 1
    assert mine[0].size == 4587160039424 + 1000000000000
    raid = [e for e in p.entries if e.label == "sys_raid"]
    assert len(raid) == 1
    # entries come back largest-first for stable rendering
    sizes = [e.size for e in p.entries]
    assert sizes == sorted(sizes, reverse=True)


def test_usage_summary_drops_zero_entries_and_maps_labels(client):
    client._http.post.return_value = _mock("200", _diskusage())
    p = client.usage_summary()[0]
    labels = {e.label for e in p.entries}
    assert "my_recycle" not in labels      # phy_size 0 -> dropped
    assert "pub_recycle" not in labels
    tm = [e for e in p.entries if e.label == "my_tm"][0]
    assert tm.label_cn == "Time Machine 备份"
    assert tm.kind == "user"
    dock = [e for e in p.entries if e.label == "sys_docker"][0]
    assert dock.kind == "sys" and dock.label_cn == "Docker"


def test_disk_usage_status_and_refresh(client):
    client._http.post.side_effect = [
        _mock("200", {"is_running": 0, "updated_at": 1791534369}),
        _mock("200", {}),
        _mock("200", {"is_running": 1, "updated_at": 1791534543}),
    ]
    assert client.disk_usage_status()["is_running"] == 0
    client.disk_usage_refresh()
    assert "/system/diskusage/runanyway" in client._http.post.call_args_list[1].args[0]
    assert client.disk_usage_status()["is_running"] == 1


# ── RecycleEntry ───────────────────────────────────────────────────────────


def test_recycle_entry_from_api():
    e = RecycleEntry.from_api({
        "name": "x.iso", "path": "/sata11/.recycle/my/x.iso",
        "original_path": "/sata11/my/data/x.iso", "is_dir": "0", "size": "123",
        "modify_time": "1791533000",
    })
    assert e.name == "x.iso" and e.size == 123 and e.is_dir is False
    assert e.original_path == "/sata11/my/data/x.iso"


def test_recycle_entry_tolerates_missing_fields():
    e = RecycleEntry.from_api({"name": "d", "path": "/p", "is_dir": "1"})
    assert e.is_dir is True and e.size == 0 and e.original_path == ""
