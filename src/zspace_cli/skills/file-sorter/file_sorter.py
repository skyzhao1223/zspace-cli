#!/usr/bin/env python3
"""file-sorter skill 命令行入口

设计原则(沿袭 work-organizer / download-cleaner 模式):
- 只读扫描走脚本:给一个混着文档/图纸/图片/压缩包的乱目录,
  按「正向合规」判定每个文件该待在哪一类目录里,并算出 old → new 目标路径
- 写操作(mkdir / mv)不在脚本里 — LLM 拿 JSON 里的 target 出整理计划,
  用户确认后由 Agent 执行(挂载盘 shell,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用
- 默认不打散「有意的结构」:已在类别目录里的文件、疑似项目目录(多类混合的
  命名子目录)都不动,只报出来让人决定;要拆得显式加 --strict / --split-project-dirs
- 不做内容级去重(那是 dedup-finder 的活);这里只按「文件名带副本/(1) 标记
  且同大小」给出疑似副本提示,并引导用户去 dedup-finder 做内容级确认

用法:
  python file_sorter.py scan --root /Volumes/nas/data
  python file_sorter.py scan --root ... --layout type-year --json --output /tmp/sort.json
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, NoReturn

MAX_DEPTH = 6
TOP_N = 10
PROJECT_MIN_FILES = 3
MAX_RENAME_TRIES = 10      # 同名文件撞车超过这个数就不再自动 __N,交人工命名
DEFAULT_MAX_ISSUES = 500   # 计划条数上限:几万文件的库不能吐 2MB JSON 给 LLM

SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", "System Volume Information", "$RECYCLE.BIN",
    ".snapshots", ".zspace_trash", ".trash", ".cache", "node_modules", ".git",
    "lost+found",
}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
# 临时/残留:能删但要用户点头(可能是日志、未完成下载、手工 .bak)
JUNK_EXTS = {
    "tmp", "temp", "swp", "swo", "part", "crdownload", "td", "download",
    "aria2", "log", "old", "bak", "bk",
}

# -- 扩展名 → 类别 --------------------------------------------------------
# 一个扩展名只归一类;有歧义的(.ts/.obj/.m)在 SKILL.md「踩坑」里说明。
CAD_EXTS = {
    "dwg", "dxf", "dwf", "dgn", "ifc", "step", "stp", "iges", "igs",
    "sldprt", "sldasm", "slddrw", "ipt", "iam", "ipn", "rvt", "rfa", "rte",
    "catpart", "catproduct", "prt", "asm", "sat", "x_t", "x_b", "skp",
    "stl", "obj", "fbx", "3ds", "3dm", "gcode", "pln",
}
DESIGN_EXTS = {
    "psd", "psb", "ai", "eps", "sketch", "fig", "xd", "cdr", "indd", "idml",
    "aep", "blend", "c4d", "max", "ma", "mb", "prproj", "prtl", "fla", "flp",
    "afdesign", "afphoto", "afpub", "kra", "xcf", "clip", "zpr",
}
IMAGE_EXTS = {
    "jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif",
    "tiff", "ico", "avif", "jp2", "svg", "dng", "cr2", "cr3", "nef", "arw",
    "raf", "orf", "rw2", "pef", "srw", "raw",
}
VIDEO_EXTS = {
    "mp4", "mkv", "avi", "mov", "ts", "rmvb", "flv", "wmv", "mpg", "mpeg",
    "m2ts", "m4v", "webm", "3gp", "mxf", "dav", "rm",
}
AUDIO_EXTS = {
    "mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "ape", "wma", "aiff",
    "dsf", "m4b", "mid", "midi", "cue",
}
DOC_EXTS = {
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "pdf", "txt", "md", "csv",
    "rtf", "odt", "ods", "odp", "key", "numbers", "pages", "wps", "et", "dps",
    "xmind", "vsd", "vsdx", "one", "tex", "srt", "ass",
}
EBOOK_EXTS = {"epub", "mobi", "azw", "azw3", "djvu", "fb2", "chm", "pdb"}
ARCHIVE_EXTS = {
    "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "zst", "sit", "lz",
    "lz4", "cab", "arj",
}
INSTALLER_EXTS = {
    "dmg", "pkg", "exe", "msi", "apk", "deb", "rpm", "appimage", "msix",
    "ipa", "run",
}
FONT_EXTS = {"ttf", "otf", "ttc", "woff", "woff2", "fon", "fnt", "dfont"}
CODE_EXTS = {
    "py", "js", "jsx", "mjs", "tsx", "go", "rs", "java", "c", "h", "cpp",
    "hpp", "cc", "cs", "sh", "bat", "ps1", "sql", "html", "htm", "css",
    "scss", "less", "json", "yaml", "yml", "toml", "ini", "conf", "xml",
    "ipynb", "rb", "php", "swift", "kt", "lua", "pl", "r", "vue", "dart",
    "m", "mm",
}
BACKUP_EXTS = {
    "iso", "img", "dsk", "vhd", "vhdx", "ova", "ovf", "tib", "bkf", "vmdk",
    "qcow2", "sparsebundle", "sparseimage", "dd",
}
TORRENT_EXTS = {"torrent"}

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
    "doc": DOC_EXTS, "cad": CAD_EXTS, "design": DESIGN_EXTS, "image": IMAGE_EXTS,
    "video": VIDEO_EXTS, "audio": AUDIO_EXTS, "ebook": EBOOK_EXTS,
    "archive": ARCHIVE_EXTS, "installer": INSTALLER_EXTS, "font": FONT_EXTS,
    "code": CODE_EXTS, "backup": BACKUP_EXTS, "torrent": TORRENT_EXTS,
    "junk": JUNK_EXTS, "other": None,
}


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


# 类别顺序 = 人类可读报告里的展示顺序(junk/other 垫底)
CATEGORY_ORDER = (
    "doc", "cad", "design", "image", "video", "audio", "ebook", "archive",
    "installer", "font", "code", "backup", "torrent", "junk", "other",
)
CATEGORY_ZH = {
    "doc": "文档", "cad": "图纸", "design": "设计源文件", "image": "图片",
    "video": "视频", "audio": "音频", "ebook": "电子书", "archive": "压缩包",
    "installer": "安装包", "font": "字体", "code": "代码", "backup": "备份镜像",
    "torrent": "种子", "junk": "垃圾", "other": "待分类",
}
CATEGORY_EN = {
    "doc": "Documents", "cad": "Drawings", "design": "Design-Sources",
    "image": "Images", "video": "Videos", "audio": "Audio", "ebook": "Ebooks",
    "archive": "Archives", "installer": "Installers", "font": "Fonts",
    "code": "Code", "backup": "Backups", "torrent": "Torrents",
    "junk": "Junk", "other": "Unsorted",
}
# 已存在的目录名 → 类别(命中即视为「已就位」,不产生搬家计划)
# 保守取向:宁可不认(多提几条建议),也不认错(把项目目录当类别目录)
# 简繁都收:繁体库(圖紙/文檔/視頻…)若认不出来,会给用户另建一个简体类别目录,
# 等于凭空造出重复分类 —— 这正是本 skill 最该避免的事
DIR_ALIASES: dict[str, set[str]] = {
    "doc": {"文档", "文檔", "文件资料", "資料", "资料", "办公文档", "辦公文檔",
            "doc", "docs", "document", "documents", "office", "pdf"},
    "cad": {"图纸", "圖紙", "cad", "cad图纸", "cad圖紙", "工程图", "工程圖",
            "施工图", "施工圖", "机械图纸", "機械圖紙", "drawing",
            "drawings", "3d", "模型"},
    "design": {"设计", "設計", "设计源文件", "設計源文件", "源文件", "工程文件",
               "design", "source", "sources", "psd", "工程"},
    "image": {"图片", "圖片", "图像", "圖像", "照片", "截图", "截圖", "image",
              "images", "photo", "photos", "picture", "pictures", "img"},
    "video": {"视频", "視頻", "影视", "影視", "影片", "video", "videos",
              "movie", "movies"},
    "audio": {"音频", "音頻", "音乐", "音樂", "audio", "music", "song", "songs"},
    "ebook": {"电子书", "電子書", "书籍", "書籍", "ebook", "ebooks", "book",
              "books"},
    "archive": {"压缩包", "壓縮包", "归档包", "歸檔包", "archive", "archives",
                "zip"},
    "installer": {"安装包", "安裝包", "安装程序", "安裝程式", "installer",
                  "installers", "setup"},
    "font": {"字体", "字體", "fonts", "font"},
    "code": {"代码", "代碼", "脚本", "腳本", "code", "src", "script", "scripts"},
    "backup": {"备份", "備份", "镜像", "鏡像", "备份镜像", "備份鏡像",
               "backup", "backups"},
    "torrent": {"种子", "種子", "torrent", "torrents"},
    "junk": {"垃圾", "临时", "臨時", "临时文件", "臨時檔案", "junk", "temp",
             "tmp", "trash"},
    "other": {"待分类", "待分類", "未分类", "未分類", "其他", "杂项", "雜項",
              "unsorted", "misc"},
}
# 目录名(小写) → 类别;类别自己的中英文名也算
_DIR_TO_CAT: dict[str, str] = {}
for _cat, _names in DIR_ALIASES.items():
    for _n in _names:
        _DIR_TO_CAT[_n.lower()] = _cat
for _cat in CATEGORY_ORDER:
    _DIR_TO_CAT[CATEGORY_ZH[_cat].lower()] = _cat
    _DIR_TO_CAT[CATEGORY_EN[_cat].lower()] = _cat

# 文件名里的副本标记(纯命名启发式,不判定内容)
DUP_MARK_RE = re.compile(
    r"(?:\s*\(\d+\)|\s*\[\d+\]|\s*副本\s*\d*|\s+copy(?:\s*\d+)?|\s*-\s*copy)$",
    re.I,
)

# 文件名里会让 shell 命令变味儿的字符。Agent 是**照着 target 拼命令**执行的,
# 所以这类名字必须显式警告,否则计划看起来正常、执行时却失败或改变语义。
SHELL_RISKY_CHARS = (
    ('"', "含双引号,会破坏双引号包裹"),
    ("`", "含反引号,shell 会做命令替换"),
    ("$", "含 $,双引号内仍会变量展开"),
    ("\\", "含反斜杠,转义会被吃掉"),
    ("\n", "含换行,会把一条命令截成两条"),
    ("\r", "含回车,会把一条命令截断"),
)


def shell_risk(name: str) -> str | None:
    """纯函数:文件名在 shell 命令里是否会被当选项 / 破坏引号。返回原因或 None。

    实测:`mv -n "-f.pdf" 文档/` → `mv: illegal option -- .`,整条命令失败。
    更隐蔽的是 `-i`(让 mv 变交互式,在非交互 Agent 里挂住或静默跳过)和
    `-f`(可能盖掉 `-n` 的不覆盖语义)。含 `$`/反引号 的名字即使加了双引号
    也会被 shell 展开。
    """
    if name.startswith("-"):
        return "以 - 开头,会被 mv/Move-Item 当成命令选项"
    for ch, why in SHELL_RISKY_CHARS:
        if ch in name:
            return why
    return None


def categorize(name: str, ext: str) -> str:
    """纯函数:按文件名/扩展名归类。junk 优先,未识别 → other。"""
    if name in JUNK_NAMES or name.startswith("._"):
        return "junk"
    if ext in JUNK_EXTS:
        return "junk"
    if ext in CAD_EXTS:
        return "cad"
    if ext in DESIGN_EXTS:
        return "design"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in DOC_EXTS:
        return "doc"
    if ext in EBOOK_EXTS:
        return "ebook"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in INSTALLER_EXTS:
        return "installer"
    if ext in FONT_EXTS:
        return "font"
    if ext in CODE_EXTS:
        return "code"
    if ext in BACKUP_EXTS:
        return "backup"
    if ext in TORRENT_EXTS:
        return "torrent"
    return "other"


def dir_for(cat: str, naming: str) -> str:
    """纯函数:类别 → 目标目录名。"""
    return CATEGORY_EN[cat] if naming == "en" else CATEGORY_ZH[cat]


def target_parts(cat: str, layout: str, mtime: float | None,
                 naming: str = "zh") -> list[str]:
    """纯函数:按布局算目标目录层级(不含文件名)。"""
    base = dir_for(cat, naming)
    year = (datetime.fromtimestamp(mtime).strftime("%Y")
            if mtime else "未知年份")
    if layout == "type-year":
        return [base, year]
    if layout == "year-type":
        return [year, base]
    return [base]


def strip_dup_mark(stem: str) -> str:
    """纯函数:去掉文件名里的副本标记,得到「本名」。"""
    return DUP_MARK_RE.sub("", stem).strip()


def ancestor_cat(parts: list[str]) -> str | None:
    """纯函数:由内向外找第一个「类别目录」祖先,返回它的类别。"""
    for p in reversed(parts):
        cat = _DIR_TO_CAT.get(p.lower())
        if cat:
            return cat
    return None


def ancestors(rel_dir: str) -> list[str]:
    """纯函数:'a/b/c' → ['a', 'a/b'](不含自身)。"""
    parts = rel_dir.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


class FileRec(NamedTuple):
    rel: str         # 相对 root 的路径
    name: str
    parts: tuple[str, ...]  # 父目录层级(相对 root)
    size: int
    mtime: float
    ext: str
    cat: str
    protected: bool  # 命中 --keep-dir 白名单


class Sorter:
    def __init__(self, root: str, max_depth: int, sample: int, layout: str,
                 naming: str, dest: str, keep_dirs: list[str], strict: bool,
                 split_projects: bool, project_min: int,
                 stale_days: int, only_cats: set[str] | None = None,
                 max_issues: int = DEFAULT_MAX_ISSUES) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.sample = sample
        self.layout = layout
        self.naming = naming
        self.dest = dest.strip("/")
        self.keep_dirs = keep_dirs
        self.strict = strict
        self.split_projects = split_projects
        self.project_min = project_min
        self.stale_days = stale_days
        self.only_cats = only_cats
        self.max_issues = max_issues
        # --root 本身可能就是一个类别目录(如 --root /Volumes/nas/图纸)。
        # 这时把它当作所有文件的隐含祖先,否则计划会让人在自己的图纸库里
        # 再建一层 图纸/图纸/。
        self.root_cat = _DIR_TO_CAT.get(self.root.name.lower())
        self.errors: list[dict] = []
        self._files: list[FileRec] = []
        self._sizes: list[tuple[int, str]] = []
        self._n_files = 0
        self.stats: dict = {
            "files": 0, "dirs": 0, "total_size_bytes": 0,
            "by_category": {c: {"count": 0, "size": 0} for c in CATEGORY_ORDER},
            "root_files": 0, "compliant": 0, "protected": 0,
            "nested_other_cat": 0, "project_dirs": 0, "project_files": 0,
            "to_move": 0, "to_delete": 0, "to_review": 0,
            "move_bytes": 0, "junk_bytes": 0, "kept_bytes": 0,
            "dup_suspects": 0, "dup_suspect_bytes": 0,
            "conflicts": 0, "dir_file_conflicts": 0,
            "shell_unsafe_names": 0, "stale_files": 0,
            "filtered_out": 0, "omitted_issues": 0, "issues_truncated": False,
            "target_dirs": {}, "unknown_exts": {}, "largest": [],
            "layout": layout, "naming": naming, "dest": dest,
            "strict": strict, "split_project_dirs": split_projects,
            "only_cats": sorted(only_cats) if only_cats else [],
            "max_issues": max_issues,
            "sampled_out": False, "elapsed_sec": 0.0,
        }
        self.issues: list[dict] = []
        self._ci: bool | None = None      # 目标文件系统是否大小写不敏感(扫描后探测)

    # -- 判定 -----------------------------------------------------------

    def _norm(self, path: str) -> str:
        """撞名判定用的归一化键:大小写不敏感的文件系统上要折叠大小写。"""
        return path.lower() if self._ci else path

    def _probe_case_fold(self) -> bool:
        """探测目标文件系统是否大小写不敏感(macOS APFS / Windows NTFS 默认是)。

        **只读**:拿一个已存在的文件,把名字大小写翻转后 `os.path.exists()`。
        不敏感的文件系统上它会解析回原文件 → True;敏感的则查无此文件 → False。
        翻转名恰好也在扫描集合里时判不了,跳过换一个;全判不了就退回平台默认。
        """
        known = {f.rel for f in self._files}
        for f in self._files[:500]:
            swapped = f.name.swapcase()
            if swapped == f.name:
                continue                       # 纯中文/纯数字名,翻不动
            rel = f"{ '/'.join(f.parts)}/{swapped}" if f.parts else swapped
            if rel in known:
                continue                       # 真有大小写不同的同名文件,判不了
            try:
                return os.path.exists(str(self.root / rel))
            except OSError:
                continue
        return sys.platform in ("darwin", "win32")

    def _protected(self, parts: list[str]) -> bool:
        """命中白名单(--keep-dir + config.json 的 whitelist_dirs)。

        判定逻辑就是 _cfg_match —— 它本来就是从这段代码里抽出来的,所以配置文件
        里的模式与命令行 --keep-dir 走的是**同一条**匹配路径(同样的 fnmatch、
        同样的锚定规则、同样大小写不敏感),不存在两套语义。
        """
        return _cfg_match(parts, self.keep_dirs)

    def _disposition(self, f: FileRec) -> str:
        """该文件的处置:protected / compliant / nested / candidate。"""
        if f.protected:
            return "protected"
        # root 自己就是类别目录时,它算所有文件的隐含祖先
        anc = ancestor_cat(list(f.parts)) or self.root_cat
        if anc == f.cat:
            return "compliant"
        if anc is not None and not self.strict:
            return "nested"          # 已在别的类别目录里,大概率是有意的
        return "candidate"

    def _project_dirs(self, cands: list[FileRec]) -> set[str]:
        """疑似项目目录:命名子目录里多类混合(默认整体保留,不拆散)。

        只从「otherwise 会被搬走」的候选文件聚合 —— 已合规/已嵌套/白名单的
        子树不该被误判成项目目录(如 图纸/2024/ 里混了几个 pdf)。
        """
        agg: dict[str, dict] = {}
        for f in cands:
            for i in range(1, len(f.parts) + 1):
                d = "/".join(f.parts[:i])
                if any(p.lower() in _DIR_TO_CAT for p in d.split("/")):
                    continue                           # 类别目录不算项目目录
                ent = agg.setdefault(d, {"files": 0, "size": 0, "cats": set()})
                ent["files"] += 1
                ent["size"] += f.size
                ent["cats"].add(f.cat)
        cand = {d for d, v in agg.items()
                if v["files"] >= self.project_min and len(v["cats"]) >= 2
                and not self._protected(d.split("/"))}
        # 只留最上层的项目目录(嵌套的不重复报)
        return {d for d in cand
                if not any(p in cand for p in ancestors(d))}

    def _nearest_project(self, parts: tuple[str, ...],
                         projects: set[str]) -> str | None:
        for i in range(len(parts), 0, -1):
            d = "/".join(parts[:i])
            if d in projects:
                return d
        return None

    # -- 遍历 -----------------------------------------------------------

    def _walk(self, dir_path: Path | str, rel_parts: list[str]) -> None:
        if len(rel_parts) > self.max_depth:
            return
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as e:
            self.errors.append({"path": "/".join(rel_parts) or ".",
                                "error": str(e)})
            return
        for entry in entries:
            if self.sample and self._n_files >= self.sample:
                self.stats["sampled_out"] = True
                return
            name = entry.name
            try:
                if entry.is_symlink():
                    continue
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                self.stats["dirs"] += 1
                self._walk(entry.path, rel_parts + [name])
            else:
                self._collect(entry, rel_parts, name)

    def _collect(self, entry: os.DirEntry, rel_parts: list[str],
                 name: str) -> None:
        is_junk_name = name in JUNK_NAMES or name.startswith("._")
        if name.startswith(".") and not is_junk_name:
            return                      # 普通 dotfile 静默忽略
        ext = "" if is_junk_name else (
            name.rsplit(".", 1)[-1].lower() if "." in name else "")
        try:
            st = entry.stat()
        except OSError as e:
            self.errors.append({"path": "/".join(rel_parts + [name]),
                                "error": str(e)})
            return
        self._n_files += 1
        self._files.append(FileRec(
            rel="/".join([*rel_parts, name]),
            name=name,
            parts=tuple(rel_parts),
            size=st.st_size,
            mtime=st.st_mtime,
            ext=ext,
            cat=categorize(name, ext),
            protected=self._protected(rel_parts),
        ))

    # -- 计划 -----------------------------------------------------------

    def _target(self, f: FileRec,
                occupied: set[str]) -> tuple[str | None, str | None]:
        """算 old → new 目标路径;重名时加 __2/__3 后缀并回冲突方。

        撞名超过 MAX_RENAME_TRIES 次(几千个同名文件的库)就**不再自动编号**,
        返回 target=None 交人工/LLM 命名 —— 既避免 O(n²) 试探,也避免生成
        几千条 `x__4999.jpg` 这种没意义的计划。

        occupied 里存的是 _norm() 归一化后的键:大小写不敏感的文件系统
        (macOS APFS / Windows NTFS 默认)上 `X.jpg` 与 `x.jpg` 是同一个路径,
        不归一就会漏判撞名,执行时 `mv -n` 静默不搬。
        """
        parts = target_parts(f.cat, self.layout, f.mtime or None, self.naming)
        if self.dest:
            parts = [self.dest, *parts]
        base = "/".join(parts)
        cand = f"{base}/{f.name}"
        if self._norm(cand) not in occupied:
            return cand, None
        stem, dot_ext = os.path.splitext(f.name)
        for n in range(2, MAX_RENAME_TRIES + 2):
            cand = f"{base}/{stem}__{n}{dot_ext}"
            if self._norm(cand) not in occupied:
                return cand, f"{base}/{f.name}"
        return None, f"{base}/{f.name}"

    def _blocked_by_file(self, target: str, file_keys: set[str]) -> str | None:
        """目标路径的某一级目录与现存**文件**同名 → mkdir 会失败。

        例:根目录有个无扩展名的文件叫 `图纸`,同时 `平面.dwg` 要搬进 `图纸/`。
        这种计划按原样执行不下去(File exists),必须提前报出来。
        """
        parts = target.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            key = self._norm("/".join(parts[:i]))
            if key in file_keys:
                return "/".join(parts[:i])
        return None

    def _plan(self) -> None:
        s = self.stats
        now = datetime.now().timestamp()
        self._ci = self._probe_case_fold()
        s["case_insensitive_fs"] = bool(self._ci)
        s["root_category"] = self.root_cat
        # occupied / file_keys 用归一化键:大小写不敏感的文件系统上
        # X.jpg 与 x.jpg 是同一路径,不归一会漏判撞名(mv -n 会静默不搬)
        occupied = {self._norm(f.rel) for f in self._files}
        file_keys = set(occupied)
        disp = {f.rel: self._disposition(f) for f in self._files}
        projects = self._project_dirs(
            [f for f in self._files if disp[f.rel] == "candidate"])
        # 疑似副本索引:(大小, 去标记后的本名) → [(相对路径, 是否带副本标记)]
        peers: dict[tuple[int, str], list[tuple[str, bool]]] = {}
        for f in self._files:
            stem = os.path.splitext(f.name)[0]
            pkey = (f.size, strip_dup_mark(stem).lower())
            peers.setdefault(pkey, []).append(
                (f.rel, bool(DUP_MARK_RE.search(stem))))

        proj_stat: dict[str, dict] = {}
        file_issues: list[dict] = []
        for f in sorted(self._files, key=lambda x: (x.rel,)):
            cat = f.cat
            s["files"] += 1
            s["total_size_bytes"] += f.size
            c = s["by_category"][cat]
            c["count"] += 1
            c["size"] += f.size
            if not f.parts:
                s["root_files"] += 1
            age_days = ((now - f.mtime) / 86400) if f.mtime else 0.0
            if age_days > self.stale_days and cat != "junk":
                s["stale_files"] += 1
            if cat == "other":
                ukey = f".{f.ext}" if f.ext else "(无扩展名)"
                s["unknown_exts"][ukey] = s["unknown_exts"].get(ukey, 0) + 1
            self._sizes.append((f.size, f.rel))

            anc = ancestor_cat(list(f.parts))
            d = disp[f.rel]
            if d == "protected":
                s["protected"] += 1
                s["kept_bytes"] += f.size
                continue
            if d == "compliant":
                s["compliant"] += 1
                continue
            if d == "nested":
                # 已在别的类别目录里(如 图纸/ 里的效果图) — 大概率是有意的
                s["nested_other_cat"] += 1
                s["kept_bytes"] += f.size
                continue
            proj = None if self.split_projects else \
                self._nearest_project(f.parts, projects)
            if proj:
                ent = proj_stat.setdefault(proj, {
                    "files": 0, "size": 0, "cats": set(), "sample": []})
                ent["files"] += 1
                ent["size"] += f.size
                ent["cats"].add(cat)
                if len(ent["sample"]) < 3:
                    ent["sample"].append(f.name)
                continue
            if self.only_cats and cat not in self.only_cats:
                s["filtered_out"] += 1     # 本轮不处理这类,但统计仍完整
                continue

            problems: list[str] = []
            target: str | None = None
            action = "move"
            confidence = "high"
            conflict_with: str | None = None

            if cat == "junk":
                action = ("delete" if (f.name in JUNK_NAMES
                                       or f.name.startswith("._"))
                          else "delete-confirm")
                problems.append(
                    "系统垃圾文件(可直接删)" if action == "delete"
                    else "系统/临时残留文件(建议先隔离再删)")
                s["junk_bytes"] += f.size
            else:
                target, conflict_with = self._target(f, occupied)
                if target:
                    occupied.add(self._norm(target))
                    blocked = self._blocked_by_file(target, file_keys)
                    if blocked:
                        # 目标目录名被一个现存**文件**占着,mkdir 会 File exists
                        s["dir_file_conflicts"] += 1
                        action = "review"
                        confidence = "low"
                        problems.append(
                            f"目标目录「{blocked}/」被一个同名**文件**占着,"
                            "mkdir 会失败;先把那个文件移走(它自己也在本计划里)"
                            "或改用 --dest 换个归档根")
                if anc is not None and self.strict:
                    problems.append(
                        f"已在「{CATEGORY_ZH[anc]}」目录里但本文件属于"
                        f"{CATEGORY_ZH[cat]}(--strict 才检查这类)")
                if conflict_with:
                    s["conflicts"] += 1
                    action = "review"
                    if target:
                        problems.append(
                            f"目标路径与已有文件重名({conflict_with}),"
                            f"已改名 {os.path.basename(target)};确认或自定义")
                        confidence = "medium"
                    else:
                        origin = f.parts[-1] if f.parts else "root"
                        problems.append(
                            f"目标目录里同名文件超过 {MAX_RENAME_TRIES} 个"
                            f"(都叫 {f.name}),不再自动编号;建议保留来源目录名"
                            f"(如 {os.path.dirname(conflict_with)}/"
                            f"{origin}_{f.name})或让用户决定命名规则")
                        confidence = "low"
                if cat == "other":
                    action = "review"
                    confidence = "low"
                    problems.append(
                        f"扩展名 {('.' + f.ext) if f.ext else '缺失'} 不在已知"
                        "类别里,归到「待分类」前请先确认它是什么")
                elif not problems:
                    where = "根目录" if not f.parts else "/".join(f.parts) + "/"
                    problems.append(
                        f"{CATEGORY_ZH[cat]}散落在 {where},"
                        f"建议归档到 {os.path.dirname(target or '')}/")
                # 疑似副本(命名启发式,内容级确认交给 dedup-finder)
                stem = os.path.splitext(f.name)[0]
                if DUP_MARK_RE.search(stem):
                    dkey = (f.size, strip_dup_mark(stem).lower())
                    others = [p for p, _ in peers.get(dkey, []) if p != f.rel]
                    if others:
                        plain = [p for p, mk in peers.get(dkey, [])
                                 if p != f.rel and not mk]
                        ref = plain[0] if plain else others[0]
                        s["dup_suspects"] += 1
                        s["dup_suspect_bytes"] += f.size
                        problems.append(
                            f"疑似副本(名字带副本/(1) 标记,且与 {ref} 同大小)"
                            "— 是否真重复要 dedup-finder 做内容级确认;先删重复能省掉"
                            "搬这些字节,但先分类也不会漏检(dedup 与目录结构无关)")
                # 文件名对 shell 不友好 → 计划看着正常,执行时会失败或变语义
                risk = shell_risk(f.name)
                if risk:
                    s["shell_unsafe_names"] += 1
                    problems.append(
                        f"⚠ 文件名{risk};Agent 执行时不能直接拼进命令 —— "
                        f"POSIX 写 ./{f.name} 或加 -- 分隔,"
                        "PowerShell 用 -LiteralPath")
                if action == "move":
                    s["to_move"] += 1
                    s["move_bytes"] += f.size
                    td = os.path.dirname(target or "")
                    tent = s["target_dirs"].setdefault(
                        td, {"count": 0, "size": 0})
                    tent["count"] += 1
                    tent["size"] += f.size
            if action in ("delete", "delete-confirm"):
                s["to_delete"] += 1
            elif action == "review":
                s["to_review"] += 1

            issue: dict = {
                "path": f.rel, "name": f.name, "is_dir": False,
                "problems": problems, "category": cat,
                "category_zh": CATEGORY_ZH[cat],
                "action": action, "target": target,
                "size": f.size, "age_days": int(age_days),
                "confidence": confidence,
            }
            if conflict_with:
                issue["conflict_with"] = conflict_with
            file_issues.append(issue)

        # 计划条数上限:几万文件的库不能吐几 MB JSON 给 LLM 读。
        # 统计(stats)始终是全量的,只有 issues 列表被截断 —— LLM 应该
        # 按 --only-cat 分批取(见 SKILL.md 场景 2)。
        if self.max_issues and len(file_issues) > self.max_issues:
            s["omitted_issues"] = len(file_issues) - self.max_issues
            s["issues_truncated"] = True
            file_issues = file_issues[: self.max_issues]
        self.issues.extend(file_issues)

        # 疑似项目目录 → 一条目录级 issue(默认不拆)
        for d in sorted(proj_stat):
            ent = proj_stat[d]
            cats = sorted(ent["cats"], key=CATEGORY_ORDER.index)
            s["project_dirs"] += 1
            s["project_files"] += ent["files"]
            s["kept_bytes"] += ent["size"]
            self.issues.append({
                "path": d + "/", "name": d.rsplit("/", 1)[-1], "is_dir": True,
                "problems": [
                    f"疑似项目目录:{ent['files']} 个文件跨 {len(cats)} 类"
                    f"({'/'.join(CATEGORY_ZH[c] for c in cats)}),"
                    "默认整体保留不拆散",
                    "确认要按类型拆开再加 --split-project-dirs;"
                    "确认要整体不动可加 --keep-dir 明确白名单",
                ],
                "category": "mixed", "category_zh": "混合目录",
                "action": "review", "target": None,
                "size": ent["size"], "files": ent["files"],
                "categories": cats, "sample": ent["sample"],
                "confidence": "medium",
            })
            s["to_review"] += 1

        self._sizes.sort(reverse=True)
        s["largest"] = [{"path": p, "size": sz}
                        for sz, p in self._sizes[:TOP_N]]
        s["unknown_exts"] = dict(sorted(
            s["unknown_exts"].items(), key=lambda x: -x[1])[:15])
        s["target_dirs"] = dict(sorted(
            s["target_dirs"].items(), key=lambda x: -x[1]["size"]))

    # -- 入口 -----------------------------------------------------------

    def scan(self) -> dict:
        t0 = time.time()
        print(f"正在扫描 {self.root} ...\n", file=sys.stderr)
        if not self.root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {self.root}")
        self._walk(self.root, [])
        self._plan()
        self.stats["elapsed_sec"] = round(time.time() - t0, 2)
        if CONFIG_INFO:
            # 只在真加载了 config.json 时才多这一个键:没有配置文件时 JSON 与
            # 引入覆盖层之前逐字节一致(smoke 里有负控制专门盯这件事)
            self.stats["config"] = dict(CONFIG_INFO)
        print(
            f'扫描完成: {self.stats["files"]} 文件, '
            f'待归档 {self.stats["to_move"]} 个\n', file=sys.stderr,
        )
        return {
            "skill": "file-sorter",
            "root": str(self.root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": self.stats,
            "count": len(self.issues),
            "issues": self.issues,
            "errors": self.errors,
        }


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


ACTION_ZH = {
    "move": "分类归档(mv)", "delete": "直接删(系统垃圾)",
    "delete-confirm": "确认后删(临时/残留)", "review": "人工确认",
}


def _print_human(result: dict, top: int) -> None:
    s = result["stats"]
    print("=" * 70)
    print("file-sorter — 通用分类归档报告")
    print("=" * 70)
    print(f"根目录: {result['root']}")
    print(f"布局: --layout {s['layout']} | 目录命名: {s['naming']}"
          + (f" | 归档根: {s['dest']}/" if s["dest"] else ""))
    print(f"文件 {s['files']} | 总大小 {_human(s['total_size_bytes'])} "
          f"| 扫描耗时 {s['elapsed_sec']}s")
    kept = s["compliant"] + s["protected"] + s["nested_other_cat"] \
        + s["project_files"]
    pct = (kept * 100 // s["files"]) if s["files"] else 100
    print(f"不动 {kept} ({pct}%) = 已就位 {s['compliant']}"
          f" + 白名单 {s['protected']}"
          f" + 跨类别嵌套 {s['nested_other_cat']}"
          f" + 项目目录内 {s['project_files']}")
    print(f"待归档 {s['to_move']}({_human(s['move_bytes'])}) "
          f"| 待确认 {s['to_review']} | 待清理 {s['to_delete']}"
          f"({_human(s['junk_bytes'])})")
    print(f"散在根目录 {s['root_files']} | 疑似项目目录 {s['project_dirs']} "
          f"| 疑似副本 {s['dup_suspects']} | 重名冲突 {s['conflicts']} "
          f"| 久未动 {s['stale_files']}")
    if s.get("root_category"):
        print(f"└ --root 本身就是「{CATEGORY_ZH[s['root_category']]}」目录,"
              "里面的同类文件视为已就位(不会再套一层同名目录)")
    if s.get("dir_file_conflicts"):
        print(f"└ ⚠ {s['dir_file_conflicts']} 个目标目录被同名**文件**占着,"
              "mkdir 会失败 —— 已降级为人工确认,别照原计划直接执行")
    if s.get("shell_unsafe_names"):
        print(f"└ ⚠ {s['shell_unsafe_names']} 个文件名对 shell 不友好"
              "(- 开头 / 含 \" ` $ \\ 或换行):执行时不能直接拼进命令,"
              "POSIX 加 ./ 前缀或 --,PowerShell 用 -LiteralPath")
    if s.get("case_insensitive_fs"):
        print("└ 文件系统大小写不敏感(macOS/Windows),撞名判定已按此折叠;"
              "X.jpg 与 x.jpg 视为同一路径")
    if s["dup_suspects"]:
        # 顺序建议给数据,不给绝对规则:去重与分类谁先都不影响正确性
        # (dedup-finder 是内容级的、与目录结构无关),差的只是白搬多少字节。
        share = (s["dup_suspect_bytes"] * 100 // s["move_bytes"]) \
            if s["move_bytes"] else 0
        print(f"└ 疑似副本共 {_human(s['dup_suspect_bytes'])}"
              f"(占待搬体积 {share}%)— 先跑 dedup-finder 删掉就能少搬这么多;"
              "顺序不影响能否检出,只影响白搬多少")
    if s.get("only_cats"):
        print(f"本轮只处理类别: {', '.join(s['only_cats'])}"
              f"(其余 {s['filtered_out']} 个文件计入统计但不出计划)")
    if s.get("issues_truncated"):
        print(f"⚠ 计划条数已达上限 --max-issues {s['max_issues']},"
              f"省略 {s['omitted_issues']} 条(stats 是全量的);"
              "用 --only-cat 分类别分批取,或调大 --max-issues")

    print("\n类别分布:")
    for cat in CATEGORY_ORDER:
        cv = s["by_category"][cat]
        if cv["count"]:
            print(f"  {CATEGORY_ZH[cat]:<6} {cv['count']:>7} 个  "
                  f"{_human(cv['size']):>10}")

    if s["target_dirs"]:
        print(f"\n归档后的目录结构预览(前 {top} 个目标目录):")
        for i, (td, tv) in enumerate(s["target_dirs"].items()):
            if i >= top:
                print(f"  ... 还有 {len(s['target_dirs']) - top} 个目录")
                break
            print(f"  {td}/  ← {tv['count']} 个 ({_human(tv['size'])})")

    if s["unknown_exts"]:
        print("\n未识别扩展名 Top(归入「待分类」,请人工判断):")
        for ext, n in s["unknown_exts"].items():
            print(f"  {ext:<14} {n} 个")

    if s["largest"]:
        print("\n最大文件 Top10:")
        for item in s["largest"]:
            print(f"  {_human(item['size']):>10}  {item['path']}")

    if result.get("errors"):
        print(f"\n⚠️ 读取失败 {len(result['errors'])} 项(权限/挂载断连):")
        for e in result["errors"][:5]:
            print(f"  {e['path']}: {e['error']}")
    if s.get("sampled_out"):
        print("\n(已达 --sample 上限,结果不完整)")

    issues = result["issues"]
    if not issues:
        print("\n✅ 目录已经很规整,没有要搬的文件!")
        return

    by_action: dict[str, list] = {}
    for issue in issues:
        by_action.setdefault(issue["action"], []).append(issue)

    print(f"\n⚠ {len(issues)} 个待处理项(按建议动作分组):\n")
    for action in ("move", "review", "delete-confirm", "delete"):
        items = by_action.get(action)
        if not items:
            continue
        print(f"【{action} · {ACTION_ZH[action]}】{len(items)} 项")
        for item in items[:top]:
            if item.get("is_dir"):
                print(f"    {item['path']}  ({item['files']} 个文件, "
                      f"{_human(item['size'])})")
            else:
                arrow = f" → {item['target']}" if item.get("target") else ""
                print(f"    {item['path']}{arrow}  ({_human(item['size'])})")
            for p in item["problems"]:
                print(f"      - {p}")
        if len(items) > top:
            print(f"    ... 还有 {len(items) - top} 项")
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
        description="file-sorter: 通用文件分类归档只读扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="分类归档扫描(只读,算出 old→new)")
    scan_p.add_argument("--root", required=True,
                        help="要整理的目录(本地挂载路径),如 /Volumes/nas/data")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--layout", default="type",
                        choices=("type", "type-year", "year-type"),
                        help="目标结构:类别 / 类别+年份 / 年份+类别(默认 type)")
    scan_p.add_argument("--naming", default="zh", choices=("zh", "en"),
                        help="目标目录用中文名还是英文名(默认 zh)")
    scan_p.add_argument("--dest", default="",
                        help="归档根目录前缀,如 --dest 整理后(默认就地归档)")
    scan_p.add_argument("--keep-dir", action="append", default=[],
                        metavar="GLOB",
                        help="白名单目录(可重复),命中的目录整体不动,如 "
                             "--keep-dir '2024_官网改版' --keep-dir '成品*';"
                             "每次都要重打的那几条可以写进本脚本同目录的 "
                             "config.json(键 whitelist_dirs,语义完全相同)")
    scan_p.add_argument("--strict", action="store_true",
                        help="加查:已在别的类别目录里、但类别不符的文件"
                             "(默认不动,避免打散有意的结构)")
    scan_p.add_argument("--split-project-dirs", action="store_true",
                        help="拆开疑似项目目录(多类混合的命名子目录),"
                             "把里面的文件也按类型归档(默认整体保留)")
    scan_p.add_argument("--project-min-files", type=int,
                        default=PROJECT_MIN_FILES,
                        help=f"判定疑似项目目录的最少文件数(默认 "
                             f"{PROJECT_MIN_FILES})")
    scan_p.add_argument("--only-cat", default="",
                        metavar="LIST",
                        help="只给这些类别出搬家计划(逗号分隔,统计仍全量),"
                             "大库分批整理用,如 --only-cat cad,doc。可选:"
                             + ",".join(CATEGORY_ORDER))
    scan_p.add_argument("--max-issues", type=int, default=DEFAULT_MAX_ISSUES,
                        help=f"计划条数上限(默认 {DEFAULT_MAX_ISSUES},0=不限);"
                             "超出只截断 issues 列表,stats 仍是全量")
    scan_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                        help=f"最大递归深度(默认 {MAX_DEPTH})")
    scan_p.add_argument("--sample", type=int, default=0,
                        help="最多扫描文件数(0=不限,大库先摸底)")
    scan_p.add_argument("--top", type=int, default=8,
                        help="人类可读输出每组最多显示条数")
    scan_p.add_argument("--stale-days", type=int, default=365 * 3,
                        help="超过 N 天未动计入「久未动」(默认 1095)")

    args = parser.parse_args()
    if args.cmd != "scan":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    only_cats: set[str] | None = None
    if args.only_cat.strip():
        only_cats = {c.strip().lower() for c in args.only_cat.split(",")
                     if c.strip()}
        bad = sorted(only_cats - set(CATEGORY_ORDER))
        if bad:
            print(f"❌ --only-cat 未知类别: {', '.join(bad)}", file=sys.stderr)
            print(f"   可选: {', '.join(CATEGORY_ORDER)}", file=sys.stderr)
            raise SystemExit(1)

    # 可选覆盖层:同目录 config.json。没有这个文件时下面这行什么都不做,
    # CONFIG_WHITELIST 保持空列表,输出与引入覆盖层之前逐字节一致。
    load_config()

    sorter = Sorter(args.root, args.max_depth, args.sample, args.layout,
                    args.naming, args.dest,
                    args.keep_dir + CONFIG_WHITELIST, args.strict,
                    args.split_project_dirs, args.project_min_files,
                    args.stale_days, only_cats, args.max_issues)
    result = sorter.scan()
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
