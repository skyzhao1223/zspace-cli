#!/bin/bash
# portfolio-organizer skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
PF_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["PF_SKILL_DIR"]
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
if name != "portfolio-organizer":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=portfolio-organizer")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/portfolio_organizer.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(年份/封面/说明/分组键) ==="
PF_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["PF_SKILL_DIR"])
import portfolio_organizer as po

# 项目名年份
assert po.has_year("2024_品牌设计")
assert po.has_year("品牌设计-2024")
assert po.has_year("2023 官网")
assert not po.has_year("品牌设计")
assert not po.has_year("20xx_未来项目")

# 封面/说明识别
assert po.is_cover("cover.jpg")
assert po.is_cover("封面.png")
assert po.is_cover("preview_v2.png")
assert not po.is_cover("logo.png")
assert not po.is_cover("cover.txt")          # 非图片/pdf 不算
assert po.is_readme("README.md")
assert po.is_readme("说明.txt")
assert not po.is_readme("需求文档.docx")

# 分组键:版本名归一
assert po.base_stem("logo_横版_v3") == po.base_stem("logo_横版_v1")
assert po.base_stem("poster 最终版") == po.base_stem("poster_v2")
assert po.base_stem("poster(1)") == po.base_stem("poster")

# 类型分类
assert po.classify_ext("psd") == "source"
assert po.classify_ext("png") == "export"
assert po.classify_ext("xyz") == "other"

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/portfolio-organizer-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/作品集"
# 合规项目(有年份/封面/说明/成品/源文件)+ 成品多版本
mkdir -p "$ROOT/2024_品牌设计/成品" "$ROOT/2024_品牌设计/源文件"
touch "$ROOT/2024_品牌设计/cover.jpg" "$ROOT/2024_品牌设计/README.md"
touch "$ROOT/2024_品牌设计/成品/logo_v1.png" \
      "$ROOT/2024_品牌设计/成品/logo_v2.png" \
      "$ROOT/2024_品牌设计/成品/logo_v3.png"
echo "bigpsd" > "$ROOT/2024_品牌设计/源文件/logo.psd"
# 混乱项目:无年份、无封面、无说明、成品源文件混放
mkdir -p "$ROOT/老项目"
touch "$ROOT/老项目/poster.jpg" "$ROOT/老项目/poster.psd"
# 空项目
mkdir -p "$ROOT/空项目"
# 白名单功能目录(跳过校验)
mkdir -p "$ROOT/字体"
touch "$ROOT/字体/某字体.otf"
# 根目录散文件 + 垃圾
echo x > "$ROOT/loose.mp4"
touch "$ROOT/.DS_Store"

PF_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["PF_SKILL_DIR"]
root = os.environ["FIXTURE_ROOT"]
out = os.path.join(tempfile.mkdtemp(), "issues.json")
# --large-gb 0:任何非空源文件都触发大文件提示(fixture 无法造真 GB 文件)
r = subprocess.run(
    [sys.executable, f"{skill}/portfolio_organizer.py", "scan",
     "--root", root, "--large-gb", "0", "--output", out],
    capture_output=True, text=True,
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())
blob = json.dumps(data, ensure_ascii=False)

assert data["skill"] == "portfolio-organizer"
s = data["stats"]
assert s["projects"] == 3, s               # 字体是白名单,不计项目
assert s["with_cover"] == 1, s
assert s["with_readme"] == 1, s
assert s["with_final_dir"] == 1, s
assert s["loose_root"] == 1, s
assert "项目名缺年份" in blob               # 老项目 / 空项目
assert "缺封面图" in blob
assert "缺项目说明" in blob
assert "成品与源文件混放" in blob           # 老项目
assert "成品多版本共存" in blob             # logo v1-v3
assert "空项目目录" in blob
assert "根目录散文件" in blob               # loose.mp4
assert "大体积源文件" in blob               # logo.psd(--large-gb 0)
assert "垃圾/临时文件" in blob              # .DS_Store
print("  ✓ fixture scan 检出全部预期问题")
PYEOF

echo "=== TEST 5: --help ==="
"$PY" "$SKILL_DIR/portfolio_organizer.py" --help >/dev/null
"$PY" "$SKILL_DIR/portfolio_organizer.py" scan --help >/dev/null
echo "  ✓ CLI help 可用"

echo ""
echo "🎉 所有 smoke test 通过"
