#!/bin/bash
# dedup-finder skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
DD_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["DD_SKILL_DIR"]
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
if name != "dedup-finder":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=dedup-finder")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/dedup_finder.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(hash/保留优先级) ==="
DD_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys
import tempfile

sys.path.insert(0, os.environ["DD_SKILL_DIR"])
import dedup_finder as dd

# hash 一致性 + 头部/全量
d = tempfile.mkdtemp()
a = os.path.join(d, "a.bin")
b = os.path.join(d, "b.bin")
payload = os.urandom(200_000)
open(a, "wb").write(payload)
open(b, "wb").write(payload)
assert dd.full_hash(a) == dd.full_hash(b)
assert dd.head_hash(a) == dd.head_hash(b)
# 尾部不同 → head 相同但 full 不同(三级指纹的价值)
c = os.path.join(d, "c.bin")
open(c, "wb").write(payload[:100_000] + os.urandom(100_000))
assert dd.head_hash(a) == dd.head_hash(c)      # 头部 64KB 一样
assert dd.full_hash(a) != dd.full_hash(c)      # 全量不一样
# 不存在的文件返回 None 而非崩
assert dd.full_hash(os.path.join(d, "nope")) is None

# 保留优先级:成品目录 > 浅路径 > 旧文件 > 短名
items = [
    {"path": "data/x/xxx 副本.mp4", "name": "xxx 副本.mp4", "mtime": 200},
    {"path": "成品/xxx.mp4", "name": "xxx.mp4", "mtime": 300},
    {"path": "data/xxx.mp4", "name": "xxx.mp4", "mtime": 100},
]
ranked = sorted(items, key=dd.keep_rank)
assert ranked[0]["path"] == "成品/xxx.mp4", ranked  # 成品优先
assert ranked[-1]["path"] == "data/x/xxx 副本.mp4", ranked  # 深+副本名 最后

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/dedup-finder-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/data"
mkdir -p "$ROOT/照片" "$ROOT/照片/子目录" "$ROOT/下载"
DD_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

root = os.environ["FIXTURE_ROOT"]
# 3 份完全相同(应判重复),1 份同 size 但内容不同(不应判重复)
payload = os.urandom(5000)
for p in ["照片/a.jpg", "照片/子目录/a 副本.jpg", "下载/a(1).jpg"]:
    open(os.path.join(root, p), "wb").write(payload)
open(os.path.join(root, "照片/unique.jpg"), "wb").write(os.urandom(5000))
# 小文件(< min-size 默认 1KB)应被跳过
open(os.path.join(root, "照片/tiny.jpg"), "wb").write(payload[:100])

skill = os.environ["DD_SKILL_DIR"]
out = os.path.join(tempfile.mkdtemp(), "dups.json")
r = subprocess.run(
    [sys.executable, f"{skill}/dedup_finder.py", "scan",
     "--root", root, "--output", out],
    capture_output=True, text=True,
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())

assert data["skill"] == "dedup-finder"
s = data["stats"]
assert s["files_skipped_small"] >= 1, s       # tiny.jpg 被跳过
assert s["duplicate_groups"] == 1, s          # 只有一组真重复
assert s["redundant_files"] == 2, s           # 3 份 → 2 个冗余
g = data["issues"][0]
assert g["count"] == 3, g
assert g["wasted_bytes"] == 5000 * 2, g
# keep 应选中「照片/a.jpg」(最浅+短名),drop 含副本和 (1)
assert g["keep"]["path"].endswith("a.jpg") and "副本" not in g["keep"]["path"], g
drop_paths = [d["path"] for d in g["drop"]]
assert any("副本" in p for p in drop_paths), g
assert any("(1)" in p for p in drop_paths), g
# unique.jpg 不在任何重复组里
assert "unique" not in json.dumps(data["issues"]), "内容不同的文件被误判重复!"
print("  ✓ fixture scan:精确去重、零误报、保留优先级正确")
PYEOF

echo "=== TEST 5: --help / 多 root ==="
"$PY" "$SKILL_DIR/dedup_finder.py" --help >/dev/null
"$PY" "$SKILL_DIR/dedup_finder.py" scan --help | grep -q -- "--root"
"$PY" "$SKILL_DIR/dedup_finder.py" scan --help | grep -q -- "--min-size"
echo "  ✓ CLI help 可用"

echo ""
echo "🎉 所有 smoke test 通过"
