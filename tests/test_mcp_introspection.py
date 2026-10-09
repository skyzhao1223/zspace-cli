"""Tests for the introspection MCP tools.

Separate module from test_mcp_server.py so the two can be merged independently.
The destructive tools carry a confirm-gate; the tests pin that the gate actually
blocks the call rather than merely warning.
"""

import asyncio

import pytest

pytest.importorskip("mcp", reason="mcp extra requires Python 3.10+")

from unittest.mock import MagicMock, patch  # noqa: E402

from zspace_cli.client import (  # noqa: E402
    DirStat,
    DiskInfo,
    LargeFile,
    LargeFileScan,
    PoolUsage,
    RecycleEntry,
    UsageEntry,
    ZSpaceError,
)

TiB = 1024 ** 4
GiB = 1024 ** 3


def _client(**overrides):
    c = MagicMock()
    c.__enter__.return_value = c
    c.RECYCLE_MY = "/.recycle/my"
    c.RECYCLE_PUBLIC = "/.public_recycle"
    c.disk_usage_status.return_value = {"is_running": 0, "updated_at": 1791534369}
    c.disk_usage_refresh.return_value = {}
    c.usage_summary.return_value = [PoolUsage(pool="sata11", entries=[
        UsageEntry("user", "my", int(5.9 * TiB), "me (主账号)"),
        UsageEntry("user", "my_tm", int(1.3 * TiB), "me (主账号)"),
        UsageEntry("sys", "sys_docker", int(0.11 * TiB)),
    ])]
    c.pool_info.return_value = {"data": {"pool_list": [
        {"name": "sata11", "total_size": 12 * TiB,
         "usage_size": 8 * TiB, "free_size": 4 * TiB}]}}
    c.statistic.return_value = DirStat(path="/a", size=int(2.9 * TiB), files=7378,
                                       dirs=436, state="done",
                                       categories={"视频": 7000})
    c.walk_stat.return_value = DirStat(path="/a", size=TiB, files=10, dirs=4,
                                       state="done", source="walk", requests=42)
    c.find_large.return_value = LargeFileScan(
        state=2, paths=["/a"], min_size=50 * GiB // 1024, max_size=1024 ** 5,
        topk=1000, scanned=6979, matched=311,
        files=[LargeFile("big.zip", "/a/big.zip", 17349002937, "1737669313", "106")])
    c.disks.return_value = [DiskInfo(
        pool="sata11", position="1", model="ST8000NM017B", sn="SN1",
        dev_type="SATA", mount="/data_s001", total_size=8 * TiB, free_size=GiB,
        usage_size=7 * TiB, health="ok", status="ok", temp=40,
        power_on_hours=18025, reallocated_sectors=0, fragment_pct=0.0198)]
    c.free_bays.return_value = {"sata": 1, "nvme": 3, "esata": 1}
    c.smart.return_value = {"health": "ok", "attributes": [
        {"id": 5, "attr_name": "重映射扇区计数", "en_attr_name": "Reallocated Sector Count",
         "now_value": 100, "worst": 100, "thresh": 10, "str_value": "0", "health": "ok"}]}
    c.recycle_list.return_value = ([RecycleEntry(
        "a.zip", "/sata11/.recycle/my/a.zip", "/sata11/my/data/a.zip", False,
        int(27 * GiB))], 1)
    c.recycle_config.return_value = {"my_cycle": -1, "public_cycle": 30}
    c.recycle_restore.return_value = (1, [])
    c.recycle_purge.return_value = (1, [])
    c.recycle_empty.return_value = {"total_num": "72", "succeed_num": "72",
                                    "fail_num": "0", "total_size": "48435185863"}
    for k, v in overrides.items():
        parts = k.split(".")
        obj = c
        for part in parts[:-1]:
            obj = getattr(obj, part)
        if len(parts) == 1:
            getattr(obj, parts[-1]).return_value = v
        else:
            setattr(obj, parts[-1], v)
    return c


def _call(coro_fn, *args, **kwargs):
    c = kwargs.pop("_client", None) or _client(**kwargs.pop("_overrides", {}))
    with patch("zspace_cli.mcp_server.ZSpaceClient", return_value=c):
        return asyncio.run(coro_fn(*args, **kwargs)), c


# ── zspace_usage ───────────────────────────────────────────────────────────


def test_usage_reports_pools_and_breakdown():
    from zspace_cli import mcp_server as m

    r, _ = _call(m.zspace_usage)
    p = r["result"]["pools"][0]
    assert p["pool"] == "sata11"
    assert p["used_pct"] == pytest.approx(66.67, abs=0.01)
    labels = {e["label"]: e for e in p["entries"]}
    assert labels["my_tm"]["label_cn"] == "Time Machine 备份"
    assert labels["sys_docker"]["kind"] == "sys"
    assert r["result"]["snapshot"]["is_running"] == 0


def test_usage_refresh_triggers_recompute():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_usage, refresh=True)
    c.disk_usage_refresh.assert_called_once()
    assert "result" in r


def test_usage_error_is_structured():
    from zspace_cli import mcp_server as m

    r, _ = _call(m.zspace_usage,
                 _overrides={"usage_summary.side_effect": ZSpaceError("500", "boom")})
    assert "boom" in r["error"]


# ── zspace_du ──────────────────────────────────────────────────────────────


def test_du_uses_server_statistic_by_default():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_du, "/a")
    assert r["result"]["complete"] is True
    assert r["result"]["source"] == "statistic"
    assert r["result"]["files"] == 7378
    c.walk_stat.assert_not_called()


def test_du_walk_flag_uses_traversal():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_du, "/a", walk=True)
    c.walk_stat.assert_called_once_with("/a")
    assert r["result"]["source"] == "walk"
    assert r["result"]["requests"] == 42


def test_du_surfaces_incomplete_state():
    """The whole point: a partial server snapshot must not look like an answer."""
    from zspace_cli import mcp_server as m

    r, _ = _call(m.zspace_du, "/big", _overrides={"statistic.return_value": DirStat(
        path="/big", size=int(0.35 * TiB), files=30261, state="running")})
    assert r["result"]["complete"] is False
    assert r["result"]["state"] == "running"


# ── zspace_bigfiles ────────────────────────────────────────────────────────


def test_bigfiles_converts_mib_and_returns_files():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_bigfiles, ["/a"], min_size_mib=100, top=10)
    assert c.find_large.call_args.kwargs["min_size"] == 100 * 1024 * 1024
    assert r["result"]["matched"] == 311
    assert r["result"]["files"][0]["bytes"] == 17349002937
    assert r["result"]["complete"] is True


def test_bigfiles_topk_never_below_server_floor():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_bigfiles, ["/a"], top=3)
    assert c.find_large.call_args.kwargs["topk"] == 1000
    assert len(r["result"]["files"]) <= 3


# ── zspace_disks / zspace_smart ────────────────────────────────────────────


def test_disks_reports_bays_and_health():
    from zspace_cli import mcp_server as m

    r, _ = _call(m.zspace_disks)
    assert r["result"]["free_bays"]["sata"] == 1
    d = r["result"]["disks"][0]
    assert d["sn"] == "SN1" and d["power_on_hours"] == 18025
    assert d["fragment_pct"] == pytest.approx(0.0198)


def test_smart_maps_attributes():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_smart, "SN1")
    c.smart.assert_called_once_with("SN1")
    a = r["result"]["attributes"][0]
    assert a["id"] == 5 and a["en_attr_name"] == "Reallocated Sector Count"
    assert r["result"]["health"] == "ok"


# ── recycle tools ──────────────────────────────────────────────────────────


def test_recycle_list_uses_personal_root_by_default():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_recycle_list)
    assert c.recycle_list.call_args.args[0] == "/.recycle/my"
    assert r["result"]["items"][0]["original_path"] == "/sata11/my/data/a.zip"


def test_recycle_list_public_flag():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_recycle_list, public=True)
    assert c.recycle_list.call_args.args[0] == "/.public_recycle"
    assert r["result"]["bin"] == "/.public_recycle"


def test_recycle_config_explains_never():
    from zspace_cli import mcp_server as m

    r, _ = _call(m.zspace_recycle_config)
    assert r["result"]["my_cycle"] == -1
    assert r["result"]["my_cycle_meaning"] == "never auto-purge"


def test_recycle_restore_returns_counts():
    from zspace_cli import mcp_server as m

    r, _ = _call(m.zspace_recycle_restore, ["/sata11/.recycle/my/a.zip"])
    assert r["result"] == {"restored": 1, "failed": []}


def test_recycle_purge_refused_without_confirm():
    """The gate must block the call, not merely warn about it."""
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_recycle_purge, ["/sata11/.recycle/my/a.zip"])
    assert "refused" in r["error"]
    assert "confirm=true" in r["error"]
    c.recycle_purge.assert_not_called()


def test_recycle_purge_runs_with_confirm():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_recycle_purge, ["/sata11/.recycle/my/a.zip"], confirm=True)
    c.recycle_purge.assert_called_once()
    assert r["result"] == {"purged": 1, "failed": []}


def test_recycle_empty_refused_without_confirm():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_recycle_empty)
    assert "refused" in r["error"]
    c.recycle_empty.assert_not_called()


def test_recycle_empty_returns_task_summary():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_recycle_empty, confirm=True)
    c.recycle_empty.assert_called_once_with(public=False)
    assert r["result"]["bin"] == "personal"
    # total_num is what proves space was actually freed
    assert r["result"]["task"]["total_num"] == "72"


def test_recycle_empty_public_flag():
    from zspace_cli import mcp_server as m

    r, c = _call(m.zspace_recycle_empty, confirm=True, public=True)
    c.recycle_empty.assert_called_once_with(public=True)
    assert r["result"]["bin"] == "public"


# ── docstring contracts ────────────────────────────────────────────────────
#
# These tools are read by agents, so the docstring IS the interface. The
# @Recycle warning in particular prevents a silent over-count: an agent that
# also runs mount-side scripts needs to know the bin it reads as /.recycle/my
# is the same thing a mount exposes as @Recycle, and that skipping it is
# mandatory. Pinned so the note cannot be trimmed away as "just prose" --
# the same failure mode that let @Recycle sit in 1 of 9 scanner SKIP_DIRS.


@pytest.mark.parametrize("tool", ["zspace_recycle_list", "zspace_du", "zspace_usage"])
def test_space_tools_warn_about_at_recycle(tool):
    from zspace_cli import mcp_server as m

    doc = getattr(m, tool).__doc__ or ""
    assert "@Recycle" in doc, f"{tool} must warn about the mount-side @Recycle name"


def test_recycle_list_doc_names_both_views():
    """The API path and the mount path must both appear, or the mapping is useless."""
    from zspace_cli import mcp_server as m

    doc = m.zspace_recycle_list.__doc__ or ""
    assert "/.recycle/my" in doc
    assert ".zspace_trash" in doc


def test_recycle_list_doc_warns_n001411_proves_nothing():
    """Probing @Recycle via the file API looks like a check but is not one."""
    from zspace_cli import mcp_server as m

    assert "N001411" in (m.zspace_recycle_list.__doc__ or "")


def test_usage_doc_distinguishes_physical_from_logical():
    """my_recycle/my are physical; a walk is logical. Conflating them reads as a bug."""
    from zspace_cli import mcp_server as m

    doc = m.zspace_usage.__doc__ or ""
    assert "my_recycle" in doc
    assert "physical" in doc.lower()
