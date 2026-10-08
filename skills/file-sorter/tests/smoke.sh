#!/bin/bash
# file-sorter skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh          (PY=/usr/bin/python3 可验 3.9 兼容)
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
FS_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["FS_SKILL_DIR"]
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
if name != "file-sorter":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=file-sorter")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/file_sorter.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(分类/目录名/目标路径/副本标记/祖先判定) ==="
FS_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys
from datetime import datetime

sys.path.insert(0, os.environ["FS_SKILL_DIR"])
import file_sorter as fs

# --- 分类:15 类都要能命中,junk 优先,未识别 → other
cases = {
    "cad": ["a.dwg", "b.dxf", "c.step", "d.sldprt", "e.rvt", "f.skp", "g.stl"],
    "doc": ["a.pdf", "b.docx", "c.xlsx", "d.pptx", "e.md", "f.csv"],
    "design": ["a.psd", "b.ai", "c.fig", "d.blend", "e.prproj"],
    "image": ["a.jpg", "b.png", "c.heic", "d.svg", "e.cr2"],
    "video": ["a.mp4", "b.mkv", "c.ts"],
    "audio": ["a.mp3", "b.flac", "c.cue"],
    "ebook": ["a.epub", "b.mobi", "c.azw3"],
    "archive": ["a.zip", "b.rar", "c.7z"],
    "installer": ["a.dmg", "b.exe", "c.apk"],
    "font": ["a.ttf", "b.otf", "c.woff2"],
    "code": ["a.py", "b.js", "c.sql", "d.json"],
    "backup": ["a.iso", "b.vmdk", "c.sparsebundle"],
    "torrent": ["a.torrent"],
    "other": ["a.xyz", "README"],
}
for cat, names in cases.items():
    for n in names:
        ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
        assert fs.categorize(n, ext) == cat, (n, ext, cat)

# junk:系统文件名 / AppleDouble / 临时扩展名
assert fs.categorize(".DS_Store", "") == "junk"
assert fs.categorize("Thumbs.db", "") == "junk"
assert fs.categorize("._photo.jpg", "jpg") == "junk"   # AppleDouble 优先于 image
assert fs.categorize("x.log", "log") == "junk"
assert fs.categorize("x.tmp", "tmp") == "junk"
assert fs.categorize("x.bak", "bak") == "junk"
# 大小写不敏感由调用方保证(ext 已 lower),这里验一次
assert fs.categorize("A.DWG", "dwg") == "cad"

# --- 目录名 / 目标路径
assert fs.dir_for("cad", "zh") == "图纸"
assert fs.dir_for("cad", "en") == "Drawings"
assert fs.dir_for("doc", "zh") == "文档"
assert fs.dir_for("other", "zh") == "待分类"
may = datetime(2024, 5, 1).timestamp()
assert fs.target_parts("cad", "type", may) == ["图纸"]
assert fs.target_parts("cad", "type-year", may) == ["图纸", "2024"]
assert fs.target_parts("doc", "year-type", may) == ["2024", "文档"]
assert fs.target_parts("image", "type-year", None) == ["图片", "未知年份"]
assert fs.target_parts("cad", "type", may, "en") == ["Drawings"]

# --- 副本标记
assert fs.strip_dup_mark("合同 副本") == "合同"
assert fs.strip_dup_mark("合同 副本2") == "合同"
assert fs.strip_dup_mark("a (1)") == "a"
assert fs.strip_dup_mark("a[2]") == "a"
assert fs.strip_dup_mark("a copy") == "a"
assert fs.strip_dup_mark("a - copy") == "a"
assert fs.strip_dup_mark("合同") == "合同"
assert fs.DUP_MARK_RE.search("合同") is None          # 不带标记的不误判
assert fs.DUP_MARK_RE.search("副本计划书") is None     # 「副本」在中间不算

# --- 祖先类别目录判定(中英文别名)
assert fs.ancestor_cat(["图纸"]) == "cad"
assert fs.ancestor_cat(["2024", "图纸"]) == "cad"
assert fs.ancestor_cat(["图纸", "2024"]) == "cad"     # 类别目录下再分年份
assert fs.ancestor_cat(["Drawings"]) == "cad"
assert fs.ancestor_cat(["施工图"]) == "cad"
assert fs.ancestor_cat(["文档"]) == "doc"
assert fs.ancestor_cat(["Documents"]) == "doc"
assert fs.ancestor_cat(["照片"]) == "image"
assert fs.ancestor_cat(["源文件"]) == "design"
assert fs.ancestor_cat(["a", "b"]) is None
assert fs.ancestor_cat([]) is None
assert fs.ancestors("a/b/c") == ["a", "a/b"]
assert fs.ancestors("a") == []

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan(乱目录 + 合规库零误报) ==="
FIXTURE="$(mktemp -d /tmp/file-sorter-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

# --- 乱目录:12 类散文件 + 副本 + 重名冲突 + 已就位 + 跨类别嵌套 + 项目目录
ROOT="$FIXTURE/data"
mkdir -p "$ROOT/a" "$ROOT/b" "$ROOT/图纸" "$ROOT/文档" "$ROOT/图片" "$ROOT/视频" \
         "$ROOT/2024_官网改版/源文件"
printf 'dwg-bytes'        > "$ROOT/平面.dwg"
printf 'dxf-bytes'        > "$ROOT/立面.dxf"
printf 'step-bytes'       > "$ROOT/零件.step"
printf 'contract-1234'    > "$ROOT/合同.pdf"
printf 'contract-1234'    > "$ROOT/合同 副本.pdf"      # 同大小 + 副本标记
printf 'xlsx-bytes'       > "$ROOT/报价.xlsx"
printf 'jpg-bytes'        > "$ROOT/渲染.jpg"
printf 'psd-bytes'        > "$ROOT/logo.psd"
printf 'zip-bytes'        > "$ROOT/资料.zip"
printf 'dmg-bytes'        > "$ROOT/安装包.dmg"
printf 'ttf-bytes'        > "$ROOT/思源黑体.ttf"
printf 'epub-bytes'       > "$ROOT/小说.epub"
printf 'unknown'          > "$ROOT/mystery.xyz"        # 未识别扩展名
printf 'noext'            > "$ROOT/README"             # 无扩展名
printf 'log-data'         > "$ROOT/build.log"          # 临时残留
touch "$ROOT/.DS_Store"                                # 系统垃圾
touch -t 202405010000 "$ROOT/平面.dwg"                  # 固定年份供 type-year 断言
printf 'same-jpg'         > "$ROOT/a/x.jpg"
printf 'same-jpg'         > "$ROOT/b/x.jpg"            # 与 a/x.jpg 目标重名
printf 'ok-dwg'           > "$ROOT/图纸/立面图.dwg"     # 已就位
printf 'ok-jpg'           > "$ROOT/图纸/效果图.jpg"     # 跨类别嵌套(默认不动)
printf 'ok-docx'          > "$ROOT/文档/规范.docx"      # 已就位
printf 'ok-png'           > "$ROOT/图片/照片.png"       # 已就位
printf 'ok-mp4'           > "$ROOT/视频/宣传.mp4"       # 已就位
printf 'proj-doc'         > "$ROOT/2024_官网改版/需求.docx"
printf 'proj-dwg'         > "$ROOT/2024_官网改版/平面布置.dwg"
printf 'proj-png'         > "$ROOT/2024_官网改版/效果.png"
printf 'proj-psd'         > "$ROOT/2024_官网改版/源文件/首页.psd"   # 源文件 → design

# --- 合规库:期望零误报
LIB="$FIXTURE/lib"
mkdir -p "$LIB/图纸/2024" "$LIB/Documents" "$LIB/照片" "$LIB/视频" "$LIB/压缩包"
printf 'a' > "$LIB/图纸/a.dwg"
printf 'b' > "$LIB/图纸/2024/b.dxf"
printf 'c' > "$LIB/Documents/c.pdf"
printf 'd' > "$LIB/照片/d.png"
printf 'e' > "$LIB/视频/e.mp4"
printf 'f' > "$LIB/压缩包/f.zip"

# --- 同名文件泛滥:12 个 x.jpg 散在 12 个目录(撞名超过自动编号上限)
COLL="$FIXTURE/collide"
for i in $(seq 0 11); do
  mkdir -p "$COLL/d$i"
  printf 'same' > "$COLL/d$i/x.jpg"
done

FS_SKILL_DIR="$SKILL_DIR" FIX_ROOT="$ROOT" FIX_LIB="$LIB" FIX_COLL="$FIXTURE/collide" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["FS_SKILL_DIR"]
root = os.environ["FIX_ROOT"]
lib = os.environ["FIX_LIB"]
tmp = tempfile.mkdtemp()


def run(*extra, target_root=root):
    out = os.path.join(tmp, f"sort{len(extra)}.json")
    r = subprocess.run(
        [sys.executable, f"{skill}/file_sorter.py", "scan",
         "--root", target_root, "--output", out, *extra],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(open(out, encoding="utf-8").read())


by_path = None
data = run()
s = data["stats"]
assert data["skill"] == "file-sorter", data["skill"]
issues = data["issues"]
by_path = {i["path"]: i for i in issues if not i.get("is_dir")}
dirs = [i for i in issues if i.get("is_dir")]

# -- 类别分布(15 类里命中 11 类)
bc = s["by_category"]
assert s["files"] == 27, s["files"]
assert s["root_files"] == 16, s["root_files"]
assert bc["cad"]["count"] == 5, bc["cad"]          # 3 散 + 1 就位 + 1 项目内
assert bc["doc"]["count"] == 5, bc["doc"]
assert bc["image"]["count"] == 6, bc["image"]
assert bc["design"]["count"] == 2, bc["design"]
assert bc["other"]["count"] == 2, bc["other"]      # mystery.xyz + README
assert bc["junk"]["count"] == 2, bc["junk"]        # .DS_Store + build.log
assert bc["video"]["count"] == 1 and bc["ebook"]["count"] == 1
assert bc["font"]["count"] == 1 and bc["archive"]["count"] == 1
assert bc["installer"]["count"] == 1

# -- 四种「不动」
assert s["compliant"] == 5, s["compliant"]         # 图纸/文档/图片/视频 各就位 + 源文件
assert s["nested_other_cat"] == 1, s["nested_other_cat"]   # 图纸/效果图.jpg
assert s["protected"] == 0, s["protected"]
assert s["project_dirs"] == 1 and s["project_files"] == 3, s

# -- 待处理计数
assert s["to_move"] == 13, s["to_move"]
assert s["to_review"] == 4, s["to_review"]         # xyz + README + 冲突 + 项目目录
assert s["to_delete"] == 2, s["to_delete"]
assert data["count"] == 19, data["count"]
assert s["conflicts"] == 1, s["conflicts"]
assert s["dup_suspects"] == 1, s["dup_suspects"]

# -- 目标路径(类型 → 中文类别目录)
assert by_path["平面.dwg"]["target"] == "图纸/平面.dwg", by_path["平面.dwg"]
assert by_path["立面.dxf"]["target"] == "图纸/立面.dxf"
assert by_path["零件.step"]["target"] == "图纸/零件.step"
assert by_path["合同.pdf"]["target"] == "文档/合同.pdf"
assert by_path["渲染.jpg"]["target"] == "图片/渲染.jpg"
assert by_path["logo.psd"]["target"] == "设计源文件/logo.psd"
assert by_path["资料.zip"]["target"] == "压缩包/资料.zip"
assert by_path["安装包.dmg"]["target"] == "安装包/安装包.dmg"
assert by_path["思源黑体.ttf"]["target"] == "字体/思源黑体.ttf"
assert by_path["小说.epub"]["target"] == "电子书/小说.epub"
assert by_path["a/x.jpg"]["target"] == "图片/x.jpg"
for p in ("平面.dwg", "合同.pdf", "a/x.jpg"):
    assert by_path[p]["action"] == "move" and by_path[p]["confidence"] == "high"

# -- 重名冲突:降级 review + 自动改名 + 记录冲突方
c = by_path["b/x.jpg"]
assert c["target"] == "图片/x__2.jpg", c["target"]
assert c["action"] == "review" and c["confidence"] == "medium"
assert c["conflict_with"] == "图片/x.jpg", c

# -- 未识别扩展名 / 无扩展名 → 待分类 + review + low
for p, prob in (("mystery.xyz", ".xyz"), ("README", "缺失")):
    i = by_path[p]
    assert i["target"] == f"待分类/{p.split('/')[-1]}", i["target"]
    assert i["action"] == "review" and i["confidence"] == "low"
    assert i["category"] == "other"
    assert any(prob in x for x in i["problems"]), i["problems"]
assert s["unknown_exts"][".xyz"] == 1 and s["unknown_exts"]["(无扩展名)"] == 1

# -- 垃圾两种动作
assert by_path[".DS_Store"]["action"] == "delete"
assert by_path["build.log"]["action"] == "delete-confirm"
assert by_path[".DS_Store"]["target"] is None
assert s["junk_bytes"] > 0

# -- 疑似副本:指向不带标记的本尊,并引导去 dedup-finder
d = by_path["合同 副本.pdf"]
assert any("dedup-finder" in x for x in d["problems"]), d["problems"]
assert any("合同.pdf" in x for x in d["problems"]), d["problems"]
assert d["action"] == "move"        # 仍给搬运目标,由 LLM 决定先去重

# -- 默认不动:跨类别嵌套 + 项目目录内文件都不该出现在 issues 里
for absent in ("图纸/效果图.jpg", "图纸/立面图.dwg", "文档/规范.docx",
               "图片/照片.png", "视频/宣传.mp4",
               "2024_官网改版/需求.docx", "2024_官网改版/平面布置.dwg",
               "2024_官网改版/效果.png", "2024_官网改版/源文件/首页.psd"):
    assert absent not in by_path, absent
# 项目目录本身一条目录级 issue
assert len(dirs) == 1 and dirs[0]["path"] == "2024_官网改版/", dirs
assert dirs[0]["files"] == 3 and len(dirs[0]["categories"]) == 3, dirs[0]
assert dirs[0]["action"] == "review"
assert any("--split-project-dirs" in x for x in dirs[0]["problems"])

# -- 归档后结构预览 + 最大文件榜
assert s["target_dirs"]["图纸"]["count"] == 3, s["target_dirs"]
assert s["target_dirs"]["文档"]["count"] == 3, s["target_dirs"]
assert len(s["largest"]) == 10 and s["largest"][0]["size"] >= s["largest"][-1]["size"]
assert data["errors"] == []
print("  ✓ 乱目录 fixture:类别/目标/冲突/副本/垃圾/项目目录 全部符合预期")

# -- --strict:跨类别嵌套也要报
st = run("--strict")
sp = {i["path"]: i for i in st["issues"] if not i.get("is_dir")}
assert "图纸/效果图.jpg" in sp, sorted(sp)
assert sp["图纸/效果图.jpg"]["target"] == "图片/效果图.jpg"
assert any("--strict" in x for x in sp["图纸/效果图.jpg"]["problems"])
assert st["stats"]["nested_other_cat"] == 0
print("  ✓ --strict 生效(跨类别嵌套开始上报)")

# -- --split-project-dirs:项目目录被拆开
sp2 = run("--split-project-dirs")
sp2p = {i["path"]: i for i in sp2["issues"] if not i.get("is_dir")}
assert not [i for i in sp2["issues"] if i.get("is_dir")]
assert sp2["stats"]["project_files"] == 0
assert sp2["stats"]["project_dirs"] == 0
assert sp2p["2024_官网改版/需求.docx"]["target"] == "文档/需求.docx"
assert sp2p["2024_官网改版/平面布置.dwg"]["target"] == "图纸/平面布置.dwg"
assert sp2p["2024_官网改版/效果.png"]["target"] == "图片/效果.png"
print("  ✓ --split-project-dirs 生效(项目目录按类型拆开)")

# -- --keep-dir 白名单:整个子树不动
kd = run("--keep-dir", "2024_*")
kdp = {i["path"] for i in kd["issues"]}
assert kd["stats"]["protected"] == 4, kd["stats"]["protected"]
assert not [p for p in kdp if p.startswith("2024_官网改版")], sorted(kdp)
print("  ✓ --keep-dir 白名单生效(含子目录里的源文件)")

# -- --layout / --naming / --dest 组合
ly = run("--layout", "type-year")
lp = {i["path"]: i for i in ly["issues"] if not i.get("is_dir")}
assert lp["平面.dwg"]["target"] == "图纸/2024/平面.dwg", lp["平面.dwg"]["target"]
yt = run("--layout", "year-type")
yp = {i["path"]: i for i in yt["issues"] if not i.get("is_dir")}
assert yp["合同.pdf"]["target"].startswith("20") and "/文档/合同.pdf" in yp["合同.pdf"]["target"]
en = run("--naming", "en")
ep = {i["path"]: i for i in en["issues"] if not i.get("is_dir")}
assert ep["平面.dwg"]["target"] == "Drawings/平面.dwg", ep["平面.dwg"]["target"]
assert ep["mystery.xyz"]["target"] == "Unsorted/mystery.xyz"
ds = run("--dest", "整理后")
dp = {i["path"]: i for i in ds["issues"] if not i.get("is_dir")}
assert dp["平面.dwg"]["target"] == "整理后/图纸/平面.dwg", dp["平面.dwg"]["target"]
print("  ✓ --layout/--naming/--dest 目标路径正确")

# -- 计划条数上限:issues 截断但 stats 全量
cap = run("--max-issues", "5")
assert cap["stats"]["issues_truncated"] is True, cap["stats"]
assert cap["stats"]["omitted_issues"] == 13, cap["stats"]["omitted_issues"]
assert cap["stats"]["to_move"] == 13, cap["stats"]       # 统计不受截断影响
assert len([i for i in cap["issues"] if not i.get("is_dir")]) == 5
assert [i for i in cap["issues"] if i.get("is_dir")], "项目目录 issue 不该被截断"
print("  ✓ --max-issues 截断计划但保留全量统计")

# -- --only-cat 分批:只给指定类别出计划
oc = run("--only-cat", "cad")
ocp = [i for i in oc["issues"] if not i.get("is_dir")]
assert ocp and all(i["category"] == "cad" for i in ocp), ocp
assert oc["stats"]["to_move"] == 3, oc["stats"]["to_move"]   # 3 个散落 cad
assert oc["stats"]["filtered_out"] > 0, oc["stats"]
assert oc["stats"]["by_category"]["doc"]["count"] == 5       # 统计仍全量
print("  ✓ --only-cat 分批生效(统计仍全量)")

# -- 同名文件泛滥:自动编号封顶后交人工,且不崩(回归:曾因 tuple 传给 basename 崩溃)
coll = run(target_root=os.environ["FIX_COLL"])
cs = coll["stats"]
assert cs["files"] == 12 and cs["conflicts"] == 11, cs
overflow = [i for i in coll["issues"]
            if not i.get("is_dir") and i["target"] is None]
assert len(overflow) == 1, overflow           # 第 12 个撞名 → 不再自动编号
assert overflow[0]["action"] == "review" and overflow[0]["confidence"] == "low"
assert any("同名文件超过" in x for x in overflow[0]["problems"]), overflow[0]
# 建议名里带的是该文件自己的来源目录(不依赖遍历顺序)
origin = overflow[0]["path"].split("/")[0]
assert any(f"{origin}_x.jpg" in x for x in overflow[0]["problems"]), overflow[0]
renamed = [i for i in coll["issues"]
           if not i.get("is_dir") and i["target"] and "__" in i["target"]]
assert len(renamed) == 10, len(renamed)       # x__2 .. x__11
assert cs["to_move"] == 1, cs                 # 第一个正常归档
print("  ✓ 同名文件泛滥:自动编号封顶 + 溢出交人工命名")

# -- 合规库:零误报
ok = run(target_root=lib)
assert ok["count"] == 0, ok["issues"]
assert ok["stats"]["compliant"] == 6, ok["stats"]
assert ok["stats"]["to_move"] == 0 and ok["stats"]["to_review"] == 0
assert ok["stats"]["project_dirs"] == 0, ok["stats"]
print("  ✓ 合规库 fixture:0 误报(中英文目录名都认)")
PYEOF

echo "=== TEST 5: --help ==="
"$PY" "$SKILL_DIR/file_sorter.py" --help >/dev/null
"$PY" "$SKILL_DIR/file_sorter.py" scan --help | grep -q -- "--split-project-dirs"
"$PY" "$SKILL_DIR/file_sorter.py" scan --help | grep -q -- "--keep-dir"
"$PY" "$SKILL_DIR/file_sorter.py" scan --help | grep -q -- "--layout"
echo "  ✓ CLI help 可用"

echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/filesorter-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/file_sorter.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/file_sorter.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
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
if ! echo "$out_ascii" | grep -q "图纸\|文件"; then
  echo "  ⚠️ 输出里看不到中文(可能被替换),但退出码一致,不阻断"
fi
echo "  ✓ ascii stdout 下行为与 utf-8 一致(exit=$rc_ascii),中文不崩"

echo ""
echo "🎉 所有 smoke test 通过"
