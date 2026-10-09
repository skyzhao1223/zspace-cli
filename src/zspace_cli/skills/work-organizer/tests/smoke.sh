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
    encoding="utf-8", errors="replace",
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
echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/workorgani-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/work_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/work_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
rm -rf "$ENC_DIR"
if echo "$out_ascii" | grep -q "UnicodeEncodeError"; then
  echo "  ❌ ascii stdout 下 UnicodeEncodeError — 中文报告把输出编码搞崩了"
  echo "     需要脚本里的 _force_utf8_stdio() 兜底"
  exit 1
fi
if [ "$rc_utf8" != "$rc_ascii" ]; then
  echo "  ❌ 退出码随 stdout 编码变化: utf-8=$rc_utf8 ascii=$rc_ascii"
  exit 1
fi
echo "  ✓ ascii stdout 下行为与 utf-8 一致(exit=$rc_ascii),中文不崩"

echo ""
echo "=== TEST 7: config.json 覆盖层(issue #15) ==="
# config.json 按**脚本自己所在的目录**(__file__)解析 —— 不是 cwd,也不是被扫描的
# root。所以这里把脚本复制进一个临时目录,模拟「zs skill 装好之后」的样子。
WOCFG_SRC="$(mktemp -d /tmp/workorganizer-cfg.XXXXXX)"
mkdir -p "$WOCFG_SRC/installed" "$WOCFG_SRC/lib"
cp "$SKILL_DIR/work_organizer.py" "$WOCFG_SRC/installed/"
WO_CFG_SRC="$WOCFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
from pathlib import Path

src = os.environ["WO_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "work_organizer.py")
cfg = os.path.join(installed, "config.json")
lib = os.path.join(src, "lib")
OLD = 1433116800                      # 2015-06-01,给 --archive-years 用

# ---- fixture:散文档 / 版本混乱 / 副本 / 陈旧文件 / 安装包 / 同名目录多份 ----
FILES = [
    "2024/官网改版/20240501_官网改版_需求文档_v2.docx",   # 合规命名
    "2024/官网改版/需求文档 最终版.docx",                 # 版本混乱
    "2024/官网改版/需求文档 final.docx",
    "2024/官网改版/需求文档 定稿.docx",                   # → 同名多版本共存(4)
    "2024/官网改版/需求文档 副本.docx",                   # 副本标记
    "2024/官网改版/设计稿.psd",
    "2024/官网改版/installer.dmg",                        # 安装包(INSTALLER_EXTS)
    "2024/官网改版/笔记.md",                              # --strict-naming 的目标
    "合同/合同2024.pdf",                                  # 内置 WHITELIST_DIRS
    "模板/简报.pptx",                                     # 内置 WHITELIST_DIRS
    "归档/2022/旧.doc",                                   # ARCHIVE_DIR_RE 豁免
    "新建文件夹/a.txt",                                   # BAD_DIR
    "temp/b.tmp",                                         # BAD_DIR + 里面的垃圾文件
    "TMP/f.docx",                                         # 大写 BAD_DIR(大小写双向)
    "项目 副本/c.docx",                                   # COPY_MARK_RE 目录
    "深层/temp/d.docx",                                   # 同名目录,不同路径
    "2024/temp/e.docx",
    "根目录散文件.docx",
    "随便记的笔记.md",
    "报告.bak",                                           # JUNK_EXTS({"bak":"doc"} 目标)
    "软件/tool.exe",                                      # INSTALLER_EXTS
    "-leading-dash.md",                                   # shell 危险名(前导 -)
    "空格 文件 名.xlsx",
    "._report.docx",                                      # AppleDouble
    ".DS_Store",
    "~$锁定.docx",                                        # LOCK_PREFIXES
    "samples/s.docx",
    "没有扩展名",
    "陈旧/2015方案.doc",                                  # --archive-years 候选
]
for rel in FILES:
    p = Path(lib) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
for rel in ("归档/2022/旧.doc", "陈旧/2015方案.doc"):
    os.utime(os.path.join(lib, rel), (OLD, OLD))


def write_cfg(text):
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(text)


def drop_cfg():
    if os.path.exists(cfg):
        os.remove(cfg)


def run(*extra, expect=0, cwd=None):
    # cwd 故意设成 src(既不是脚本目录也不是被扫描目录):证明解析与 cwd 无关
    r = subprocess.run(
        [sys.executable, script, "scan", "--root", lib, "--json", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd or src)
    assert r.returncode == expect, (r.returncode, r.stdout[-500:], r.stderr)
    return r


def scan(*extra, cwd=None):
    r = run(*extra, cwd=cwd)
    return json.loads(r.stdout), r.stderr


def kinds(d, needle):
    return sorted(i["path"] for i in d["issues"]
                  if any(needle in p for p in i["problems"]))


def flagged_dirs(d):
    return sorted(i["path"] for i in d["issues"] if i["is_dir"])


# -- 0. 基线:没有 config.json 时,stats 里连 config 这个键都不该出现 ---------
drop_cfg()
base, base_err = scan()
s = base["stats"]
assert "config" not in s, s.get("config")
assert "已加载覆盖配置" not in base_err, base_err
assert (s["dirs"], s["files"], s["junk"]) == (16, 24, 5), s
assert s["loose_root"] == 5 and s["copy_files"] == 1, s
assert s["version_chaos"] == 3 and s["installers"] == 2, s
assert s["by_type"] == {"doc": 17, "installer": 2, "design": 1, "pdf": 1,
                        "ppt": 1, "other": 1, "sheet": 1}, s["by_type"]
assert base["count"] == 23, base["count"]
assert flagged_dirs(base) == sorted(
    ["2024/temp", "TMP", "temp", "新建文件夹", "深层/temp", "项目 副本"]
), flagged_dirs(base)
assert kinds(base, "临时/锁定/垃圾文件") == sorted(
    [".DS_Store", "._report.docx", "temp/b.tmp", "~$锁定.docx", "报告.bak"]
), kinds(base, "临时/锁定/垃圾文件")
assert kinds(base, "安装包/镜像") == sorted(
    ["2024/官网改版/installer.dmg", "软件/tool.exe"])
assert len(kinds(base, "同名文档多版本共存")) == 1
# 内置 WHITELIST_DIRS 的职能目录一条都不报
for quiet in ("合同", "模板", "归档", "归档/2022"):
    assert quiet not in flagged_dirs(base), flagged_dirs(base)
# --strict-naming / --archive-years 的基线(下面几条覆盖要与它们对照)
strict_base, _ = scan("--strict-naming")
assert strict_base["count"] == 34, strict_base["count"]
arch_base, _ = scan("--archive-years", "3")
assert arch_base["stats"]["archive_candidates"] == 1, arch_base["stats"]
assert kinds(arch_base, "年未修改") == ["陈旧/2015方案.doc"], \
    kinds(arch_base, "年未修改")          # 归档/ 下的那个被 ARCHIVE_DIR_RE 豁免
print("  ✓ 无 config.json:stats 无 config 键、stderr 无提示、23 条问题与旧版一致")

# -- 1. whitelist_dirs 豁免目录名(这正是内置 WHITELIST_DIRS 的作用点)-------
write_cfg(json.dumps({"whitelist_dirs": ["temp"]}))
d, err = scan()
assert flagged_dirs(d) == sorted(["TMP", "新建文件夹", "项目 副本"]), flagged_dirs(d)
assert d["count"] == 20, d["count"]                     # 23 - 3 个 temp
assert kinds(d, "临时/无语义目录名") == sorted(["TMP", "新建文件夹"]), \
    kinds(d, "临时/无语义目录名")
assert "已加载覆盖配置" in err, err
assert d["stats"]["config"]["whitelist_dirs"] == ["temp"]
assert d["stats"]["config"]["extension_overrides"] == {}
# **本 skill 与 photo/music 的关键差别**:白名单只豁免目录名,子树照扫照报。
# 所以 dirs/files 一个不少,也没有 skipped_files 这个字段。
assert (d["stats"]["dirs"], d["stats"]["files"]) == (16, 24), d["stats"]
assert "skipped_files" not in d["stats"]["config"], d["stats"]["config"]
assert "temp/b.tmp" in kinds(d, "临时/锁定/垃圾文件"), kinds(d, "临时/锁定/垃圾文件")
print("  ✓ whitelist_dirs=[temp]:3 个 temp 目录不再判名,但子树里的文件照扫照报")

# -- 2. 大小写不敏感必须**双向**成立(端到端)------------------------------
# 只测「目录大写 / 模式小写」测不出来:实现里相对路径总是先 .lower(),那个方向
# 即使忘了给模式做 lower 也照样过。所以两个方向都要跑一遍。
for pat in ("temp", "TEMP", "Temp"):        # 小写目录 × 三种模式写法
    write_cfg(json.dumps({"whitelist_dirs": [pat]}))
    d, _ = scan()
    assert flagged_dirs(d) == sorted(["TMP", "新建文件夹", "项目 副本"]), \
        (pat, flagged_dirs(d))
for pat in ("tmp", "TMP", "Tmp"):           # 大写目录 TMP × 三种模式写法
    write_cfg(json.dumps({"whitelist_dirs": [pat]}))
    d, _ = scan()
    assert "TMP" not in flagged_dirs(d), (pat, flagged_dirs(d))
    assert d["count"] == 22, (pat, d["count"])
print("  ✓ 大小写不敏感双向成立:TEMP 命中 temp/,tmp 命中 TMP/(各 3 种写法)")

# -- 3. 锚定:带 / 的模式只命中那一条路径 ----------------------------------
write_cfg(json.dumps({"whitelist_dirs": ["2024/temp"]}))
d, _ = scan()
assert flagged_dirs(d) == sorted(
    ["TMP", "temp", "新建文件夹", "深层/temp", "项目 副本"]), flagged_dirs(d)
assert d["count"] == 22, d["count"]                     # 只少 2024/temp 这一条
# 与裸名字 temp(一次豁免 3 个,count 20)可区分 —— 不是同义反复
write_cfg(json.dumps({"whitelist_dirs": ["深层/temp"]}))
d2, _ = scan()
assert "深层/temp" not in flagged_dirs(d2) and "2024/temp" in flagged_dirs(d2), \
    flagged_dirs(d2)
print("  ✓ 深层/temp 与 2024/temp 可分别锚定:裸名字豁免 3 个、带 / 的只豁免 1 个")

# -- 4. fnmatch glob + "*" 也管不到根目录散文件 ---------------------------
write_cfg(json.dumps({"whitelist_dirs": ["项目*"]}))
d, _ = scan()
assert "项目 副本" not in flagged_dirs(d), flagged_dirs(d)
assert kinds(d, "副本目录") == [], kinds(d, "副本目录")
assert d["count"] == 22, d["count"]
write_cfg(json.dumps({"whitelist_dirs": ["*"]}))
d, _ = scan()
assert flagged_dirs(d) == [], flagged_dirs(d)
assert d["count"] == 17, d["count"]                     # 23 - 6 个目录名问题
# 白名单只作用于**目录名**:文件级检查一条都没少
assert (d["stats"]["files"], d["stats"]["junk"]) == (24, 5), d["stats"]
assert len(kinds(d, "根目录散文件")) == 5, kinds(d, "根目录散文件")
assert len(kinds(d, "临时/锁定/垃圾文件")) == 5, kinds(d, "临时/锁定/垃圾文件")
assert d["stats"]["installers"] == 2, d["stats"]["installers"]
print("  ✓ glob(项目*)可用;* 抹掉全部 6 条目录名问题,但文件级检查一条不少")

# -- 5. 内置 WHITELIST_DIRS 是**追加**,不是替换 ---------------------------
write_cfg(json.dumps({"whitelist_dirs": ["temp"]}))
d, _ = scan()
for quiet in ("合同", "模板", "归档", "归档/2022"):
    assert quiet not in flagged_dirs(d), (quiet, flagged_dirs(d))
# 另一个机制也不受影响:归档/ 下的旧文件仍然不算归档候选
d2, _ = scan("--archive-years", "3")
assert d2["stats"]["archive_candidates"] == 1, d2["stats"]
assert kinds(d2, "年未修改") == ["陈旧/2015方案.doc"], kinds(d2, "年未修改")
print("  ✓ whitelist_dirs 是追加:内置的 合同/模板/归档 加配置后仍然豁免")

# -- 6. extension_overrides:junk 可以被救回成正常类别 --------------------
# JUNK_EXTS 在 classify_ext() 之前就先命中,不「先摘掉」的话覆盖等于没写。
write_cfg(json.dumps({"extension_overrides": {"bak": "doc"}}))
d, _ = scan()
assert d["stats"]["junk"] == 4, d["stats"]["junk"]                   # 5 - 报告.bak
assert d["stats"]["files"] == 25, d["stats"]["files"]                # 24 + 1
assert d["stats"]["by_type"]["doc"] == 18, d["stats"]["by_type"]
assert d["stats"]["loose_root"] == 6, d["stats"]["loose_root"]       # 它现在是散文档
assert "报告.bak" not in kinds(d, "临时/锁定/垃圾文件"), kinds(d, "临时/锁定/垃圾文件")
assert "报告.bak" in kinds(d, "根目录散文件"), kinds(d, "根目录散文件")
print("  ✓ extension_overrides 把 .bak 从 junk 救回成 doc(不摘掉就永远先是垃圾)")

# -- 6b. 键归一化:前导点可写可不写、大小写都行 ---------------------------
# 只测小写键测不出「忘了 .lower()」:内置表全是小写,而实现里键也总是先小写。
# 所以这里用**大写键**跑同一条改判,期望值与上面完全相同。
write_cfg(json.dumps({"extension_overrides": {".BAK": "doc"}}))
d, _ = scan()
assert d["stats"]["junk"] == 4, d["stats"]["junk"]
assert d["stats"]["files"] == 25, d["stats"]["files"]
assert d["stats"]["by_type"]["doc"] == 18, d["stats"]["by_type"]
assert d["stats"]["config"]["extension_overrides"] == {"bak": "doc"}, \
    d["stats"]["config"]                      # 归一化之后的键才回写进 stats
write_cfg(json.dumps({"extension_overrides": {"MD": "code"}}))
d, _ = scan()
assert d["stats"]["by_type"]["code"] == 3, d["stats"]["by_type"]
assert d["stats"]["by_type"]["doc"] == 14, d["stats"]["by_type"]
print("  ✓ 键归一化:.BAK / MD 与大写无关地认出来,回写进 stats 的是归一化后的键")

# -- 7. 从**更早命中**的表里摘掉:md 从 DOC_EXTS 挪到 CODE_EXTS ------------
# classify_ext() 的阶梯是 doc → sheet → ppt → pdf → archive → installer →
# design → code → media → other。只往 CODE_EXTS 里加 md,doc 那一级会先命中。
write_cfg(json.dumps({"extension_overrides": {"md": "code"}}))
d, _ = scan()
assert d["stats"]["by_type"]["code"] == 3, d["stats"]["by_type"]      # 3 个 .md
assert d["stats"]["by_type"]["doc"] == 14, d["stats"]["by_type"]      # 17 - 3
#  observable 后果:--strict-naming 只对 doc/sheet/ppt/pdf 生效,.md 改判后不再查
d_strict, _ = scan("--strict-naming")
assert d_strict["count"] == 33, d_strict["count"]        # 34 - 2024/官网改版/笔记.md
assert "2024/官网改版/笔记.md" not in kinds(d_strict, "缺日期前缀"), \
    kinds(d_strict, "缺日期前缀")
assert "随便记的笔记.md" in [i["path"] for i in d["issues"]]   # 它仍是根目录散文件
print("  ✓ md 从 DOC_EXTS 摘出来改判 code:by_type 变了,--strict-naming 也不再查它")

# -- 8. 改判能触达「直接吃这张表」的检查,不只是 by_type -------------------
write_cfg(json.dumps({"extension_overrides": {"exe": "archive"}}))
d, _ = scan()
assert d["stats"]["installers"] == 1, d["stats"]["installers"]        # 2 - tool.exe
assert d["stats"]["by_type"]["installer"] == 1, d["stats"]["by_type"]
assert d["stats"]["by_type"]["archive"] == 1, d["stats"]["by_type"]
assert kinds(d, "安装包/镜像") == ["2024/官网改版/installer.dmg"], \
    kinds(d, "安装包/镜像")
write_cfg(json.dumps({"extension_overrides": {"dmg": "junk"}}))
d, _ = scan()
assert d["stats"]["junk"] == 6 and d["stats"]["files"] == 23, d["stats"]
assert d["stats"]["installers"] == 1, d["stats"]["installers"]
assert "archive" not in d["stats"]["by_type"], d["stats"]["by_type"]
print("  ✓ exe→archive / dmg→junk:installers 计数与「安装包混在工作区」都跟着变")

# -- 9. 兜底类别 other(表值是 None)= 「从所有表里摘掉,不放进任何一张」----
# 用 fixture 里真存在的 .pptx 来钉:改判之后它必须从 by_type["ppt"] 里消失、
# 落到 other,而不是静默无事发生。
write_cfg(json.dumps({"extension_overrides": {"pptx": "other"}}))
d, err = scan()
assert "ppt" not in d["stats"]["by_type"], d["stats"]["by_type"]
assert d["stats"]["by_type"]["other"] == 2, d["stats"]["by_type"]    # 没有扩展名 + pptx
assert d["stats"]["by_type"]["doc"] == 17, d["stats"]["by_type"]     # 其余一条不动
assert "已加载覆盖配置" in err
print("  ✓ 兜底类别 other 可写:.pptx 从 ppt 落到 other,不是静默无效")

# -- 10. 空对象 {}:加载并提示,但行为与无配置逐条相同 --------------------
def shape(x):
    return sorted((i["path"], tuple(i["problems"])) for i in x["issues"])


write_cfg("{}")
d, err = scan()
assert shape(d) == shape(base), "空对象改变了判定"
assert d["stats"]["by_type"] == base["stats"]["by_type"]
assert "config" in d["stats"]
assert d["stats"]["config"]["whitelist_dirs"] == []
assert d["stats"]["config"]["extension_overrides"] == {}
assert "skipped_files" not in d["stats"]["config"], d["stats"]["config"]
assert "已加载覆盖配置" in err, err
print("  ✓ 空对象 {} 加载成功:问题清单与无配置时逐条相同,但会明确提示已加载")

# -- 11. 配置文件在,但某个键不在 → 那个键不产生任何影响 ------------------
write_cfg(json.dumps({"whitelist_dirs": ["temp"]}))
d, _ = scan()
assert d["stats"]["config"]["extension_overrides"] == {}
assert d["stats"]["by_type"] == base["stats"]["by_type"]   # 没写 ext 覆盖
print("  ✓ 只有 whitelist_dirs 时,扩展名归类一条都不变")

# -- 12. 被扫描目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)---
drop_cfg()
root_cfg = os.path.join(lib, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"extension_overrides": {"bak": "doc"},
               "whitelist_dirs": ["*"]}, fh)
for cwd in (src, lib, installed):        # 三种 cwd 都不能让它被读到
    d, err = scan(cwd=cwd)
    assert "报告.bak" in kinds(d, "临时/锁定/垃圾文件"), (cwd, kinds(d, "临时/锁定/垃圾文件"))
    assert flagged_dirs(d) == flagged_dirs(base), (cwd, flagged_dirs(d))
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
    # 它自己被当成一个普通文件扫出来了(doc 类,根目录散文件)
    assert d["stats"]["files"] == 25, (cwd, d["stats"]["files"])
os.remove(root_cfg)
print("  ✓ 被扫描目录里的 config.json 被忽略(3 种 cwd 都试过),只当普通文件扫")

# -- 13. 畸形配置:一律 exit=1,指名文件与键,且不污染 --json stdout --------
BAD = [
    ("非法 JSON", "{not json", "不是合法 JSON"),
    ("空文件", "", "不是合法 JSON"),
    ("顶层不是对象", '"just a string"', "顶层"),
    ("顶层是数组", '["whitelist_dirs"]', "顶层"),
    ("未知键(少写一个 s)", '{"whitelist_dir": ["temp"]}', "whitelist_dir"),
    ("未知键(多写一个)", '{"whitelist_dirs": [], "extra": 1}', "extra"),
    ("whitelist_dirs 不是数组", '{"whitelist_dirs": "temp"}', "whitelist_dirs"),
    ("whitelist_dirs 元素不是字符串", '{"whitelist_dirs": [null]}',
     "whitelist_dirs[0]"),
    ("whitelist_dirs 第二个元素不是字符串", '{"whitelist_dirs": ["a", 2]}',
     "whitelist_dirs[1]"),
    ("whitelist_dirs 空字符串", '{"whitelist_dirs": ["  "]}', "whitelist_dirs[0]"),
    ("whitelist_dirs 绝对路径", '{"whitelist_dirs": ["/temp"]}', "绝对路径"),
    ("whitelist_dirs 反斜杠绝对路径", '{"whitelist_dirs": ["\\\\temp"]}', "绝对路径"),
    ("whitelist_dirs Windows 盘符", '{"whitelist_dirs": ["Z:\\\\work"]}',
     "绝对路径"),
    ("extension_overrides 不是对象", '{"extension_overrides": []}',
     "extension_overrides"),
    ("类别值不是字符串", '{"extension_overrides": {"bak": true}}',
     "必须是字符串类别名"),
    ("类别值是 null", '{"extension_overrides": {"bak": null}}',
     "必须是字符串类别名"),
    ("扩展名含多个点", '{"extension_overrides": {"tar.gz": "doc"}}', "tar.gz"),
    ("扩展名归一化后为空", '{"extension_overrides": {".": "doc"}}', "空的"),
    ("扩展名是空串", '{"extension_overrides": {"": "doc"}}', "空的"),
    ("类别名不存在", '{"extension_overrides": {"bak": "document"}}', "document"),
    ("两个键都拼错", '{"whitelist_dir": [], "extension_override": {}}',
     "extension_override"),
]
# 本 skill 的类别表与另外四个 scanner **不同**:同一份配置在别处合法、在这里必须
# 报错 —— 这条钉住「CONFIG_EXT_TABLES 是 per-skill 的」,不是全局一张表。
for label, text, needle in BAD + [
    ("photo-organizer 的类别名", '{"extension_overrides": {"bak": "sidecar"}}',
     "sidecar"),
    ("file-sorter 的类别名", '{"extension_overrides": {"bak": "cad"}}', "cad"),
    ("music-organizer 的类别名", '{"extension_overrides": {"bak": "audio"}}',
     "audio"),
    ("portfolio-organizer 的类别名", '{"extension_overrides": {"bak": "source"}}',
     "source"),
]:
    write_cfg(text)
    r = run(expect=1)
    assert r.stdout == "", (label, "错误不能污染 --json 的 stdout", r.stdout[:200])
    assert "config.json" in r.stderr, (label, r.stderr)
    assert needle in r.stderr, (label, needle, r.stderr)
    assert "❌" in r.stderr, (label, r.stderr)
# 报错里要列出**本 skill** 的可用类别,照着改就能修
write_cfg('{"extension_overrides": {"bak": "audio"}}')
r = run(expect=1)
for cat in ("doc", "sheet", "ppt", "pdf", "archive", "installer", "design",
            "code", "media", "junk", "other"):
    assert cat in r.stderr, (cat, r.stderr)
drop_cfg()
print(f"  ✓ {len(BAD) + 4} 种畸形配置全部 exit=1、只写 stderr、指名键,"
      "并列出本 skill 的 11 个可用类别")

# -- 14. 校验先于写入 + dir_problems() 没被改 + 锚定规则 -------------------
sys.path.insert(0, installed)
import contextlib                                           # noqa: E402
import io                                                   # noqa: E402
import work_organizer as wo                                 # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "whitelist_dirs": ["temp"],
    "extension_overrides": {"bak": "audio"},      # music-organizer 的类别,这里没有
}), encoding="utf-8")
before = (set(wo.DOC_EXTS), set(wo.SHEET_EXTS), set(wo.PPT_EXTS), set(wo.PDF_EXTS),
          set(wo.ARCHIVE_EXTS), set(wo.INSTALLER_EXTS), set(wo.DESIGN_EXTS),
          set(wo.CODE_EXTS), set(wo.MEDIA_EXTS), set(wo.JUNK_EXTS),
          set(wo.WHITELIST_DIRS))
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        wo.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "audio" in str(e) and "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()     # 失败路径不该先打印「已加载」
assert wo.CONFIG_WHITELIST == [], wo.CONFIG_WHITELIST
assert wo.CONFIG_INFO == {}, wo.CONFIG_INFO
assert (set(wo.DOC_EXTS), set(wo.SHEET_EXTS), set(wo.PPT_EXTS), set(wo.PDF_EXTS),
        set(wo.ARCHIVE_EXTS), set(wo.INSTALLER_EXTS), set(wo.DESIGN_EXTS),
        set(wo.CODE_EXTS), set(wo.MEDIA_EXTS), set(wo.JUNK_EXTS),
        set(wo.WHITELIST_DIRS)) == before
assert set(wo.CONFIG_EXT_TABLES) == {
    "doc", "sheet", "ppt", "pdf", "archive", "installer", "design", "code",
    "media", "junk", "other"}, set(wo.CONFIG_EXT_TABLES)

# 本 skill 的门开在 dir_problems() 的**调用点**,不在函数里 —— 所以它的签名与
# 函数体一行没改,TEST 3 那批直接调它的纯函数断言照旧成立,而且配置生效前后
# dir_problems() 自己的返回值不变(内置 WHITELIST_DIRS 一个都没被替换掉)。
assert wo.dir_problems("temp", 1) == ["临时/无语义目录名(建议重命名或清理)"]
assert wo.dir_problems("合同", 1) == []
ok_dir = Path(src) / "ok"
ok_dir.mkdir(exist_ok=True)
(ok_dir / "config.json").write_text(
    json.dumps({"whitelist_dirs": ["temp"]}), encoding="utf-8")
with contextlib.redirect_stderr(io.StringIO()):
    wo.load_config(ok_dir)
assert wo.CONFIG_WHITELIST == ["temp"], wo.CONFIG_WHITELIST
assert wo.dir_problems("temp", 1) == ["临时/无语义目录名(建议重命名或清理)"]
assert wo.dir_problems("合同", 1) == []           # 内置白名单仍在(追加,不是替换)
assert "合同" in wo.WHITELIST_DIRS and "temp" not in wo.WHITELIST_DIRS

m = wo._cfg_match
assert m(["2024", "temp"], ["2024/temp"])              # 整段相对路径命中
assert m(["2024", "temp"], ["temp"])                   # 任一级目录名命中
assert not m(["2024"], ["2024/temp"])                  # 带 / 的模式不命中父级
assert not m(["深层", "temp", "子目录"], ["2024/temp"])  # 不在那条路径上就不算
assert m(["深层", "temp", "子目录"], ["深层/temp/*"])
assert m(["深层", "temp", "子目录"], ["temp"])           # 裸名字命中任意深度
assert m(["官网改版", "需求 文档"], ["官网*"])            # glob + 含空格的路径
assert m(["TMP"], ["tmp"])                              # 大小写不敏感(目录大写)
assert m(["tmp"], ["TMP"])                              # 反方向:模式大写
assert m(["2024", "temp"], ["2024/TEMP"])                # 整段路径也要双向不敏感
assert m(["Samples", "Sub"], ["SAMPLES/*"])             # 带通配时同样双向
assert m(["项目 副本"], ["项目*"])                       # fnmatch 通配 CJK
assert not m([], ["*"])                                 # root 下的散文件不吃白名单
assert not m(["temp"], [])                              # 没有模式 = 不命中
print("  ✓ 校验先于写入(内置 11 个集合分毫未动)+ dir_problems() 未被改动"
      " + _cfg_match 锚定规则 14 条断言")

# -- 15. 模式必须**锚定**在 root:带 / 的模式不能被当成后缀匹配 ------------
# 前面第 3 组用的 2024/temp 在 fixture 里只有一条路径能对上,所以「锚定」与
# 「前面偷偷加了个 *」两种实现给出的结果一样 —— 那条断言抓不住不锚定的实现。
# 这里再建一条**三级**路径,让 2024/temp 变成它的后缀:锚定时不命中,不锚定时命中。
os.makedirs(os.path.join(lib, "备份区/2024/temp"), exist_ok=True)
with open(os.path.join(lib, "备份区/2024/temp/g.docx"), "w", encoding="utf-8") as fh:
    fh.write("x")
drop_cfg()
b2, _ = scan()
assert (b2["stats"]["dirs"], b2["stats"]["files"]) == (19, 25), b2["stats"]
assert b2["count"] == 24, b2["count"]
assert "备份区/2024/temp" in flagged_dirs(b2), flagged_dirs(b2)   # 它本身也违规
write_cfg(json.dumps({"whitelist_dirs": ["2024/temp"]}))
d, _ = scan()
# 锚定:只豁免 root 下那一条 2024/temp;备份区/2024/temp 照报
assert flagged_dirs(d) == sorted(["TMP", "temp", "备份区/2024/temp", "新建文件夹",
                                 "深层/temp", "项目 副本"]), flagged_dirs(d)
assert d["count"] == 23, d["count"]
# 想豁免那条三级路径,必须写全路径(证明匹配对象是「相对 root 的整段路径」)
write_cfg(json.dumps({"whitelist_dirs": ["备份区/2024/temp"]}))
d, _ = scan()
assert "备份区/2024/temp" not in flagged_dirs(d), flagged_dirs(d)
assert "2024/temp" in flagged_dirs(d), flagged_dirs(d)
assert d["count"] == 23, d["count"]
# 显式加通配才允许跨层:*2024/temp 两条都命中
write_cfg(json.dumps({"whitelist_dirs": ["*2024/temp"]}))
d, _ = scan()
assert "备份区/2024/temp" not in flagged_dirs(d), flagged_dirs(d)
assert "2024/temp" not in flagged_dirs(d), flagged_dirs(d)
assert d["count"] == 22, d["count"]
os.remove(os.path.join(lib, "备份区/2024/temp/g.docx"))
print("  ✓ 模式锚定在 root:2024/temp 不命中 备份区/2024/temp(不锚定的实现会命中)")
PYEOF
rm -rf "$WOCFG_SRC"

echo "🎉 所有 smoke test 通过"
