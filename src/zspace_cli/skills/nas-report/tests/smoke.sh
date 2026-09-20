#!/bin/bash
# nas-report skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
NR_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["NR_SKILL_DIR"]
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
if name != "nas-report":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=nas-report")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/nas_report.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(分类/冷热分层) ==="
NR_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["NR_SKILL_DIR"])
import nas_report as nr

# 扩展名分类
assert nr.categorize("mkv") == "video"
assert nr.categorize("flac") == "audio"
assert nr.categorize("heic") == "photo"
assert nr.categorize("docx") == "doc"
assert nr.categorize("psd") == "design"
assert nr.categorize("dmg") == "installer"
assert nr.categorize("py") == "code"
assert nr.categorize("vhdx") == "backup"
assert nr.categorize("xyz") == "other"

# 冷热分层
assert nr.growth_bucket(10) == "hot_30d"
assert nr.growth_bucket(100) == "warm_1y"
assert nr.growth_bucket(500) == "cool_3y"
assert nr.growth_bucket(2000) == "cold_3y+"

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 report + 路由 ==="
FIXTURE="$(mktemp -d /tmp/nas-report-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/data"
mkdir -p "$ROOT/影视" "$ROOT/照片" "$ROOT/音乐" "$ROOT/下载" "$ROOT/备份" "$ROOT/空目录"
# 各类别文件(造点体积)
head -c 2000000 /dev/urandom > "$ROOT/影视/movie.mkv"
head -c 500000 /dev/urandom > "$ROOT/影视/show.mp4"
for i in $(seq 1 10); do head -c 10000 /dev/urandom > "$ROOT/照片/img$i.jpg"; done
for i in $(seq 1 5); do head -c 20000 /dev/urandom > "$ROOT/音乐/song$i.mp3"; done
head -c 100000 /dev/urandom > "$ROOT/下载/app.dmg"
touch "$ROOT/下载/x.torrent" "$ROOT/.DS_Store"
head -c 50000 /dev/urandom > "$ROOT/备份/db_2024-01-01.tar.gz"
# 冷数据(3 年前)
head -c 300000 /dev/urandom > "$ROOT/影视/old_movie.mkv"
touch -t 202201010000 "$ROOT/影视/old_movie.mkv"

NR_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["NR_SKILL_DIR"]
root = os.environ["FIXTURE_ROOT"]
out = os.path.join(tempfile.mkdtemp(), "report.json")
r = subprocess.run(
    [sys.executable, f"{skill}/nas_report.py", "report",
     "--root", root, "--output", out],
    capture_output=True, text=True,
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())
s = data["stats"]

assert data["skill"] == "nas-report"
assert s["total_files"] >= 19, s
assert s["total_size_bytes"] > 2_000_000, s
assert s["by_category"]["video"]["count"] == 3, s        # mkv + mp4 + old mkv
assert s["by_category"]["photo"]["count"] == 10, s
assert s["by_category"]["audio"]["count"] == 5, s
assert s["junk_files"] >= 2, s                            # torrent + .DS_Store
assert s["empty_dirs"] >= 1, s                            # 空目录
assert s["largest_files"] and s["largest_files"][0]["size"] >= 2_000_000
assert s["largest_dirs"], "大目录榜为空"
assert "影视" in s["toplevel"] and "照片" in s["toplevel"]
# 冷热分层:old_movie.mkv 是 2022 → cold_3y+
assert "cold_3y+" in s["by_growth"], s["by_growth"]
# 路由建议:影视占比高→media-naming;照片 10<500 不触发;垃圾少;文件少
rec_skills = [x["skill"] for x in data["recommendations"]]
assert any("media-naming" in x or "media-manager" in x for x in rec_skills), rec_skills
assert any("backup-auditor" in x for x in rec_skills), rec_skills  # 发现 备份/ 目录
assert all("why" in x for x in data["recommendations"])
print("  ✓ report 画像 + 路由建议正确")
PYEOF

echo "=== TEST 5: --help ==="
"$PY" "$SKILL_DIR/nas_report.py" --help >/dev/null
"$PY" "$SKILL_DIR/nas_report.py" report --help | grep -q -- "--max-files"
echo "  ✓ CLI help 可用"

echo ""
echo "🎉 所有 smoke test 通过"
