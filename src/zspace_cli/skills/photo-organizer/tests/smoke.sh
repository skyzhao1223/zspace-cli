#!/bin/bash
# photo-organizer skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
PHOTO_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["PHOTO_SKILL_DIR"]
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
if name != "photo-organizer":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=photo-organizer")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/photo_organizer.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(日期提取/目录校验) ==="
PHOTO_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys
from datetime import datetime

sys.path.insert(0, os.environ["PHOTO_SKILL_DIR"])
import photo_organizer as po

# 日期提取
assert po.extract_date("IMG_20240501_101530.jpg") == ("2024-05-01", "camera")
assert po.extract_date("微信图片_20240501101530_12_226.jpg")[0] == "2024-05-01"
assert po.extract_date("微信图片_20240501101530_12_226.jpg")[1] == "wechat"
assert po.extract_date("Screenshot_2024-05-01-10-15-30.png") == ("2024-05-01", "screenshot")
assert po.extract_date("截屏2024-05-01 10.15.30.png") == ("2024-05-01", "screenshot")
assert po.extract_date("CleanShot 2024-05-01 at 10.15.30.png")[0] == "2024-05-01"
assert po.extract_date("2024-05-01 海边.jpg") == ("2024-05-01", "generic")
assert po.extract_date("20240501_海边.jpg") == ("2024-05-01", "generic")
assert po.extract_date("IMG_1234.jpg") == (None, "camera")
assert po.extract_date("Screenshot (1).png") == (None, "screenshot")
# mmexport epoch 毫秒
ms = 1714521600123
expect = datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
assert po.extract_date(f"mmexport{ms}.jpg") == (expect, "wechat")
# 非法日期不放行
assert po.extract_date("2024-13-45 foo.jpg")[0] is None

# 目录校验
assert po.dir_problems("2024", 1, False) == []
assert po.dir_problems("2024-05", 2, True) == []
assert po.dir_problems("2024-05-01 五一杭州行", 2, True) == []
assert po.dir_problems("2024.5.1", 1, False) == []
assert po.dir_problems("截图", 1, False) == []
assert any("日期规范" in p for p in po.dir_problems("五一出游", 1, False))
assert any("原始卷" in p for p in po.dir_problems("100APPLE", 2, True))
assert any("临时" in p for p in po.dir_problems("新建文件夹", 1, False))
# 事件目录内部自由命名(depth>=3)
assert po.dir_problems("day1 海边", 3, False) == []

# 散文件判定
assert po.is_loose(["IMG_1.jpg"], "") is True
assert po.is_loose(["2024", "IMG_1.jpg"], "2024") is True
assert po.is_loose(["2024", "2024-05", "IMG_1.jpg"], "2024-05") is False

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/photo-organizer-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/照片"
mkdir -p "$ROOT/2024/2024-05" "$ROOT/100APPLE" "$ROOT/五一出游"
# 合规文件
touch "$ROOT/2024/2024-05/IMG_20240501_101530.jpg"
# 散文件(root 直下)
touch "$ROOT/微信图片_20240501101530.jpg" "$ROOT/IMG_1234.jpg" \
      "$ROOT/Screenshot_2024-05-01-10-15-30.png" "$ROOT/报告.pdf"
# 重复副本
touch "$ROOT/2024/2024-05/IMG_9999 (1).jpg"
# 连拍(5 张连号)
for i in 3001 3002 3003 3004 3005; do touch "$ROOT/2024/2024-05/IMG_$i.jpg"; done
# 垃圾
touch "$ROOT/._IMG_1234.jpg" "$ROOT/x.td"
# 目录问题:100APPLE(原始卷)、五一出游(无日期)

PHOTO_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["PHOTO_SKILL_DIR"]
root = os.environ["FIXTURE_ROOT"]
out = os.path.join(tempfile.mkdtemp(), "issues.json")
r = subprocess.run(
    [sys.executable, f"{skill}/photo_organizer.py", "scan",
     "--root", root, "--output", out],
    capture_output=True, text=True,
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())
blob = json.dumps(data, ensure_ascii=False)

assert data["skill"] == "photo-organizer"
assert data["stats"]["photos"] >= 8, data["stats"]
assert data["stats"]["loose"] >= 3, data["stats"]  # pdf 走非媒体分支不计散文件
assert data["stats"]["by_month"].get("2024-05", 0) >= 1, data["stats"]
assert "散文件" in blob
assert "非媒体文件混入" in blob          # 报告.pdf
assert "垃圾/系统残留" in blob           # ._IMG / x.td
assert "疑似重复副本" in blob            # IMG_9999 (1).jpg
assert "疑似连拍组" in blob              # IMG_3001-3005
assert "相机/手机原始卷目录" in blob     # 100APPLE
assert "目录名不符合日期规范" in blob    # 五一出游
# 散文件的 date/suggested_dir 字段
loose = [i for i in data["issues"]
         if any("散文件" in p for p in i["problems"])]
wechat = next(i for i in loose if i["name"].startswith("微信图片"))
assert wechat["date"] == "2024-05-01" and wechat["suggested_dir"] == "2024/2024-05"
assert "mtime" in (loose[0].get("date_source") or "") or \
       any(i.get("date_source") == "filename" for i in loose)
print("  ✓ fixture scan 检出全部预期问题")
PYEOF

echo "=== TEST 5: --help ==="
"$PY" "$SKILL_DIR/photo_organizer.py" --help >/dev/null
"$PY" "$SKILL_DIR/photo_organizer.py" scan --help >/dev/null
echo "  ✓ CLI help 可用"

echo ""
echo "🎉 所有 smoke test 通过"
