#!/usr/bin/env python3
"""work-organizer skill 命令行入口

设计原则(沿袭 media-naming / photo-organizer 模式):
- 只读扫描走脚本(正向合规验证:定义合规结构,不匹配即报问题)
- 写操作(mkdir / mv / rename / rm)不在脚本里 — LLM 生成 old→new 计划,
  用户确认后由 Agent 执行(挂载盘 shell,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用

用法:
  python work_organizer.py scan --root /Volumes/nas/工作
  python work_organizer.py scan --root ... --json --output /tmp/issues.json
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

DOC_EXTS = {"doc", "docx", "wps", "rtf", "odt", "pages", "txt", "md"}
SHEET_EXTS = {"xls", "xlsx", "et", "csv", "numbers", "ods"}
PPT_EXTS = {"ppt", "pptx", "dps", "key", "odp"}
PDF_EXTS = {"pdf"}
ARCHIVE_EXTS = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"}
INSTALLER_EXTS = {"dmg", "pkg", "exe", "msi", "apk", "iso", "deb", "rpm", "appimage"}
DESIGN_EXTS = {"psd", "ai", "eps", "sketch", "fig", "xd", "cdr"}
CODE_EXTS = {
    "py", "js", "ts", "go", "rs", "java", "c", "cpp", "h", "sh", "sql",
    "html", "css", "json", "yaml", "yml", "toml", "xml", "ipynb",
}
MEDIA_EXTS = {
    "jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tif", "tiff",
    "mp4", "mov", "m4v", "avi", "mkv", "wmv", "flv", "mp3", "wav", "m4a", "flac",
}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
JUNK_EXTS = {
    "tmp", "temp", "bak", "old", "swp", "crdownload", "part", "download",
    "td", "aria2", "ckp", "wbcat", "gid", "dmp",
}
LOCK_PREFIXES = ("~$", ".~", "~")
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", ".AppleDB", ".AppleDesktop", ".apdisk",
    "System Volume Information", "$RECYCLE.BIN", ".snapshots", ".zspace_trash",
    ".trash", ".cache", "node_modules", ".git", ".svn", ".idea", ".vscode",
    "__pycache__", ".venv", "venv", "lost+found",
}
WHITELIST_DIRS = {
    "模板", "归档", "公共", "共享", "资料", "参考", "参考资料", "合同", "财务",
    "人事", "行政", "市场", "销售", "项目", "项目归档", "客户", "报表", "制度",
    "素材", "字体", "软件", "工具", "下载", "发票", "报销", "简历", "培训",
    "archive", "archives", "templates", "shared", "public", "references",
    "docs", "admin", "hr", "finance", "tools", "software", "inbox", "outbox",
}
ARCHIVE_DIR_RE = re.compile(r"归档|存档|archive|Archive|旧资料|backup", re.I)

BAD_DIR = re.compile(
    r"^(新建文件夹.*|未命名.*|无标题.*|untitled.*|new folder.*|temp|tmp|test|测试|"
    r"aaa+|asd+|qwe.*|\d{1,3}|各种|杂项|其他1)$",
    re.I,
)
YEAR_DIR_OK = re.compile(r"^(?:19|20)\d{2}年?$")
COPY_MARK_RE = re.compile(
    r"(?:\s*\(\d+\)|\s*副本\d*|\s*拷贝\d*|\s*-\s*copy(?:\s*\d+)?|\s+copy(?:\s*\d+)?)$",
    re.I,
)
VERSION_CHAOS_RE = re.compile(
    r"(最终|终版|定稿|真的|打死|不改|不再改|最后|完美|绝对|修改版|修订版|新版|旧版|"
    r"final|last|ultimate|definitive)",
    re.I,
)
VN_OK_RE = re.compile(r"[_\-\s][vV]\d+(\.\d+)*\b")
DATE_PREFIX_RE = re.compile(r"^(?:19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}")
DATE_ANYWHERE_RE = re.compile(
    r"(?:19|20)\d{2}[-_.年](?:0?\d|1[0-2])(?:[-_.月](?:0?\d|[12]\d|3[01]))?"
)


def dir_problems(name: str, depth: int) -> list[str]:
    """目录名校验:允许年份/白名单/正常项目名;抓临时、副本、无语义名。"""
    if name.lower() in WHITELIST_DIRS or YEAR_DIR_OK.match(name):
        return []
    if BAD_DIR.match(name):
        return ["临时/无语义目录名(建议重命名或清理)"]
    if COPY_MARK_RE.search(name):
        return ["副本目录(建议比对后合并或删除)"]
    if depth >= 2:
        return []
    return []  # 工作区项目/职能目录允许自由命名,只抓明显垃圾


def base_stem(stem: str) -> str:
    """剥离日期前缀/版本标记/副本标记,得到「同名文档」分组键。"""
    s = re.sub(r"^(?:19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}[-_.]?", "", stem)
    s = re.sub(r"^(?:19|20)\d{2}[-_.年]?(?:\d{1,2}[-_.月]?(?:\d{1,2}日?)?)?[-_.]?", "", s)
    s = re.sub(r"[\s_\-]*[vV]\d+(\.\d+)*[\s_\-]*", "", s)
    s = re.sub(
        r"[\s_\-]*(最终版?|终版|定稿|final|last|新版|旧版|修改版|修订版|完美版|"
        r"真的[^_\-\s]*|打死不改|不再改)[\s_\-]*",
        "", s, flags=re.I,
    )
    s = re.sub(r"[\s_\-]*\(\d+\)\s*$", "", s)
    s = re.sub(r"[\s_\-]*(副本|拷贝|copy)\s*\d*\s*$", "", s, flags=re.I)
    s = re.sub(r"[\s_\-.]+", "", s)
    return s.lower()


def classify_ext(ext: str) -> str:
    if ext in DOC_EXTS:
        return "doc"
    if ext in SHEET_EXTS:
        return "sheet"
    if ext in PPT_EXTS:
        return "ppt"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in INSTALLER_EXTS:
        return "installer"
    if ext in DESIGN_EXTS:
        return "design"
    if ext in CODE_EXTS:
        return "code"
    if ext in MEDIA_EXTS:
        return "media"
    return "other"


class Scanner:
    def __init__(self, root: str, max_depth: int, sample: int,
                 strict_naming: bool, archive_years: int) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.sample = sample
        self.strict_naming = strict_naming
        self.archive_years = archive_years
        self.stats: dict = {
            "dirs": 0, "files": 0, "total_size_bytes": 0,
            "by_type": {}, "junk": 0, "loose_root": 0, "copy_files": 0,
            "version_chaos": 0, "installers": 0, "archive_candidates": 0,
            "largest": [], "oldest": None, "newest": None, "sampled_out": False,
        }
        self.issues: list[dict] = []
        self._versions: dict[tuple[str, str], list[str]] = {}
        self._sizes: list[tuple[int, str]] = []
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
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                self.stats["dirs"] += 1
                child_rel = rel_parts + [name]
                problems = dir_problems(name, len(child_rel))
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
        full = "/".join(rel_parts + [name])

        try:
            st = entry.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, None
        mtime_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d") if mtime else None

        # 临时/锁定/垃圾文件
        if (name in JUNK_NAMES or ext in JUNK_EXTS or name.startswith("._")
                or stem.startswith(LOCK_PREFIXES)):
            self.stats["junk"] += 1
            self._add(rel_parts + [name], name, False,
                      ["临时/锁定/垃圾文件(可删)"], size=size)
            return

        if name.startswith("."):
            return

        self.stats["files"] += 1
        self.stats["total_size_bytes"] += size
        ftype = classify_ext(ext)
        self.stats["by_type"][ftype] = self.stats["by_type"].get(ftype, 0) + 1
        self._sizes.append((size, full))
        if mtime:
            if not self.stats["oldest"] or mtime < self.stats["oldest"][0]:
                self.stats["oldest"] = (mtime, full)
            if not self.stats["newest"] or mtime > self.stats["newest"][0]:
                self.stats["newest"] = (mtime, full)

        problems: list[str] = []

        # 根目录散文件
        if not rel_parts:
            self.stats["loose_root"] += 1
            problems.append("根目录散文件(建议归入 年份/项目 目录)")

        # 副本标记
        if COPY_MARK_RE.search(stem):
            self.stats["copy_files"] += 1
            problems.append("副本文件(建议比对后删除或转正)")

        # 版本标记混乱
        if VERSION_CHAOS_RE.search(stem):
            self.stats["version_chaos"] += 1
            problems.append("版本标记混乱(建议统一 _vN 或日期后缀)")

        # 安装包/镜像
        if ext in INSTALLER_EXTS:
            self.stats["installers"] += 1
            problems.append("安装包/镜像混在工作区(建议移出或删除)")

        # 过期未动(可选)
        if self.archive_years and mtime:
            age_days = (datetime.now().timestamp() - mtime) / 86400
            if age_days > self.archive_years * 365:
                if not any(ARCHIVE_DIR_RE.search(p) for p in rel_parts):
                    self.stats["archive_candidates"] += 1
                    problems.append(
                        f"超过 {self.archive_years} 年未修改(建议移入 归档/)")

        # 缺日期前缀(可选,仅办公文档)
        if (self.strict_naming and rel_parts
                and ftype in ("doc", "sheet", "ppt", "pdf")
                and not DATE_PREFIX_RE.match(stem)
                and not DATE_ANYWHERE_RE.search(stem)):
            problems.append("缺日期前缀(建议 YYYYMMDD_项目_主题_vN)")

        if problems:
            extra = {"size": size}
            if mtime_date:
                extra["mtime"] = mtime_date
                if not rel_parts:
                    extra["suggested_dir"] = mtime_date[:4]
            self._add(rel_parts + [name], name, False, problems, **extra)

        # 同名多版本分组(办公文档/设计稿)
        if ftype in ("doc", "sheet", "ppt", "pdf", "design"):
            key = ("/".join(rel_parts), base_stem(stem))
            if key[1]:
                self._versions.setdefault(key, []).append(name)

    # -- 版本组聚合 ----------------------------------------------------

    def _flush_versions(self) -> None:
        for (dir_rel, base), names in self._versions.items():
            if len(names) >= 3:
                path = f"{dir_rel}/{base}" if dir_rel else base
                self.issues.append({
                    "path": path,
                    "name": base,
                    "is_dir": False,
                    "problems": [
                        f"同名文档多版本共存({len(names)} 个,建议保留最新、归档旧版)"
                    ],
                    "versions": sorted(names)[:6],
                })

    # -- 入口 ----------------------------------------------------------

    def scan(self) -> dict:
        print(f"正在扫描 {self.root} ...\n", file=sys.stderr)
        if not self.root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {self.root}")
        self._walk(self.root, [])
        self._flush_versions()
        self._sizes.sort(reverse=True)
        self.stats["largest"] = [
            {"path": p, "size": s} for s, p in self._sizes[:5]
        ]
        del self._sizes
        for key in ("oldest", "newest"):
            val = self.stats[key]
            if val:
                self.stats[key] = {
                    "path": val[1],
                    "mtime": datetime.fromtimestamp(val[0]).strftime("%Y-%m-%d"),
                }
        print(
            f'扫描完成: {self.stats["dirs"]} 目录, {self.stats["files"]} 文件\n',
            file=sys.stderr,
        )
        return {
            "skill": "work-organizer",
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
    print("work-organizer — 工作文件库合规扫描报告")
    print("=" * 70)
    print(f"根目录: {result['root']}")
    print(f"目录 {s['dirs']} | 文件 {s['files']} | 总大小 {_human_size(s['total_size_bytes'])}")
    if s["by_type"]:
        types = " / ".join(f"{k}:{v}" for k, v in
                           sorted(s["by_type"].items(), key=lambda x: -x[1]))
        print(f"类型分布: {types}")
    print(f"根目录散文件 {s['loose_root']} | 副本 {s['copy_files']} "
          f"| 版本混乱 {s['version_chaos']} | 安装包 {s['installers']} "
          f"| 垃圾 {s['junk']}")
    if s["archive_candidates"]:
        print(f"过期归档候选: {s['archive_candidates']}")
    if s["largest"]:
        print("\n最大文件 Top5:")
        for item in s["largest"]:
            print(f"  {_human_size(item['size']):>10}  {item['path']}")
    if s["oldest"]:
        print(f"\n最旧文件: {s['oldest']['path']} ({s['oldest']['mtime']})")
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
        description="work-organizer: 工作文件库只读合规扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="正向合规扫描(只读)")
    scan_p.add_argument("--root", required=True,
                        help="工作文件根目录(本地挂载路径),如 /Volumes/nas/工作")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                        help=f"最大递归深度(默认 {MAX_DEPTH})")
    scan_p.add_argument("--sample", type=int, default=0,
                        help="最多扫描文件数(0=不限,测试用)")
    scan_p.add_argument("--top", type=int, default=8,
                        help="人类可读输出每类问题最多显示条数")
    scan_p.add_argument("--strict-naming", action="store_true",
                        help="启用严格命名检查:办公文档需带日期前缀")
    scan_p.add_argument("--archive-years", type=int, default=0,
                        help="mtime 超过 N 年视为归档候选(0=关闭)")

    args = parser.parse_args()
    if args.cmd != "scan":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    scanner = Scanner(args.root, args.max_depth, args.sample,
                      args.strict_naming, args.archive_years)
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
