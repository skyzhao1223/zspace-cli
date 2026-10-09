"""CLI tests for the introspection commands (usage / du / disks / smart / recycle).

Kept in a separate module from test_cli.py so the two can evolve -- and be
merged -- independently. Uses the same CliRunner + mocked-client approach.
"""

import io
from unittest.mock import MagicMock, patch

from rich.console import Console
from typer.testing import CliRunner

from zspace_cli.cli import _tib_str, app
from zspace_cli.client import (
    DirStat,
    DiskInfo,
    PoolUsage,
    RecycleEntry,
    UsageEntry,
    ZSpaceError,
)

TiB = 1024 ** 4
GiB = 1024 ** 3


def _run(*args, **overrides):
    c = MagicMock()
    c.__enter__.return_value = c
    c.RECYCLE_MY = "/.recycle/my"
    c.RECYCLE_PUBLIC = "/.public_recycle"
    c.disk_usage_status.return_value = {"is_running": 0, "updated_at": 1791534369}
    c.disk_usage_refresh.return_value = {}
    c.pool_info.return_value = {"data": {"pool_list": [
        {"name": "sata11", "total_size": 12 * TiB, "usage_size": 8 * TiB,
         "free_size": 4 * TiB},
    ]}}
    c.usage_summary.return_value = [PoolUsage(pool="sata11", entries=[
        UsageEntry("user", "my", int(6.0 * TiB), "me (主账号)"),
        UsageEntry("user", "my_tm", int(1.3 * TiB), "me (主账号)"),
        UsageEntry("sys", "sys_docker", int(0.11 * TiB)),
    ])]
    c.disks.return_value = [
        DiskInfo(pool="sata11", position="1", model="ST8000NM017B", sn="SN1",
                 dev_type="SATA", mount="/data_s001", total_size=8 * TiB,
                 free_size=GiB, usage_size=7 * TiB, health="ok", status="ok",
                 temp=40, power_on_hours=18025, reallocated_sectors=0,
                 fragment_pct=0.0198),
        DiskInfo(pool="sata11", position="3", model="ST1000DM003", sn="SN3",
                 dev_type="SATA", mount="/data_s003", total_size=TiB,
                 free_size=GiB, usage_size=TiB - GiB, health="warning",
                 status="ok", temp=34, power_on_hours=18463,
                 reallocated_sectors=5, fragment_pct=0.126),
    ]
    c.free_bays.return_value = {"sata": 1, "nvme": 3, "esata": 1}
    c.smart.return_value = {"health": "ok", "attributes": [
        {"id": 5, "attr_name": "重映射扇区计数", "now_value": 100, "worst": 100,
         "thresh": 10, "str_value": "0", "health": "ok"},
        {"id": 197, "attr_name": "当前待映射扇区", "now_value": 100, "worst": 100,
         "thresh": 0, "str_value": "0", "health": "ok"},
    ]}
    c.statistic.return_value = DirStat(path="/a", size=int(2.9 * TiB), files=7378,
                                       dirs=436, state="done",
                                       categories={"视频": 7000})
    c.walk_stat.return_value = DirStat(path="/a", size=TiB, files=10, dirs=4,
                                       state="done", source="walk", requests=42)
    c.recycle_list.return_value = ([
        RecycleEntry("a.zip", "/sata11/.recycle/my/a.zip",
                     "/sata11/my/data/a.zip", False, int(27 * GiB)),
        RecycleEntry("d", "/sata11/.recycle/my/d", "/sata11/my/data/d", True, 0),
    ], 2)
    c.recycle_empty.return_value = {"total_num": "2", "fail_num": "0"}
    c.recycle_restore.return_value = (1, [])
    c.recycle_purge.return_value = (1, [])
    c.recycle_config.return_value = {"my_cycle": -1, "public_cycle": -1}
    c.recycle_set_config.return_value = {"my_cycle": 30, "public_cycle": -1}
    for k, v in overrides.items():
        obj = c
        parts = k.split(".")
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], v)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=140)
    with patch("zspace_cli.cli.console", console), \
            patch("zspace_cli.cli._client", return_value=c):
        r = CliRunner().invoke(app, list(args))
    return r, buf.getvalue(), c


# ── helpers ────────────────────────────────────────────────────────────────


def test_tib_str():
    assert _tib_str(0) == "0 B"
    assert _tib_str(512) == "512 B"
    assert _tib_str(int(2.5 * TiB)) == "2.500 TiB"
    assert _tib_str(int(1.5 * GiB)) == "1.5 GB"  # below 1 TiB -> _size_str


# ── zs usage ───────────────────────────────────────────────────────────────


def test_usage_renders_pool_and_rows():
    r, out, _ = _run("usage")
    assert r.exit_code == 0
    assert "sata11" in out
    assert "Time Machine 备份" in out
    assert "Docker" in out
    assert "66.7% 已用" in out


def test_usage_json():
    r, out, _ = _run("usage", "--json")
    assert r.exit_code == 0
    import json
    d = json.loads(out)
    assert d["pools"][0]["pool"] == "sata11"
    assert d["pools"][0]["entries"][0]["label_cn"] == "个人文件"
    assert d["is_running"] == 0


def test_usage_refresh_polls_status():
    r, out, c = _run("usage", "--refresh")
    assert r.exit_code == 0
    c.disk_usage_refresh.assert_called_once()


def test_usage_refresh_waits_while_running():
    c_status = [{"is_running": 1, "updated_at": 1}, {"is_running": 0, "updated_at": 2}]
    r, out, c = _run(
        "usage", "--refresh", "--wait", "1",
        **{"disk_usage_status.side_effect": c_status + [{"is_running": 0}]}
    )
    assert r.exit_code == 0
    assert c.disk_usage_status.call_count >= 2


def test_usage_running_snapshot_warning():
    r, out, _ = _run("usage", **{"disk_usage_status.return_value":
                                 {"is_running": 1, "updated_at": 1}})
    assert r.exit_code == 0
    assert "正在重新统计" in out


def test_usage_api_error_exits_1():
    r, out, _ = _run("usage", **{"usage_summary.side_effect":
                                 ZSpaceError("500", "boom")})
    assert r.exit_code == 1
    assert "boom" in out


# ── zs du ──────────────────────────────────────────────────────────────────


def test_du_complete():
    r, out, _ = _run("du", "/a")
    assert r.exit_code == 0
    assert "OK" in out and "2.900 TiB" in out
    assert "7,378" in out


def test_du_json():
    r, out, _ = _run("du", "/a", "--json")
    import json
    d = json.loads(out)
    assert d[0]["complete"] is True and d[0]["source"] == "statistic"


def test_du_warns_on_non_converged_statistic():
    """A partial server-side snapshot must be labelled, never shown as final."""
    r, out, _ = _run("du", "/big", **{"statistic.return_value": DirStat(
        path="/big", size=int(0.35 * TiB), files=30261, state="running")})
    assert r.exit_code == 0
    assert "未收敛" in out and "部分值" in out
    assert "--walk" in out
    assert "不完整" in out


def test_du_walk_mode():
    r, out, c = _run("du", "/a", "--walk", "--workers", "4")
    assert r.exit_code == 0
    c.walk_stat.assert_called_once()
    assert "客户端遍历" in out and "42" in out


def test_du_walk_partial_is_flagged():
    r, out, _ = _run("du", "/a", "--walk", **{"walk_stat.return_value": DirStat(
        path="/a", size=TiB, files=3, dirs=1, state="partial", source="walk")})
    assert r.exit_code == 0
    assert "不完整 state=partial" in out


def test_du_multiple_paths():
    r, out, _ = _run("du", "/a", "/b")
    assert r.exit_code == 0
    assert out.count("OK") == 2


def test_du_error_exits_1():
    r, out, _ = _run("du", "/nope", **{"statistic.side_effect":
                                       ZSpaceError("N001315", "不存在")})
    assert r.exit_code == 1


def test_du_shows_hidden_breakdown():
    r, out, _ = _run("du", "/a", **{"statistic.return_value": DirStat(
        path="/a", size=TiB, files=5, dirs=2, state="done", hidden_size=int(2 * GiB),
        hidden_files=3)})
    assert "隐藏文件" in out


# ── zs disks ───────────────────────────────────────────────────────────────


def test_disks_renders_and_flags_unhealthy():
    r, out, _ = _run("disks")
    assert r.exit_code == 0
    assert "ST8000NM017B" in out
    assert "18,025" in out          # power-on hours, thousands-separated
    assert "warning" in out          # unhealthy disk surfaced
    assert "重映射扇区=5" in out
    assert "空闲盘位" in out and "sata 空 1 位" in out


def test_disks_json():
    r, out, _ = _run("disks", "--json")
    import json
    d = json.loads(out)
    assert d["free_bays"]["nvme"] == 3
    assert d["disks"][0]["sn"] == "SN1"
    assert d["disks"][1]["reallocated_sectors"] == 5


def test_disks_error_exits_1():
    r, out, _ = _run("disks", **{"disks.side_effect": ZSpaceError("500", "x")})
    assert r.exit_code == 1


# ── zs smart ───────────────────────────────────────────────────────────────


def test_smart_by_serial():
    r, out, c = _run("smart", "SN1")
    assert r.exit_code == 0
    c.smart.assert_called_once_with("SN1")
    assert "重映射扇区计数" in out
    assert "关键故障计数器均正常" in out


def test_smart_all_uses_every_disk():
    r, out, c = _run("smart", "--all")
    assert r.exit_code == 0
    assert c.smart.call_count == 2


def test_smart_no_target_defaults_to_all_disks():
    r, out, c = _run("smart")
    assert r.exit_code == 0
    assert c.smart.call_count == 2


def test_smart_no_disks_exits_1():
    r, out, _ = _run("smart", **{"disks.return_value": []})
    assert r.exit_code == 1
    assert "序列号" in out


def test_smart_json():
    r, out, _ = _run("smart", "SN1", "--json")
    import json
    assert json.loads(out)["SN1"]["health"] == "ok"


def test_smart_flags_non_ok_attribute():
    r, out, _ = _run("smart", "SN1", **{"smart.return_value": {
        "health": "warning",
        "attributes": [{"id": 5, "attr_name": "重映射扇区计数", "now_value": 90,
                        "worst": 90, "thresh": 10, "str_value": "12",
                        "health": "warning"}]}})
    assert r.exit_code == 0
    assert "非 ok" in out and "12" in out


def test_smart_nvme_without_attributes():
    r, out, _ = _run("smart", "NV1", **{"smart.return_value": {"health": "ok",
                                                               "attributes": []}})
    assert r.exit_code == 0
    assert "NVMe" in out


def test_smart_error_exits_1():
    r, out, _ = _run("smart", "BAD", **{"smart.side_effect":
                                         ZSpaceError("N300403", "参数错误")})
    assert r.exit_code == 1


# ── zs recycle ─────────────────────────────────────────────────────────────


def test_recycle_list_renders_items():
    r, out, _ = _run("recycle", "list")
    assert r.exit_code == 0
    assert "个人回收站" in out and "a.zip" in out
    assert "/sata11/my/data/a.zip" in out


def test_recycle_list_empty():
    r, out, _ = _run("recycle", "list", **{"recycle_list.return_value": ([], 0)})
    assert r.exit_code == 0
    assert "是空的" in out


def test_recycle_list_public_and_json():
    r, out, c = _run("recycle", "list", "--public", "--json")
    assert r.exit_code == 0
    c.recycle_list.assert_called_once()
    assert c.recycle_list.call_args.args[0] == "/.public_recycle"
    import json
    assert json.loads(out)["bin"] == "/.public_recycle"


def test_recycle_list_error_exits_1():
    r, out, _ = _run("recycle", "list",
                     **{"recycle_list.side_effect": ZSpaceError("N001411", "无权限")})
    assert r.exit_code == 1


def test_recycle_empty_with_force():
    r, out, c = _run("recycle", "empty", "--force")
    assert r.exit_code == 0
    c.recycle_empty.assert_called_once_with(public=False)
    assert "已清除 2 项" in out


def test_recycle_empty_confirms_interactively():
    r, out, c = _run("recycle", "empty", **{})
    # no --force and no tty input -> typer.confirm aborts
    assert r.exit_code != 0 or "确认" in out
    c.recycle_empty.assert_not_called()


def test_recycle_empty_warns_when_bin_already_empty():
    """The mistake this guards against: purging the public bin frees nothing."""
    r, out, _ = _run("recycle", "empty", "--force",
                     **{"recycle_empty.return_value": {"total_num": "0", "fail_num": "0"}})
    assert r.exit_code == 0
    assert "本来就是空的" in out
    assert "两个独立回收站" in out


def test_recycle_empty_public_flag():
    r, out, c = _run("recycle", "empty", "--public", "--force")
    assert r.exit_code == 0
    c.recycle_empty.assert_called_once_with(public=True)


def test_recycle_empty_error_exits_1():
    r, out, _ = _run("recycle", "empty", "--force",
                     **{"recycle_empty.side_effect": ZSpaceError("500", "x")})
    assert r.exit_code == 1


def test_recycle_purge_by_name():
    r, out, c = _run("recycle", "purge", "a.zip", "--force")
    assert r.exit_code == 0
    assert c.recycle_purge.call_args.args[0] == ["/sata11/.recycle/my/a.zip"]
    assert "已永久删除 1 项" in out


def test_recycle_purge_leaves_other_items_alone():
    """The point of purge vs empty: only the named path is passed through."""
    r, out, c = _run("recycle", "purge", "a.zip", "--force")
    assert r.exit_code == 0
    c.recycle_empty.assert_not_called()


def test_recycle_purge_unknown_name_exits_1():
    r, out, _ = _run("recycle", "purge", "ghost.bin")
    assert r.exit_code == 1
    assert "没有 ghost.bin" in out


def test_recycle_purge_confirms_interactively():
    r, out, c = _run("recycle", "purge", "a.zip")
    c.recycle_purge.assert_not_called()


def test_recycle_purge_reports_failures():
    r, out, _ = _run("recycle", "purge", "a.zip", "--force",
                     **{"recycle_purge.return_value": (0, ["/sata11/.recycle/my/a.zip"])})
    assert r.exit_code == 0
    assert "删除失败" in out


def test_recycle_restore_by_name():
    r, out, c = _run("recycle", "restore", "a.zip")
    assert r.exit_code == 0
    assert "已恢复 1 项" in out
    assert c.recycle_restore.call_args.args[0] == ["/sata11/.recycle/my/a.zip"]


def test_recycle_restore_unknown_name_exits_1():
    r, out, _ = _run("recycle", "restore", "ghost.bin")
    assert r.exit_code == 1
    assert "没有 ghost.bin" in out


def test_recycle_restore_reports_failures():
    r, out, _ = _run("recycle", "restore", "a.zip",
                     **{"recycle_restore.return_value": (0, ["/sata11/.recycle/my/a.zip"])})
    assert r.exit_code == 0
    assert "恢复失败" in out


def test_recycle_config_get_shows_never_warning():
    r, out, _ = _run("recycle", "config")
    assert r.exit_code == 0
    assert "永不自动清理" in out
    assert "一直占着" in out


def test_recycle_config_set():
    r, out, c = _run("recycle", "config", "--my-cycle", "30")
    assert r.exit_code == 0
    c.recycle_set_config.assert_called_once_with(my_cycle=30, public_cycle=None)
    assert "已更新保留策略" in out
    assert "30 天" in out


def test_recycle_config_json():
    r, out, _ = _run("recycle", "config", "--json")
    import json
    assert json.loads(out)["my_cycle"] == -1


def test_recycle_config_error_exits_1():
    r, out, _ = _run("recycle", "config",
                     **{"recycle_config.side_effect": ZSpaceError("500", "x")})
    assert r.exit_code == 1
