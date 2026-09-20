#!/bin/bash
# work-organizer skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
WORK_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["WORK_SKILL_DIR"]
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
if name != "work-organizer":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=work-organizer")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/work_organizer.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(目录校验/分组键) ==="
WORK_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["WORK_SKILL_DIR"])
import work_organizer as wo

# 目录校验
assert wo.dir_problems("2024", 1) == []
assert wo.dir_problems("合同", 1) == []
assert wo.dir_problems("官网改版", 1) == []
assert any("临时" in p for p in wo.dir_problems("新建文件夹", 1))
assert any("临时" in p for p in wo.dir_problems("test", 1))
assert any("副本" in p for p in wo.dir_problems("项目 副本", 1))

# 分组键:各种脏版本名归一到同一 base
assert wo.base_stem("20240501_需求文档_v2") == wo.base_stem("需求文档 最终版")
assert wo.base_stem("需求文档(1)") == wo.base_stem("需求文档")
assert wo.base_stem("需求文档 副本") == wo.base_stem("需求文档")
assert wo.base_stem("需求文档_final_v3") == wo.base_stem("需求文档")

# 版本标记混乱
assert wo.VERSION_CHAOS_RE.search("方案-最终版")
assert wo.VERSION_CHAOS_RE.search("report_final")
assert not wo.VERSION_CHAOS_RE.search("方案_v2")

# 类型分类
assert wo.classify_ext("docx") == "doc"
assert wo.classify_ext("xlsx") == "sheet"
assert wo.classify_ext("dmg") == "installer"
assert wo.classify_ext("psd") == "design"
assert wo.classify_ext("xyz") == "other"

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/work-organizer-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/工作"
mkdir -p "$ROOT/2024/官网改版" "$ROOT/新建文件夹" "$ROOT/node_modules/pkg"
# 合规文件
touch "$ROOT/2024/官网改版/20240501_官网改版_需求文档_v2.docx"
# 根目录散文件 + 版本混乱 + 副本
touch "$ROOT/需求文档 最终版.docx" "$ROOT/方案(1).pptx" "$ROOT/预算表 副本.xlsx"
# 同名多版本组(≥3)
touch "$ROOT/2024/官网改版/会议纪要_v1.docx" \
      "$ROOT/2024/官网改版/会议纪要_v2.docx" \
      "$ROOT/2024/官网改版/会议纪要 最终版.docx"
# 垃圾/锁定
touch "$ROOT/~\$需求文档.docx" "$ROOT/x.bak" "$ROOT/._y.docx"
# 安装包
touch "$ROOT/2024/官网改版/installer.dmg"
# node_modules 应被静默跳过
touch "$ROOT/node_modules/pkg/index.js"

WORK_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["WORK_SKILL_DIR"]
root = os.environ["FIXTURE_ROOT"]
out = os.path.join(tempfile.mkdtemp(), "issues.json")
r = subprocess.run(
    [sys.executable, f"{skill}/work_organizer.py", "scan",
     "--root", root, "--output", out],
    capture_output=True, text=True,
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())
blob = json.dumps(data, ensure_ascii=False)

assert data["skill"] == "work-organizer"
s = data["stats"]
assert s["loose_root"] == 3, s
assert s["junk"] == 3, s
assert s["version_chaos"] >= 2, s          # 最终版.docx + 会议纪要 最终版
assert s["copy_files"] >= 2, s             # 方案(1) + 预算表 副本
assert s["installers"] == 1, s
assert "node_modules" not in blob          # 静默跳过
assert "根目录散文件" in blob
assert "版本标记混乱" in blob
assert "副本文件" in blob
assert "临时/锁定/垃圾文件" in blob
assert "安装包/镜像" in blob
assert "同名文档多版本共存" in blob        # 会议纪要 x3
assert "临时/无语义目录名" in blob         # 新建文件夹
# 散文件带 suggested_dir(mtime 年份)
loose = [i for i in data["issues"] if any("散文件" in p for p in i["problems"])]
assert all(i.get("suggested_dir") for i in loose), loose
print("  ✓ fixture scan 检出全部预期问题")
PYEOF

echo "=== TEST 5: --help / 可选参数 ==="
"$PY" "$SKILL_DIR/work_organizer.py" --help >/dev/null
"$PY" "$SKILL_DIR/work_organizer.py" scan --help | grep -q "strict-naming"
"$PY" "$SKILL_DIR/work_organizer.py" scan --help | grep -q "archive-years"
echo "  ✓ CLI help 可用"

echo ""
echo "🎉 所有 smoke test 通过"
