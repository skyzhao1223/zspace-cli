#!/usr/bin/env python3
"""nas-report skill 命令行入口

设计原则(沿袭 media-naming / photo-organizer 模式):
- 只读扫描走脚本(全盘存储画像:体积/文件数/增长/大文件榜/冷热分层)
- 本 skill 是「元技能」:出画像 + 按发现的问题路由到专门的整理 skill
- 写操作一律不在脚本里 — 交给被路由到的 skill 或 Agent
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用

用法:
  python nas_report.py report --root /Volumes/nas/data
  python nas_report.py report --root ... --json --output /tmp/report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 10
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", ".AppleDB", ".AppleDesktop", ".apdisk",
    "System Volume Information", "$RECYCLE.BIN", ".snapshots", ".zspace_trash",
    ".trash", "node_modules", ".git", ".svn", "__pycache__", ".venv", "venv",
    "lost+found",
}

# 扩展名 → 类别
CATEGORIES: dict[str, set[str]] = {
    "video": {"mp4", "mkv", "avi", "mov", "ts", "rmvb", "flv", "wmv", "mpg",
              "mpeg", "m2ts", "m4v", "webm", "3gp", "iso"},
    "audio": {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "ape", "wma",
              "aiff", "dsf", "m4b"},
    "photo": {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp",
              "tif", "tiff", "raw", "cr2", "nef", "arw", "dng", "raf", "orf"},
    "doc": {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "pdf", "txt", "md",
            "csv", "key", "numbers", "pages", "wps", "et", "dps", "rtf", "odt",
            "epub", "mobi", "azw3"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "zst"},
    "installer": {"dmg", "pkg", "exe", "msi", "apk", "deb", "rpm", "appimage",
                  "ipa", "msix"},
    "design": {"psd", "psb", "ai", "eps", "sketch", "fig", "xd", "cdr", "aep",
               "blend", "c4d", "prproj", "indd", "dwg", "dxf"},
    "code": {"py", "js", "ts", "go", "rs", "java", "c", "cpp", "h", "sh",
             "sql", "html", "css", "json", "yaml", "yml", "toml", "xml",
             "ipynb", "rb", "php", "swift", "kt"},
    "backup": {"bak", "vhd", "vhdx", "ova", "tib", "bkf", "sparsebundle",
               "sparseimage", "dsk", "img"},
}
_EXT_TO_CAT = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
JUNK_EXTS = {"tmp", "temp", "bak", "old", "swp", "part", "crdownload", "td",
             "download", "aria2", "log"}
TORRENT_EXTS = {"torrent"}


def categorize(ext: str) -> str:
    return _EXT_TO_CAT.get(ext, "other")


def growth_bucket(days: float) -> str:
    """按 mtime 距今天数分冷热层。"""
    if days <= 30:
        return "hot_30d"        # 近 30 天:活跃
    if days <= 365:
        return "warm_1y"        # 30 天~1 年:温
    if days <= 365 * 3:
        return "cool_3y"        # 1~3 年:凉
    return "cold_3y+"           # 3 年以上:冷(归档候选)


class Reporter:
    def __init__(self, root: str, max_depth: int, max_files: int,
                 top_n: int) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.max_files = max_files
        self.top_n = top_n
        self.stats: dict = {
            "total_files": 0, "total_dirs": 0, "total_size_bytes": 0,
            "by_category": {}, "by_growth": {}, "junk_files": 0,
            "junk_bytes": 0, "empty_dirs": 0, "largest_files": [],
            "largest_dirs": [], "elapsed_sec": 0.0, "truncated": False,
        }
        self._cat: dict[str, dict] = {}
        self._growth: dict[str, dict] = {}
        self._largest: list[tuple[int, str, str]] = []
        self._toplevel: dict[str, dict] = {}
        self._dir_sizes: list[tuple[int, str, int]] = []
        self._n_files = 0

    def _bump(self, store: dict, key: str, size: int) -> None:
        e = store.setdefault(key, {"count": 0, "size": 0})
        e["count"] += 1
        e["size"] += size

    def _walk(self, dir_path: Path, rel_parts: list[str], top_key: str | None,
              ) -> tuple[int, int]:
        """返回 (子树字节数, 子树文件数);用于顶层目录汇总与大目录榜。"""
        if len(rel_parts) > self.max_depth:
            return 0, 0
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as e:
            print(f"⚠️ 无法读取 {dir_path}: {e}", file=sys.stderr)
            return 0, 0
        self.stats["total_dirs"] += 1
        subtree_bytes = 0
        subtree_files = 0
        has_child = False
        for entry in entries:
            if self.max_files and self._n_files >= self.max_files:
                self.stats["truncated"] = True
                return subtree_bytes, subtree_files
            name = entry.name
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                has_child = True
                tk = top_key if top_key is not None else (
                    name if not rel_parts else None)
                b, f = self._walk(entry.path, rel_parts + [name], tk)
                subtree_bytes += b
                subtree_files += f
            elif entry.is_file():
                has_child = True
                self._n_files += 1
                size, mtime = self._stat(entry)
                self._account(name, size, mtime, top_key, rel_parts)
                subtree_bytes += size
                subtree_files += 1
        if not has_child:
            self.stats["empty_dirs"] += 1
        if rel_parts:
            self._dir_sizes.append((subtree_bytes, "/".join(rel_parts),
                                    subtree_files))
        return subtree_bytes, subtree_files

    def _stat(self, entry: os.DirEntry) -> tuple[int, float | None]:
        try:
            st = entry.stat()
            return st.st_size, st.st_mtime
        except OSError:
            return 0, None

    def _account(self, name: str, size: int, mtime: float | None,
                 top_key: str | None, rel_parts: list[str]) -> None:
        self.stats["total_files"] += 1
        self.stats["total_size_bytes"] += size

        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if name in JUNK_NAMES or name.startswith("._") or ext in JUNK_EXTS \
                or ext in TORRENT_EXTS:
            self.stats["junk_files"] += 1
            self.stats["junk_bytes"] += size

        cat = categorize(ext)
        self._bump(self._cat, cat, size)

        if mtime:
            days = (datetime.now().timestamp() - mtime) / 86400
            self._bump(self._growth, growth_bucket(days), size)

        if top_key is not None:
            self._bump(self._toplevel, top_key, size)

        full = "/".join(rel_parts + [name])
        self._largest.append((size, full, cat))

    def report(self) -> dict:
        t0 = time.time()
        if not self.root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {self.root}")
        print(f"正在生成存储画像 {self.root} ...\n", file=sys.stderr)
        self._walk(self.root, [], None)

        self.stats["by_category"] = dict(
            sorted(self._cat.items(), key=lambda x: -x[1]["size"]))
        self.stats["by_growth"] = self._growth
        self._largest.sort(reverse=True)
        self.stats["largest_files"] = [
            {"path": p, "size": s, "category": c}
            for s, p, c in self._largest[: self.top_n]
        ]
        self._dir_sizes.sort(reverse=True)
        self.stats["largest_dirs"] = [
            {"path": p, "size": s, "files": f}
            for s, p, f in self._dir_sizes[: self.top_n]
        ]
        self.stats["toplevel"] = dict(
            sorted(self._toplevel.items(), key=lambda x: -x[1]["size"]))
        self.stats["elapsed_sec"] = round(time.time() - t0, 2)

        result = {
            "skill": "nas-report",
            "root": str(self.root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": self.stats,
            "recommendations": self._recommend(),
        }
        print(
            f'画像完成: {self.stats["total_files"]} 文件, '
            f'{_human(self.stats["total_size_bytes"])}\n', file=sys.stderr,
        )
        return result

    # -- 路由建议:按画像推荐该跑哪个专门 skill -------------------------

    def _recommend(self) -> list[dict]:
        recs: list[dict] = []
        cat = self._cat
        total = self.stats["total_size_bytes"] or 1

        def top_cat(name: str) -> dict:
            return cat.get(name, {"count": 0, "size": 0})

        if top_cat("video")["size"] > total * 0.15:
            recs.append({
                "skill": "media-naming / media-manager-skill",
                "why": f"影视占比大({_human(top_cat('video')['size'])}),"
                       "建议做命名规范扫描",
            })
        if top_cat("photo")["count"] > 500:
            recs.append({
                "skill": "photo-organizer",
                "why": f"照片多({top_cat('photo')['count']} 张),建议按日期归档",
            })
        if top_cat("audio")["count"] > 100:
            recs.append({
                "skill": "music-organizer",
                "why": f"音频多({top_cat('audio')['count']} 首),建议整理歌手/专辑结构",
            })
        if top_cat("doc")["count"] > 100:
            recs.append({
                "skill": "work-organizer",
                "why": f"文档多({top_cat('doc')['count']} 个),建议按年份/项目归档",
            })
        if top_cat("design")["count"] > 20:
            recs.append({
                "skill": "portfolio-organizer",
                "why": f"设计源文件多({top_cat('design')['count']} 个),建议按项目整理",
            })
        cold = self._growth.get("cold_3y+", {"size": 0})["size"]
        if cold > total * 0.2:
            recs.append({
                "skill": "(冷热分层)",
                "why": f"3 年以上未动的冷数据 {_human(cold)},建议归档到冷存储",
            })
        if self.stats["junk_files"] > 50:
            recs.append({
                "skill": "download-cleaner",
                "why": f"垃圾/临时/种子文件 {self.stats['junk_files']} 个"
                       f"({_human(self.stats['junk_bytes'])}),建议清理",
            })
        if self.stats["total_files"] > 1000:
            recs.append({
                "skill": "dedup-finder",
                "why": f"文件数多({self.stats['total_files']}),"
                       "建议跑内容级去重找可回收空间",
            })
        if self.stats["empty_dirs"] > 20:
            recs.append({
                "skill": "(空目录清理)",
                "why": f"空目录 {self.stats['empty_dirs']} 个,建议清理",
            })
        # 备份目录探测
        for name in self._toplevel:
            low = name.lower()
            if any(k in low for k in ("备份", "backup", "快照", "snapshot",
                                      "time machine", "timemachine")):
                recs.append({
                    "skill": "backup-auditor",
                    "why": f"发现疑似备份目录「{name}」,建议审计新鲜度与轮转",
                })
                break
        return recs


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


CAT_ZH = {
    "video": "影视", "audio": "音频", "photo": "照片", "doc": "文档",
    "archive": "压缩包", "installer": "安装包", "design": "设计源文件",
    "code": "代码", "backup": "备份镜像", "other": "其他",
}
GROWTH_ZH = {
    "hot_30d": "活跃(≤30天)", "warm_1y": "温(30天~1年)",
    "cool_3y": "凉(1~3年)", "cold_3y+": "冷(3年+)",
}


def _print_human(result: dict, top: int) -> None:
    s = result["stats"]
    print("=" * 70)
    print("nas-report — NAS 存储画像")
    print("=" * 70)
    print(f"根目录: {result['root']}")
    print(f"总览: {s['total_files']} 文件 | {s['total_dirs']} 目录 "
          f"| {_human(s['total_size_bytes'])} | 耗时 {s['elapsed_sec']}s")
    if s.get("truncated"):
        print("(已达 --max-files 上限,画像不完整)")

    print("\n【按类别】")
    for cat, e in s["by_category"].items():
        pct = e["size"] / (s["total_size_bytes"] or 1) * 100
        print(f"  {CAT_ZH.get(cat, cat):<8} {e['count']:>7} 个  "
              f"{_human(e['size']):>10}  {pct:>5.1f}%")

    if s["by_growth"]:
        print("\n【冷热分层】(按最后修改时间)")
        for g in ("hot_30d", "warm_1y", "cool_3y", "cold_3y+"):
            if g in s["by_growth"]:
                e = s["by_growth"][g]
                print(f"  {GROWTH_ZH[g]:<14} {e['count']:>7} 个  "
                      f"{_human(e['size']):>10}")

    if s.get("toplevel"):
        print("\n【顶层目录 Top】")
        for name, e in list(s["toplevel"].items())[:top]:
            print(f"  {_human(e['size']):>10}  {e['count']:>6} 个  {name}")

    if s["largest_dirs"]:
        print(f"\n【最大目录 Top{top}】")
        for d in s["largest_dirs"][:top]:
            print(f"  {_human(d['size']):>10}  {d['files']:>6} 个  {d['path']}")

    if s["largest_files"]:
        print(f"\n【最大文件 Top{top}】")
        for f in s["largest_files"][:top]:
            print(f"  {_human(f['size']):>10}  [{CAT_ZH.get(f['category'], '?')}] "
                  f"{f['path']}")

    print(f"\n垃圾/临时/种子: {s['junk_files']} 个 "
          f"({_human(s['junk_bytes'])}) | 空目录: {s['empty_dirs']} 个")

    recs = result["recommendations"]
    print("\n" + "=" * 70)
    if recs:
        print("【下一步建议】(路由到专门 skill)")
        for r in recs:
            print(f"  → {r['skill']}")
            print(f"      {r['why']}")
    else:
        print("【下一步建议】库很规整,暂无特别建议。")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="nas-report: NAS 存储画像 + skill 路由(只读,各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    rep_p = sub.add_parser("report", help="生成存储画像(只读)")
    rep_p.add_argument("--root", required=True,
                       help="扫描根目录(本地挂载路径),如 /Volumes/nas/data")
    rep_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    rep_p.add_argument("--output", help="写入 JSON 文件路径")
    rep_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                       help=f"最大递归深度(默认 {MAX_DEPTH})")
    rep_p.add_argument("--max-files", type=int, default=0,
                       help="最多统计文件数(0=不限,大库先摸底)")
    rep_p.add_argument("--top", type=int, default=10,
                       help="各榜单显示条数(默认 10)")

    args = parser.parse_args()
    if args.cmd != "report":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    reporter = Reporter(args.root, args.max_depth, args.max_files, args.top)
    result = reporter.report()
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
