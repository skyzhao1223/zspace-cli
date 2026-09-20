#!/usr/bin/env python3
"""download-cleaner skill 命令行入口

设计原则(沿袭 media-naming / photo-organizer 模式):
- 只读扫描走脚本(下载区分诊:按类别给出处理建议)
- 写操作(rm / mv)不在脚本里 — LLM 生成清理/归档计划,
  用户确认后由 Agent 执行(挂载盘 shell,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用

用法:
  python download_cleaner.py scan --root /Volumes/nas/下载
  python download_cleaner.py scan --root ... --json --output /tmp/downloads.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 6

PARTIAL_EXTS = {
    "part", "crdownload", "download", "td", "aria2", "qb", "xcdownload",
    "opdownload", "partial", "fxdownload", "bdp",
}
TORRENT_EXTS = {"torrent"}
INSTALLER_EXTS = {
    "dmg", "pkg", "exe", "msi", "apk", "deb", "rpm", "appimage", "msix", "ipa",
}
ARCHIVE_EXTS = {
    "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "zst", "sit", "lz",
}
VIDEO_EXTS = {"mp4", "mov", "m4v", "avi", "mkv", "wmv", "flv", "ts", "rmvb", "mpg"}
AUDIO_EXTS = {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "ape", "wma"}
PHOTO_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tif", "tiff"}
DOC_EXTS = {
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "pdf", "txt", "md", "csv",
    "key", "numbers", "pages", "wps", "et", "dps", "epub", "mobi", "azw3",
}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", "System Volume Information", "$RECYCLE.BIN",
    ".snapshots", ".zspace_trash", ".trash", ".cache", "node_modules", ".git",
    "lost+found",
}
DUP_MARK_RE = re.compile(r"(?:\s*\(\d+\)|\s*副本\d*|\s+copy(?:\s*\d+)?)$", re.I)
# 建议归档去向(按类别)
ARCHIVE_HINT = {
    "video": "影视库(整理走 media-naming)",
    "audio": "音乐库(整理走 music-organizer)",
    "photo": "照片库(整理走 photo-organizer)",
    "doc": "工作文档区(整理走 work-organizer)",
}

CATEGORIES = (
    "junk", "partial", "torrent", "installer", "archive",
    "video", "audio", "photo", "doc", "other",
)


def categorize(name: str, ext: str) -> str:
    """纯函数:按文件名/扩展名给下载文件分类。"""
    if name in JUNK_NAMES or name.startswith("._") or ext in ("tmp", "temp", "bak"):
        return "junk"
    if ext in PARTIAL_EXTS or name.endswith(".bt.td"):
        return "partial"
    if ext in TORRENT_EXTS:
        return "torrent"
    if ext in INSTALLER_EXTS:
        return "installer"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in DOC_EXTS:
        return "doc"
    return "other"


def advise(category: str, age_days: float, extracted: bool,
           stale_days: int) -> tuple[list[str], str]:
    """纯函数:类别+文件年龄 → (问题列表, 建议动作)。"""
    problems: list[str] = []
    if category == "junk":
        return ["垃圾/系统文件(可删)"], "delete"
    if category == "partial":
        return ["未完成下载残留(无法续传则可删)"], "delete-confirm"
    if category == "torrent":
        return ["种子文件(任务完成后可删)"], "delete-confirm"
    if category == "installer":
        if age_days > stale_days:
            problems.append(f"老旧安装包({int(age_days)} 天未动,大概率已过时)")
            return problems, "delete-confirm"
        return ["安装包(确认已安装后可删)"], "review"
    if category == "archive":
        if extracted:
            return ["压缩包已解压(同名目录存在,原包可删)"], "delete-confirm"
        return ["压缩包未解压(确认内容后解压归档或删包)"], "extract-or-review"
    if category in ARCHIVE_HINT:
        problems.append(f"建议归档到对应库:{ARCHIVE_HINT[category]}")
        if age_days > stale_days:
            problems.append(f"下载后 {int(age_days)} 天未处理")
        return problems, "move-to-library"
    if age_days > stale_days:
        return [f"杂项文件 {int(age_days)} 天未处理(确认去留)"], "review"
    return [], "keep"


class Scanner:
    def __init__(self, root: str, max_depth: int, sample: int,
                 stale_days: int) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.sample = sample
        self.stale_days = stale_days
        self.stats: dict = {
            "files": 0, "dirs": 0, "total_size_bytes": 0,
            "by_category": {c: {"count": 0, "size": 0} for c in CATEGORIES},
            "reclaimable_bytes": 0, "duplicate_downloads": 0,
            "stale_files": 0, "largest": [], "sampled_out": False,
        }
        self.issues: list[dict] = []
        self._sizes: list[tuple[int, str]] = []
        self._n_files = 0

    # -- 遍历 ----------------------------------------------------------

    def _walk(self, dir_path: Path | str, rel_parts: list[str]) -> None:
        if len(rel_parts) > self.max_depth:
            return
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as e:
            print(f"⚠️ 无法读取 {dir_path}: {e}", file=sys.stderr)
            return
        # 本层目录名集合:用于「压缩包已解压」判定
        dir_names = {e.name for e in entries if e.is_dir()}
        for entry in entries:
            if self.sample and self._n_files >= self.sample:
                self.stats["sampled_out"] = True
                return
            name = entry.name
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                self.stats["dirs"] += 1
                self._walk(entry.path, rel_parts + [name])
            elif entry.is_file():
                self._n_files += 1
                self._check_file(entry, rel_parts, name, dir_names)

    def _check_file(self, entry: os.DirEntry, rel_parts: list[str],
                    name: str, sibling_dirs: set[str]) -> None:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        stem = os.path.splitext(name)[0]
        full = "/".join(rel_parts + [name])
        if name.startswith(".") and name not in JUNK_NAMES:
            return

        try:
            st = entry.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, None
        age_days = ((datetime.now().timestamp() - mtime) / 86400) if mtime else 0.0

        category = categorize(name, ext)
        # 压缩包:同目录存在同名(去扩展名)目录 → 视为已解压
        extracted = category == "archive" and (
            stem in sibling_dirs
            or any(stem.endswith("." + a) and stem[: -(len(a) + 1)] in sibling_dirs
                   for a in ARCHIVE_EXTS)
        )
        problems, action = advise(category, age_days, extracted, self.stale_days)

        self.stats["files"] += 1
        self.stats["total_size_bytes"] += size
        cat = self.stats["by_category"][category]
        cat["count"] += 1
        cat["size"] += size
        if category in ("junk", "partial", "torrent") or extracted \
                or (category == "installer" and age_days > self.stale_days):
            self.stats["reclaimable_bytes"] += size
        if DUP_MARK_RE.search(stem):
            self.stats["duplicate_downloads"] += 1
            problems = problems + ["重复下载(带 (1)/副本 后缀,建议二选一)"]
        if age_days > self.stale_days and category not in ("junk", "partial"):
            self.stats["stale_files"] += 1
        self._sizes.append((size, full))

        if problems:
            self.issues.append({
                "path": full, "name": name, "is_dir": False,
                "problems": problems, "category": category,
                "action": action, "size": size, "age_days": int(age_days),
            })

    # -- 入口 ----------------------------------------------------------

    def scan(self) -> dict:
        print(f"正在扫描 {self.root} ...\n", file=sys.stderr)
        if not self.root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {self.root}")
        self._walk(self.root, [])
        self._sizes.sort(reverse=True)
        self.stats["largest"] = [
            {"path": p, "size": s} for s, p in self._sizes[:10]
        ]
        print(
            f'扫描完成: {self.stats["files"]} 文件\n', file=sys.stderr,
        )
        return {
            "skill": "download-cleaner",
            "root": str(self.root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": self.stats,
            "count": len(self.issues),
            "issues": self.issues,
        }


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


CATEGORY_ZH = {
    "junk": "垃圾", "partial": "未完成下载", "torrent": "种子",
    "installer": "安装包", "archive": "压缩包", "video": "视频",
    "audio": "音频", "photo": "图片", "doc": "文档", "other": "杂项",
}


def _print_human(result: dict, top: int) -> None:
    s = result["stats"]
    print("=" * 70)
    print("download-cleaner — 下载区分诊报告")
    print("=" * 70)
    print(f"根目录: {result['root']}")
    print(f"文件 {s['files']} | 总大小 {_human(s['total_size_bytes'])} "
          f"| 可直接回收约 {_human(s['reclaimable_bytes'])}")
    print("\n类别分布:")
    for cname, cv in s["by_category"].items():
        if cv["count"]:
            print(f"  {CATEGORY_ZH[cname]:<6} {cv['count']:>6} 个  "
                  f"{_human(cv['size']):>10}")
    print(f"\n重复下载 {s['duplicate_downloads']} | 久未处理 {s['stale_files']}")
    if s["largest"]:
        print("\n最大文件 Top10:")
        for item in s["largest"]:
            print(f"  {_human(item['size']):>10}  {item['path']}")
    if s.get("sampled_out"):
        print("\n(已达 --sample 上限,结果不完整)")

    issues = result["issues"]
    if not issues:
        print("\n✅ 下载区很干净!")
        return

    by_action: dict[str, list] = {}
    for issue in issues:
        by_action.setdefault(issue["action"], []).append(issue)

    print(f"\n⚠ {len(issues)} 个待处理项(按建议动作分组):\n")
    for action, items in sorted(by_action.items(), key=lambda x: -len(x[1])):
        print(f"【{action}】{len(items)} 项")
        for item in items[:top]:
            age = f" ({item['age_days']}天)" if item["age_days"] else ""
            print(f"    {item['path']}  {_human(item['size'])}{age}")
            for p in item["problems"]:
                print(f"      - {p}")
        if len(items) > top:
            print(f"    ... 还有 {len(items) - top} 项")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="download-cleaner: 下载区只读分诊扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="下载区分诊扫描(只读)")
    scan_p.add_argument("--root", required=True,
                        help="下载目录(本地挂载路径),如 /Volumes/nas/下载")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                        help=f"最大递归深度(默认 {MAX_DEPTH})")
    scan_p.add_argument("--sample", type=int, default=0,
                        help="最多扫描文件数(0=不限)")
    scan_p.add_argument("--top", type=int, default=8,
                        help="人类可读输出每类最多显示条数")
    scan_p.add_argument("--stale-days", type=int, default=365,
                        help="超过 N 天视为久未处理(默认 365)")

    args = parser.parse_args()
    if args.cmd != "scan":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    scanner = Scanner(args.root, args.max_depth, args.sample, args.stale_days)
    result = scanner.scan()
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {args.output}", file=sys.stderr)
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        _print_human(result, args.top)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
