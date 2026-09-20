#!/bin/bash
# backup-auditor skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
BA_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["BA_SKILL_DIR"]
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
if name != "backup-auditor":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=backup-auditor")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/backup_auditor.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(备份名解析) ==="
BA_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["BA_SKILL_DIR"])
import backup_auditor as ba

# 归档扩展名剥离(含双扩展名)
assert ba.strip_archive_ext("照片备份.tar.gz") == "照片备份"
assert ba.strip_archive_ext("db.zip") == "db"
assert ba.strip_archive_ext("vol.sparsebundle") == "vol"
assert ba.strip_archive_ext("目录名") == "目录名"

# 日期提取
assert ba.parse_backup_name("照片备份_2024-01-01.tar.gz") == ("照片备份", "2024-01-01", None)
assert ba.parse_backup_name("照片备份_20240101") [0] == "照片备份"
assert ba.parse_backup_name("照片备份_20240101")[1] == "2024-01-01"
assert ba.parse_backup_name("docs 2024年03月05日")[1] == "2024-03-05"

# 版本号提取
assert ba.parse_backup_name("db_v2.tar.gz") == ("db", None, 2)
assert ba.parse_backup_name("网站备份3")[2] == 3

# 同一目标多版本聚到同一 base_key
k1 = ba.parse_backup_name("照片备份_2024-01-01.tar.gz")[0]
k2 = ba.parse_backup_name("照片备份_2024-02-01.tar.gz")[0]
assert k1 == k2 == "照片备份", (k1, k2)

# 无日期无版本
assert ba.parse_backup_name("misc") == ("misc", None, None)

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan + coverage ==="
FIXTURE="$(mktemp -d /tmp/backup-auditor-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

BK="$FIXTURE/备份"
SRC="$FIXTURE/data"
mkdir -p "$BK" "$SRC/照片" "$SRC/文档" "$SRC/项目X"

# 备份集:照片备份 4 个版本(超过 keep=3,最旧 1 个可轮转)
for d in 2024-01-01 2024-02-01 2024-03-01 2024-04-01; do
  mkdir -p "$BK/照片备份_$d"
  echo "photo data $d" > "$BK/照片备份_$d/img.jpg"
done
# 单版本 + 陈旧备份(mtime 设为 2 年前)
mkdir -p "$BK/文档备份_20230101"
echo "old doc" > "$BK/文档备份_20230101/a.txt"
touch -t 202301010000 "$BK/文档备份_20230101" "$BK/文档备份_20230101/a.txt"
# 空备份项
mkdir -p "$BK/空备份_2024-05-01"
# 孤儿备份(源里没有 老项目)
mkdir -p "$BK/老项目备份_2024-01-01"
echo "x" > "$BK/老项目备份_2024-01-01/f.bin"
# 源里 项目X 无对应备份(缺失)

BA_SKILL_DIR="$SKILL_DIR" BK="$BK" SRC="$SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["BA_SKILL_DIR"]
bk, src = os.environ["BK"], os.environ["SRC"]
tmpd = tempfile.mkdtemp()

# --- scan ---
out = os.path.join(tmpd, "scan.json")
r = subprocess.run(
    [sys.executable, f"{skill}/backup_auditor.py", "scan",
     "--root", bk, "--stale-days", "35", "--keep", "3", "--output", out],
    capture_output=True, text=True,
)
assert r.returncode == 0, r.stderr
scan = json.loads(open(out, encoding="utf-8").read())
sb = json.dumps(scan, ensure_ascii=False)
s = scan["stats"]
assert scan["skill"] == "backup-auditor" and scan["mode"] == "scan"
assert s["backup_sets"] >= 4, s                 # 照片备份/文档备份/空备份/老项目备份
assert s["rotatable_versions"] >= 1, s          # 照片备份 4 版 > keep 3
assert s["single_version_sets"] >= 2, s         # 文档/空/老项目 都是单版本
assert s["stale_sets"] >= 1, s                  # 文档备份 2023 陈旧
assert s["empty_items"] >= 1, s                 # 空备份
assert "可轮转旧版本" in sb
assert "备份陈旧" in sb
assert "空备份项" in sb
assert "单版本备份" in sb
actions = {i["action"] for i in scan["issues"]}
assert {"rotate-out", "review-set", "investigate"} <= actions, actions

# --- coverage ---
out2 = os.path.join(tmpd, "cov.json")
r2 = subprocess.run(
    [sys.executable, f"{skill}/backup_auditor.py", "coverage",
     "--source", src, "--backup", bk, "--stale-days", "35", "--output", out2],
    capture_output=True, text=True,
)
assert r2.returncode == 0, r2.stderr
cov = json.loads(open(out2, encoding="utf-8").read())
cb = json.dumps(cov, ensure_ascii=False)
cs = cov["stats"]
assert cov["mode"] == "coverage"
assert cs["source_dirs"] == 3, cs               # 照片/文档/项目X
assert cs["missing"] >= 1, cs                   # 项目X 无备份
assert cs["orphan"] >= 1, cs                    # 老项目备份 是孤儿
assert "关键目录无对应备份" in cb
assert "孤儿备份" in cb
cactions = {i["action"] for i in cov["issues"]}
assert "add-backup" in cactions and "review-orphan" in cactions, cactions

print("  ✓ scan + coverage 检出全部预期项")
PYEOF

echo "=== TEST 5: --help(scan + coverage) ==="
"$PY" "$SKILL_DIR/backup_auditor.py" --help >/dev/null
"$PY" "$SKILL_DIR/backup_auditor.py" scan --help | grep -q -- "--keep"
"$PY" "$SKILL_DIR/backup_auditor.py" coverage --help | grep -q -- "--source"
echo "  ✓ CLI help 可用"

echo ""
echo "🎉 所有 smoke test 通过"
