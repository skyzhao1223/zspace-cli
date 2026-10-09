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

# --- 繁体目录名也要认(否则会给繁体库另建一套简体类别目录 = 重复分类)
for trad, cat in (("圖紙", "cad"), ("文檔", "doc"), ("視頻", "video"),
                  ("圖片", "image"), ("壓縮包", "archive"), ("安裝包", "installer"),
                  ("字體", "font"), ("代碼", "code"), ("備份", "backup"),
                  ("電子書", "ebook"), ("種子", "torrent"), ("待分類", "other"),
                  ("截圖", "image"), ("音樂", "audio"), ("施工圖", "cad")):
    assert fs.ancestor_cat([trad]) == cat, (trad, cat)

# --- 撞名判定的归一化:大小写不敏感的文件系统上要折叠
class _Fake(fs.Sorter):
    def __init__(self):        # 不碰文件系统的裸壳,只测纯逻辑
        self._ci = None
        self.root = None
_fake = _Fake()
_fake._ci = False
assert _fake._norm("图片/X.jpg") == "图片/X.jpg"
assert _fake._norm("图片/X.jpg") != _fake._norm("图片/x.jpg")
_fake._ci = True
assert _fake._norm("图片/X.jpg") == _fake._norm("图片/x.jpg")

# --- 目标目录被同名文件占着要能查出来(mkdir 会 File exists)
_fake._ci = False
assert _fake._blocked_by_file("图纸/平面.dwg", {"图纸"}) == "图纸"
assert _fake._blocked_by_file("图纸/2024/平面.dwg", {"图纸"}) == "图纸"
assert _fake._blocked_by_file("图纸/2024/平面.dwg", {"图纸/2024"}) == "图纸/2024"
assert _fake._blocked_by_file("图纸/平面.dwg", {"文档"}) is None
assert _fake._blocked_by_file("图纸/平面.dwg", set()) is None

# --- shell 不友好的文件名(计划会被 Agent 拼成命令执行,必须警告)
assert fs.shell_risk("-f.pdf") is not None and "- 开头" in fs.shell_risk("-f.pdf")
assert fs.shell_risk("--force.jpg") is not None
assert fs.shell_risk("-i.dwg") is not None
for bad, why in (('a"b.pdf', "双引号"), ("a`b.pdf", "反引号"), ("a$b.pdf", "$"),
                 ("a\\b.pdf", "反斜杠"), ("a\nb.pdf", "换行")):
    r = fs.shell_risk(bad)
    assert r is not None, bad
    assert why in r, (bad, r)
# 正常名字(含空格与中文)不该误报 —— 空格用双引号就够了,报多了会被忽略
assert fs.shell_risk("with space.pdf") is None
assert fs.shell_risk("正常文件.dwg") is None
assert fs.shell_risk("IMG_0001.jpg") is None
assert fs.shell_risk("a-b.pdf") is None          # 中间的 - 不是选项
assert fs.shell_risk("") is None

print("  ✓ 纯函数用例通过(含繁体别名 / 大小写归一 / 目录撞文件 / shell 危险名)")
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

# --- 目标目录被同名「文件」占着(mkdir 会 File exists)
BLOCK="$FIXTURE/blocked"
mkdir -p "$BLOCK"
printf 'x' > "$BLOCK/图纸"          # 无扩展名的**文件**,恰好叫 图纸
printf 'y' > "$BLOCK/平面.dwg"      # 要搬进 图纸/ → 冲突
printf 'z' > "$BLOCK/合同.pdf"      # 对照组:文档/ 没被占,应正常 move

# --- --root 本身就是类别目录(不该在里面再套一层 图纸/图纸/)
# 目录名必须真的叫「图纸」:修复是按 root 的 basename 判定的
ROOTCAT="$FIXTURE/图纸库-root"
mkdir -p "$ROOTCAT/图纸"
printf 'a' > "$ROOTCAT/图纸/平面.dwg"
printf 'b' > "$ROOTCAT/图纸/立面.dxf"

# --- 繁体目录名(不该被当成未分类而另建简体目录)
TRAD="$FIXTURE/trad"
mkdir -p "$TRAD/圖紙" "$TRAD/文檔" "$TRAD/視頻"
printf 'a' > "$TRAD/圖紙/平面.dwg"
printf 'b' > "$TRAD/文檔/合同.pdf"
printf 'c' > "$TRAD/視頻/宣传.mp4"

# --- shell 不友好的文件名(- 开头 / 含 $ 与引号):计划必须带警告
RISK="$FIXTURE/risky"
mkdir -p "$RISK"
printf 'x' > "$RISK/-f.pdf"
printf 'x' > "$RISK/--force.jpg"
printf 'x' > "$RISK/price\$100.xlsx"
printf 'x' > "$RISK/正常.dwg"        # 对照组:不该被警告

# --- 大小写撞名(X.jpg / x.jpg 在 macOS+Windows 上是同一路径)
CASE="$FIXTURE/casefold"
mkdir -p "$CASE/a" "$CASE/b"
printf 'xxx' > "$CASE/a/X.jpg"
printf 'yyy' > "$CASE/b/x.jpg"

FS_SKILL_DIR="$SKILL_DIR" FIX_ROOT="$ROOT" FIX_LIB="$LIB" FIX_COLL="$FIXTURE/collide" \
  FIX_BLOCK="$BLOCK" FIX_ROOTCAT="$ROOTCAT/图纸" FIX_TRAD="$TRAD" FIX_CASE="$CASE" \
  FIX_RISK="$RISK" \
  "$PY" - <<'PYEOF'
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
        encoding="utf-8", errors="replace",
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

# -- 疑似副本:指向不带标记的本尊,引导去 dedup-finder,并给出字节数让人自己判断
d = by_path["合同 副本.pdf"]
assert any("dedup-finder" in x for x in d["problems"]), d["problems"]
assert any("合同.pdf" in x for x in d["problems"]), d["problems"]
# 不再断言"必须先删再搬":顺序不影响正确性,措辞里要说清这点
assert any("先分类也不会漏检" in x for x in d["problems"]), d["problems"]
assert not any("确认后再搬" in x for x in d["problems"]), d["problems"]
assert d["action"] == "move"        # 仍给搬运目标,由 LLM 决定先去重
# dup_suspect_bytes = 被标记的那个文件大小('contract-1234' = 13B),
# 让人按字节数判断值不值得先跑一趟去重,而不是背一条规则
assert s["dup_suspect_bytes"] == 13, s["dup_suspect_bytes"]
assert s["dup_suspect_bytes"] <= s["move_bytes"], s

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

# -- C: 目标目录被同名「文件」占着 → 不给执行不下去的计划
bl = run(target_root=os.environ["FIX_BLOCK"])
blp = {i["path"]: i for i in bl["issues"] if not i.get("is_dir")}
assert bl["stats"]["dir_file_conflicts"] == 1, bl["stats"]
assert blp["平面.dwg"]["action"] == "review", blp["平面.dwg"]
assert blp["平面.dwg"]["confidence"] == "low"
assert any("mkdir 会失败" in x for x in blp["平面.dwg"]["problems"]), blp["平面.dwg"]
assert blp["合同.pdf"]["action"] == "move"      # 对照组:文档/ 没被占,照常搬
assert blp["图纸"]["action"] == "review"        # 那个占位的文件本身归待分类
print("  ✓ 目标目录被同名文件占着:降级人工确认(旧版会给出 mkdir 必败的计划)")

# -- F: --root 本身就是类别目录 → 不该在里面再套一层
rc = run(target_root=os.environ["FIX_ROOTCAT"])
assert rc["stats"]["root_category"] == "cad", rc["stats"]
assert rc["stats"]["compliant"] == 2 and rc["stats"]["to_move"] == 0, rc["stats"]
assert rc["count"] == 0, rc["issues"]
print("  ✓ --root 就是「图纸」目录时:视为已就位,不套 图纸/图纸/")

# -- G: 繁体目录名 → 不该被当成未分类而另建简体目录
tr = run(target_root=os.environ["FIX_TRAD"])
assert tr["stats"]["compliant"] == 3 and tr["stats"]["to_move"] == 0, tr["stats"]
assert tr["count"] == 0, tr["issues"]
print("  ✓ 繁体目录名(圖紙/文檔/視頻)认得,不会另建简体目录造成重复分类")

# -- E: 大小写撞名。按文件系统**实际**行为断言,所以在 敏感/不敏感 两种 CI 上都成立
cf = run(target_root=os.environ["FIX_CASE"])
ci = cf["stats"]["case_insensitive_fs"]
tgts = [i["target"] for i in cf["issues"] if i.get("target")]
if ci:
    # macOS APFS / Windows NTFS:X.jpg 与 x.jpg 是**同一路径**,
    # 折叠后不得重复,否则执行时第二条 mv -n 会静默不搬
    assert len(set(t.lower() for t in tgts)) == len(tgts), tgts
    assert cf["stats"]["conflicts"] == 1, cf["stats"]
    assert cf["stats"]["to_move"] == 1 and cf["stats"]["to_review"] == 1, cf["stats"]
else:
    # Linux ext4 等大小写敏感文件系统:两者是合法的不同目标,不该判冲突。
    # (这条分支在 macOS 上跑不到 —— 由 ubuntu CI 覆盖,两边合起来才是完整验证)
    assert len(set(tgts)) == len(tgts), tgts
    assert cf["stats"]["conflicts"] == 0, cf["stats"]
    assert cf["stats"]["to_move"] == 2, cf["stats"]
print(f"  ✓ 大小写撞名按文件系统实况判定(本机 case_insensitive={ci})")

# -- shell 不友好的文件名:仍给 target,但必须带警告(否则 Agent 拼出的命令会失败)
rk = run(target_root=os.environ["FIX_RISK"])
rkp = {i["path"]: i for i in rk["issues"] if not i.get("is_dir")}
assert rk["stats"]["shell_unsafe_names"] == 3, rk["stats"]["shell_unsafe_names"]
for bad in ("-f.pdf", "--force.jpg", "price$100.xlsx"):
    assert bad in rkp, sorted(rkp)
    assert any("不能直接拼进命令" in x for x in rkp[bad]["problems"]), rkp[bad]
    assert rkp[bad]["target"], bad          # 仍给目标路径,只是附警告
assert "- 开头" in " ".join(rkp["-f.pdf"]["problems"])
assert "变量展开" in " ".join(rkp["price$100.xlsx"]["problems"])
# 对照组:正常名字不该被警告
assert not any("shell" in x or "拼进命令" in x for x in rkp["正常.dwg"]["problems"]), \
    rkp["正常.dwg"]
assert rkp["正常.dwg"]["action"] == "move"
print("  ✓ shell 危险文件名:带警告且不误伤正常名字(含空格/中文)")

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

echo "=== TEST 7: config.json 覆盖层(issue #15) ==="
# config.json 按**脚本自己所在目录**(__file__)解析 —— 不是 cwd,也不是被扫描的
# root。所以这里把脚本复制进一个临时目录,模拟「zs skill 装好之后」的样子;顺便
# 也就证明了放在被扫描目录里的 config.json 不会被误读。
CFG_SRC="$(mktemp -d /tmp/filesorter-cfg.XXXXXX)"
mkdir -p "$CFG_SRC/installed" "$CFG_SRC/lib"
cp "$SKILL_DIR/file_sorter.py" "$CFG_SRC/installed/"
FS_CFG_SRC="$CFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
from pathlib import Path

src = os.environ["FS_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "file_sorter.py")
cfg = os.path.join(installed, "config.json")
lib = os.path.join(src, "lib")

# ---- fixture:有意的例外结构(issue #15 点名的 原盘/VIDEO_TS)+ 归类边界 ----
# 原盘子树刻意只放一种类别(.VOB → other),否则 3 文件跨 2 类会被判成「疑似
# 项目目录」,那些文件就不出单独 issue 了,断言会测到别的东西上去。
for d in ("原盘/VIDEO_TS", "深层/原盘/子目录", "samples", "影视/Season 1"):
    os.makedirs(os.path.join(lib, d), exist_ok=True)
FILES = {
    "平面.dwg": "dwg-bytes",
    "合同.pdf": "pdf-bytes",
    "movie.ass": "ass-bytes",      # 内置判 doc;字幕其实该跟着视频走
    "movie.srt": "srt-bytes",      # 对照组:只改 .ass 时它必须仍是 doc
    "mystery.xyz": "xyz-bytes",    # 内置判 other(review / low)
    "build.log": "log-bytes",      # 内置判 junk(delete-confirm)
    "原盘/VIDEO_TS/VTS_01_1.VOB": "vob-1",
    "原盘/VIDEO_TS/VTS_01_2.VOB": "vob-2",
    "原盘/散落.VOB": "vob-3",       # 直接在 原盘/ 下,不在 VIDEO_TS/ 里
    "深层/原盘/子目录/a.jpg": "jpg-deep",
    "samples/sample.mkv": "mkv-sample",
    "影视/Season 1/E01.mp4": "mp4-ok",   # 影视 是内置 video 别名 → 已就位
}
for rel, body in FILES.items():
    with open(os.path.join(lib, rel), "w", encoding="utf-8") as fh:
        fh.write(body)


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


def paths(d):
    return {i["path"]: i for i in d["issues"]}


def shape(d):
    """与时间无关的 issue 摘要(age_days 会随运行时刻漂,不能直接比 issues)。"""
    return sorted((i["path"], i.get("category"), i["action"], i.get("target"))
                  for i in d["issues"])


# -- 0. 基线:没有 config.json 时,stats 里连 config 这个键都不该出现 ---------
drop_cfg()
base, base_err = scan()
bp = paths(base)
assert "config" not in base["stats"], base["stats"].get("config")
assert "已加载覆盖配置" not in base_err, base_err
assert base["count"] == 11, base["count"]
assert base["stats"]["files"] == 12 and base["stats"]["protected"] == 0
assert base["stats"]["project_dirs"] == 0, base["stats"]
assert bp["movie.ass"]["category"] == "doc", bp["movie.ass"]
assert bp["movie.srt"]["category"] == "doc"
assert bp["mystery.xyz"]["category"] == "other"
assert bp["mystery.xyz"]["action"] == "review"
assert bp["build.log"]["category"] == "junk"
assert bp["build.log"]["action"] == "delete-confirm"
assert bp["原盘/VIDEO_TS/VTS_01_1.VOB"]["category"] == "other"
assert "影视/Season 1/E01.mp4" not in bp        # 已就位
assert base["stats"]["compliant"] == 1
print("  ✓ 无 config.json:stats 无 config 键、stderr 无提示、11 条计划与旧版一致")

# -- 1. extension_overrides:把 .ass 从 doc 挪到 video ----------------------
write_cfg(json.dumps({"extension_overrides": {"ass": "video"}}))
d, err = scan()
dp = paths(d)
assert dp["movie.ass"]["category"] == "video", dp["movie.ass"]
assert dp["movie.ass"]["target"] == "视频/movie.ass", dp["movie.ass"]["target"]
assert d["stats"]["by_category"]["video"]["count"] == 3      # mp4 + mkv + ass
assert d["stats"]["by_category"]["doc"]["count"] == 2        # pdf + srt
# 逐扩展名改判,不是整表替换:没写到的 .srt 必须还是 doc
assert dp["movie.srt"]["category"] == "doc", dp["movie.srt"]
# 内置条目一条都没丢:.dwg 仍是 cad,影视/ 仍被认作 video 别名目录
assert dp["平面.dwg"]["category"] == "cad"
assert d["stats"]["compliant"] == 1, d["stats"]
assert "已加载覆盖配置" in err, err
assert d["stats"]["config"]["extension_overrides"] == {"ass": "video"}
assert d["stats"]["config"]["whitelist_dirs"] == []
assert d["stats"]["config"]["path"].endswith("config.json")
print("  ✓ extension_overrides 改判 .ass → 视频/,且只动这一个扩展名")

# -- 2. 键归一化(前导点 / 大小写)+ 把 junk 救回来 -------------------------
write_cfg(json.dumps({"extension_overrides": {".XYZ": "cad", "LOG": "doc"}}))
d, _ = scan()
dp = paths(d)
assert dp["mystery.xyz"]["category"] == "cad", dp["mystery.xyz"]
assert dp["mystery.xyz"]["target"] == "图纸/mystery.xyz"
assert dp["mystery.xyz"]["action"] == "move"           # 不再是 review
assert dp["mystery.xyz"]["confidence"] == "high"
assert dp["build.log"]["category"] == "doc", dp["build.log"]
assert dp["build.log"]["action"] == "move"             # 不再是 delete-confirm
assert dp["build.log"]["target"] == "文档/build.log"
assert d["stats"]["to_delete"] == 0, d["stats"]
print("  ✓ 键归一化(.XYZ/LOG 都认)+ junk 可以被救回成正常类别")

# -- 3. whitelist_dirs:模式锚定在 --root(整段相对路径 或 任一级目录名)-----
write_cfg(json.dumps({"whitelist_dirs": ["原盘/*", "samples"]}))
d, _ = scan()
dp = paths(d)
assert "原盘/VIDEO_TS/VTS_01_1.VOB" not in dp, sorted(dp)
assert "原盘/VIDEO_TS/VTS_01_2.VOB" not in dp, sorted(dp)
assert "samples/sample.mkv" not in dp                 # 裸名字 = 任意深度同名目录
assert d["stats"]["protected"] == 3, d["stats"]["protected"]
# 锚定边界:原盘/* 命中 原盘/VIDEO_TS,但**不**命中 原盘 自己这一层
assert "原盘/散落.VOB" in dp, sorted(dp)
# 也不命中「深层/原盘/子目录」—— 相对路径不是以 原盘/ 开头的
assert "深层/原盘/子目录/a.jpg" in dp, sorted(dp)
# 白名单只作用于目录:直接躺在 --root 下的文件不受影响
assert "平面.dwg" in dp and "合同.pdf" in dp
assert d["stats"]["root_files"] == 6, d["stats"]
assert d["stats"]["config"]["whitelist_dirs"] == ["原盘/*", "samples"]
print("  ✓ whitelist_dirs 生效;原盘/* 命中 原盘/VIDEO_TS 但不命中 原盘 自身")

# -- 3b. 裸名字 原盘 → 整棵子树都保住(与 3 形成对照,证明锚定不是糊的)-----
# 裸名字走的是「任一级目录名」那条规则,所以它在**任意深度**都命中 ——
# 深层/原盘/子目录/ 也算。这正是 3(原盘/* 只认 root 第一层)的对照组:
# 两条规则给出不同的 protected 数,说明匹配不是「路径里出现过就算」。
write_cfg(json.dumps({"whitelist_dirs": ["原盘"]}))
d, _ = scan()
dp = paths(d)
assert d["stats"]["protected"] == 4, d["stats"]["protected"]
assert not [p for p in dp if p.startswith("原盘")], sorted(dp)
assert "深层/原盘/子目录/a.jpg" not in dp, sorted(dp)   # 任意深度的同名目录也命中
assert "samples/sample.mkv" in dp                      # 没写的目录不受影响
print("  ✓ 裸名字 原盘 在任意深度命中整棵子树(3+1=4),与 原盘/* 的行为可区分")

# -- 4. 带 / 的模式锚定在 root:深层/原盘/* 只命中那一条 --------------------
write_cfg(json.dumps({"whitelist_dirs": ["深层/原盘/*"]}))
d, _ = scan()
dp = paths(d)
assert "深层/原盘/子目录/a.jpg" not in dp, sorted(dp)
assert d["stats"]["protected"] == 1, d["stats"]["protected"]
assert "原盘/VIDEO_TS/VTS_01_1.VOB" in dp     # 反过来不命中
print("  ✓ 深层/原盘/* 只命中 root 下那一条路径,不波及同名的 原盘/")

# -- 4b. 大小写不敏感必须**双向**成立 --------------------------------------
# 只测「目录大写 / 模式小写」是测不出来的:实现里相对路径总是先 .lower(),
# 所以那个方向即使忘了给模式做 lower 也照样过。反方向才是真的判据。
write_cfg(json.dumps({"whitelist_dirs": ["SAMPLES"]}))
d, _ = scan()
assert "samples/sample.mkv" not in paths(d), sorted(paths(d))
assert d["stats"]["protected"] == 1, d["stats"]["protected"]
write_cfg(json.dumps({"whitelist_dirs": ["samples"]}))
d, _ = scan()
assert d["stats"]["protected"] == 1, d["stats"]["protected"]
print("  ✓ 大小写不敏感双向成立:SAMPLES 与 samples 都命中小写的 samples/")

# -- 5. 与 --keep-dir 合并(追加,不是替换),两条来源同时生效 ---------------
# samples 来自 config,原盘 来自命令行;裸名字 原盘 顺带命中 深层/原盘/子目录,
# 所以是 1 + 3 + 1 = 5。少了任何一条来源都会掉到 4 或 1。
write_cfg(json.dumps({"whitelist_dirs": ["samples"]}))
d, _ = scan("--keep-dir", "原盘")
dp = paths(d)
assert "samples/sample.mkv" not in dp, sorted(dp)
assert not [p for p in dp if p.startswith("原盘")], sorted(dp)
assert "深层/原盘/子目录/a.jpg" not in dp, sorted(dp)
assert d["stats"]["protected"] == 5, d["stats"]["protected"]
# 反向对照:只留 config 那一条,--keep-dir 那条必须真的消失
d2, _ = scan()
assert d2["stats"]["protected"] == 1, d2["stats"]["protected"]
print("  ✓ config 的 whitelist_dirs 与 --keep-dir 追加合并(1+3+1=5),缺一即掉")

# -- 6. "*" 也不能白名单掉 root 下的散文件(与 --keep-dir 语义一致)---------
# 6 = 原盘子树 3 + 深层/原盘/子目录 1 + samples 1 + 影视/Season 1 1。
# 最后那个原本是「已就位」(compliant),被白名单抢走了 —— protected 在判定阶梯
# 里排在 compliant 前面,这是既有行为,这里顺带钉住它。
write_cfg(json.dumps({"whitelist_dirs": ["*"]}))
d, _ = scan()
dp = paths(d)
assert d["stats"]["protected"] == 6, d["stats"]["protected"]
assert d["stats"]["compliant"] == 0, d["stats"]["compliant"]
for root_file in ("平面.dwg", "合同.pdf", "movie.ass", "movie.srt",
                  "mystery.xyz", "build.log"):
    assert root_file in dp, root_file
assert d["stats"]["root_files"] == 6, d["stats"]
print("  ✓ 白名单只作用于目录:* 也管不到直接躺在 root 下的 6 个文件")

# -- 7. 空对象 {}:加载并提示,但行为与无配置逐条相同 ----------------------
write_cfg("{}")
d, err = scan()
assert shape(d) == shape(base), (shape(d), shape(base))
assert "config" in d["stats"]
assert d["stats"]["config"]["whitelist_dirs"] == []
assert d["stats"]["config"]["extension_overrides"] == {}
assert "已加载覆盖配置" in err, err
print("  ✓ 空对象 {} 加载成功:计划与无配置时逐条相同,但会明确提示已加载")

# -- 8. 配置文件在,但某个键不在 → 那个键不产生任何影响 --------------------
write_cfg(json.dumps({"whitelist_dirs": ["samples"]}))
d, _ = scan()
dp = paths(d)
assert dp["movie.ass"]["category"] == "doc", dp["movie.ass"]
assert "samples/sample.mkv" not in dp
assert d["stats"]["config"]["extension_overrides"] == {}
print("  ✓ 只有 whitelist_dirs 时,extension_overrides 缺省 = 不改判任何扩展名")

# -- 9. 被扫描目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)----
drop_cfg()
root_cfg = os.path.join(lib, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"extension_overrides": {"dwg": "doc"},
               "whitelist_dirs": ["*"]}, fh, ensure_ascii=False)
for cwd in (src, lib, installed):        # 三种 cwd 都不能让它被读到
    d, err = scan(cwd=cwd)
    dp = paths(d)
    assert dp["平面.dwg"]["category"] == "cad", (cwd, dp["平面.dwg"])
    assert dp["平面.dwg"]["target"] == "图纸/平面.dwg", (cwd, dp["平面.dwg"])
    assert d["stats"]["protected"] == 0, (cwd, d["stats"])
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
# 它只是被当成一个普通 .json 文件扫到了 —— 这正好证明它是数据、不是配置
d, _ = scan()
assert paths(d)["config.json"]["category"] == "code", paths(d)["config.json"]
os.remove(root_cfg)
print("  ✓ 被扫描目录里的 config.json 被忽略(3 种 cwd 都试过),只当普通文件扫")

# -- 10. 畸形配置:一律 exit=1,指名文件与 offending 键,且不污染 --json stdout
BAD = [
    ("非法 JSON", "{not json", "不是合法 JSON"),
    ("空文件", "", "不是合法 JSON"),
    ("顶层不是对象", "[]", "顶层"),
    ("未知键(少写一个 s)", '{"whitelist_dir": ["原盘"]}', "whitelist_dir"),
    ("whitelist_dirs 不是数组", '{"whitelist_dirs": "原盘"}', "whitelist_dirs"),
    ("whitelist_dirs 元素不是字符串", '{"whitelist_dirs": [1]}',
     "whitelist_dirs[0]"),
    ("whitelist_dirs 空字符串", '{"whitelist_dirs": ["  "]}', "whitelist_dirs[0]"),
    ("whitelist_dirs 绝对路径", '{"whitelist_dirs": ["/原盘"]}', "绝对路径"),
    ("whitelist_dirs Windows 盘符", '{"whitelist_dirs": ["Z:\\\\data"]}',
     "绝对路径"),
    ("extension_overrides 不是对象", '{"extension_overrides": ["ass"]}',
     "extension_overrides"),
    ("类别值不是字符串", '{"extension_overrides": {"ass": 3}}',
     "必须是字符串类别名"),
    ("类别名不存在", '{"extension_overrides": {"ass": "subtitle"}}', "subtitle"),
    ("扩展名含多个点", '{"extension_overrides": {"tar.gz": "archive"}}',
     "tar.gz"),
    ("扩展名归一化后为空", '{"extension_overrides": {".": "doc"}}', "空的"),
    ("两个键都拼错", '{"whitelist_dir": [], "extension_override": {}}',
     "extension_override"),
]
for label, text, needle in BAD:
    write_cfg(text)
    r = run(expect=1)
    assert r.stdout == "", (label, "错误不能污染 --json 的 stdout", r.stdout[:200])
    assert "config.json" in r.stderr, (label, r.stderr)
    assert needle in r.stderr, (label, needle, r.stderr)
    assert "❌" in r.stderr, (label, r.stderr)
drop_cfg()
print(f"  ✓ {len(BAD)} 种畸形配置全部 exit=1、只写 stderr、指名文件与键")

# -- 11. 未知键报错里要列出可用键(否则用户不知道正确拼法)----------------
write_cfg('{"whitelist_dir": ["原盘"]}')
r = run(expect=1)
assert "whitelist_dirs" in r.stderr, r.stderr          # 提示正确拼法
assert "extension_overrides" in r.stderr, r.stderr
drop_cfg()
print("  ✓ 未知键的报错里列出了可用键名(照着改就能修)")

# -- 12. 校验先于写入:半途失败的配置不得留下副作用(进程内直接考)--------
sys.path.insert(0, installed)
import contextlib                                          # noqa: E402
import io                                                  # noqa: E402
import file_sorter as fs                                   # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "whitelist_dirs": ["samples"],
    "extension_overrides": {"ass": "nope"},
}, ensure_ascii=False), encoding="utf-8")
before_doc = set(fs.DOC_EXTS)
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        fs.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "nope" in str(e), str(e)
    assert "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()   # 失败路径不该先打印「已加载」
assert fs.CONFIG_WHITELIST == [], fs.CONFIG_WHITELIST     # 白名单没被半路写进去
assert fs.CONFIG_INFO == {}, fs.CONFIG_INFO
assert set(fs.DOC_EXTS) == before_doc, "扩展名表被改了一半"

# 同一进程里再加载一份好的,确认没被上一次的失败污染
good_dir = Path(src) / "good"
good_dir.mkdir(exist_ok=True)
(good_dir / "config.json").write_text(json.dumps({
    "whitelist_dirs": [" 原盘 "],
    "extension_overrides": {"ass": "video"},
}), encoding="utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    fs.load_config(good_dir)
assert fs.CONFIG_WHITELIST == ["原盘"], fs.CONFIG_WHITELIST   # 前后空白被 strip
assert "ass" in fs.VIDEO_EXTS and "ass" not in fs.DOC_EXTS
assert fs.categorize("movie.ass", "ass") == "video"
assert fs.categorize("movie.srt", "srt") == "doc"             # 其余扩展名不受影响
assert fs.CONFIG_INFO["path"] == str(good_dir / "config.json"), fs.CONFIG_INFO
# 「配置到底生效没有」必须有据可查:成功时打印一行,含文件路径与两个键的条数
msg = err.getvalue()
assert "已加载覆盖配置" in msg and str(good_dir / "config.json") in msg, msg
assert "whitelist_dirs 1 条" in msg and "extension_overrides 1 条" in msg, msg
print("  ✓ 校验先于写入:坏配置退出后内置表与 CONFIG_* 全都没动,也不打印「已加载」")

# -- 13. 锚定规则的纯函数证据(这是 SKILL.md 那张表的来源)----------------
m = fs._cfg_match
assert m(["原盘", "VIDEO_TS"], ["原盘/*"])              # 整段相对路径命中
assert m(["原盘", "VIDEO_TS"], ["原盘"])                # 任一级目录名命中
assert not m(["原盘"], ["原盘/*"])                      # 原盘/* 不命中 原盘 自身
assert not m(["深层", "原盘", "子目录"], ["原盘/*"])     # 不在 root 第一层就不算
assert m(["深层", "原盘", "子目录"], ["深层/原盘/*"])
assert m(["深层", "原盘", "子目录"], ["原盘"])           # 裸名字命中任意深度
assert m(["影视", "Season 1"], ["影视/*"])              # 含空格的中文路径
assert m(["SAMPLES"], ["samples"])                      # 大小写不敏感(目录大写)
assert m(["samples"], ["SAMPLES"])                      # 反方向:模式大写
assert m(["原盘", "video_ts"], ["原盘/VIDEO_TS"])        # 整段路径也要双向不敏感
assert m(["Samples", "Sub"], ["SAMPLES/*"])             # 带通配时同样双向
assert m(["原盘"], ["*"])                               # * 跨 / 匹配
assert not m([], ["*"])                                 # root 下的散文件不吃白名单
assert not m(["原盘"], [])                              # 没有模式 = 不命中
assert not m([], [])
print("  ✓ _cfg_match 锚定规则:15 条纯函数断言(与 --keep-dir 同一个函数)")
PYEOF
rm -rf "$CFG_SRC"

echo "=== TEST 8: FIFO/socket 不当文件(家族审计 F8)==="
# F8:_walk 末尾曾是裸 else → _collect(...),把 FIFO 与 socket 也当普通文件收进
# stats,还给出行动计划 —— 名叫 capture.jpg 的 FIFO 拿到 category=image
# action=move 的**移动计划**,而 file-sorter 是家族里输出直接被 Agent 拿去 mv
# 执行的那个。其余 8 家都是 elif entry.is_file()。对全常规文件的树,本改动
# 零输出差异(TEST 4 的既有断言即负控制)。
FS_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

skill = os.environ["FS_SKILL_DIR"]
script = f"{skill}/file_sorter.py"

if not hasattr(os, "mkfifo") or not hasattr(socket, "AF_UNIX"):
    print("  ⊘ F8 在 Windows 上跳过(无 FIFO/AF_UNIX socket);常规文件行为由 TEST 4 钉住")
else:
    root = tempfile.mkdtemp(prefix="fs-f8.")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with open(os.path.join(root, "notes.txt"), "wb") as fh:
            fh.write(b"N" * 1000)
        os.mkfifo(os.path.join(root, "capture.jpg"))
        s.bind(os.path.join(root, "live.sock"))
        r = subprocess.run([sys.executable, script, "scan", "--root", root, "--json"],
                           capture_output=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, (r.returncode, r.stderr[-400:])
        d = json.loads(r.stdout)
        st = d["stats"]
        blob = json.dumps(d, ensure_ascii=False)
        # 修复前:files=3,capture.jpg 领 category=image action=move,live.sock 领 review
        assert st["files"] == 1, st
        assert "capture.jpg" not in blob, blob[:300]
        assert "live.sock" not in blob, blob[:300]
        paths = [i["path"] for i in d["issues"]]
        assert paths == ["notes.txt"], paths
        print("  ✓ FIFO/socket 不入统计、不给行动计划:files=1,只剩 notes.txt")
    finally:
        s.close()
        shutil.rmtree(root, ignore_errors=True)
PYEOF

echo ""
echo "🎉 所有 smoke test 通过"
