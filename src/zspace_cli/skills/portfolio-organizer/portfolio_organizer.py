#!/usr/bin/env python3
"""portfolio-organizer skill 命令行入口

设计原则(沿袭 media-naming / photo-organizer 模式):
- 只读扫描走脚本(正向合规验证:定义合规结构,不匹配即报问题)
- 写操作(mkdir / mv / rename)不在脚本里 — LLM 生成 old→new 计划,
  用户确认后由 Agent 执行(挂载盘 shell,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用

用法:
  python portfolio_organizer.py scan --root /Volumes/nas/作品集
  python portfolio_organizer.py scan --root ... --json --output /tmp/issues.json
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
DEFAULT_LARGE_GB = 2.0

SOURCE_EXTS = {
    "psd", "psb", "ai", "aep", "c4d", "blend", "max", "ma", "mb", "prproj",
    "prtl", "fla", "flp", "fig", "sketch", "xd", "indd", "idml", "dwg", "dxf",
    "sldprt", "sldasm", "slddrw", "step", "stp", "f3d", "afdesign", "afphoto",
    "clip", "kra", "xcf", "cpr", "als", "aepx",
}
EXPORT_EXTS = {
    "jpg", "jpeg", "png", "webp", "gif", "tif", "tiff", "bmp", "pdf",
    "mp4", "mov", "webm", "avi", "mkv", "m4v", "mp3", "wav", "m4a", "html",
}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "tif", "tiff", "bmp", "heic"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
JUNK_EXTS = {"tmp", "temp", "bak", "old", "swp", "crdownload", "part", "td"}
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", "System Volume Information", "$RECYCLE.BIN",
    ".snapshots", ".zspace_trash", ".trash", ".cache", "node_modules", ".git",
    "lost+found",
}
# root 下的非项目功能目录,跳过项目级校验
WHITELIST_DIRS = {
    "模板", "素材库", "素材", "共享", "归档", "字体", "参考资料", "参考", "练习",
    "插件", "笔刷", "资产", "待整理", "回收站",
    "templates", "assets", "resources", "archive", "fonts", "references",
    "practice", "plugins", "brushes", "textures", "stock", "shared",
}
FINAL_DIR_NAMES = {
    "成品", "最终", "导出", "发布", "输出", "交付", "成片",
    "final", "finals", "export", "exports", "output", "outputs",
    "dist", "delivery", "publish", "published",
}
SOURCE_DIR_NAMES = {
    "源文件", "工程", "工程文件", "分层", "源", "素材", "原始文件",
    "source", "sources", "src", "working", "editable", "project files", "raw",
}
COVER_PAT = re.compile(r"cover|封面|preview|预览|thumb|首图|展示图", re.I)
README_PAT = re.compile(r"^(readme|说明|简介|介绍|项目说明|description|about|index)", re.I)
YEAR_IN_NAME_RE = re.compile(r"(?:^|[_\-. ·])((?:19|20)\d{2})(?:[_\-. ·]|$)")
COPY_MARK_RE = re.compile(
    r"(?:\s*\(\d+\)|\s*副本\d*|\s*拷贝\d*|\s*-\s*copy(?:\s*\d+)?)$", re.I)


def has_year(name: str) -> bool:
    return bool(YEAR_IN_NAME_RE.search(name))


def is_cover(name: str) -> bool:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    stem = os.path.splitext(name)[0]
    return ext in IMAGE_EXTS | {"pdf"} and bool(COVER_PAT.search(stem))


def is_readme(name: str) -> bool:
    return bool(README_PAT.match(os.path.splitext(name)[0]))


def base_stem(stem: str) -> str:
    """剥离日期前缀/版本标记,得到「同一成品」分组键。"""
    s = re.sub(r"^(?:19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}[-_.]?", "", stem)
    s = re.sub(r"[\s_\-]*[vV]\d+(\.\d+)*[\s_\-]*", "", s)
    s = re.sub(
        r"[\s_\-]*(最终版?|终版|定稿|final|last|修?改版|横版|竖版|副本|拷贝|copy)"
        r"[\s_\-]*", "", s, flags=re.I)
    s = re.sub(r"[\s_\-]*\(\d+\)\s*$", "", s)
    s = re.sub(r"[\s_\-.]+", "", s)
    return s.lower()


def classify_ext(ext: str) -> str:
    if ext in SOURCE_EXTS:
        return "source"
    if ext in EXPORT_EXTS:
        return "export"
    return "other"


class Project:
    def __init__(self, name: str) -> None:
        self.name = name
        self.files: list[dict] = []  # {rel, name, ext, kind, size}
        self.dirs: set[str] = set()  # 一级子目录名(小写)

    @property
    def top_files(self) -> list[dict]:
        return [f for f in self.files if "/" not in f["rel"]]

    def files_in(self, dirname_pred) -> list[dict]:
        out = []
        for f in self.files:
            parts = f["rel"].split("/")
            if len(parts) >= 2 and dirname_pred(parts[0]):
                out.append(f)
        return out


class Scanner:
    def __init__(self, root: str, max_depth: int, sample: int,
                 large_gb: float) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.sample = sample
        self.large_gb = large_gb
        self.stats: dict = {
            "projects": 0, "with_cover": 0, "with_readme": 0,
            "with_final_dir": 0, "with_source_dir": 0,
            "files": 0, "dirs": 0, "junk": 0, "loose_root": 0,
            "source_files": 0, "export_files": 0,
            "source_size_bytes": 0, "export_size_bytes": 0,
            "total_size_bytes": 0, "largest": [], "sampled_out": False,
        }
        self.issues: list[dict] = []
        self.projects: dict[str, Project] = {}
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
                child_rel = rel_parts + [name]
                if len(child_rel) == 1 and name.lower() not in WHITELIST_DIRS:
                    # 注册项目(即使只有子目录、没有任何文件)
                    self.projects.setdefault(name, Project(name))
                elif len(child_rel) == 2:
                    proj = self.projects.get(child_rel[0])
                    if proj:
                        proj.dirs.add(name.lower())
                self._walk(entry.path, child_rel)
            elif entry.is_file():
                self._n_files += 1
                self._check_file(entry, rel_parts, name)

    def _add(self, path: str, name: str, is_dir: bool,
             problems: list[str], **extra) -> None:
        issue = {"path": path, "name": name, "is_dir": is_dir, "problems": problems}
        issue.update({k: v for k, v in extra.items() if v is not None})
        self.issues.append(issue)

    def _check_file(self, entry: os.DirEntry, rel_parts: list[str], name: str) -> None:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        full = "/".join(rel_parts + [name])

        try:
            st = entry.stat()
            size = st.st_size
        except OSError:
            size = 0

        if (name in JUNK_NAMES or ext in JUNK_EXTS or name.startswith("._")
                or name.startswith("~$")):
            self.stats["junk"] += 1
            self._add(full, name, False, ["垃圾/临时文件(可删)"], size=size)
            return
        if name.startswith("."):
            return

        self.stats["files"] += 1
        self.stats["total_size_bytes"] += size
        self._sizes.append((size, full))

        # root 直下散文件(不属于任何项目)
        if not rel_parts:
            self.stats["loose_root"] += 1
            kind = classify_ext(ext)
            self._add(full, name, False,
                      ["根目录散文件(建议归入项目目录)"], size=size, kind=kind)
            return

        proj_name = rel_parts[0]
        if proj_name.lower() in WHITELIST_DIRS:
            return  # 功能目录,不做项目校验

        kind = classify_ext(ext)
        if kind == "source":
            self.stats["source_files"] += 1
            self.stats["source_size_bytes"] += size
        elif kind == "export":
            self.stats["export_files"] += 1
            self.stats["export_size_bytes"] += size

        proj = self.projects.setdefault(proj_name, Project(proj_name))
        proj.files.append({
            "rel": "/".join(rel_parts[1:] + [name]) if len(rel_parts) > 1 else name,
            "name": name, "ext": ext, "kind": kind, "size": size,
        })

        # 大体积源文件
        if kind == "source" and size > self.large_gb * (1024 ** 3):
            self._add(full, name, False,
                      [f"大体积源文件({size / 1024**3:.1f} GB,建议压缩或外置归档)"],
                      size=size)

    # -- 项目级校验 ----------------------------------------------------

    def _eval_project(self, proj: Project) -> None:
        self.stats["projects"] += 1
        problems: list[str] = []

        if not has_year(proj.name):
            problems.append("项目名缺年份(建议 YYYY_项目名)")

        tops = proj.top_files
        if not tops and not proj.files:
            self._add(proj.name, proj.name, True, ["空项目目录(建议删除或补充内容)"])
            return

        final_dirs = [d for d in proj.dirs if d in FINAL_DIR_NAMES]
        source_dirs = [d for d in proj.dirs if d in SOURCE_DIR_NAMES]
        if final_dirs:
            self.stats["with_final_dir"] += 1
        if source_dirs:
            self.stats["with_source_dir"] += 1

        # 封面:项目顶层或成品目录里
        cover_pool = tops + (proj.files_in(lambda d: d.lower() in FINAL_DIR_NAMES)
                             if final_dirs else [])
        has_cover = any(is_cover(f["name"]) for f in cover_pool)
        if has_cover:
            self.stats["with_cover"] += 1
        else:
            problems.append("缺封面图(建议 cover.jpg / 封面.png 放项目顶层)")

        # 说明文件
        has_readme = any(is_readme(f["name"]) for f in tops)
        if has_readme:
            self.stats["with_readme"] += 1
        else:
            problems.append("缺项目说明(建议 README.md / 说明.txt)")

        # 成品与源文件混放(顶层同时有两类)
        top_kinds = {f["kind"] for f in tops}
        if "source" in top_kinds and "export" in top_kinds and not final_dirs:
            problems.append("成品与源文件混放(建议拆分 成品/ 与 源文件/)")

        # 成品目录里混入源文件
        if final_dirs:
            src_in_final = [f for f in proj.files_in(
                lambda d: d.lower() in FINAL_DIR_NAMES) if f["kind"] == "source"]
            if src_in_final:
                self._add(
                    f"{proj.name}/{src_in_final[0]['rel']}",
                    src_in_final[0]["name"], False,
                    [f"成品目录混入源文件({len(src_in_final)} 个,建议移入 源文件/)"])

        # 成品多版本:成品目录(无则顶层)按 base_stem 分组 ≥3
        export_pool = (proj.files_in(lambda d: d.lower() in FINAL_DIR_NAMES)
                       if final_dirs else
                       [f for f in tops if f["kind"] == "export"])
        groups: dict[str, list[str]] = {}
        for f in export_pool:
            if f["kind"] != "export":
                continue
            key = base_stem(os.path.splitext(f["name"])[0])
            if key:
                groups.setdefault(key, []).append(f["rel"])
        for key, names in groups.items():
            if len(names) >= 3:
                self._add(
                    f"{proj.name}/{key}", key, False,
                    [f"成品多版本共存({len(names)} 个,建议只保留最终版)"],
                    versions=sorted(names)[:6])

        if problems:
            self._add(proj.name, proj.name, True, problems)

    # -- 入口 ----------------------------------------------------------

    def scan(self) -> dict:
        print(f"正在扫描 {self.root} ...\n", file=sys.stderr)
        if not self.root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {self.root}")
        self._walk(self.root, [])
        for name in sorted(self.projects):
            self._eval_project(self.projects[name])
        self._sizes.sort(reverse=True)
        self.stats["largest"] = [
            {"path": p, "size": s} for s, p in self._sizes[:5]
        ]
        print(
            f'扫描完成: {self.stats["projects"]} 个项目, '
            f'{self.stats["files"]} 文件\n', file=sys.stderr,
        )
        return {
            "skill": "portfolio-organizer",
            "root": str(self.root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": self.stats,
            "count": len(self.issues),
            "issues": self.issues,
        }


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def _print_human(result: dict, top: int) -> None:
    s = result["stats"]
    print("=" * 70)
    print("portfolio-organizer — 作品集合规扫描报告")
    print("=" * 70)
    print(f"根目录: {result['root']}")
    n_proj = s["projects"] or 1
    print(f"项目 {s['projects']} 个 | 文件 {s['files']} "
          f"(源文件 {s['source_files']} / 成品 {s['export_files']})")
    print(f"总大小: {_human_size(s['total_size_bytes'])} "
          f"(源 {_human_size(s['source_size_bytes'])} "
          f"/ 成品 {_human_size(s['export_size_bytes'])})")
    print(f"有封面 {s['with_cover']}/{n_proj} | 有说明 {s['with_readme']}/{n_proj} "
          f"| 有成品目录 {s['with_final_dir']}/{n_proj} "
          f"| 有源文件目录 {s['with_source_dir']}/{n_proj}")
    print(f"根目录散文件 {s['loose_root']} | 垃圾 {s['junk']}")
    if s["largest"]:
        print("\n最大文件 Top5:")
        for item in s["largest"]:
            print(f"  {_human_size(item['size']):>10}  {item['path']}")
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
            print(f"  {tag} {item['path']}")
        if len(items) > top:
            print(f"  ... 还有 {len(items) - top} 项")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="portfolio-organizer: 作品集只读合规扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="正向合规扫描(只读)")
    scan_p.add_argument("--root", required=True,
                        help="作品集根目录(本地挂载路径),如 /Volumes/nas/作品集")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                        help=f"最大递归深度(默认 {MAX_DEPTH})")
    scan_p.add_argument("--sample", type=int, default=0,
                        help="最多扫描文件数(0=不限,测试用)")
    scan_p.add_argument("--top", type=int, default=8,
                        help="人类可读输出每类问题最多显示条数")
    scan_p.add_argument("--large-gb", type=float, default=DEFAULT_LARGE_GB,
                        help=f"源文件超过 N GB 提示压缩归档(默认 {DEFAULT_LARGE_GB})")

    args = parser.parse_args()
    if args.cmd != "scan":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    scanner = Scanner(args.root, args.max_depth, args.sample, args.large_gb)
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
