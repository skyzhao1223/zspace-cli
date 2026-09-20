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
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 12
HEAD_SIZE = 64 * 1024        # 头部指纹读 64KB
CHUNK = 1024 * 1024          # 全量 hash 分块 1MB
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", ".AppleDB", ".AppleDesktop", ".apdisk",
    "System Volume Information", "$RECYCLE.BIN", ".snapshots", ".zspace_trash",
    ".trash", ".cache", "node_modules", ".git", ".svn", ".idea", ".vscode",
    "__pycache__", ".venv", "venv", "lost+found",
}
# 这些目录里的副本通常是"正主",重复时优先保留(降权删除优先级)
PREFER_KEEP_HINTS = ("成品", "源文件", "原始", "master", "original", "import", "相册")


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
                self._walk(entry.path, rel_root, rel_parts + [name])
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
                    rel = entry.path[len(str(rel_root)) + 1:]
                except Exception:
                    rel = entry.path
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


def main() -> None:
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
