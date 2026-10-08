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
  python photo_organizer.py scan --root ... --exif      # 读 EXIF 拍摄日期(可选)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn

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
    "photo": PHOTO_EXTS, "video": VIDEO_EXTS, "sidecar": SIDECAR_EXTS,
    "junk": JUNK_EXTS, "non_media": None,
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


# ── 拍摄日期的外部来源(仅 --exif 启用)────────────────────────────────
# 硬规则是纯 stdlib:不 import Pillow/exifread,而是 shell out 到机器上已经装好
# 的 exiftool;macOS 再退一档用 Spotlight 的 mdls;两者都不可用才退回 mtime。
# 不加 --exif 时下面这些代码一行都不会执行,默认行为与旧版逐字节一致。
EXIFTOOL_CHUNK = 200       # 单次 exiftool 带多少文件(给命令行长度上限留余量)
EXIFTOOL_TIMEOUT = 300     # 单批秒数;SMB 挂载卡死时不能把整个扫描挂住
MDLS_TIMEOUT = 30
MIN_PLAUSIBLE_YEAR = 1900  # 再早的拍摄日期是坏数据,不是照片

MTIME_SOURCE = "mtime(弱,仅参考)"

# exiftool 给 2024:05:03 14:22:31;mdls 给 2024-05-03 14:22:31 +0000
_EXIF_TS_RE = re.compile(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_MDLS_TS_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})\s*([+-]\d{2}:?\d{2})?"
)

# 报告里给每个日期挂的短标签(完整 date_source 仍写进 JSON)
_SOURCE_TAG = {"exif": "exif", "mdls": "mdls", "filename": "filename",
               MTIME_SOURCE: "mtime"}


def _warn(message: str) -> None:
    print(f"⚠️ {message}", file=sys.stderr)


def _valid_calendar_date(y: str, m: str, d: str) -> str | None:
    """是真实历法日期、且不早于 MIN_PLAUSIBLE_YEAR 才放行,否则 None。"""
    try:
        yi, mi, di = int(y), int(m), int(d)
        datetime(yi, mi, di)   # 2024-02-30、月日为 00 都在这里抛 ValueError
    except ValueError:
        return None
    if yi < MIN_PLAUSIBLE_YEAR:
        return None
    return f"{yi:04d}-{mi:02d}-{di:02d}"


def parse_exif_datetime(raw: object) -> str | None:
    """exiftool 的 DateTimeOriginal → 'YYYY-MM-DD';读不出就是 None。

    exiftool 会把坏值原样吐出来(实测:全零的 `0000:00:00 00:00:00`,甚至任意
    字符串),所以校验必须在这里做,不能指望它已经过滤过。DateTimeOriginal 是
    相机当时的本地墙上时间、不带时区,直接取日期即可。
    """
    if not isinstance(raw, str):
        return None
    m = _EXIF_TS_RE.search(raw)
    if m is None:
        return None
    hh, mi, ss = int(m.group(4)), int(m.group(5)), int(m.group(6))
    if hh > 23 or mi > 59 or ss > 59:
        return None
    return _valid_calendar_date(m.group(1), m.group(2), m.group(3))


def parse_mdls_datetime(raw: object) -> str | None:
    """mdls 的 kMDItemContentCreationDate → 'YYYY-MM-DD'(换算到本机时区)。

    形如 `kMDItemContentCreationDate = 2024-05-03 14:22:31 +0000`;文件没被
    Spotlight 索引时输出 `(null)`,甚至直接 exit=1 报 could not find。

    Spotlight 给的是 UTC,而本脚本其余日期(mmexport epoch、mtime)都是本机
    时区 —— 不换算的话,东八区下午拍的照片会被归到前一天,与既有行为不一致。
    """
    if not isinstance(raw, str):
        return None
    m = _MDLS_TS_RE.search(raw)
    if m is None:
        return None
    stamp = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    stamp += f" {m.group(4)}:{m.group(5)}:{m.group(6)}"
    offset = (m.group(7) or "").replace(":", "")
    fmt = "%Y-%m-%d %H:%M:%S"
    if offset:
        stamp, fmt = f"{stamp} {offset}", f"{fmt} %z"
    try:
        parsed = datetime.strptime(stamp, fmt)
    except ValueError:
        return None
    if parsed.year < MIN_PLAUSIBLE_YEAR:
        return None
    return parsed.astimezone().strftime("%Y-%m-%d")


def _path_key(path: str) -> str:
    """跨平台路径匹配键:大小写归一(Windows)+ 解析符号链接(macOS 的 /tmp)。"""
    return os.path.normcase(os.path.realpath(path))


class DateResolver:
    """--exif 模式的拍摄日期解析:exiftool → mdls →(调用方兜底 mtime)。

    两个设计决定都是冲着大库去的:
    - 工具可用性只在构造时用 `shutil.which` 探测一次,不逐文件探测;
    - exiftool 一次能吃多个路径并输出 `-json`,所以按 EXIFTOOL_CHUNK 批量调用,
      而不是一张图起一个进程 —— 几万张图的库上那是数量级的差距。

    每一档都失败即退、绝不抛异常:二进制不存在、非零退出、输出不是合法 JSON、
    文件压根没出现在输出里、日期是坏值,统统交给下一档。
    """

    def __init__(self) -> None:
        self.exiftool = shutil.which("exiftool")
        # mdls 是 macOS 独有的 Spotlight 前端,Linux/Windows 上不存在
        self.mdls = shutil.which("mdls") if sys.platform == "darwin" else None

    @property
    def available(self) -> bool:
        return bool(self.exiftool or self.mdls)

    def tools(self) -> dict[str, str | None]:
        """探测结果,写进 JSON/报告 —— 用来解释"为什么全退回了 mtime"。"""
        return {"exiftool": self.exiftool, "mdls": self.mdls}

    def resolve(self, paths: list[str]) -> dict[str, tuple[str, str]]:
        """批量解析。返回 {原样传入的路径: (date, 'exif'|'mdls')},解不出的不出现。"""
        found = self._exiftool_batch(paths)
        rest = [p for p in paths if p not in found]
        if rest:
            found.update(self._mdls_batch(rest))
        return found

    def _exiftool_batch(self, paths: list[str]) -> dict[str, tuple[str, str]]:
        found: dict[str, tuple[str, str]] = {}
        exe = self.exiftool
        if exe is None:
            return found
        for start in range(0, len(paths), EXIFTOOL_CHUNK):
            chunk = paths[start:start + EXIFTOOL_CHUNK]
            records = self._run_exiftool(exe, chunk)
            if records is None:
                continue              # 整批失败 → 这些文件交给 mdls/mtime
            alias: dict[str, object] | None = None
            for path in chunk:
                if path in records:
                    raw = records[path]
                else:
                    # exiftool 对打不开的文件根本不输出条目(只往 stderr 报错),
                    # 路径也可能被规范化过 —— 用 realpath 键再兜一次
                    if alias is None:
                        alias = {_path_key(k): v for k, v in records.items()}
                    raw = alias.get(_path_key(path))
                    if raw is None:
                        continue      # 确实不在输出里 → 交给 mdls/mtime
                date_text = parse_exif_datetime(raw)
                if date_text:
                    found[path] = (date_text, "exif")
        return found

    def _run_exiftool(self, exe: str, chunk: list[str]) -> dict[str, object] | None:
        """跑一批。返回 {SourceFile: DateTimeOriginal 原值};整批失败返回 None。"""
        cmd = [exe, "-json", "-charset", "UTF8", "-DateTimeOriginal", *chunk]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=EXIFTOOL_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as e:
            _warn(f"exiftool 调用失败,这批退回下一档: {e}")
            return None
        # 只要有任意一个文件读不了,exiftool 就 exit=1;但 stdout 里的 JSON 对它
        # 读得到的那些文件依然完全有效(实测)—— 所以不能拿 returncode 判成败。
        text = (proc.stdout or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError as e:
            _warn(f"exiftool 输出不是合法 JSON,这批退回下一档: {e}")
            return None
        if not isinstance(data, list):
            return None
        out: dict[str, object] = {}
        for record in data:
            if not isinstance(record, dict):
                continue
            src = record.get("SourceFile")
            if isinstance(src, str):
                out[src] = record.get("DateTimeOriginal")
        return out

    def _mdls_batch(self, paths: list[str]) -> dict[str, tuple[str, str]]:
        """逐个问 Spotlight。

        这里刻意不批量:mdls 收多个文件时只按顺序打印值、不带文件名表头,而且实测
        遇到一个没被索引的文件就**中止整批**(给 f1 / 不存在的文件 / f3,只回了 f1
        一行就 exit=1)。按行号回填会把日期安到错误的文件上 —— 那是会搬错目录的
        静默错误,比慢严重得多。所以一次一个。

        代价:每文件一次进程,实测约 24ms/张。它只接手 exiftool 没搞定那部分、
        且仅 macOS;EXIF 缺失量大的库应该装 exiftool(见 SKILL.md 踩坑)。
        """
        exe = self.mdls
        found: dict[str, tuple[str, str]] = {}
        if exe is None:
            return found
        for path in paths:
            try:
                proc = subprocess.run(
                    [exe, "-name", "kMDItemContentCreationDate", path],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=MDLS_TIMEOUT,
                )
            except (OSError, subprocess.SubprocessError):
                continue              # 单个文件失败不影响其余
            date_text = parse_mdls_datetime(proc.stdout)
            if date_text:
                found[path] = (date_text, "mdls")
        return found


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
    def __init__(self, root: str, max_depth: int, sample: int,
                 use_exif: bool = False) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.sample = sample
        self.use_exif = use_exif
        # 不加 --exif 就是 None:整条 exiftool/mdls 代码路径都不会被碰到
        self.resolver = DateResolver() if use_exif else None
        # 只有强证据日期才进归档分布;mtime 是弱证据,不计(与既有行为一致)。
        # --exif 时 exif/mdls 也算强证据 —— mdls 给的是 Spotlight 内容创建时间,
        # 不是 EXIF 拍摄时间,比 mtime 稳,但报告里会单独标注、SKILL.md 要求抽查。
        self._strong_sources = {"filename", "exif", "mdls"} if use_exif else {"filename"}
        self._date_sources: dict[str, int] = {}
        # --exif 时,文件名提不出日期的媒体文件先攒这里,遍历完再批量问 exiftool
        self._pending: list[tuple] = []
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
        # 被 config.json 的 whitelist_dirs 整棵跳过的文件数。没有配置文件时恒为 0,
        # 且只有真加载了配置才会写进 stats(见 scan()),所以不影响逐字节一致性。
        self._wl_files = 0

    # -- 遍历 ----------------------------------------------------------

    def _walk(self, dir_path: Path | str, rel_parts: list[str],
              wl: bool = False) -> None:
        """遍历。wl=True 表示当前目录在 config.json 的白名单子树里。

        默认 wl=False,而 CONFIG_WHITELIST 在没有配置文件时是空列表(_cfg_match
        直接返回 False),所以 child_wl 恒为 False —— 下面两处 `if child_wl` /
        `if wl` 分支一行都不会走到,行为与引入覆盖层之前完全一致。
        """
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
                child_wl = wl or _cfg_match(child_rel, CONFIG_WHITELIST)
                depth = len(child_rel)
                parent_is_year = bool(rel_parts) and bool(YEAR_DIR_OK.match(rel_parts[-1]))
                # 白名单子树:目录名不判合规,里面的文件也不产 issue(整棵跳过)
                problems = ([] if child_wl
                            else dir_problems(name, depth, parent_is_year))
                if problems:
                    self._add(child_rel, name, True, problems)
                self._walk(entry.path, child_rel, child_wl)
            elif entry.is_file():
                if wl:
                    self._wl_files += 1
                    continue
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

        # 日期提取:文件名优先 —— 强证据,而且不用起任何子进程
        date, kind = extract_date(name)
        if date:
            self._report_media(rel_parts, name, size, date, "filename", kind)
            return

        if self.resolver is not None:
            # --exif:攒起来,遍历结束后一次性批量问 exiftool/mdls。
            # 记下此刻的 issues 长度,是为了事后能把结果插回原来的遍历顺序
            self._pending.append(
                (len(self.issues), entry.path, rel_parts, name, size, mtime, kind))
            return

        if mtime:
            date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            self._report_media(rel_parts, name, size, date, MTIME_SOURCE, kind)
        else:
            self._report_media(rel_parts, name, size, None, None, kind)

    def _report_media(self, rel_parts: list[str], name: str, size: int,
                      date: str | None, date_source: str | None,
                      kind: str) -> None:
        """照片/视频:日期入账 + 问题判定。

        --exif 模式下文件名提不出日期的文件会推迟到 _resolve_pending() 再走这里,
        默认模式在遍历中直接调。两条路径共用这一个方法 —— 人类可读报告和 JSON
        才不会各算一套而漂移。
        """
        stem = os.path.splitext(name)[0]
        if date_source:
            source_key = "mtime" if date_source == MTIME_SOURCE else date_source
            self._date_sources[source_key] = self._date_sources.get(source_key, 0) + 1
        self._account_date(date, date_source, kind, stem)

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

    def _account_date(self, date: str | None, date_source: str | None,
                      kind: str, stem: str) -> None:
        """把日期计入归档分布(by_month / date_min / date_max)。

        分桶逻辑与改动前完全一致,只是"哪些来源算强证据"多了一个开关:
        默认只有 filename,--exif 时再加上 exif / mdls。mtime 始终是弱证据、
        不入桶 —— 否则一次 SMB 拷贝刷新的时间戳就会伪造出月份分布。
        """
        if date is not None and date_source in self._strong_sources:
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

    def _resolve_pending(self) -> None:
        """--exif:遍历结束后批量解析攒下的文件,再补完它们的问题判定。

        为什么不在遍历里逐个解析:exiftool 一次能吃多个路径,批量调用比一张图一个
        进程快一个数量级;而攒着不立刻判定,是为了避免为拿 EXIF 在 SMB 上走第二遍
        目录树(那才是真的慢)。默认模式下 _pending 永远是空的。

        解析完把 issue 插回记录的位置,而不是直接 append:这样 --exif 与默认模式的
        issues 顺序完全一致,两次扫描的 JSON 可以直接对 diff,只有日期字段会变。
        """
        if self.resolver is None or not self._pending:
            return
        print(f"正在读取拍摄日期({len(self._pending)} 个文件,批量)…",
              file=sys.stderr)
        resolved = self.resolver.resolve([item[1] for item in self._pending])
        inserted = 0
        for pos, path, rel_parts, name, size, mtime, kind in self._pending:
            if path in resolved:
                date, date_source = resolved[path]
            elif mtime:
                date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                date_source = MTIME_SOURCE
            else:
                date, date_source = None, None
            tail = len(self.issues)
            self._report_media(rel_parts, name, size, date, date_source, kind)
            produced = self.issues[tail:]
            del self.issues[tail:]
            at = pos + inserted
            self.issues[at:at] = produced
            inserted += len(produced)
        self._pending = []

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
        if self.resolver is not None and not self.resolver.available:
            _warn("没找到 exiftool,也没有 mdls —— --exif 会全部退回 mtime"
                  "(装法见 SKILL.md)")
        self._walk(self.root, [])
        self._resolve_pending()
        self._flush_bursts()
        by_month = dict(sorted(self.stats.pop("by_month").items()))
        print(
            f'扫描完成: {self.stats["dirs"]} 目录, {self.stats["files"]} 文件\n',
            file=sys.stderr,
        )
        if self.use_exif:
            # 只在 --exif 时才多出这两个字段:不加参数时 JSON 与旧版逐字节一致
            self.stats["date_sources"] = {
                key: self._date_sources.get(key, 0)
                for key in ("filename", "exif", "mdls", "mtime")
            }
            self.stats["date_tools"] = (
                self.resolver.tools() if self.resolver is not None else {}
            )
        if CONFIG_INFO:
            # 同 --exif 的手法:只在真加载了 config.json 时才多这一个键,没有配置
            # 文件时 JSON 与引入覆盖层之前逐字节一致(smoke 里有负控制盯着)
            self.stats["config"] = {**CONFIG_INFO,
                                    "skipped_files": self._wl_files}
        return {
            "skill": "photo-organizer",
            "root": str(self.root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": {**self.stats, "by_month": by_month},
            "count": len(self.issues),
            "issues": self.issues,
        }


def _human_size(n: float) -> str:
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
    # date_sources 只在 --exif 时才存在;不加参数时下面这块一行都不打印
    sources = s.get("date_sources")
    if sources:
        shown = " | ".join(f"{k} {v}" for k, v in sources.items())
        print(f"日期来源(--exif): {shown}")
        tools = s.get("date_tools") or {}
        ready = [name for name, path in tools.items() if path]
        print(f"外部工具: {'、'.join(ready) if ready else '都没找到'}"
              f"(缺失时只能退回 mtime)")
        if sources.get("mdls"):
            print(f"ℹ️ {sources['mdls']} 个来自 mdls —— Spotlight 的内容创建时间,"
                  "不是 EXIF 拍摄时间;比 mtime 强,仍建议抽查")
        if sources.get("mtime"):
            print(f"⚠️ 有 {sources['mtime']} 个文件没读到 EXIF,退回了 mtime ——"
                  " 弱证据,归档前请让用户抽查")
        used = [k for k in ("filename", "exif", "mdls") if sources.get(k)]
        evidence = f"{'/'.join(used)} 可提取" if used else "无可提取日期"
    else:
        evidence = "文件名可提取"
    if s["date_min"]:
        print(f"日期范围({evidence}): {s['date_min']} ~ {s['date_max']}")
    if s["by_month"]:
        print(f"\n按日期归档分布({evidence}部分):")
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
                # 逐条标出日期是谁给的;默认模式不加,保持输出与旧版一致
                src = item.get("date_source") if sources else None
                if src:
                    extra += f" [{_SOURCE_TAG.get(src, src)}]"
                if item.get("suggested_dir"):
                    extra += f" (建议 {item['suggested_dir']}/)"
            print(f"  {tag} {item['path']}{extra}")
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
        description="photo-organizer: 照片/视频库只读合规扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="正向合规扫描(只读)")
    scan_p.add_argument("--root", required=True,
                        help="照片库根目录(本地挂载路径),如 /Volumes/nas/照片")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument(
        "--exif", action="store_true",
        help="用外部工具读拍摄日期:exiftool → mdls(仅 macOS)→ mtime 逐级回退。"
             "可选功能,两者都没装也只是退回 mtime;不加此参数行为与旧版一致")
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

    # 可选覆盖层:同目录 config.json。没有这个文件时下面这行什么都不做,
    # 内置的 PHOTO_EXTS / VIDEO_EXTS / SIDECAR_EXTS / JUNK_EXTS 一个都不变,
    # 输出与引入覆盖层之前逐字节一致。
    load_config()

    scanner = Scanner(args.root, args.max_depth, args.sample, args.exif)
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
