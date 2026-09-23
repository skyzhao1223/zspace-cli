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

    def _walk(self, dir_path: Path | str, rel_parts: list[str], top_key: str | None,
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


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def diff_reports(old: dict, new: dict, capacity_gb: float | None = None) -> dict:
    """两份 nas-report 快照的差分(纯函数,无 IO,便于单测)。

    边界要说在前面,因为它决定这份差分**能**回答什么:

    * 快照只保留 `--top N` 的 `largest_files` / `largest_dirs`,所以文件级
      的增减**不是全库 diff**,只是"两份榜单里出现的/消失的"。摘要里带
      `file_lists_truncated` 标明这一点,不要让读者以为它是全量。
    * 快照里**没有容量字段**(没有 pool / free / capacity),所以"离满盘还有多久"
      只能算**增长速率**(字节/天),要 ETA 必须由调用方给 `--capacity-gb`;
      不给就只报速率,并明写缺什么。
    * 两份快照的顺序反了会得到负增长——那不是一个"负增长的世界",是**输入顺序错了**,
      所以单列 `time_reversed` 而不是把负数混进速率里。
    """
    so = old.get("stats") or {}
    sn = new.get("stats") or {}

    def _delta(a: dict, b: dict) -> dict:
        out = {}
        for key in sorted(set(a) | set(b)):
            ea, eb = a.get(key) or {}, b.get(key) or {}
            before, after = ea.get("size") or 0, eb.get("size") or 0
            cbefore, cafter = ea.get("count") or 0, eb.get("count") or 0
            if after == before and cafter == cbefore:
                continue
            out[key] = {
                "bytes_before": before, "bytes_after": after,
                "bytes_added": after - before,
                "gb_added": round((after - before) / 1e9, 3),
                "count_before": cbefore, "count_after": cafter,
                "count_added": cafter - cbefore,
            }
        return dict(sorted(out.items(), key=lambda kv: -abs(kv[1]["bytes_added"])))

    t_old, t_new = _parse_ts(old.get("generated_at")), _parse_ts(new.get("generated_at"))
    days = (t_new - t_old).total_seconds() / 86400 if (t_old and t_new) else None
    time_reversed = bool(days is not None and days < 0)
    d_bytes = (sn.get("total_size_bytes") or 0) - (so.get("total_size_bytes") or 0)
    d_files = (sn.get("total_files") or 0) - (so.get("total_files") or 0)
    d_dirs = (sn.get("total_dirs") or 0) - (so.get("total_dirs") or 0)
    per_day = (d_bytes / days) if (days and days > 0) else None

    def _files(which: str) -> list[dict]:
        a = {f.get("path"): f for f in (so.get("largest_files") or []) if f.get("path")}
        b = {f.get("path"): f for f in (sn.get("largest_files") or []) if f.get("path")}
        keys = [k for k in (b if which == "added" else a)
                if k not in (a if which == "added" else b)]
        rows = [(b if which == "added" else a)[k] for k in keys]
        rows.sort(key=lambda r: -(r.get("size") or 0))
        return [{"path": r.get("path"), "size": r.get("size"),
                 "gb": round((r.get("size") or 0) / 1e9, 3),
                 "category": r.get("category")} for r in rows]

    eta_days = None
    if capacity_gb and per_day and per_day > 0 and not time_reversed:
        remain = capacity_gb * 1e9 - (sn.get("total_size_bytes") or 0)
        if remain > 0:
            eta_days = round(remain / per_day, 1)

    return {
        "skill": "nas-report.diff",
        "capacity_gb": capacity_gb,
        "eta_days": eta_days,
        "old": {"generated_at": old.get("generated_at"), "root": old.get("root")},
        "new": {"generated_at": new.get("generated_at"), "root": new.get("root")},
        "same_root": old.get("root") == new.get("root"),
        "interval_days": round(days, 4) if days is not None else None,
        "time_reversed": time_reversed,
        "totals": {
            "bytes_before": so.get("total_size_bytes") or 0,
            "bytes_after": sn.get("total_size_bytes") or 0,
            "bytes_added": d_bytes, "gb_added": round(d_bytes / 1e9, 3),
            "files_before": so.get("total_files") or 0,
            "files_after": sn.get("total_files") or 0, "files_added": d_files,
            "dirs_added": d_dirs,
        },
        "growth_bytes_per_day": round(per_day) if per_day is not None else None,
        "by_category": _delta(so.get("by_category") or {}, sn.get("by_category") or {}),
        "by_growth": _delta(so.get("by_growth") or {}, sn.get("by_growth") or {}),
        "toplevel": _delta(so.get("toplevel") or {}, sn.get("toplevel") or {}),
        "new_large_files": _files("added"),
        "gone_large_files": _files("removed"),
        "file_lists_truncated": True,      # 快照只留 top-N,这不是全库文件级 diff
        "capacity_note": "快照里没有容量/可用空间字段；给 --capacity-gb 才能算 ETA",
        "warnings": [w for w in (
            ("两份快照的 root 不同,差分仍然成立但读者要知道"
             if old.get("root") != new.get("root") else None),
            ("新的这份 generated_at 早于旧的——顺序反了,"
             "速率与增量都不解读" if time_reversed else None),
            "old 侧 truncated=True：那份画像本来就不完整" if so.get("truncated") else None,
            "new 侧 truncated=True：那份画像本来就不完整" if sn.get("truncated") else None,
        ) if w],
    }


def _print_diff_human(d: dict, top: int, capacity_gb: float | None) -> None:
    t = d["totals"]
    print("=" * 70)
    print("nas-report diff — 两份快照之间发生了什么")
    print("=" * 70)
    print(f"旧: {d['old']['generated_at']}  {d['old']['root']}")
    print(f"新: {d['new']['generated_at']}  {d['new']['root']}")
    if d["interval_days"] is not None:
        print(f"间隔: {d['interval_days']:.2f} 天")
    for w in d["warnings"]:
        print(f"⚠ {w}")
    print(f"\n总览: {t['files_before']} → {t['files_after']} 文件 "
          f"({t['files_added']:+d}) | {_human(t['bytes_before'])} → {_human(t['bytes_after'])} "
          f"({t['gb_added']:+.3f} GB)")
    if d["growth_bytes_per_day"] is not None:
        print(f"增长: {_human(d['growth_bytes_per_day'])}/天")
        if capacity_gb:
            remain = capacity_gb * 1e9 - t["bytes_after"]
            rate = d["growth_bytes_per_day"]
            if d.get("eta_days") is not None:
                print(f"距满盘: 还剩 {_human(remain)}（按当前速率 ≈ {d['eta_days']:.0f} 天）")
            elif rate <= 0:
                print("距满盘: 本次是净减少,没有 ETA")
            else:
                print("距满盘: 已经超过给出的容量")
    else:
        print("增长: 算不出（缺 generated_at 或时间反了）")
    print(f"（{d['capacity_note']}）")

    if d["by_category"]:
        print("\n【按类别】")
        for cat, e in list(d["by_category"].items())[:top]:
            print(f"  {CAT_ZH.get(cat, cat):<8} {e['count_added']:+7d} 个  "
                  f"{e['gb_added']:+8.3f} GB   "
                  f"({_human(e['bytes_before'])} → {_human(e['bytes_after'])})")
    else:
        print("\n【按类别】无变化")
    if d["toplevel"]:
        print("\n【按顶层目录】")
        for name, e in list(d["toplevel"].items())[:top]:
            print(f"  {name:<20} {e['count_added']:+7d} 个  {e['gb_added']:+8.3f} GB")
    if d["new_large_files"]:
        print(f"\n【新出现的大文件】Top {top}（只在两份榜单内比较,不是全库 diff）")
        for f in d["new_large_files"][:top]:
            print(f"  {_human(f.get('size') or 0):>9}  {f['path']}")
    if d["gone_large_files"]:
        print(f"\n【从榜单消失的大文件】Top {top}")
        for f in d["gone_large_files"][:top]:
            print(f"  {_human(f.get('size') or 0):>9}  {f['path']}")
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

    dif_p = sub.add_parser("diff", help="两份快照的差分(离线,不碰 NAS)")
    dif_p.add_argument("old", help="旧快照 JSON")
    dif_p.add_argument("new", help="新快照 JSON")
    dif_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    dif_p.add_argument("--top", type=int, default=10, help="每个榜单显示条数(默认 10)")
    dif_p.add_argument("--capacity-gb", type=float, default=None,
                       help="池容量(GB)；给了才算'离满盘还有多久'")

    args = parser.parse_args()

    if args.cmd == "diff":
        try:
            old = json.loads(Path(args.old).read_text(encoding="utf-8"))
            new = json.loads(Path(args.new).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"❌ 读不了快照: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        d = diff_reports(old, new, args.capacity_gb)
        if args.json:
            json.dump(d, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            _print_diff_human(d, args.top, args.capacity_gb)
        raise SystemExit(0)

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
