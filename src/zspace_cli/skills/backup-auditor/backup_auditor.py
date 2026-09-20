#!/usr/bin/env python3
"""backup-auditor skill 命令行入口

设计原则(沿袭 media-naming / photo-organizer 模式):
- 只读审计走脚本(备份集版本分析 / 陈旧检测 / 覆盖核对)
- 写操作(rm / mv 归档旧版本)不在脚本里 — LLM 生成轮转计划,
  用户确认后由 Agent 执行(挂载盘 shell,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用

两个子命令:
  scan     --root BACKUP_DIR                 备份集版本/陈旧/轮转分析
  coverage --source DATA_DIR --backup BACKUP 关键目录有无备份核对

用法:
  python backup_auditor.py scan --root /Volumes/nas/备份
  python backup_auditor.py coverage --source /Volumes/nas/data --backup /Volumes/nas/备份
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 4

ARCHIVE_EXTS = {
    "gz", "tgz", "zip", "tar", "bz2", "xz", "7z", "bak", "dmg", "img", "iso",
    "sparsebundle", "sparseimage", "vhd", "vhdx", "ova", "old", "bkf", "tib",
}
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", "System Volume Information", "$RECYCLE.BIN",
    ".snapshots", ".zspace_trash", ".trash", ".cache", "node_modules", ".git",
    "lost+found",
}
# 备份集识别:日期 / 时间 / 版本号 后缀
DATE_RE = re.compile(
    r"(20\d{2})[-._年/]?(0[1-9]|1[0-2])[-._月/]?(0[1-9]|[12]\d|3[01])日?"
)
TIME_SUFFIX_RE = re.compile(r"[-_ ]?\d{6,14}$")
VERSION_RE = re.compile(r"[-_ ]?[vV](\d+)$|[-_ ]?(\d{1,3})$|备份(\d+)$|副本(\d+)$")
BACKUP_HINT_RE = re.compile(
    r"备份|backup|bak|快照|snapshot|镜像|时间机器|time\s?machine|归档", re.I
)


def strip_archive_ext(name: str) -> str:
    """去掉 .tar.gz / .zip 等归档扩展名(支持双扩展名)。"""
    lower = name.lower()
    for double in (".tar.gz", ".tar.bz2", ".tar.xz", ".sparsebundle"):
        if lower.endswith(double):
            return name[: -len(double)]
    stem = name
    if "." in stem:
        base, ext = stem.rsplit(".", 1)
        if ext.lower() in ARCHIVE_EXTS:
            return base
    return stem


def parse_backup_name(name: str) -> tuple[str, str | None, int | None]:
    """拆分备份项名 → (base_key, date 'YYYY-MM-DD' 或 None, version 或 None)。

    base_key 用于把同一备份目标的多个版本聚成一组。
    """
    stem = strip_archive_ext(name)
    date = None
    m = DATE_RE.search(stem)
    if m:
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        stem = (stem[: m.start()] + stem[m.end():]).strip(" -_.")

    version = None
    mv = VERSION_RE.search(stem)
    if mv:
        version = int(next(g for g in mv.groups() if g))
        stem = stem[: mv.start()].strip(" -_.")

    stem = TIME_SUFFIX_RE.sub("", stem).strip(" -_.")
    base_key = re.sub(r"[\s_\-.]+", "", stem).lower()
    return base_key or stem.lower(), date, version


def dir_size(path: Path | str, max_files: int = 20000) -> tuple[int, int]:
    """递归求目录 (字节数, 文件数);带文件数上限防止超大目录卡死。"""
    total, count = 0, 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            if e.is_symlink():
                continue
            if e.is_dir():
                if e.name in SKIP_DIRS or e.name.startswith("."):
                    continue
                stack.append(e.path)
            elif e.is_file():
                count += 1
                if count > max_files:
                    return total, count
                try:
                    total += e.stat().st_size
                except OSError:
                    pass
    return total, count


class Auditor:
    def __init__(self, stale_days: int, keep: int) -> None:
        self.stale_days = stale_days
        self.keep = keep

    # -- 收集备份项 ----------------------------------------------------

    def _collect_items(self, backup_root: Path) -> list[dict]:
        items: list[dict] = []
        try:
            entries = sorted(os.scandir(backup_root), key=lambda e: e.name)
        except OSError as e:
            raise SystemExit(f"❌ 无法读取备份目录 {backup_root}: {e}")
        for entry in entries:
            name = entry.name
            if name in SKIP_DIRS or name.startswith("."):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            if entry.is_dir():
                size, fcount = dir_size(entry.path)
            elif entry.is_file():
                size, fcount = st.st_size, 1
            else:
                continue
            base_key, date, version = parse_backup_name(name)
            items.append({
                "name": name,
                "path": str(entry.path),
                "rel": name,
                "is_dir": entry.is_dir(),
                "size": size,
                "file_count": fcount,
                "mtime": int(st.st_mtime),
                "mtime_date": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                "date": date,
                "version": version,
                "base_key": base_key,
                "is_backup_named": bool(BACKUP_HINT_RE.search(name)),
            })
        return items

    def _group_sets(self, items: list[dict]) -> dict[str, list[dict]]:
        sets: dict[str, list[dict]] = {}
        for it in items:
            sets.setdefault(it["base_key"], []).append(it)
        for members in sets.values():
            members.sort(key=lambda x: (x["date"] or x["mtime_date"], x["version"] or 0))
        return sets

    # -- scan:备份集分析 ----------------------------------------------

    def scan(self, backup_root: Path) -> dict:
        print(f"正在审计备份目录 {backup_root} ...\n", file=sys.stderr)
        if not backup_root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {backup_root}")
        items = self._collect_items(backup_root)
        sets = self._group_sets(items)
        now = datetime.now()

        issues: list[dict] = []
        stats = {
            "backup_items": len(items),
            "backup_sets": len(sets),
            "total_size_bytes": sum(i["size"] for i in items),
            "stale_sets": 0, "single_version_sets": 0, "empty_items": 0,
            "rotatable_versions": 0, "reclaimable_bytes": 0,
            "multi_version_sets": 0,
        }

        for base_key, members in sorted(sets.items()):
            newest = max(members, key=lambda x: x["mtime"])
            newest_age = (now.timestamp() - newest["mtime"]) / 86400
            set_size = sum(m["size"] for m in members)
            set_problems: list[str] = []

            # 陈旧:最新版本也超过 stale_days 没更新 → 备份作业可能挂了
            if newest_age > self.stale_days:
                stats["stale_sets"] += 1
                set_problems.append(
                    f"备份陈旧(最新 {newest['mtime_date']},"
                    f"{int(newest_age)} 天未更新,检查备份作业)")

            # 单版本:无冗余
            if len(members) == 1:
                stats["single_version_sets"] += 1
                set_problems.append("单版本备份(无历史冗余,建议保留 ≥2 份)")

            if len(members) > 1:
                stats["multi_version_sets"] += 1

            # 空备份项
            empty = [m for m in members if m["file_count"] == 0 or m["size"] == 0]
            if empty:
                stats["empty_items"] += len(empty)
                for m in empty:
                    issues.append({
                        "path": m["rel"], "name": m["name"], "is_dir": m["is_dir"],
                        "problems": ["空备份项(0 字节/0 文件,疑似失败)"],
                        "action": "investigate", "set": base_key, "size": m["size"],
                    })

            # 轮转:版本数超过 keep,最旧的列为可归档/删除
            if len(members) > self.keep:
                rotate_out = members[: len(members) - self.keep]
                stats["rotatable_versions"] += len(rotate_out)
                for m in rotate_out:
                    stats["reclaimable_bytes"] += m["size"]
                    issues.append({
                        "path": m["rel"], "name": m["name"], "is_dir": m["is_dir"],
                        "problems": [
                            f"可轮转旧版本(该组已 {len(members)} 份,保留最近 {self.keep})"
                        ],
                        "action": "rotate-out", "set": base_key, "size": m["size"],
                        "date": m["date"] or m["mtime_date"],
                    })

            if set_problems:
                issues.append({
                    "path": members[-1]["rel"], "name": base_key, "is_dir": True,
                    "problems": set_problems, "action": "review-set",
                    "set": base_key, "versions": len(members),
                    "size": set_size, "newest": newest["mtime_date"],
                })

        print(
            f'审计完成: {stats["backup_sets"]} 个备份集, '
            f'{stats["backup_items"]} 个备份项\n', file=sys.stderr,
        )
        return {
            "skill": "backup-auditor", "mode": "scan",
            "backup_root": str(backup_root),
            "params": {"stale_days": self.stale_days, "keep": self.keep},
            "generated_at": now.isoformat(timespec="seconds"),
            "stats": stats, "count": len(issues), "issues": issues,
        }

    # -- coverage:关键目录覆盖核对 ------------------------------------

    def coverage(self, source_root: Path, backup_root: Path) -> dict:
        print(f"核对覆盖: 源 {source_root} ↔ 备份 {backup_root} ...\n",
              file=sys.stderr)
        for p, label in ((source_root, "--source"), (backup_root, "--backup")):
            if not p.is_dir():
                raise SystemExit(f"❌ {label} 不是有效目录: {p}")

        def top_dirs(root: Path) -> dict[str, str]:
            out: dict[str, str] = {}
            for e in sorted(os.scandir(root), key=lambda x: x.name):
                if e.is_dir() and e.name not in SKIP_DIRS \
                        and not e.name.startswith("."):
                    key = re.sub(r"[\s_\-.]+", "", e.name).lower()
                    out[key] = e.name
            return out

        src_dirs = top_dirs(source_root)
        backup_items = self._collect_items(backup_root)
        backup_sets = self._group_sets(backup_items)
        now = datetime.now()

        # 备份集 base_key → 最新版本
        backup_index: dict[str, dict] = {}
        for key, members in backup_sets.items():
            backup_index[key] = max(members, key=lambda x: x["mtime"])

        issues: list[dict] = []
        covered = missing = orphan = stale = 0
        for key, src_name in sorted(src_dirs.items()):
            match = None
            for bkey in backup_index:
                if key and (key in bkey or bkey in key):
                    match = bkey
                    break
            if match:
                covered += 1
                latest = backup_index[match]
                age = (now.timestamp() - latest["mtime"]) / 86400
                if age > self.stale_days:
                    stale += 1
                    issues.append({
                        "path": src_name, "name": src_name, "is_dir": True,
                        "problems": [
                            f"有备份但陈旧(最新 {latest['mtime_date']},"
                            f"{int(age)} 天前)"],
                        "action": "refresh-backup",
                        "backup": latest["rel"],
                    })
            else:
                missing += 1
                issues.append({
                    "path": src_name, "name": src_name, "is_dir": True,
                    "problems": ["关键目录无对应备份"], "action": "add-backup",
                })

        for bkey, latest in backup_index.items():
            if not any(key and (key in bkey or bkey in key) for key in src_dirs):
                orphan += 1
                issues.append({
                    "path": latest["rel"], "name": latest["name"],
                    "is_dir": latest["is_dir"],
                    "problems": ["孤儿备份(源目录已不存在/改名,确认后清理)"],
                    "action": "review-orphan", "size": latest["size"],
                })

        stats = {
            "source_dirs": len(src_dirs), "backup_sets": len(backup_sets),
            "covered": covered, "missing": missing, "orphan": orphan,
            "stale": stale,
        }
        print(
            f'核对完成: {covered} 已覆盖, {missing} 缺失, '
            f'{stale} 陈旧, {orphan} 孤儿\n', file=sys.stderr,
        )
        return {
            "skill": "backup-auditor", "mode": "coverage",
            "source_root": str(source_root), "backup_root": str(backup_root),
            "params": {"stale_days": self.stale_days},
            "generated_at": now.isoformat(timespec="seconds"),
            "stats": stats, "count": len(issues), "issues": issues,
        }


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def _print_scan(result: dict, top: int) -> None:
    s = result["stats"]
    p = result["params"]
    print("=" * 70)
    print("backup-auditor — 备份审计报告")
    print("=" * 70)
    print(f"备份目录: {result['backup_root']}")
    print(f"参数: 陈旧阈值 {p['stale_days']} 天 | 每组保留 {p['keep']} 份")
    print(f"备份集 {s['backup_sets']} | 备份项 {s['backup_items']} "
          f"| 总大小 {_human(s['total_size_bytes'])}")
    print(f"多版本集 {s['multi_version_sets']} | 单版本集 {s['single_version_sets']} "
          f"| 陈旧集 {s['stale_sets']} | 空备份 {s['empty_items']}")
    print(f"可轮转旧版本 {s['rotatable_versions']} "
          f"(可回收约 {_human(s['reclaimable_bytes'])})")

    issues = result["issues"]
    if not issues:
        print("\n✅ 备份健康:无陈旧、无冗余、无空备份!")
        return
    by_action: dict[str, list] = {}
    for issue in issues:
        by_action.setdefault(issue["action"], []).append(issue)
    print(f"\n⚠ {len(issues)} 个审计项:\n")
    for action, items in sorted(by_action.items(), key=lambda x: -len(x[1])):
        print(f"【{action}】{len(items)} 项")
        for item in items[:top]:
            size = f"  {_human(item['size'])}" if item.get("size") else ""
            print(f"    {item['path']}{size}")
            for prob in item["problems"]:
                print(f"      - {prob}")
        if len(items) > top:
            print(f"    ... 还有 {len(items) - top} 项")
        print()


def _print_coverage(result: dict, top: int) -> None:
    s = result["stats"]
    print("=" * 70)
    print("backup-auditor — 关键目录覆盖核对")
    print("=" * 70)
    print(f"源目录: {result['source_root']}")
    print(f"备份目录: {result['backup_root']}")
    print(f"源关键目录 {s['source_dirs']} | 备份集 {s['backup_sets']}")
    print(f"已覆盖 {s['covered']} | 缺失 {s['missing']} "
          f"| 陈旧 {s['stale']} | 孤儿备份 {s['orphan']}")
    issues = result["issues"]
    if not issues:
        print("\n✅ 所有关键目录都有新鲜备份!")
        return
    by_action: dict[str, list] = {}
    for issue in issues:
        by_action.setdefault(issue["action"], []).append(issue)
    print(f"\n⚠ {len(issues)} 个核对项:\n")
    for action, items in sorted(by_action.items(), key=lambda x: -len(x[1])):
        print(f"【{action}】{len(items)} 项")
        for item in items[:top]:
            print(f"    {item['path']}")
            for prob in item["problems"]:
                print(f"      - {prob}")
        if len(items) > top:
            print(f"    ... 还有 {len(items) - top} 项")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="backup-auditor: 备份只读审计(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="备份集版本/陈旧/轮转分析(只读)")
    scan_p.add_argument("--root", required=True,
                        help="备份根目录,如 /Volumes/nas/备份")
    scan_p.add_argument("--stale-days", type=int, default=35,
                        help="最新版本超过 N 天视为陈旧(默认 35)")
    scan_p.add_argument("--keep", type=int, default=3,
                        help="每个备份集保留最近 N 份(默认 3)")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--top", type=int, default=10, help="每类显示条数")

    cov_p = sub.add_parser("coverage", help="关键目录有无备份核对(只读)")
    cov_p.add_argument("--source", required=True, help="源数据目录")
    cov_p.add_argument("--backup", required=True, help="备份目录")
    cov_p.add_argument("--stale-days", type=int, default=35,
                        help="备份超过 N 天视为陈旧(默认 35)")
    cov_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    cov_p.add_argument("--output", help="写入 JSON 文件路径")
    cov_p.add_argument("--top", type=int, default=10, help="每类显示条数")

    args = parser.parse_args()
    if args.cmd == "scan":
        auditor = Auditor(args.stale_days, args.keep)
        result = auditor.scan(Path(args.root).resolve())
        printer = _print_scan
    elif args.cmd == "coverage":
        auditor = Auditor(args.stale_days, 3)
        result = auditor.coverage(Path(args.source).resolve(),
                                  Path(args.backup).resolve())
        printer = _print_coverage
    else:
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {args.output}", file=sys.stderr)
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        printer(result, args.top)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
