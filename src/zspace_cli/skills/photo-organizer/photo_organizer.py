#!/usr/bin/env python3
"""photo-organizer skill 命令行入口

设计原则(沿袭 media-naming 模式):
- 只读扫描走脚本(正向合规验证:定义合规结构,不匹配即报问题)
- 写操作(mkdir / mv / rename)不在脚本里 — LLM 生成 old→new 计划,
  用户确认后由 Agent 执行(挂载盘 shell mv,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用

用法:
  python photo_organizer.py scan --root /Volumes/nas/照片
  python photo_organizer.py scan --root ... --json --output /tmp/issues.json
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

PHOTO_EXTS = {
    "jpg", "jpeg", "png", "heic", "heif", "gif", "webp", "bmp", "tif", "tiff",
    "avif", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "dng", "pef", "srw",
}
VIDEO_EXTS = {"mp4", "mov", "m4v", "avi", "mkv", "3gp", "wmv", "flv", "mpg", "mpeg", "ts"}
SIDECAR_EXTS = {"aae", "xmp"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized", "Picasa.ini"}
JUNK_EXTS = {"part", "crdownload", "download", "td", "temp", "tmp", "aria2"}
# 各品牌 NAS / 系统的元数据目录,静默跳过
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", ".AppleDB", ".AppleDesktop", ".apdisk",
    "System Volume Information", "$RECYCLE.BIN", ".snapshots", ".zspace_trash",
    ".trash", ".cache", "@Recycle", "lost+found",
}
WHITELIST_DIRS = {
    "截图", "截屏", "视频", "照片", "相册", "待整理", "其他", "收藏", "原图", "实况",
    "全景", "人像", "延时", "慢动作", "导入", "精选", "已整理", "长曝光", "RAW",
    "screenshots", "videos", "albums", "favorites", "camera", "imports",
    "recents", "edited", "panorama", "portrait", "slo-mo", "burst", "timelapse",
}

# ── 合规目录名:YYYY / YYYY-MM / YYYY-MM-DD / 日期前缀+事件名 ──────────
DATE_DIR_OK = re.compile(
    r"^(?:19|20)\d{2}年?"
    r"(?:[-._/]?(?:0?[1-9]|1[0-2])月?)?"
    r"(?:[-._/]?(?:0?[1-9]|[12]\d|3[01])日?)?"
    r"(?:[\s_\-·~至]+.*\S)?$"
)
YEAR_DIR_OK = re.compile(r"^(?:19|20)\d{2}年?$")
CAMERA_ROLL_DIR = re.compile(r"^\d{3}[_ ]?[A-Za-z]{2,8}\d*$")
BAD_DIR = re.compile(
    r"^(新建文件夹.*|未命名.*|无标题.*|untitled.*|new folder.*|temp|tmp|test|测试|aaa+|\d{1,2})$",
    re.I,
)

# ── 文件名日期提取 ─────────────────────────────────────────────────────
_WECHAT_RE = re.compile(
    r"(?:微信(?:图片|视频|截图)_?|企业微信截图_?|wx_camera_?|WeChat_?)(\d{4})(\d{2})(\d{2})"
)
_EPOCH_RE = re.compile(r"mmexport(\d{13})")
_SCREENSHOT_RE = re.compile(
    r"(?:Screenshot|截屏|屏幕快照|CleanShot|Xnip|ScreenShot|屏幕截图)"
    r"[ _\-@]*(\d{4})[-._]?(\d{2})[-._]?(\d{2})",
    re.I,
)
_SCREENSHOT_NODATE_RE = re.compile(
    r"^(?:Screenshot|截屏|屏幕快照|CleanShot|Xnip|ScreenShot|屏幕截图)", re.I
)
_CAMERA_DATED_RE = re.compile(
    r"(?:IMG|VID|PXL|MVIMG|DJI|GOPR|RMX|PANO)[_-]?(\d{4})(\d{2})(\d{2})", re.I
)
_GENERIC_LEAD_RE = re.compile(r"^((?:19|20)\d{2})[-._年](\d{1,2})[-._月](\d{1,2})")
_GENERIC_COMPACT_RE = re.compile(
    r"^((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"
)
_ANYWHERE_STRICT_RE = re.compile(r"((?:19|20)\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
_CAMERA_NODATE_RE = re.compile(
    r"^(?:IMG|DSC|DSCN|DSCF|PXL|MVIMG|PANO|VID|GOPR|DJI|RMX|_MG|P\d|DCIM)"
    r"[_\-]?\d+\b",
    re.I,
)
_BURST_RE = re.compile(
    r"^(IMG|_MG|DSC|DSCN|DJI|GOPR|PXL|MVIMG)[_-]?(\d{3,5})$", re.I
)
_COPY_MARK_RE = re.compile(
    r"(?:\s*\(\d+\)|\s*副本\d*|\s*拷贝\d*|\s*-\s*copy(?:\s*\d+)?|\s+copy(?:\s*\d+)?)$", re.I
)


def _valid_ymd(y: str, m: str, d: str) -> str | None:
    try:
        mi, di = int(m), int(d)
        if not (1 <= mi <= 12 and 1 <= di <= 31):
            return None
        return f"{int(y):04d}-{mi:02d}-{di:02d}"
    except ValueError:
        return None


def extract_date(name: str) -> tuple[str | None, str]:
    """从文件名提取拍摄/创建日期。

    返回 (date 'YYYY-MM-DD' 或 None, 类别)。
    类别: wechat / screenshot / camera / generic / none
    """
    stem = os.path.splitext(name)[0]

    m = _EPOCH_RE.search(stem)
    if m:
        dt = datetime.fromtimestamp(int(m.group(1)) / 1000)
        return dt.strftime("%Y-%m-%d"), "wechat"

    m = _WECHAT_RE.search(stem)
    if m:
        return _valid_ymd(m.group(1), m.group(2), m.group(3)), "wechat"

    m = _SCREENSHOT_RE.search(stem)
    if m:
        return _valid_ymd(m.group(1), m.group(2), m.group(3)), "screenshot"
    if _SCREENSHOT_NODATE_RE.match(stem):
        return None, "screenshot"

    m = _GENERIC_LEAD_RE.match(stem)
    if m:
        return _valid_ymd(m.group(1), m.group(2), m.group(3)), "generic"

    m = _GENERIC_COMPACT_RE.match(stem)
    if m:
        return _valid_ymd(m.group(1), m.group(2), m.group(3)), "generic"

    m = _CAMERA_DATED_RE.search(stem)
    if m:
        return _valid_ymd(m.group(1), m.group(2), m.group(3)), "camera"

    m = _ANYWHERE_STRICT_RE.search(stem)
    if m:
        return _valid_ymd(m.group(1), m.group(2), m.group(3)), "generic"

    if _CAMERA_NODATE_RE.match(stem):
        return None, "camera"

    return None, "none"


def dir_problems(name: str, depth: int, parent_is_year: bool) -> list[str]:
    """目录名合规校验(正向)。depth: root 的直接子目录=1。"""
    if name.lower() in WHITELIST_DIRS:
        return []
    if BAD_DIR.match(name):
        return ["临时/未命名目录(建议重命名或清理)"]
    if CAMERA_ROLL_DIR.match(name):
        return ["相机/手机原始卷目录(建议按内容日期重组)"]
    if DATE_DIR_OK.match(name):
        return []
    if depth >= 3:
        return []  # 事件目录内部允许自由命名(day1/海边/…)
    if depth == 2 and not parent_is_year:
        return []  # 月/事件目录的子目录自由命名
    return ["目录名不符合日期规范(建议 YYYY/、YYYY-MM/ 或 YYYY-MM-DD_事件名)"]


def is_loose(rel_parts: list[str], parent_name: str) -> bool:
    """散文件:直接在 root 下,或直接在 YYYY/ 年目录下。"""
    if len(rel_parts) == 1:
        return True
    if len(rel_parts) == 2 and YEAR_DIR_OK.match(parent_name):
        return True
    return False


class Scanner:
    def __init__(self, root: str, max_depth: int, sample: int) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.sample = sample
        self.stats: dict = {
            "dirs": 0, "files": 0, "photos": 0, "videos": 0, "others": 0,
            "junk": 0, "loose": 0, "wechat": 0, "screenshots": 0,
            "camera_no_date": 0, "burst_groups": 0, "non_media": 0,
            "total_size_bytes": 0, "date_min": None, "date_max": None,
            "by_month": {}, "sampled_out": False,
        }
        self.issues: list[dict] = []
        self._burst: dict[tuple[str, str], list[int]] = {}
        self._n_files = 0

    # -- 遍历 ----------------------------------------------------------

    def _walk(self, dir_path: Path, rel_parts: list[str]) -> None:
        if len(rel_parts) > self.max_depth:
            return
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as e:
            print(f"⚠️ 无法读取 {dir_path}: {e}", file=sys.stderr)
            return
        for entry in entries:
            if self.sample and self._n_files >= self.sample:
                self.stats["sampled_out"] = True
                return
            name = entry.name
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if name in SKIP_DIRS or (name.startswith(".") and name not in JUNK_NAMES):
                    continue
                self.stats["dirs"] += 1
                child_rel = rel_parts + [name]
                depth = len(child_rel)
                parent_is_year = bool(rel_parts) and bool(YEAR_DIR_OK.match(rel_parts[-1]))
                problems = dir_problems(name, depth, parent_is_year)
                if problems:
                    self._add(child_rel, name, True, problems)
                self._walk(entry.path, child_rel)
            elif entry.is_file():
                self._n_files += 1
                self._check_file(entry, rel_parts, name)

    def _add(self, rel_parts: list[str], name: str, is_dir: bool,
             problems: list[str], **extra) -> None:
        issue = {
            "path": "/".join(rel_parts) if rel_parts else name,
            "name": name,
            "is_dir": is_dir,
            "problems": problems,
        }
        issue.update({k: v for k, v in extra.items() if v is not None})
        self.issues.append(issue)

    # -- 文件校验 ------------------------------------------------------

    def _check_file(self, entry: os.DirEntry, rel_parts: list[str], name: str) -> None:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        stem = os.path.splitext(name)[0]

        try:
            st = entry.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, None

        # 垃圾 / 系统残留(._* 为 AppleDouble)
        if name in JUNK_NAMES or ext in JUNK_EXTS or name.startswith("._"):
            self.stats["junk"] += 1
            self._add(rel_parts + [name], name, False, ["垃圾/系统残留文件(可删)"])
            return

        # 静默跳过其余点文件
        if name.startswith("."):
            return

        self.stats["files"] += 1
        self.stats["total_size_bytes"] += size

        is_photo = ext in PHOTO_EXTS
        is_video = ext in VIDEO_EXTS
        if is_photo:
            self.stats["photos"] += 1
        elif is_video:
            self.stats["videos"] += 1

        # 非媒体文件混入
        if not is_photo and not is_video and ext not in SIDECAR_EXTS:
            self.stats["non_media"] += 1
            self._add(rel_parts + [name], name, False,
                      ["非媒体文件混入照片库(建议移出)"], size=size)
            return

        # sidecar(.aae/.xmp)
        if ext in SIDECAR_EXTS:
            self._add(rel_parts + [name], name, False,
                      ["编辑附属文件 sidecar(移动主图时需一并处理)"])
            return

        # 日期提取
        date, kind = extract_date(name)
        date_source = "filename" if date else None
        if not date and mtime:
            date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            date_source = "mtime(弱,仅参考)"
        if date and date_source == "filename":
            if kind == "wechat":
                self.stats["wechat"] += 1
            elif kind == "screenshot":
                self.stats["screenshots"] += 1
            elif kind == "camera" and not _CAMERA_DATED_RE.search(stem):
                self.stats["camera_no_date"] += 1
            if not self.stats["date_min"] or date < self.stats["date_min"]:
                self.stats["date_min"] = date
            if not self.stats["date_max"] or date > self.stats["date_max"]:
                self.stats["date_max"] = date
            month = date[:7]
            self.stats["by_month"][month] = self.stats["by_month"].get(month, 0) + 1
        elif kind == "camera":
            self.stats["camera_no_date"] += 1
        elif kind == "screenshot":
            self.stats["screenshots"] += 1

        problems: list[str] = []

        # 散文件
        parent_name = rel_parts[-1] if rel_parts else ""
        if is_loose(rel_parts + [name], parent_name):
            self.stats["loose"] += 1
            suggested = f"{date[:4]}/{date[:7]}" if date and len(date) >= 7 else None
            problems.append("散文件(建议按拍摄日期归入 YYYY/YYYY-MM/)")
            self._add(rel_parts + [name], name, False, problems,
                      date=date, date_source=date_source, kind=kind,
                      suggested_dir=suggested, size=size)
            return

        # 疑似重复副本
        if _COPY_MARK_RE.search(stem):
            problems.append("疑似重复副本(建议与原文件比对后去留)")

        if problems:
            self._add(rel_parts + [name], name, False, problems,
                      date=date, date_source=date_source, kind=kind, size=size)

        # 连拍统计(目录级,稍后聚合)
        m = _BURST_RE.match(stem)
        if m:
            key = ("/".join(rel_parts), m.group(1).upper())
            self._burst.setdefault(key, []).append(int(m.group(2)))

    # -- 连拍聚合 ------------------------------------------------------

    def _flush_bursts(self) -> None:
        for (dir_rel, prefix), nums in self._burst.items():
            nums = sorted(set(nums))
            runs: list[list[int]] = []
            cur = [nums[0]]
            for n in nums[1:]:
                if n - cur[-1] <= 1:
                    cur.append(n)
                else:
                    runs.append(cur)
                    cur = [n]
            runs.append(cur)
            for run in runs:
                if len(run) >= 5:
                    self.stats["burst_groups"] += 1
                    rel = f"{dir_rel}/{prefix}_{run[0]:04d}-{run[-1]:04d}" if dir_rel \
                        else f"{prefix}_{run[0]:04d}-{run[-1]:04d}"
                    self.issues.append({
                        "path": rel,
                        "name": f"{prefix}_{run[0]:04d}…{prefix}_{run[-1]:04d}",
                        "is_dir": False,
                        "problems": [f"疑似连拍组({len(run)} 张连号,建议精选保留)"],
                    })

    # -- 入口 ----------------------------------------------------------

    def scan(self) -> dict:
        print(f"正在扫描 {self.root} ...\n", file=sys.stderr)
        if not self.root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {self.root}")
        self._walk(self.root, [])
        self._flush_bursts()
        by_month = dict(sorted(self.stats.pop("by_month").items()))
        print(
            f'扫描完成: {self.stats["dirs"]} 目录, {self.stats["files"]} 文件\n',
            file=sys.stderr,
        )
        return {
            "skill": "photo-organizer",
            "root": str(self.root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": {**self.stats, "by_month": by_month},
            "count": len(self.issues),
            "issues": self.issues,
        }


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _print_human(result: dict, top: int) -> None:
    s = result["stats"]
    print("=" * 70)
    print("photo-organizer — 照片/视频库合规扫描报告")
    print("=" * 70)
    print(f"根目录: {result['root']}")
    print(f"目录 {s['dirs']} | 文件 {s['files']} "
          f"(照片 {s['photos']} / 视频 {s['videos']} / 非媒体 {s['non_media']})")
    print(f"总大小: {_human_size(s['total_size_bytes'])}")
    print(f"散文件 {s['loose']} | 微信图 {s['wechat']} | 截图 {s['screenshots']} "
          f"| 无日期相机原图 {s['camera_no_date']} | 连拍组 {s['burst_groups']} "
          f"| 垃圾 {s['junk']}")
    if s["date_min"]:
        print(f"日期范围(文件名可提取): {s['date_min']} ~ {s['date_max']}")
    if s["by_month"]:
        print("\n按日期归档分布(文件名可提取部分):")
        for month, n in s["by_month"].items():
            print(f"  {month}: {n} 个文件")
    if s.get("sampled_out"):
        print("\n(已达 --sample 上限,结果不完整)")

    issues = result["issues"]
    if not issues:
        print("\n✅ 全部合规,零问题!")
        return

    by_type: dict[str, list] = {}
    for issue in issues:
        for p in issue["problems"]:
            by_type.setdefault(p, []).append(issue)

    print(f"\n⚠ 发现 {len(issues)} 个问题项:\n")
    for ptype, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"【{ptype}】{len(items)} 项")
        for item in items[:top]:
            tag = "📁" if item["is_dir"] else "  "
            extra = ""
            if item.get("date") and not item["is_dir"]:
                extra = f"  → {item['date']}"
                if item.get("suggested_dir"):
                    extra += f" (建议 {item['suggested_dir']}/)"
            print(f"  {tag} {item['path']}{extra}")
        if len(items) > top:
            print(f"  ... 还有 {len(items) - top} 项")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="photo-organizer: 照片/视频库只读合规扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="正向合规扫描(只读)")
    scan_p.add_argument("--root", required=True,
                        help="照片库根目录(本地挂载路径),如 /Volumes/nas/照片")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                        help=f"最大递归深度(默认 {MAX_DEPTH})")
    scan_p.add_argument("--sample", type=int, default=0,
                        help="最多扫描文件数(0=不限,测试用)")
    scan_p.add_argument("--top", type=int, default=8,
                        help="人类可读输出每类问题最多显示条数")

    args = parser.parse_args()
    if args.cmd != "scan":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    scanner = Scanner(args.root, args.max_depth, args.sample)
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
