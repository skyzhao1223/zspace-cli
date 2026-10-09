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
import fnmatch
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn

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
# 与 file-sorter / photo-organizer 同名同义:categorize() 判定阶梯的第一级。
# 原本内联在 categorize() 里,提成常量才能让 config.json 的
# extension_overrides 把一个扩展名从 junk 里摘出来(见 CONFIG_EXT_TABLES)。
JUNK_EXTS = {"tmp", "temp", "bak"}
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

# ── 可选覆盖层:config.json(与本脚本同目录)────────────────────────────
# 「装 skill」是逐目录 copytree(cli.py 的 zs skill,加 --only 也一样),共享模块
# 装不进用户目录,所以这一段在每个 scanner 里逐字重复 —— 要改就全局搜索替换,
# 别只改一处。per-skill 的只有紧跟其后的 CONFIG_KEYS 和它点到的那张表。
CONFIG_NAME = "config.json"
CONFIG_KEYS = ("skip_dirs", "extension_overrides")
CONFIG_SKIP: list[str] = []           # skip_dirs 追加到这里(扩展内置 SKIP_DIRS)
CONFIG_INFO: dict[str, object] = {}   # 非空 = 真加载了配置,回写进 stats 供核对
# 类别名 → 本 skill 的扩展名集合;值为 None 表示「不属于任何集合」,即兜底类别。
CONFIG_EXT_TABLES: dict[str, set[str] | None] = {
    "junk": JUNK_EXTS, "partial": PARTIAL_EXTS, "torrent": TORRENT_EXTS,
    "installer": INSTALLER_EXTS, "archive": ARCHIVE_EXTS, "video": VIDEO_EXTS,
    "audio": AUDIO_EXTS, "photo": PHOTO_EXTS, "doc": DOC_EXTS, "other": None,
}


def _cfg_die(path: Path, msg: str) -> NoReturn:
    """配置有问题就**立刻退出**,并指名是哪个文件、哪个键。

    静默忽略一份用户以为生效了的配置是最坏的失败方式:扫描会继续按内置规则出
    结果,而且不给任何解释。
    """
    raise SystemExit(f"❌ 配置文件 {path} 无效:{msg}")


def _cfg_match(rel_parts: list[str], patterns: list[str]) -> bool:
    """目录是否命中 skip_dirs 模式。锚定在被扫描的根目录,大小写不敏感。

    模式匹配「整段相对路径」**或**「任一级目录名」即命中,fnmatch 的 `*` 会跨过
    `/`。所以 `临时/*` 命中 <root>/临时/2024,但不命中 <root>/x/临时;不含
    `/` 的 `临时` 命中任意深度的同名目录。命中一个目录即命中它的整棵子树。
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
    skip = raw.get("skip_dirs", [])
    if not isinstance(skip, list):
        _cfg_die(path, f"skip_dirs 必须是字符串数组,实际是 "
                 f"{type(skip).__name__}(只有一条也要写成 [\"临时\"])")
    for i, item in enumerate(skip):
        if not isinstance(item, str):
            _cfg_die(path, f"skip_dirs[{i}] 必须是字符串,实际是 "
                     f"{type(item).__name__}")
        pat = item.strip()
        if not pat:
            _cfg_die(path, f"skip_dirs[{i}] 是空字符串")
        if pat.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", pat):
            _cfg_die(path, f"skip_dirs[{i}] 写成了绝对路径 {item!r};"
                     "模式锚定在被扫描的根目录,只能写相对路径(如 临时/*)")
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
    # 校验全过才动手。扩展名先从别的表里摘掉再放进指定的那一张,否则判定阶梯
    # 会按它自己的顺序命中旧类别,覆盖看起来完全没生效。
    CONFIG_SKIP.extend(item.strip() for item in skip)
    for e, cat in pairs:
        for name, table in CONFIG_EXT_TABLES.items():
            if table is not None and name != cat:
                table.discard(e)
        target = CONFIG_EXT_TABLES[cat]
        if target is not None:
            target.add(e)
    CONFIG_INFO.update({
        "path": str(path),
        "skip_dirs": list(CONFIG_SKIP),
        "extension_overrides": dict(pairs),
    })
    print(f"ℹ️ 已加载覆盖配置 {path}(skip_dirs {len(skip)} 条,"
          f"extension_overrides {len(pairs)} 条)", file=sys.stderr)


def categorize(name: str, ext: str) -> str:
    """纯函数:按文件名/扩展名给下载文件分类。"""
    if name in JUNK_NAMES or name.startswith("._") or ext in JUNK_EXTS:
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
        self._cfg_skipped_dirs = 0   # 因 config skip_dirs 未纳入的目录数

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
                child_rel = rel_parts + [name]
                if _cfg_match(child_rel, CONFIG_SKIP):
                    self._cfg_skipped_dirs += 1
                    continue
                self.stats["dirs"] += 1
                self._walk(entry.path, child_rel)
            elif entry.is_file():
                self._n_files += 1
                self._check_file(entry, rel_parts, name, dir_names)

    def _check_file(self, entry: os.DirEntry, rel_parts: list[str],
                    name: str, sibling_dirs: set[str]) -> None:
        # 判定顺序与 file-sorter 一致:**先**认出 junk 名,再决定要不要跳过点文件。
        # 原先这里写的是 `name.startswith(".") and name not in JUNK_NAMES`,于是
        # `.DS_Store`(在 JUNK_NAMES 里)能过、`._movie.ass`(不在)被提前 return,
        # 永远走不到 categorize() 里那条 `startswith("._")` → junk —— 那条规则
        # 经 CLI 是死代码,只有直接调纯函数才看得到(issue #58)。
        is_junk_name = name in JUNK_NAMES or name.startswith("._")
        if name.startswith(".") and not is_junk_name:
            return                      # 普通 dotfile 静默忽略
        # junk 名不参与扩展名推断:`._movie.ass` 不该拿到 `ass` 这个扩展名,
        # 否则一旦 categorize() 的阶梯顺序变动,它就可能被判成字幕而不是垃圾。
        ext = "" if is_junk_name else (
            name.rsplit(".", 1)[-1].lower() if "." in name else "")
        stem = os.path.splitext(name)[0]
        full = "/".join(rel_parts + [name])

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
        if CONFIG_INFO:
            # 只在真加载了 config.json 时才多这一个键(与 photo-organizer 的
            # --exif 同一个手法),没有配置文件时 JSON 与引入覆盖层之前逐字节一致
            self.stats["config"] = {**CONFIG_INFO,
                                    "skipped_dirs": self._cfg_skipped_dirs}
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


def _print_config(s: dict) -> None:
    """把生效的覆盖配置打进人类报告 —— 「哪份配置在生效」必须一眼可查。

    三个 scanner 里 dedup-finder 的输出是删除计划,而 prefer_keep_hints 能反转
    「重复组里保留哪个」,所以生效的提示词尤其不能只躺在 JSON 里。没有加载配置
    时一个字都不打印:人类报告与引入覆盖层之前逐字节一致。
    """
    cfg: dict = s.get("config") or {}
    if not cfg:
        return
    print(f"\n⚙ 已加载覆盖配置 {cfg['path']}")
    for key in CONFIG_KEYS:
        val = cfg.get(key)
        if not val:
            continue
        if isinstance(val, list):
            shown = ", ".join(val)
        elif isinstance(val, dict):
            shown = ", ".join(f"{k}→{v}" for k, v in val.items())
        else:
            shown = str(val)
        print(f"  {key}: {shown}")
    if cfg.get("prefer_keep_hints_effective"):
        print("  保留提示词生效全集(内置 + 本次追加,决定重复组里先保留哪个):")
        print(f"    {', '.join(cfg['prefer_keep_hints_effective'])}")
    print(f"  因 skip_dirs 未纳入的目录: {cfg.get('skipped_dirs', 0)} 个")


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
    _print_config(s)
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

    # 可选覆盖层:同目录 config.json。没有这个文件时下面这行什么都不做,
    # 内置的 SKIP_DIRS 与 9 张 *_EXTS 表 一个都不变,输出与引入覆盖层之前逐字节一致。
    load_config()

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
