#!/bin/bash
# download-cleaner skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
DL_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["DL_SKILL_DIR"]
content = open(f"{skill_dir}/SKILL.md", encoding="utf-8").read()
m = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
if not m:
    print("❌ frontmatter 缺失")
    sys.exit(1)
block = m.group(1)
name = None
for line in block.splitlines():
    if line.startswith("name:"):
        name = line.split(":", 1)[1].strip()
        break
if name != "download-cleaner":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=download-cleaner")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/download_cleaner.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(分类/建议) ==="
DL_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["DL_SKILL_DIR"])
import download_cleaner as dc

# 分类
assert dc.categorize("x.part", "part") == "partial"
assert dc.categorize("x.bt.td", "td") == "partial"
assert dc.categorize("a.torrent", "torrent") == "torrent"
assert dc.categorize("app.dmg", "dmg") == "installer"
assert dc.categorize("arc.zip", "zip") == "archive"
assert dc.categorize("movie.mkv", "mkv") == "video"
assert dc.categorize("song.flac", "flac") == "audio"
assert dc.categorize("pic.jpg", "jpg") == "photo"
assert dc.categorize("report.pdf", "pdf") == "doc"
assert dc.categorize(".DS_Store", "") == "junk"
assert dc.categorize("whatever.xyz", "xyz") == "other"

# 建议动作
p, a = dc.advise("junk", 0, False, 365)
assert a == "delete"
p, a = dc.advise("partial", 10, False, 365)
assert a == "delete-confirm"
p, a = dc.advise("archive", 10, True, 365)     # 已解压
assert a == "delete-confirm" and any("已解压" in x for x in p)
p, a = dc.advise("archive", 10, False, 365)    # 未解压
assert a == "extract-or-review"
p, a = dc.advise("installer", 10, False, 365)  # 新安装包
assert a == "review"
p, a = dc.advise("installer", 500, False, 365) # 老旧安装包
assert a == "delete-confirm" and any("老旧" in x for x in p)
p, a = dc.advise("video", 10, False, 365)
assert a == "move-to-library" and any("影视库" in x for x in p)
p, a = dc.advise("other", 10, False, 365)      # 新杂项 → keep
assert a == "keep" and p == []

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/download-cleaner-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/下载"
mkdir -p "$ROOT/已解压包"
# 各类别文件
touch "$ROOT/movie.mkv" "$ROOT/song.mp3" "$ROOT/report.pdf"      # 待归档
touch "$ROOT/x.torrent" "$ROOT/y.part" "$ROOT/app.dmg"            # 种子/未完成/安装包
echo data > "$ROOT/已解压包.zip"                                   # 已解压(同名目录在)
touch "$ROOT/未解压.rar"                                           # 未解压
touch "$ROOT/movie (1).mkv"                                       # 重复下载
touch "$ROOT/.DS_Store"                                          # 垃圾
# 老旧文件(mtime 设为 2 年前)
touch -t 202301010000 "$ROOT/old-installer.dmg" "$ROOT/ancient.pdf"

DL_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["DL_SKILL_DIR"]
root = os.environ["FIXTURE_ROOT"]
out = os.path.join(tempfile.mkdtemp(), "dl.json")
r = subprocess.run(
    [sys.executable, f"{skill}/download_cleaner.py", "scan",
     "--root", root, "--stale-days", "365", "--output", out],
    capture_output=True, text=True,
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())
blob = json.dumps(data, ensure_ascii=False)
s = data["stats"]

assert data["skill"] == "download-cleaner"
assert s["by_category"]["torrent"]["count"] == 1, s
assert s["by_category"]["partial"]["count"] == 1, s
assert s["by_category"]["video"]["count"] == 2, s   # movie.mkv + movie (1).mkv
assert s["duplicate_downloads"] == 1, s
assert s["reclaimable_bytes"] > 0, s
assert s["stale_files"] >= 2, s                      # old-installer + ancient
assert "种子文件" in blob
assert "未完成下载" in blob
assert "已解压" in blob                              # 已解压包.zip
assert "未解压" in blob                              # 未解压.rar
assert "老旧安装包" in blob                          # old-installer.dmg
assert "move-to-library" in blob or "建议归档" in blob
assert "重复下载" in blob
# action 字段存在
actions = {i["action"] for i in data["issues"]}
assert "delete-confirm" in actions, actions
assert "move-to-library" in actions, actions
print("  ✓ fixture scan 检出全部预期类别与动作")
PYEOF

echo "=== TEST 5: --help ==="
"$PY" "$SKILL_DIR/download_cleaner.py" --help >/dev/null
"$PY" "$SKILL_DIR/download_cleaner.py" scan --help | grep -q -- "--stale-days"
echo "  ✓ CLI help 可用"

echo ""
echo "🎉 所有 smoke test 通过"
