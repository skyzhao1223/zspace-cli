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
import fnmatch
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn

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
    "System Volume Information", "$RECYCLE.BIN", ".snapshots", ".zspace_trash", "@Recycle",
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

# ── 可选覆盖层:config.json(与本脚本同目录)────────────────────────────
# 「装 skill」是逐目录 copytree(cli.py 的 zs skill,加 --only 也一样),共享模块
# 装不进用户目录,所以这一段在每个 scanner 里逐字重复 —— 要改就全局搜索替换,
# 别只改一处。唯一 per-skill 的部分是下面的 CONFIG_EXT_TABLES:类别名 → 本 skill
# 的扩展名集合;值为 None 表示「不属于任何集合」,即本 skill 的兜底类别。
CONFIG_NAME = "config.json"
CONFIG_KEYS = ("whitelist_dirs", "extension_overrides")
CONFIG_WHITELIST: list[str] = []      # whitelist_dirs 追加到这里(不替换内置)
CONFIG_INFO: dict[str, object] = {}   # 非空 = 真加载了配置,回写进 stats 供核对
CONFIG_EXT_TABLES: dict[str, set[str] | None] = {
    "doc": DOC_EXTS, "sheet": SHEET_EXTS, "ppt": PPT_EXTS, "pdf": PDF_EXTS,
    "archive": ARCHIVE_EXTS, "installer": INSTALLER_EXTS,
    "design": DESIGN_EXTS, "code": CODE_EXTS, "media": MEDIA_EXTS,
    "junk": JUNK_EXTS, "other": None,
}
# 10 张表两两不相交(实测过),所以「先摘掉再放进」在这里不会误伤别的类别。


def _cfg_die(path: Path, msg: str) -> NoReturn:
    """配置有问题就**立刻退出**,并指名是哪个文件、哪个键。

    静默忽略一份用户以为生效了的配置是最坏的失败方式:扫描会继续标记他刚加进
    白名单的目录,而且不给任何解释。
    """
    raise SystemExit(f"❌ 配置文件 {path} 无效:{msg}")


def _cfg_match(rel_parts: list[str], patterns: list[str]) -> bool:
    """目录是否命中白名单模式。锚定在 --root,大小写不敏感。

    模式匹配「整段相对路径」**或**「任一级目录名」即命中,fnmatch 的 `*` 会跨过
    `/`。所以 `原盘/*` 命中 <root>/原盘/VIDEO_TS,但不命中 <root>/x/原盘;不含
    `/` 的 `原盘` 命中任意深度的同名目录。命中一个目录即命中它的整棵子树。
    绝对路径永远匹配不上,load_config 会直接报错而不是让你以为它生效了。
    """
    if not patterns or not rel_parts:
        return False
    joined = "/".join(rel_parts).lower()
    for pat in patterns:
        low = pat.lower()
        if fnmatch.fnmatch(joined, low):
            return True
        if any(fnmatch.fnmatch(p.lower(), low) for p in rel_parts):
            return True
    return False


def load_config(script_dir: Path | None = None) -> None:
    """读同目录的 config.json,把两个键**并入**内置默认值(不替换)。

    文件不存在 → 立刻返回:没有 config.json 时本 scanner 的输出与引入这段代码
    之前**逐字节一致**。合并语义、模式锚定规则、报错行为逐条写在 SKILL.md 的
    「配置覆盖(config.json)」一节。校验全部跑完才动手改内置集合 —— 半途退出
    会留下一张只改了一半的表,那比直接报错难查得多。
    """
    path = (script_dir or Path(__file__).resolve().parent) / CONFIG_NAME
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        _cfg_die(path, f"读不了这个文件:{e}")
    except ValueError as e:
        _cfg_die(path, f"不是合法 JSON(空文件也算):{e}")
    if not isinstance(raw, dict):
        _cfg_die(path, f"顶层必须是 JSON 对象,实际是 {type(raw).__name__}")
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        _cfg_die(path, f"未知键 {', '.join(unknown)};可用键只有 "
                 f"{', '.join(CONFIG_KEYS)}。宁可报错也不警告后忽略 —— 键名拼错"
                 "一个字母就会让整份配置静默失效,而用户看不出任何区别")
    wl = raw.get("whitelist_dirs", [])
    if not isinstance(wl, list):
        _cfg_die(path, f"whitelist_dirs 必须是字符串数组,实际是 "
                 f"{type(wl).__name__}(只有一条也要写成 [\"原盘\"])")
    for i, item in enumerate(wl):
        if not isinstance(item, str):
            _cfg_die(path, f"whitelist_dirs[{i}] 必须是字符串,实际是 "
                     f"{type(item).__name__}")
        pat = item.strip()
        if not pat:
            _cfg_die(path, f"whitelist_dirs[{i}] 是空字符串")
        if pat.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", pat):
            _cfg_die(path, f"whitelist_dirs[{i}] 写成了绝对路径 {item!r};"
                     "模式锚定在 --root,只能写相对路径(如 原盘/*)")
    ext = raw.get("extension_overrides", {})
    if not isinstance(ext, dict):
        _cfg_die(path, f"extension_overrides 必须是对象,实际是 "
                 f"{type(ext).__name__}")
    pairs: list[tuple[str, str]] = []
    for key, cat in ext.items():
        if not isinstance(cat, str):
            _cfg_die(path, f"extension_overrides[{key!r}] 必须是字符串类别名,"
                     f"实际是 {type(cat).__name__}")
        e = key.strip().lstrip(".").lower()
        if not e:
            _cfg_die(path, f"extension_overrides 的键 {key!r} 归一化后是空的")
        if "." in e:
            _cfg_die(path, f"extension_overrides 的键 {key!r} 含多个点:扩展名只取"
                     f"文件名最后一段(tar.gz 的扩展名是 gz),请写成 "
                     f"{e.rsplit('.', 1)[-1]!r}")
        if cat not in CONFIG_EXT_TABLES:
            _cfg_die(path, f"extension_overrides[{key!r}] 的类别 {cat!r} 本 skill "
                     f"不认识;可用类别:{', '.join(sorted(CONFIG_EXT_TABLES))}")
        pairs.append((e, cat))
    # 校验全过才动手。扩展名先从别的表里摘掉再放进指定的那一张,否则
    # categorize() 的判定阶梯会按它自己的顺序命中旧类别,覆盖看起来没生效。
    CONFIG_WHITELIST.extend(item.strip() for item in wl)
    for e, cat in pairs:
        for name, table in CONFIG_EXT_TABLES.items():
            if table is not None and name != cat:
                table.discard(e)
        target = CONFIG_EXT_TABLES[cat]
        if target is not None:
            target.add(e)
    CONFIG_INFO.update({
        "path": str(path),
        "whitelist_dirs": list(CONFIG_WHITELIST),
        "extension_overrides": dict(pairs),
    })
    print(f"ℹ️ 已加载覆盖配置 {path}(whitelist_dirs {len(wl)} 条,"
          f"extension_overrides {len(pairs)} 条)", file=sys.stderr)


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
                # config.json 的 whitelist_dirs:命中即豁免目录名校验 —— 与内置
                # WHITELIST_DIRS 在 dir_problems() 里的作用完全相同(只是多了 glob、
                # 大小写不敏感、可锚定在任意深度)。dir_problems() 的签名与函数体
                # 一行没改,所以直接调它的那批纯函数断言不受影响。
                problems = ([] if _cfg_match(child_rel, CONFIG_WHITELIST)
                            else dir_problems(name, len(child_rel)))
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
            extra: dict[str, object] = {"size": size}
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
        if CONFIG_INFO:
            # 只在真加载了 config.json 时才多这一个键:没有配置文件时 JSON 与
            # 引入覆盖层之前逐字节一致(smoke 里有负控制专门盯这件事)。
            # 本 skill 的白名单只豁免目录名、不跳过子树(与内置 WHITELIST_DIRS
            # 相同),所以这里没有 photo/music 那个 skipped_files 字段。
            self.stats["config"] = dict(CONFIG_INFO)
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


def _force_utf8_stdio() -> None:
    """强制 stdout/stderr 用 UTF-8(否则 Windows 上打印中文会崩)。

    Windows 的输出被重定向时(AI Agent 就是这样调脚本的),Python 用 locale
    编码(cp1252 / GBK)而不是 UTF-8,任何中文字符都会触发 UnicodeEncodeError
    让整个扫描中断。errors="replace" 保证再差的编码环境也只是降级显示。
    人在 PowerShell 里想看清中文,先 `chcp 65001`。
    """
    for stream in (sys.stdout, sys.stderr):
        # sys.stdout 的静态类型是 TextIO,reconfigure 只存在于 TextIOWrapper;
        # 用 getattr 取既避开类型检查报错,也兼容被替换掉的 stdout(如测试捕获)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main() -> None:
    _force_utf8_stdio()
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

    # 可选覆盖层:同目录 config.json。没有这个文件时下面这行什么都不做,
    # classify_ext() 的 10 张表与 WHITELIST_DIRS 一个都不变,
    # 输出与引入覆盖层之前逐字节一致。
    load_config()

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
