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
    encoding="utf-8", errors="replace",
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
echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/portfolioo-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/portfolio_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/portfolio_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
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
PFCFG_SRC="$(mktemp -d /tmp/portfolioorg-cfg.XXXXXX)"
mkdir -p "$PFCFG_SRC/installed" "$PFCFG_SRC/lib"
cp "$SKILL_DIR/portfolio_organizer.py" "$PFCFG_SRC/installed/"
PF_CFG_SRC="$PFCFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
from pathlib import Path

src = os.environ["PF_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "portfolio_organizer.py")
cfg = os.path.join(installed, "config.json")
lib = os.path.join(src, "lib")

# ---- fixture:项目目录、成品 vs 源文件、大文件、内置功能目录、大小写 ----
FILES = [
    "2023_品牌视觉/cover.jpg",                       # 封面
    "2023_品牌视觉/README.md",                       # 说明
    "2023_品牌视觉/logo.psd",                        # 顶层源文件
    "2023_品牌视觉/logo.png",                        # 顶层成品
    "2023_品牌视觉/成品/海报_v1.jpg",
    "2023_品牌视觉/成品/海报_v2.jpg",
    "2023_品牌视觉/成品/海报_final.jpg",              # → 成品多版本共存(3)
    "2023_品牌视觉/成品/源文件.psd",                  # → 成品目录混入源文件
    "2023_品牌视觉/源文件/main.ai",                   # → with_source_dir
    "2022_网站/index.html",
    "2022_网站/site.fig",                            # 无成品目录 → 成品与源文件混放
    "2022_网站/说明.txt",
    "练习稿/logo.sketch",                             # 缺年份/封面/说明(glob 的目标)
    "练习稿/out.mp4",
    "素材库/texture.png",                             # 内置 WHITELIST_DIRS
    "素材库/brush.abr",                               # 内置 WHITELIST_DIRS
    "templates/cover.png",                            # 内置 WHITELIST_DIRS(英文)
    "2024_影视/big.c4d",                              # 大体积源文件(--large-gb)
    "2024_影视/cover 预览.jpg",                       # COVER_PAT 命中「预览」
    "2024_影视/说明.txt",
    "DRAFTS/x.psd",                                   # 大写目录名(大小写双向)
    "wip/notes.psd",                                  # 小写目录名(大小写双向)
    "空格 项目 2020/cover.png",                       # 含空格 + 靠 png 拿到封面
    "空格 项目 2020/交付/终稿.pdf",                   # 交付 ∈ FINAL_DIR_NAMES
    "loose_root.png", "lops.psd",
    ".DS_Store", "._cover.jpg",                       # 垃圾 / AppleDouble
    "临时.bak",                                       # JUNK_EXTS
    "-leading-dash.ai",                               # shell 危险名(前导 -)
    "没有扩展名",
]
for rel in FILES:
    p = Path(lib) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x" * (5000 if "big.c4d" in rel else 1), encoding="utf-8")
os.makedirs(os.path.join(lib, "空项目"), exist_ok=True)   # 空项目目录


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


def proj_dirs(d):
    return sorted(i["path"] for i in d["issues"] if i["is_dir"])


# -- 0. 基线:没有 config.json 时,stats 里连 config 这个键都不该出现 ---------
drop_cfg()
base, base_err = scan()
s = base["stats"]
assert "config" not in s, s.get("config")
assert "已加载覆盖配置" not in base_err, base_err
assert s["projects"] == 8 and s["files"] == 28 and s["dirs"] == 13, s
assert s["with_cover"] == 3 and s["with_readme"] == 3, s
assert s["with_final_dir"] == 2 and s["with_source_dir"] == 1, s
assert s["junk"] == 3 and s["loose_root"] == 4, s
assert s["source_files"] == 8 and s["export_files"] == 10, s
assert base["count"] == 16, base["count"]
assert proj_dirs(base) == sorted(["2022_网站", "2024_影视", "DRAFTS", "wip",
                                 "空格 项目 2020", "空项目", "练习稿"]), proj_dirs(base)
assert kinds(base, "成品目录混入源文件") == ["2023_品牌视觉/成品/源文件.psd"]
assert kinds(base, "成品多版本共存") == ["2023_品牌视觉/海报"]
assert kinds(base, "垃圾/临时文件") == sorted(
    [".DS_Store", "._cover.jpg", "临时.bak"]), kinds(base, "垃圾/临时文件")
# 内置 WHITELIST_DIRS 的目录不算项目
for quiet in ("素材库", "templates"):
    assert quiet not in proj_dirs(base), proj_dirs(base)
assert s["projects"] == 8, s["projects"]
# --large-gb 的基线(下面 psd/c4d 改判要与它对照)
lg_base, _ = scan("--large-gb", "0.000001")
assert lg_base["count"] == 17, lg_base["count"]
assert kinds(lg_base, "大体积源文件") == ["2024_影视/big.c4d"]
print("  ✓ 无 config.json:stats 无 config 键、stderr 无提示、16 条问题与旧版一致")

# -- 1. whitelist_dirs:命中的目录「不算项目」(内置 WHITELIST_DIRS 的语义)--
write_cfg(json.dumps({"whitelist_dirs": ["练习*"]}))
d, err = scan()
assert d["stats"]["projects"] == 7, d["stats"]["projects"]
assert "练习稿" not in proj_dirs(d), proj_dirs(d)
assert d["count"] == 15, d["count"]
assert d["stats"]["source_files"] == 7 and d["stats"]["export_files"] == 9, d["stats"]
assert "已加载覆盖配置" in err, err
assert d["stats"]["config"]["whitelist_dirs"] == ["练习*"]
assert d["stats"]["config"]["extension_overrides"] == {}
# **本 skill 与 photo/music 的关键差别**:白名单只让目录「不算项目」,子树里的文件
# 照扫照计入 stats(与内置 WHITELIST_DIRS 完全相同),所以没有 skipped_files。
assert (d["stats"]["files"], d["stats"]["dirs"]) == (28, 13), d["stats"]
assert "skipped_files" not in d["stats"]["config"], d["stats"]["config"]
assert kinds(d, "垃圾/临时文件") == kinds(base, "垃圾/临时文件")
assert len(kinds(d, "根目录散文件")) == 4, kinds(d, "根目录散文件")
print("  ✓ whitelist_dirs=[练习*]:练习稿 不再算项目(少 1 条),但它的文件仍计入 stats")

# -- 2. 内置 WHITELIST_DIRS 是**追加**,不是替换 ---------------------------
write_cfg(json.dumps({"whitelist_dirs": ["练习*"]}))
d, _ = scan()
for quiet in ("素材库", "templates"):
    assert quiet not in proj_dirs(d), (quiet, proj_dirs(d))
# 它们的文件仍然计入 stats.files(内置机制本来就是「不做项目校验」而非「不扫」)
assert d["stats"]["files"] == 28, d["stats"]["files"]
print("  ✓ whitelist_dirs 是追加:内置的 素材库/ templates/ 加配置后仍然不算项目")

# -- 3. 大小写不敏感必须**双向**成立(端到端)------------------------------
# 只测「目录大写 / 模式小写」测不出来:实现里相对路径总是先 .lower(),那个方向
# 即使忘了给模式做 lower 也照样过。所以两个方向都要跑一遍。
for pat in ("DRAFTS", "drafts", "Drafts"):      # 大写目录 × 三种模式写法
    write_cfg(json.dumps({"whitelist_dirs": [pat]}))
    d, _ = scan()
    assert "DRAFTS" not in proj_dirs(d), (pat, proj_dirs(d))
    assert d["stats"]["projects"] == 7, (pat, d["stats"]["projects"])
for pat in ("wip", "WIP", "Wip"):               # 小写目录 × 三种模式写法
    write_cfg(json.dumps({"whitelist_dirs": [pat]}))
    d, _ = scan()
    assert "wip" not in proj_dirs(d), (pat, proj_dirs(d))
    assert d["stats"]["projects"] == 7, (pat, d["stats"]["projects"])
print("  ✓ 大小写不敏感双向成立:drafts 命中 DRAFTS/,WIP 命中 wip/(各 3 种写法)")

# -- 4. "*" 让所有目录都不算项目,但文件一条不少 ---------------------------
write_cfg(json.dumps({"whitelist_dirs": ["*"]}))
d, _ = scan()
assert d["stats"]["projects"] == 0, d["stats"]["projects"]
assert proj_dirs(d) == [], proj_dirs(d)
assert d["count"] == 7, d["count"]              # 只剩 3 条垃圾 + 4 条根目录散文件
assert (d["stats"]["files"], d["stats"]["junk"]) == (28, 3), d["stats"]
assert d["stats"]["source_files"] == 0 and d["stats"]["export_files"] == 0, d["stats"]
assert len(kinds(d, "根目录散文件")) == 4, kinds(d, "根目录散文件")
print("  ✓ * 让 8 个项目全部不算项目,但 files/junk 一条不少(白名单不跳过文件)")

# -- 5. 本 skill 的判定只看 root 下一级 → 含 / 的模式**永远不命中** --------
# 这不是 bug,是内置 WHITELIST_DIRS 的既有语义(「root 下的非项目功能目录」)。
# 与其让它静默失效,不如把它钉成断言并在 SKILL.md 里写明。
for pat in ("练习稿/logo.sketch", "2023_品牌视觉/成品", "2023_品牌视觉/*",
            "深层/不存在"):
    write_cfg(json.dumps({"whitelist_dirs": [pat]}))
    d, err = scan()
    assert d["stats"]["projects"] == 8, (pat, d["stats"]["projects"])
    assert d["count"] == base["count"], (pat, d["count"], base["count"])
    assert proj_dirs(d) == proj_dirs(base), (pat, proj_dirs(d))
    # 配置**确实加载了**(有提示、有 stats.config),只是这些模式没有可命中的对象
    assert "已加载覆盖配置" in err, (pat, err)
    assert d["stats"]["config"]["whitelist_dirs"] == [pat], (pat, d["stats"])
# 特别是:成品/ 没被白名单掉,所以「成品目录混入源文件」照报
write_cfg(json.dumps({"whitelist_dirs": ["2023_品牌视觉/成品"]}))
d, _ = scan()
assert kinds(d, "成品目录混入源文件") == ["2023_品牌视觉/成品/源文件.psd"]
print("  ✓ 含 / 的模式加载成功但永不命中:判定只看 root 下一级(SKILL.md 已写明)")

# -- 5b. 模式必须**锚定**在 root:不能被当成后缀匹配 ----------------------
# 上面那些含 / 的模式在「锚定」与「前面偷偷加了个 *」两种实现下都同样不命中,
# 所以抓不住不锚定的实现。这里换成**真后缀**:锚定时不命中,不锚定时命中。
# 注意不能拿 " drafts" 这种**前后带空格**的写法当反例:load_config 会 .strip(),
# 它就变成合法的 drafts 并真的命中 DRAFTS/(那是归一化,不是不锚定)。
for pat in ("项目 2020", "牌视觉", "视觉", "稿"):
    write_cfg(json.dumps({"whitelist_dirs": [pat]}))
    d, err = scan()
    assert d["stats"]["projects"] == 8, (pat, d["stats"]["projects"])
    assert d["count"] == base["count"], (pat, d["count"], base["count"])
    assert proj_dirs(d) == proj_dirs(base), (pat, proj_dirs(d))
    assert "已加载覆盖配置" in err, (pat, err)     # 配置加载了,只是没有命中对象
# 正对照:显式写通配就**应该**命中(证明上面的不命中来自锚定,不是模式写错了)
write_cfg(json.dumps({"whitelist_dirs": ["* 项目 2020"]}))
d, _ = scan()
assert d["stats"]["projects"] == 7, d["stats"]["projects"]
assert "空格 项目 2020" not in proj_dirs(d), proj_dirs(d)
assert d["count"] == 15, d["count"]
assert d["stats"]["export_files"] == 8, d["stats"]["export_files"]
print("  ✓ 模式锚定在 root:项目 2020 不命中 空格 项目 2020,显式加 * 才命中")

# -- 6. extension_overrides:junk 可以被救回成正常类别 --------------------
# JUNK_EXTS 在 classify_ext() 之前就先命中,不「先摘掉」的话覆盖等于没写。
write_cfg(json.dumps({"extension_overrides": {"bak": "source"}}))
d, _ = scan()
assert d["stats"]["junk"] == 2, d["stats"]["junk"]                  # 3 - 临时.bak
assert d["stats"]["files"] == 29, d["stats"]["files"]               # 28 + 1
assert d["stats"]["loose_root"] == 5, d["stats"]["loose_root"]
assert "临时.bak" not in kinds(d, "垃圾/临时文件"), kinds(d, "垃圾/临时文件")
assert "临时.bak" in kinds(d, "根目录散文件"), kinds(d, "根目录散文件")
print("  ✓ extension_overrides 把 .bak 从 junk 救回成 source(不摘掉就永远先是垃圾)")

# -- 7. 从**更早命中**的表里摘掉:psd 从 SOURCE_EXTS 挪到 EXPORT_EXTS ------
# classify_ext() 的阶梯是 source → export → other。只往 EXPORT_EXTS 里加 psd
# 而不从 SOURCE_EXTS 里摘掉,source 那一级会先命中,覆盖看起来完全没生效。
write_cfg(json.dumps({"extension_overrides": {"psd": "export"}}))
d, _ = scan()
assert d["stats"]["source_files"] == 4, d["stats"]["source_files"]   # 8 - 4 个 .psd
assert d["stats"]["export_files"] == 14, d["stats"]["export_files"]  # 10 + 4
assert kinds(d, "成品目录混入源文件") == [], kinds(d, "成品目录混入源文件")
assert d["count"] == 15, d["count"]
# 反向:pdf 从 EXPORT_EXTS 挪进 SOURCE_EXTS,成品目录里那个 pdf 立刻变成「混入源文件」
write_cfg(json.dumps({"extension_overrides": {"pdf": "source"}}))
d, _ = scan()
assert d["stats"]["source_files"] == 9 and d["stats"]["export_files"] == 9, d["stats"]
assert d["count"] == 17, d["count"]
assert len(kinds(d, "成品目录混入源文件")) == 2, kinds(d, "成品目录混入源文件")
assert "空格 项目 2020/交付/终稿.pdf" in [i["path"] for i in d["issues"]], d["issues"]
print("  ✓ psd→export / pdf→source:两张表互相摘取,成品混入判定跟着翻转")

# -- 7b. 键归一化:前导点可写可不写、大小写都行 ---------------------------
# 只测小写键测不出「忘了 .lower()」:内置表全是小写,而实现里键也总是先小写。
# 所以这里用**大写键**跑同一条改判,期望值与上面 psd→export 完全相同。
write_cfg(json.dumps({"extension_overrides": {".PSD": "export"}}))
d, _ = scan()
assert d["stats"]["source_files"] == 4, d["stats"]["source_files"]
assert d["stats"]["export_files"] == 14, d["stats"]["export_files"]
assert kinds(d, "成品目录混入源文件") == [], kinds(d, "成品目录混入源文件")
assert d["count"] == 15, d["count"]
assert d["stats"]["config"]["extension_overrides"] == {"psd": "export"}, \
    d["stats"]["config"]                      # 归一化之后的键才回写进 stats
print("  ✓ 键归一化:.PSD 与小写 psd 完全等效,回写进 stats 的是归一化后的键")

# -- 8. 大体积源文件检查吃的是同一张表 ----------------------------------
write_cfg(json.dumps({"extension_overrides": {"c4d": "export"}}))
d, _ = scan("--large-gb", "0.000001")
assert kinds(d, "大体积源文件") == [], kinds(d, "大体积源文件")
assert d["stats"]["source_files"] == 7, d["stats"]["source_files"]    # 8 - big.c4d
assert d["stats"]["export_files"] == 11, d["stats"]["export_files"]   # 10 + 1
# 连带 2024_影视 顶层不再有「源文件」那一类 → 成品与源文件混放 也不报了。
# 17(基线)--大体积源文件 --成品与源文件混放 = 15,两条都是真判定跟着翻。
assert "2024_影视" not in kinds(d, "成品与源文件混放"), kinds(d, "成品与源文件混放")
assert d["count"] == 15, d["count"]
print("  ✓ c4d→export:大体积源文件 与 成品与源文件混放 两条真判定一起翻(17→15)")

# -- 9. IMAGE_EXTS 刻意不在覆盖范围内(与 EXPORT_EXTS 故意重叠)-----------
# 如果 IMAGE_EXTS 也在 CONFIG_EXT_TABLES 里,`{"png": "export"}` 会按「先从别的表
# 里摘掉」把 png 从 IMAGE_EXTS 里摘走 —— cover.png 就静默失去封面资格了。
write_cfg(json.dumps({"extension_overrides": {"png": "export"}}))
d, _ = scan()
assert d["stats"]["with_cover"] == 3, d["stats"]["with_cover"]       # 一个都没少
assert "空格 项目 2020" not in kinds(d, "缺封面图"), kinds(d, "缺封面图")
assert d["count"] == base["count"], (d["count"], base["count"])
write_cfg(json.dumps({"extension_overrides": {"jpg": "source"}}))
d, _ = scan()
assert d["stats"]["with_cover"] == 3, d["stats"]["with_cover"]       # cover.jpg 仍在
assert "2023_品牌视觉" not in kinds(d, "缺封面图"), kinds(d, "缺封面图")
print("  ✓ png/jpg 改判不影响封面判定:IMAGE_EXTS 刻意不在 CONFIG_EXT_TABLES 里")

# -- 10. 兜底类别 other(表值是 None)= 从所有表里摘掉、不放进任何一张 -----
write_cfg(json.dumps({"extension_overrides": {"sketch": "other"}}))
d, err = scan()
assert d["stats"]["source_files"] == 7, d["stats"]["source_files"]    # 8 - logo.sketch
assert d["stats"]["export_files"] == 10, d["stats"]["export_files"]
assert "已加载覆盖配置" in err
print("  ✓ 兜底类别 other 可写:.sketch 从 source 落到 other,不是静默无效")

# -- 11. 空对象 {}:加载并提示,但行为与无配置逐条相同 --------------------
def shape(x):
    return sorted((i["path"], tuple(i["problems"])) for i in x["issues"])


write_cfg("{}")
d, err = scan()
assert shape(d) == shape(base), "空对象改变了判定"
assert d["stats"]["projects"] == base["stats"]["projects"]
assert "config" in d["stats"]
assert d["stats"]["config"]["whitelist_dirs"] == []
assert d["stats"]["config"]["extension_overrides"] == {}
assert "skipped_files" not in d["stats"]["config"], d["stats"]["config"]
assert "已加载覆盖配置" in err, err
print("  ✓ 空对象 {} 加载成功:问题清单与无配置时逐条相同,但会明确提示已加载")

# -- 12. 配置文件在,但某个键不在 → 那个键不产生任何影响 ------------------
write_cfg(json.dumps({"whitelist_dirs": ["练习*"]}))
d, _ = scan()
assert d["stats"]["config"]["extension_overrides"] == {}
assert d["stats"]["source_files"] == 7, d["stats"]["source_files"]   # 只少了练习稿的
print("  ✓ 只有 whitelist_dirs 时,扩展名归类一条都不变")

# -- 13. 被扫描目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)---
drop_cfg()
root_cfg = os.path.join(lib, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"extension_overrides": {"bak": "source"},
               "whitelist_dirs": ["*"]}, fh)
for cwd in (src, lib, installed):        # 三种 cwd 都不能让它被读到
    d, err = scan(cwd=cwd)
    assert "临时.bak" in kinds(d, "垃圾/临时文件"), (cwd, kinds(d, "垃圾"))
    assert d["stats"]["projects"] == 8, (cwd, d["stats"]["projects"])
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
    # 它自己被当成一个普通文件扫出来了(json 不在任何表里 → other)
    assert d["stats"]["files"] == 29, (cwd, d["stats"]["files"])
    assert d["stats"]["loose_root"] == 5, (cwd, d["stats"]["loose_root"])
os.remove(root_cfg)
print("  ✓ 被扫描目录里的 config.json 被忽略(3 种 cwd 都试过),只当普通文件扫")

# -- 14. 畸形配置:一律 exit=1,指名文件与键,且不污染 --json stdout --------
BAD = [
    ("非法 JSON", "{not json", "不是合法 JSON"),
    ("空文件", "", "不是合法 JSON"),
    ("顶层不是对象", '"just a string"', "顶层"),
    ("顶层是数组", '["whitelist_dirs"]', "顶层"),
    ("未知键(少写一个 s)", '{"whitelist_dir": ["练习稿"]}', "whitelist_dir"),
    ("未知键(多写一个)", '{"whitelist_dirs": [], "extra": 1}', "extra"),
    ("whitelist_dirs 不是数组", '{"whitelist_dirs": "练习稿"}', "whitelist_dirs"),
    ("whitelist_dirs 元素不是字符串", '{"whitelist_dirs": [null]}',
     "whitelist_dirs[0]"),
    ("whitelist_dirs 第二个元素不是字符串", '{"whitelist_dirs": ["a", 2]}',
     "whitelist_dirs[1]"),
    ("whitelist_dirs 空字符串", '{"whitelist_dirs": ["  "]}', "whitelist_dirs[0]"),
    ("whitelist_dirs 绝对路径", '{"whitelist_dirs": ["/练习稿"]}', "绝对路径"),
    ("whitelist_dirs 反斜杠绝对路径", '{"whitelist_dirs": ["\\\\练习稿"]}', "绝对路径"),
    ("whitelist_dirs Windows 盘符", '{"whitelist_dirs": ["Z:\\\\art"]}', "绝对路径"),
    ("extension_overrides 不是对象", '{"extension_overrides": []}',
     "extension_overrides"),
    ("类别值不是字符串", '{"extension_overrides": {"bak": true}}',
     "必须是字符串类别名"),
    ("类别值是 null", '{"extension_overrides": {"bak": null}}',
     "必须是字符串类别名"),
    ("扩展名含多个点", '{"extension_overrides": {"tar.gz": "source"}}', "tar.gz"),
    ("扩展名归一化后为空", '{"extension_overrides": {".": "source"}}', "空的"),
    ("扩展名是空串", '{"extension_overrides": {"": "source"}}', "空的"),
    ("类别名不存在", '{"extension_overrides": {"bak": "src"}}', "src"),
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
    ("work-organizer 的类别名", '{"extension_overrides": {"bak": "doc"}}', "doc"),
    ("本 skill 刻意不开放的 image", '{"extension_overrides": {"bak": "image"}}',
     "image"),
]:
    write_cfg(text)
    r = run(expect=1)
    assert r.stdout == "", (label, "错误不能污染 --json 的 stdout", r.stdout[:200])
    assert "config.json" in r.stderr, (label, r.stderr)
    assert needle in r.stderr, (label, needle, r.stderr)
    assert "❌" in r.stderr, (label, r.stderr)
# 报错里要列出**本 skill** 的可用类别,照着改就能修
write_cfg('{"extension_overrides": {"bak": "doc"}}')
r = run(expect=1)
for cat in ("source", "export", "junk", "other"):
    assert cat in r.stderr, (cat, r.stderr)
assert "image" not in r.stderr.split("可用类别:")[-1], r.stderr
drop_cfg()
print(f"  ✓ {len(BAD) + 5} 种畸形配置全部 exit=1、只写 stderr、指名键,"
      "并列出本 skill 的 4 个可用类别")

# -- 15. 校验先于写入 + IMAGE_EXTS 不受影响 + 锚定规则 ---------------------
sys.path.insert(0, installed)
import contextlib                                           # noqa: E402
import io                                                   # noqa: E402
import portfolio_organizer as pfo                           # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "whitelist_dirs": ["练习稿"],
    "extension_overrides": {"bak": "doc"},      # work-organizer 的类别,这里没有
}), encoding="utf-8")
before = (set(pfo.SOURCE_EXTS), set(pfo.EXPORT_EXTS), set(pfo.JUNK_EXTS),
          set(pfo.IMAGE_EXTS), set(pfo.WHITELIST_DIRS))
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        pfo.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "doc" in str(e) and "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()     # 失败路径不该先打印「已加载」
assert pfo.CONFIG_WHITELIST == [], pfo.CONFIG_WHITELIST
assert pfo.CONFIG_INFO == {}, pfo.CONFIG_INFO
assert (set(pfo.SOURCE_EXTS), set(pfo.EXPORT_EXTS), set(pfo.JUNK_EXTS),
        set(pfo.IMAGE_EXTS), set(pfo.WHITELIST_DIRS)) == before
assert set(pfo.CONFIG_EXT_TABLES) == {"source", "export", "junk", "other"}, \
    set(pfo.CONFIG_EXT_TABLES)

# 合法配置生效之后:IMAGE_EXTS 必须**分毫不动** —— 它刻意不在 CONFIG_EXT_TABLES
# 里(与 EXPORT_EXTS 有 8 个扩展名故意重叠),所以 is_cover() 的判定不会被覆盖层
# 静默改掉。这正是上面第 9 组断言在纯函数层的对应证据。
ok_dir = Path(src) / "ok"
ok_dir.mkdir(exist_ok=True)
(ok_dir / "config.json").write_text(json.dumps({
    "extension_overrides": {"png": "export", "jpg": "source", "bak": "source"},
    "whitelist_dirs": ["练习*"],
}), encoding="utf-8")
with contextlib.redirect_stderr(io.StringIO()):
    pfo.load_config(ok_dir)
assert set(pfo.IMAGE_EXTS) == {
    "jpg", "jpeg", "png", "webp", "gif", "tif", "tiff", "bmp", "heic"}, \
    pfo.IMAGE_EXTS
assert pfo.is_cover("cover.png") and pfo.is_cover("封面.jpg"), "封面判定被改掉了"
assert "png" in pfo.EXPORT_EXTS and "jpg" in pfo.SOURCE_EXTS, pfo.EXPORT_EXTS
assert "bak" not in pfo.JUNK_EXTS and "bak" in pfo.SOURCE_EXTS, pfo.JUNK_EXTS
assert pfo.CONFIG_WHITELIST == ["练习*"], pfo.CONFIG_WHITELIST
# 内置 WHITELIST_DIRS 一个都没被删(追加,不是替换)
assert {"素材库", "templates", "归档"} <= pfo.WHITELIST_DIRS, pfo.WHITELIST_DIRS

m = pfo._cfg_match
assert m(["练习稿"], ["练习*"])                        # fnmatch 通配 CJK
assert m(["2023_品牌视觉", "成品"], ["2023_品牌视觉"])   # 任一级目录名命中
assert m(["2023_品牌视觉", "成品"], ["2023_品牌视觉/*"])  # 整段相对路径命中
assert not m(["2023_品牌视觉"], ["2023_品牌视觉/*"])     # 带 /* 不命中自己这一层
assert not m(["深层", "练习稿"], ["2023_品牌视觉/*"])    # 不在那条路径上就不算
assert m(["深层", "练习稿"], ["深层/练习稿"])
assert m(["空格 项目 2020"], ["空格*"])                 # 含空格的名字
assert m(["DRAFTS"], ["drafts"])                       # 大小写不敏感(目录大写)
assert m(["drafts"], ["DRAFTS"])                       # 反方向:模式大写
assert m(["WIP", "sub"], ["wip/*"])                    # 带通配时同样双向
assert m(["练习稿"], ["*"])                             # * 跨 / 匹配
assert not m([], ["*"])                                # root 下的散文件不吃白名单
assert not m(["练习稿"], [])                            # 没有模式 = 不命中
assert not m(["成品"], ["练习稿/成品"])                  # 单级相对路径配不上带 / 的模式
print("  ✓ 校验先于写入(内置五个集合分毫未动)+ IMAGE_EXTS/is_cover 不受覆盖影响"
      " + _cfg_match 锚定规则 14 条断言")
PYEOF
rm -rf "$PFCFG_SRC"

echo "=== TEST 8: 读不了的目录不等于空项目(家族审计 F5)==="
# F5:此前 scandir 失败只打一行 stderr,JSON 里没有 errors 键 —— 一个读不了的
# 项目与真空项目在 JSON 里逐字节相同,都收到「空项目目录(建议删除或补充内容)」,
# 而那是删除引导。本测试与 backup-auditor smoke TEST 8 的 F1 段同构。
PF_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import json
import os
import shutil
import subprocess
import sys
import tempfile

skill = os.environ["PF_SKILL_DIR"]
script = f"{skill}/portfolio_organizer.py"


def run(root):
    r = subprocess.run([sys.executable, script, "scan", "--root", root, "--json"],
                       capture_output=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    return json.loads(r.stdout)


# chmod 000 在 Windows 上不产生同样的效果(os.chmod 只改只读位),scandir 不会
# 抛 PermissionError,所以这一段在 Windows 上**跳过并明说**,不静默通过。
if os.name == "nt":
    print("  ⊘ F5 权限段在 Windows 上跳过(chmod 000 不产生 scandir 失败)")
else:
    root = tempfile.mkdtemp(prefix="pf-f5.")
    try:
        locked = os.path.join(root, "2024_锁定项目")
        real_empty = os.path.join(root, "2024_真空项目")
        normal = os.path.join(root, "2024_正常项目")
        for d in (locked, real_empty, normal):
            os.makedirs(d)
        # 关键:锁住的项目里放**真实数据**,这样「报成空项目」才是可检出的错误
        with open(os.path.join(locked, "big.psd"), "wb") as fh:
            fh.write(b"D" * 5_000_000)
        with open(os.path.join(normal, "design.psd"), "wb") as fh:
            fh.write(b"A" * 2000)
        os.chmod(locked, 0o000)

        d = run(root)
        s = d["stats"]
        # 1) 读失败必须可见:errors 通道 + 专门计数
        assert "errors" in d, sorted(d)
        assert len(d["errors"]) >= 1, d["errors"]
        assert any(e["path"] == "2024_锁定项目" for e in d["errors"]), d["errors"]
        assert s["unreadable_projects"] == 1, s
        # 2) 读不了的项目**不能**收到「空项目目录(建议删除)」这条删除引导
        by = {}
        for i in d["issues"]:
            by.setdefault(i["path"], []).append(i["problems"][0])
        assert not any("空项目目录" in p for p in by.get("2024_锁定项目", [])), by
        assert any("无法读取" in p for p in by.get("2024_锁定项目", [])), by
        # 3) 真正的空项目**仍然**被判空(负控制:别把修复做成「不再报空项目」)
        assert any("空项目目录" in p for p in by.get("2024_真空项目", [])), by
        # 4) 读不了的 5MB 不能被算进总量
        assert s["total_size_bytes"] == 2000, s
        assert s["projects"] == 3, s
    finally:
        os.chmod(os.path.join(root, "2024_锁定项目"), 0o755)
        shutil.rmtree(root, ignore_errors=True)
    print("  ✓ 读不了的项目:errors 可见、单列 unreadable_projects、"
          "不再误报「空项目目录(建议删除)」,而真正的空项目仍照报")

# ── 干净树上 errors 必须存在且为空(file-sorter / dedup-finder / backup-auditor 同族约定)──
clean = tempfile.mkdtemp(prefix="pf-clean.")
try:
    os.makedirs(os.path.join(clean, "2024_项目甲"))
    with open(os.path.join(clean, "2024_项目甲", "cover.jpg"), "wb") as fh:
        fh.write(b"J" * 1000)
    d = run(clean)
    assert d["errors"] == [], d["errors"]
    assert d["stats"]["unreadable_projects"] == 0, d["stats"]
    assert d["stats"]["unreadable_files"] == 0, d["stats"]
    print("  ✓ 干净树:errors 键存在且为 [](与家族一致)")
finally:
    shutil.rmtree(clean, ignore_errors=True)
PYEOF

echo "🎉 所有 smoke test 通过"
