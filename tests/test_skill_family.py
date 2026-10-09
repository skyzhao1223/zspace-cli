"""家族一致性守卫:9 个 scanner 的 SKIP_DIRS 常量。

skills/ 与 src/zspace_cli/skills/ 是逐字节镜像,两棵树都查。常量用 ast 静态
解析,不 import 脚本 —— import 会执行模块级代码(config 加载、stdout 重配),
不适合在 pytest 里对 9 个 skill 各来一遍。

背景(跨 scanner 一致性审计 F4):@Recycle 是极空间(ZSpace)的回收站目录,
此前只有 photo-organizer 跳过它,其余 8 家把回收站内容当活数据 ——
music-organizer 把 @Recycle 下的目录注册成「歌手」、把已删除的曲目当库内
缺封面的歌;file-sorter 会计划把回收站里的文件搬走。其他品牌的回收站名
(#recycle / #@__recycle_bin / $RECYCLE.BIN / .Trashes / .zspace_trash …)
早已是 9 家统一跳过,唯独极空间这个漏了 8 家。这组测试把「回收站家族」
钉死成全家统一约定:以后新增 scanner 或者谁手滑删掉一个名字,这里先红。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TREES = [REPO / "skills", REPO / "src" / "zspace_cli" / "skills"]
SCANNERS = [
    "backup-auditor", "dedup-finder", "download-cleaner", "file-sorter",
    "music-organizer", "nas-report", "photo-organizer",
    "portfolio-organizer", "work-organizer",
]

# 各品牌 NAS / 各操作系统的回收站与系统元数据目录 —— 9 家必须全跳。
TRASH_DIRS = {
    "@eaDir",            # 群晖缩略图元数据
    "#recycle",          # 群晖回收站
    "#@__recycle_bin",   # 威联通回收站
    "@Recycle",          # 极空间(ZSpace)回收站 —— F4 修复补上的那个
    ".Trashes",          # macOS
    ".Spotlight-V100",   # macOS Spotlight
    ".fseventsd",        # macOS FSEvents
    ".TemporaryItems",   # macOS
    "System Volume Information",  # Windows
    "$RECYCLE.BIN",      # Windows 回收站
    ".snapshots",        # NFS/快照
    ".zspace_trash",     # 极空间回收站(点前缀变体)
    ".trash",            # 通用
    "lost+found",        # ext 文件系统
}


def _skip_dirs(tree: Path, scanner: str) -> set:
    py = tree / scanner / f"{scanner.replace('-', '_')}.py"
    assert py.is_file(), f"scanner script missing: {py}"
    mod = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(mod):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "SKIP_DIRS" for t in node.targets):
            value = ast.literal_eval(node.value)
            assert isinstance(value, set), f"{py}: SKIP_DIRS is not a set literal"
            return value
    raise AssertionError(f"{py}: SKIP_DIRS assignment not found")


@pytest.mark.parametrize("tree", TREES, ids=["skills", "src-mirror"])
@pytest.mark.parametrize("scanner", SCANNERS)
def test_skip_dirs_cover_trash_family(tree: Path, scanner: str) -> None:
    missing = TRASH_DIRS - _skip_dirs(tree, scanner)
    assert not missing, (
        f"{scanner}: SKIP_DIRS 缺回收站/系统目录 {sorted(missing)} —— "
        f"回收站内容会被当成活数据(见模块 docstring 的 F4 背景)")
