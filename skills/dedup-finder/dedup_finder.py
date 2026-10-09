#!/usr/bin/env python3
"""dedup-finder skill 命令行入口

设计原则(沿袭 media-naming / photo-organizer 模式):
- 只读扫描走脚本(三级指纹精确去重:size → 头部 64KB → 全量)
- 写操作(rm / mv)不在脚本里 — LLM 生成删除计划(每组保留哪个),
  用户确认后由 Agent 执行(挂载盘 shell,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用

三级指纹的意义:
  1. size 分组 —— 免费,先砍掉绝大多数不可能重复的文件
  2. 头部 64KB hash —— 廉价,只读每文件开头,砍掉同 size 但内容不同的
  3. 全量 hash —— 精确,只对头哈希也相同的极少数文件做,零误报

用法:
  python dedup_finder.py scan --root /Volumes/nas/data
  python dedup_finder.py scan --root ... --json --output /tmp/dups.json
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn

MAX_DEPTH = 12
HEAD_SIZE = 64 * 1024        # 头部指纹读 64KB
CHUNK = 1024 * 1024          # 全量 hash 分块 1MB
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", ".AppleDB", ".AppleDesktop", ".apdisk",
    "System Volume Information", "$RECYCLE.BIN", ".snapshots", ".zspace_trash", "@Recycle",
    ".trash", ".cache", "node_modules", ".git", ".svn", ".idea", ".vscode",
    "__pycache__", ".venv", "venv", "lost+found",
}
# 这些目录里的副本通常是"正主",重复时优先保留(降权删除优先级)。
# 用 list 而不是 tuple:config.json 的 prefer_keep_hints 要**追加**到这里
# (与 #47 对内置 *_EXTS 的处置一致 —— 内置条目一条都不删),原地 extend
# 比重新绑定全局名少一处 global 声明,keep_rank() 一行都不用改。
PREFER_KEEP_HINTS = ["成品", "源文件", "原始", "master", "original", "import", "相册"]

# ── 可选覆盖层:config.json(与本脚本同目录)────────────────────────────
# 「装 skill」是逐目录 copytree(cli.py 的 zs skill,加 --only 也一样),共享模块
# 装不进用户目录,所以这一段在每个 scanner 里逐字重复 —— 要改就全局搜索替换,
# 别只改一处。per-skill 的只有紧跟其后的 CONFIG_KEYS 和它点到的那张表。
CONFIG_NAME = "config.json"
CONFIG_KEYS = ("skip_dirs", "prefer_keep_hints")
CONFIG_SKIP: list[str] = []           # skip_dirs 追加到这里(扩展内置 SKIP_DIRS)
CONFIG_INFO: dict[str, object] = {}   # 非空 = 真加载了配置,回写进 stats 供核对


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
    hints = raw.get("prefer_keep_hints", [])
    if not isinstance(hints, list):
        _cfg_die(path, f"prefer_keep_hints 必须是字符串数组,实际是 "
                 f"{type(hints).__name__}(只有一条也要写成 [\"终稿\"])")
    for i, item in enumerate(hints):
        if not isinstance(item, str):
            _cfg_die(path, f"prefer_keep_hints[{i}] 必须是字符串,实际是 "
                     f"{type(item).__name__}")
        if not item.strip():
            _cfg_die(path, f"prefer_keep_hints[{i}] 是空字符串")
    # 校验全过才动手。prefer_keep_hints 是**追加**:内置那 7 条一条都不删,与
    # #47 对内置 *_EXTS 的处置一致。归一化成小写是因为 keep_rank 比的就是
    # item["path"].lower() —— 内置提示词本来就全是小写,所以这不改变内置语义。
    # 仍然按内置的做法做**子串**匹配,没有偷偷升级成 glob(那会连内置提示词的
    # 语义一起改掉);去重只是让汇报里不出现重复条目。
    CONFIG_SKIP.extend(item.strip() for item in skip)
    added = [h.strip().lower() for h in hints]
    PREFER_KEEP_HINTS.extend(h for h in added if h not in PREFER_KEEP_HINTS)
    CONFIG_INFO.update({
        "path": str(path),
        "skip_dirs": list(CONFIG_SKIP),
        "prefer_keep_hints": added,
        "prefer_keep_hints_effective": list(PREFER_KEEP_HINTS),
    })
    print(f"ℹ️ 已加载覆盖配置 {path}(skip_dirs {len(skip)} 条,"
          f"prefer_keep_hints {len(hints)} 条)", file=sys.stderr)


def head_hash(path: str) -> str | None:
    """读文件头部 64KB 算 sha1;失败返回 None。"""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            h.update(f.read(HEAD_SIZE))
    except OSError:
        return None
    return h.hexdigest()


def full_hash(path: str) -> str | None:
    """全量 sha1(分块读,内存恒定);失败返回 None。"""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while True:
                blk = f.read(CHUNK)
                if not blk:
                    break
                h.update(blk)
    except OSError:
        return None
    return h.hexdigest()


def keep_rank(item: dict) -> tuple:
    """重复组内「保留优先级」排序键 — 越小越该保留。

    策略(纯启发式,最终由用户裁决):
    1. 路径含 成品/源文件/原始/master… 提示词的优先保留
    2. 路径更浅(层级少)的优先保留 — 通常是"正主",深层是散落副本
    3. mtime 更旧的优先保留 — 原始文件通常更早
    4. 名字更短的优先保留 — 避免 "xxx 副本"/"xxx (1)" 这类派生名
    """
    path = item["path"].lower()
    prefer = 0 if any(h in path for h in PREFER_KEEP_HINTS) else 1
    depth = item["path"].count("/")
    return (prefer, depth, item.get("mtime") or 0, len(item["name"]))


class Scanner:
    def __init__(self, roots: list[str], max_depth: int, min_size: int,
                 max_files: int) -> None:
        self.roots = [Path(r).resolve() for r in roots]
        self.max_depth = max_depth
        self.min_size = min_size
        self.max_files = max_files
        self.errors: list[str] = []
        self.stats: dict = {
            "files_scanned": 0, "files_skipped_small": 0, "dirs_visited": 0,
            "hashed_head": 0, "hashed_full": 0, "bytes_read": 0,
            "duplicate_groups": 0, "redundant_files": 0,
            "wasted_bytes": 0, "elapsed_sec": 0.0, "truncated": False,
        }
        self._size_map: dict[int, list[dict]] = {}
        self._cfg_skipped_dirs = 0   # 因 config skip_dirs 未纳入的目录数

    # -- 遍历:按 size 分组 -------------------------------------------

    def _walk(self, dir_path: Path | str, rel_root: Path, rel_parts: list[str]) -> None:
        if len(rel_parts) > self.max_depth:
            return
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as e:
            self.errors.append(f"无法读取 {dir_path}: {e}")
            return
        self.stats["dirs_visited"] += 1
        for entry in entries:
            if self.max_files and self.stats["files_scanned"] >= self.max_files:
                self.stats["truncated"] = True
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
                self._walk(entry.path, rel_root, child_rel)
            elif entry.is_file():
                if name.startswith("._") or name == ".DS_Store":
                    continue
                try:
                    st = entry.stat()
                except OSError as e:
                    self.errors.append(f"stat 失败 {entry.path}: {e}")
                    continue
                if st.st_size < self.min_size:
                    self.stats["files_skipped_small"] += 1
                    continue
                self.stats["files_scanned"] += 1
                try:
                    # 统一成正斜杠:其余 8 个 scanner 都用 "/".join(rel_parts),只有这里
                    # 是字符串切片,于是 Windows 上会输出 OLD\G.bin。后果不止是汇报里
                    # 混着两种分隔符 —— keep_rank() 的深度这一级是
                    # item["path"].count("/"),在反斜杠路径上恒为 0,「浅路径优先」
                    # 在整个 Windows 平台上都是失效的。POSIX 上 os.sep 本就是 "/",
                    # 这个 replace 是空操作,输出逐字节不变。
                    rel = entry.path[len(str(rel_root)) + 1:].replace(os.sep, "/")
                except Exception:
                    rel = entry.path.replace(os.sep, "/")
                self._size_map.setdefault(st.st_size, []).append({
                    "path": rel,
                    "abs": entry.path,
                    "name": name,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })

    # -- 三级指纹去重 --------------------------------------------------

    def _dedup(self) -> list[dict]:
        groups: list[dict] = []
        # 阶段 1:只有 size 相同的才可能重复
        candidates = [lst for lst in self._size_map.values() if len(lst) >= 2]

        # 阶段 2:头部 hash
        head_groups: list[list[dict]] = []
        for lst in candidates:
            by_head: dict[str, list[dict]] = {}
            for item in lst:
                hh = head_hash(item["abs"])
                if hh is None:
                    self.errors.append(f"读失败 {item['path']}")
                    continue
                self.stats["hashed_head"] += 1
                self.stats["bytes_read"] += min(item["size"], HEAD_SIZE)
                by_head.setdefault(hh, []).append(item)
            head_groups.extend(v for v in by_head.values() if len(v) >= 2)

        # 阶段 3:全量 hash(只对头哈希也相同的做)
        for lst in head_groups:
            by_full: dict[str, list[dict]] = {}
            for item in lst:
                fh = full_hash(item["abs"])
                if fh is None:
                    self.errors.append(f"读失败 {item['path']}")
                    continue
                self.stats["hashed_full"] += 1
                self.stats["bytes_read"] += item["size"]
                by_full.setdefault(fh, []).append(item)
            for fh, members in by_full.items():
                if len(members) >= 2:
                    groups.append(self._make_group(fh, members))
        return groups

    def _make_group(self, fingerprint: str, members: list[dict]) -> dict:
        members = sorted(members, key=keep_rank)
        size = members[0]["size"]
        wasted = size * (len(members) - 1)
        self.stats["duplicate_groups"] += 1
        self.stats["redundant_files"] += len(members) - 1
        self.stats["wasted_bytes"] += wasted
        keep = members[0]
        drop = members[1:]
        return {
            "fingerprint": fingerprint,
            "size": size,
            "count": len(members),
            "wasted_bytes": wasted,
            "keep": {"path": keep["path"], "mtime": keep["mtime"],
                     "reason": "启发式:成品/源目录优先→浅路径→旧文件→短名"},
            "drop": [{"path": d["path"], "mtime": d["mtime"]} for d in drop],
            "is_dir": False,
            "problems": [f"重复文件组({len(members)} 份,浪费 {_human(size * (len(members) - 1))})"],
        }

    # -- 入口 ----------------------------------------------------------

    def scan(self) -> dict:
        t0 = time.time()
        for root in self.roots:
            if not root.is_dir():
                raise SystemExit(f"❌ --root 不是有效目录: {root}")
            print(f"正在扫描 {root} ...\n", file=sys.stderr)
            self._walk(root, root, [])
        groups = self._dedup()
        groups.sort(key=lambda g: -g["wasted_bytes"])
        self.stats["elapsed_sec"] = round(time.time() - t0, 2)
        if CONFIG_INFO:
            # 只在真加载了 config.json 时才多这一个键(与 photo-organizer 的
            # --exif 同一个手法),没有配置文件时 JSON 与引入覆盖层之前逐字节一致
            self.stats["config"] = {**CONFIG_INFO,
                                    "skipped_dirs": self._cfg_skipped_dirs}
        print(
            f'扫描完成: {self.stats["files_scanned"]} 文件参与比对, '
            f'{self.stats["duplicate_groups"]} 组重复\n', file=sys.stderr,
        )
        return {
            "skill": "dedup-finder",
            "roots": [str(r) for r in self.roots],
            "strategy": "size → head64KB → full sha1(三级指纹,精确零误报)",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": self.stats,
            "count": len(groups),
            "issues": groups,
            "errors": self.errors[:50],
        }


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


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
    print("dedup-finder — 精确去重报告(内容级,零误报)")
    print("=" * 70)
    print(f"根目录: {', '.join(result['roots'])}")
    print(f"指纹策略: {result['strategy']}")
    print(f"参与比对 {s['files_scanned']} 文件 | 头哈希 {s['hashed_head']} "
          f"| 全量哈希 {s['hashed_full']} | 读取 {_human(s['bytes_read'])}")
    print(f"重复组 {s['duplicate_groups']} | 冗余文件 {s['redundant_files']} "
          f"| 可回收 {_human(s['wasted_bytes'])} | 耗时 {s['elapsed_sec']}s")
    if s.get("truncated"):
        print("(已达 --max-files 上限,结果不完整)")
    _print_config(s)

    issues = result["issues"]
    if not issues:
        print("\n✅ 没有发现重复文件!")
        return

    print(f"\n⚠ 发现 {len(issues)} 组重复(按浪费空间降序,前 {top} 组):\n")
    for i, g in enumerate(issues[:top], 1):
        print(f"[{i}] {_human(g['size'])} × {g['count']} 份 → "
              f"浪费 {_human(g['wasted_bytes'])}")
        print(f"    ✓ 保留: {g['keep']['path']}")
        for d in g["drop"]:
            print(f"    ✗ 删除: {d['path']}")
    if len(issues) > top:
        print(f"\n... 还有 {len(issues) - top} 组(见 JSON)")
    if result.get("errors"):
        print(f"\n(有 {len(result['errors'])} 条读取错误,见 JSON errors)")


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
        description="dedup-finder: 内容级精确去重只读扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="三级指纹去重扫描(只读)")
    scan_p.add_argument("--root", required=True, action="append",
                        help="扫描根目录(可重复传多个,跨目录找重复)")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                        help=f"最大递归深度(默认 {MAX_DEPTH})")
    scan_p.add_argument("--min-size", type=int, default=1,
                        help="忽略小于 N KB 的文件(默认 1KB)")
    scan_p.add_argument("--max-files", type=int, default=0,
                        help="最多比对文件数(0=不限)")
    scan_p.add_argument("--top", type=int, default=20,
                        help="人类可读输出显示前 N 组(默认 20)")

    args = parser.parse_args()
    if args.cmd != "scan":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    # 可选覆盖层:同目录 config.json。没有这个文件时下面这行什么都不做,
    # 内置的 SKIP_DIRS 与 PREFER_KEEP_HINTS 一个都不变,输出与引入覆盖层之前逐字节一致。
    load_config()

    scanner = Scanner(args.root, args.max_depth, args.min_size * 1024, args.max_files)
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
