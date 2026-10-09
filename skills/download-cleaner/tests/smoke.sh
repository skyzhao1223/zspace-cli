#!/bin/bash
# download-cleaner skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
DL_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["DL_SKILL_DIR"]
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
if name != "download-cleaner":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=download-cleaner")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/download_cleaner.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(分类/建议) ==="
DL_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["DL_SKILL_DIR"])
import download_cleaner as dc

# 分类
assert dc.categorize("x.part", "part") == "partial"
assert dc.categorize("x.bt.td", "td") == "partial"
assert dc.categorize("a.torrent", "torrent") == "torrent"
assert dc.categorize("app.dmg", "dmg") == "installer"
assert dc.categorize("arc.zip", "zip") == "archive"
assert dc.categorize("movie.mkv", "mkv") == "video"
assert dc.categorize("song.flac", "flac") == "audio"
assert dc.categorize("pic.jpg", "jpg") == "photo"
assert dc.categorize("report.pdf", "pdf") == "doc"
assert dc.categorize(".DS_Store", "") == "junk"
assert dc.categorize("whatever.xyz", "xyz") == "other"

# 建议动作
p, a = dc.advise("junk", 0, False, 365)
assert a == "delete"
p, a = dc.advise("partial", 10, False, 365)
assert a == "delete-confirm"
p, a = dc.advise("archive", 10, True, 365)     # 已解压
assert a == "delete-confirm" and any("已解压" in x for x in p)
p, a = dc.advise("archive", 10, False, 365)    # 未解压
assert a == "extract-or-review"
p, a = dc.advise("installer", 10, False, 365)  # 新安装包
assert a == "review"
p, a = dc.advise("installer", 500, False, 365) # 老旧安装包
assert a == "delete-confirm" and any("老旧" in x for x in p)
p, a = dc.advise("video", 10, False, 365)
assert a == "move-to-library" and any("影视库" in x for x in p)
p, a = dc.advise("other", 10, False, 365)      # 新杂项 → keep
assert a == "keep" and p == []

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/download-cleaner-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/下载"
mkdir -p "$ROOT/已解压包"
# 各类别文件
touch "$ROOT/movie.mkv" "$ROOT/song.mp3" "$ROOT/report.pdf"      # 待归档
touch "$ROOT/x.torrent" "$ROOT/y.part" "$ROOT/app.dmg"            # 种子/未完成/安装包
echo data > "$ROOT/已解压包.zip"                                   # 已解压(同名目录在)
touch "$ROOT/未解压.rar"                                           # 未解压
touch "$ROOT/movie (1).mkv"                                       # 重复下载
touch "$ROOT/.DS_Store"                                          # 垃圾
# 老旧文件(mtime 设为 2 年前)
touch -t 202301010000 "$ROOT/old-installer.dmg" "$ROOT/ancient.pdf"

DL_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["DL_SKILL_DIR"]
root = os.environ["FIXTURE_ROOT"]
out = os.path.join(tempfile.mkdtemp(), "dl.json")
r = subprocess.run(
    [sys.executable, f"{skill}/download_cleaner.py", "scan",
     "--root", root, "--stale-days", "365", "--output", out],
    capture_output=True, text=True,
    encoding="utf-8", errors="replace",
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())
blob = json.dumps(data, ensure_ascii=False)
s = data["stats"]

assert data["skill"] == "download-cleaner"
assert s["by_category"]["torrent"]["count"] == 1, s
assert s["by_category"]["partial"]["count"] == 1, s
assert s["by_category"]["video"]["count"] == 2, s   # movie.mkv + movie (1).mkv
assert s["duplicate_downloads"] == 1, s
assert s["reclaimable_bytes"] > 0, s
assert s["stale_files"] >= 2, s                      # old-installer + ancient
assert "种子文件" in blob
assert "未完成下载" in blob
assert "已解压" in blob                              # 已解压包.zip
assert "未解压" in blob                              # 未解压.rar
assert "老旧安装包" in blob                          # old-installer.dmg
assert "move-to-library" in blob or "建议归档" in blob
assert "重复下载" in blob
# action 字段存在
actions = {i["action"] for i in data["issues"]}
assert "delete-confirm" in actions, actions
assert "move-to-library" in actions, actions
print("  ✓ fixture scan 检出全部预期类别与动作")
PYEOF

echo "=== TEST 5: --help ==="
"$PY" "$SKILL_DIR/download_cleaner.py" --help >/dev/null
"$PY" "$SKILL_DIR/download_cleaner.py" scan --help | grep -q -- "--stale-days"
echo "  ✓ CLI help 可用"

echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/downloadcl-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/download_cleaner.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/download_cleaner.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
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
CFG_SRC="$(mktemp -d /tmp/downloadcl-cfg.XXXXXX)"
mkdir -p "$CFG_SRC/installed" "$CFG_SRC/lib"
cp "$SKILL_DIR/download_cleaner.py" "$CFG_SRC/installed/"
DL_CFG_SRC="$CFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import time
from pathlib import Path

src = os.environ["DL_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "download_cleaner.py")
cfg = os.path.join(installed, "config.json")
lib = os.path.join(src, "lib")
DAY = 86400


def w(rel, body, age_days=0):
    p = os.path.join(lib, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    if age_days:
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))


# ---- fixture:每个类别都要有,外加 skip_dirs 的锚定边界与大小写撞名 ----
# 「other 且不老」的文件在本 skill 里根本不出 issue(advise 返回 keep),所以这几个
# 覆盖对象一律设成 800 天未动 —— 它们必须出现在 issues 里,断言才有东西可断。
w("movie.ass", "ass-bytes", 800)     # 内置判 other;字幕其实该跟着视频走
w("movie.srt", "srt-bytes", 800)     # 对照组:只改 .ass 时它必须仍是 other
w("mystery.xyz", "xyz-bytes", 800)   # 内置判 other
w("site.iso", "iso-bytes", 800)      # 内置判 other(iso 不在本 skill 的 ARCHIVE_EXTS)
w("scratch.tmp", "tmp-bytes")        # 内置判 junk(判定阶梯第一级)
w("x.part", "part-bytes")            # 内置 partial,任何覆盖都不该动它
w("pack.zip", "zip-bytes", 800)      # 内置 archive
w("pack/inner.txt", "inner")         # 同名目录 → pack.zip 判「已解压」
w("临时/2024/old.torrent", "t1")      # 临时/* 命中这一层
w("临时/loose.part", "t2")            # 直接躺在 临时/ 里:临时/* 管不到它
w("深层/临时/子目录/x.torrent", "t3")  # 同名目录在更深处:裸名字 临时 命中它
w("SAMPLES/sample.mkv", "s1")        # 大小写不敏感(目录大写)
w("samples/other.mkv", "s2")         # 大小写不敏感(目录小写)
# 在大小写不敏感的卷上(macOS 默认 / Windows)这两个是**同一个**目录,只有先建
# 的那个拼法留下来;在 ubuntu CI 上是两个。断言跟着实测走,不假定文件系统。
CASE_DIRS = sorted(n for n in os.listdir(lib) if n.lower() == "samples"
                   and os.path.isdir(os.path.join(lib, n)))
assert 1 <= len(CASE_DIRS) <= 2, CASE_DIRS
w("._movie.ass", "appledouble")      # AppleDouble → junk(issue #58:曾经进不了统计)
w("._pack.zip", "appledouble2")      # 第二个 AppleDouble:扩展名是 archive 类,
                                     # 用来证明 junk 判定优先于扩展名阶梯
w(".DS_Store", "junk")
# 普通 dotfile(既不在 JUNK_NAMES 也不是 ._ 前缀)必须**继续**被静默忽略。
# 这两条是上面那个修复的负控制。**扩展名刻意选 torrent / part**:如果早退被整个
# 删掉,它们会被判成 torrent / partial 并**产生 issue**,于是下面 `not in bp` 就会
# 失败。用 `.hidden_config` 这种名字是无效的负控制 —— 它会被判成 other 且没有
# problems,压根不出现在 issues 里(实测:删掉早退后 stats.files 从 2 变 4、
# by_category.other.count 变 2,而 issues 一条都没多)。
w(".hidden.torrent", "plain-dotfile")
w(".ignored.part", "plain-dotfile2")
w("-rf-danger.tar.gz", "hostile")     # shell 危险名:前导 -
w("  spaced name .txt", "hostile2")   # shell 危险名:首尾空格
# 大小写撞名对:大小写不敏感的卷(macOS 默认 / Windows)上第二个会覆盖第一个,
# 所以断言跟着**实际存活**的名字走,不假定文件系统行为
w("CASE.bin", "case-1", 800)
w("case.bin", "case-2", 800)
CASE_PAIR = sorted(n for n in os.listdir(lib) if n.lower() == "case.bin")
assert 1 <= len(CASE_PAIR) <= 2, CASE_PAIR


def assert_cfg_path_in(text, label=""):
    """从输出里取出配置路径,两边都 realpath + normcase 再比。

    不能直接 `assert cfg in stdout`:脚本印的是 `Path(__file__).resolve()` 的结果,
    而 resolve() 在 Windows 上会把 8.3 短名(C:\\Users\\RUNNER~1)展开成长名、在
    macOS 上会把 /tmp 变成 /private/tmp —— bash 传进来的却是未展开的那个。
    """
    lines = [x for x in text.splitlines() if "已加载覆盖配置" in x]
    assert lines, (label, text[:400])
    shown = lines[0].split("已加载覆盖配置", 1)[1].strip()
    assert os.path.normcase(os.path.realpath(shown)) == \
        os.path.normcase(os.path.realpath(cfg)), (label, shown, cfg)


def write_cfg(text):
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(text)


def drop_cfg():
    if os.path.exists(cfg):
        os.remove(cfg)


def run(*extra, expect=0, cwd=None, json_out=True):
    # cwd 故意设成 src(既不是脚本目录也不是被扫描目录):证明解析与 cwd 无关
    args = [sys.executable, script, "scan", "--root", lib]
    if json_out:
        args.append("--json")
    r = subprocess.run(
        args + list(extra),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd or src)
    assert r.returncode == expect, (r.returncode, r.stdout[-400:], r.stderr)
    return r


def scan(*extra, cwd=None):
    r = run(*extra, cwd=cwd)
    return json.loads(r.stdout), r.stderr


def paths(d):
    return {i["path"]: i for i in d["issues"]}


def shape(d):
    """与时间无关的 issue 摘要(age_days 会随运行时刻漂,不能直接比 issues)。"""
    return sorted((i["path"], i["category"], i["action"]) for i in d["issues"])


def ci_get(mapping, key):
    """大小写不敏感地取一条 issue:大小写不敏感的卷上 SAMPLES/ 与 samples/ 是同一个
    目录,扫描结果里只会出现先建的那个拼法。"""
    for k, v in mapping.items():
        if k.lower() == key.lower():
            return v
    raise AssertionError(f"{key!r} not in {sorted(mapping)}")


def cats(d):
    return {k: v["count"] for k, v in d["stats"]["by_category"].items() if v["count"]}


# -- 0. 基线:没有 config.json 时,stats 里连 config 这个键都不该出现 ---------
drop_cfg()
base, base_err = scan()
bp = paths(base)
assert "config" not in base["stats"], base["stats"].get("config")
assert "已加载覆盖配置" not in base_err, base_err
assert bp["movie.ass"]["category"] == "other", bp["movie.ass"]
assert bp["movie.srt"]["category"] == "other"
assert bp["mystery.xyz"]["category"] == "other"
assert bp["scratch.tmp"]["category"] == "junk", bp["scratch.tmp"]
assert bp["scratch.tmp"]["action"] == "delete"
assert bp["x.part"]["category"] == "partial"
assert bp["pack.zip"]["category"] == "archive"
assert any("已解压" in p for p in bp["pack.zip"]["problems"]), bp["pack.zip"]
assert bp["site.iso"]["category"] == "other", bp["site.iso"]
# AppleDouble(issue #58):_check_file 现在与 file-sorter 同序 —— 先认 junk 名
# 再决定跳不跳点文件,所以 ._ 前缀的文件能走到 categorize() 并被判成 junk。
for ap in ("._movie.ass", "._pack.zip"):
    assert ap in bp, (ap, sorted(bp))
    assert bp[ap]["category"] == "junk", bp[ap]
    assert bp[ap]["action"] == "delete", bp[ap]
# ._pack.zip 的扩展名在 ARCHIVE_EXTS 里,却仍判 junk —— 证明 junk 名优先于扩展名
# 阶梯(也证明 _check_file 里对 junk 名把 ext 置空这一步是有意义的)
assert bp[".DS_Store"]["category"] == "junk"            # JUNK_NAMES 里的点文件仍判 junk
# 负控制:**普通** dotfile 仍被静默忽略。少了这两条,「把早退整个删掉」也能通过。
# 断言 issues 与 stats 两处 —— 只查 issues 不够(见 fixture 那里的说明)。
for plain in (".hidden.torrent", ".ignored.part"):
    assert plain not in bp, (plain, sorted(bp))
# 绝对数量断言:n_base_files 是从实现自己的输出读出来的,只能验 skip_dirs 的**增量**,
# 钉不住绝对值。这里独立数一遍 —— 结果里以 ._ 开头的条目必须恰好是 fixture 那两个。
assert sorted(p for p in bp if os.path.basename(p).startswith("._")) == \
    ["._movie.ass", "._pack.zip"], sorted(bp)
assert base["stats"]["by_category"]["junk"]["count"] >= 3, base["stats"]["by_category"]
assert "临时/2024/old.torrent" in bp and "临时/loose.part" in bp
assert "深层/临时/子目录/x.torrent" in bp
assert ci_get(bp, "SAMPLES/sample.mkv")["category"] == "video"
assert ci_get(bp, "samples/other.mkv")["category"] == "video"
assert bp["-rf-danger.tar.gz"]["category"] == "archive"
assert bp["  spaced name .txt"]["category"] == "doc"
# 大小写撞名对:存活几个就报几个,既不崩也不重复计数
assert [p for p in bp if p.lower() == "case.bin"] == CASE_PAIR, (bp, CASE_PAIR)
n_base_files = base["stats"]["files"]
# 独立测出「root 下散文件」的个数(--max-depth 0 只扫第一层),这样后面 skip_dirs=["*"]
# 那条断言的期望值不是从被测实现里读出来的
base_d0, _ = scan("--max-depth", "0")
n_root_files = base_d0["stats"]["files"]
assert n_root_files < n_base_files, (n_root_files, n_base_files)
print(f"  ✓ 无 config.json:stats 无 config 键、stderr 无提示、{base['count']} 条问题与旧版一致"
      f"(大小写撞名存活 {len(CASE_PAIR)} 个)")

# -- 0b. 人类可读报告:没有配置时不印任何覆盖层信息,有配置时必须印 ---------
r = run(json_out=False)
assert "⚙ 已加载覆盖配置" not in r.stdout, r.stdout
assert "已加载覆盖配置" not in r.stdout, r.stdout
write_cfg(json.dumps({"skip_dirs": ["SAMPLES"], "extension_overrides": {"ass": "video"}}))
r = run(json_out=False)
assert "⚙ 已加载覆盖配置" in r.stdout, r.stdout
assert_cfg_path_in(r.stdout)
assert "skip_dirs: SAMPLES" in r.stdout, r.stdout
assert "extension_overrides: ass→video" in r.stdout, r.stdout
assert "因 skip_dirs 未纳入的目录:" in r.stdout, r.stdout
drop_cfg()
print("  ✓ 人类报告:无配置时一个字都不印,有配置时印出路径与生效的键值")

# -- 1. extension_overrides:把 .ass 从 other 挪到 video --------------------
write_cfg(json.dumps({"extension_overrides": {"ass": "video"}}))
d, err = scan()
dp = paths(d)
assert dp["movie.ass"]["category"] == "video", dp["movie.ass"]
assert dp["movie.ass"]["action"] == "move-to-library", dp["movie.ass"]
assert any("影视库" in p for p in dp["movie.ass"]["problems"]), dp["movie.ass"]
assert d["stats"]["by_category"]["video"]["count"] == base["stats"]["by_category"]["video"]["count"] + 1
assert d["stats"]["by_category"]["other"]["count"] == base["stats"]["by_category"]["other"]["count"] - 1
# 逐扩展名改判,不是整表替换:没写到的必须一条都不变
assert dp["movie.srt"]["category"] == "other", dp["movie.srt"]
assert dp["mystery.xyz"]["category"] == "other"
assert dp["scratch.tmp"]["category"] == "junk"
assert dp["x.part"]["category"] == "partial"
assert "已加载覆盖配置" in err, err
assert d["stats"]["config"]["extension_overrides"] == {"ass": "video"}
assert d["stats"]["config"]["skip_dirs"] == []
assert d["stats"]["config"]["path"].endswith("config.json")
assert d["stats"]["config"]["skipped_dirs"] == 0
print("  ✓ extension_overrides 改判 .ass → video/move-to-library,且只动这一个扩展名")

# -- 2. junk 是判定阶梯的**第一级**:不先从 JUNK_EXTS 里摘掉就静默失效 -------
# 这正是 download-cleaner 必须把内联的 ("tmp","temp","bak") 提成 JUNK_EXTS 的
# 原因。下面这条断言在提成常量之前是**红**的(scratch.tmp 仍然是 junk)。
write_cfg(json.dumps({"extension_overrides": {"tmp": "doc"}}))
d, _ = scan()
dp = paths(d)
assert dp["scratch.tmp"]["category"] == "doc", dp["scratch.tmp"]
assert dp["scratch.tmp"]["action"] == "move-to-library", dp["scratch.tmp"]
assert d["stats"]["by_category"]["junk"]["count"] == base["stats"]["by_category"]["junk"]["count"] - 1
print("  ✓ 扩展名可以从 junk 里被救回来(判定阶梯第一级也吃覆盖,不是静默失效)")

# -- 3. 键归一化(前导点 / 大小写)+ 往内置表里**加**新扩展名 ---------------
write_cfg(json.dumps({"extension_overrides": {".XYZ": "doc", "ISO": "archive"}}))
d, _ = scan()
dp = paths(d)
assert dp["mystery.xyz"]["category"] == "doc", dp["mystery.xyz"]
assert dp["site.iso"]["category"] == "archive", dp["site.iso"]
assert dp["movie.ass"]["category"] == "other"        # 没写到的不动
print("  ✓ 键归一化(.XYZ / ISO 都认)+ 可以把内置表里没有的扩展名加进来")

# -- 4. ARCHIVE_EXTS 是**共享**的:改判 archive 也会改「已解压」启发式 --------
# 这条把 SKILL.md 里明写的耦合钉住:pack.zip 原本因为同目录有 pack/ 被判「已解压」
# (delete-confirm);把 zip 改判出 archive 之后它不再参与那个启发式。
write_cfg(json.dumps({"extension_overrides": {"zip": "other"}}))
d, _ = scan()
dp = paths(d)
assert dp["pack.zip"]["category"] == "other", dp["pack.zip"]
assert not any("已解压" in p for p in dp["pack.zip"]["problems"]), dp["pack.zip"]
assert dp["-rf-danger.tar.gz"]["category"] == "archive"   # 其余归档扩展名不受影响
print("  ✓ 改判出 archive 会同时退出「已解压」启发式(共享 ARCHIVE_EXTS,已在文档明写)")

# -- 5. 兜底类别 other 可写:显式维持原判,不是静默无效 ---------------------
write_cfg(json.dumps({"extension_overrides": {"ass": "other"}}))
d, err = scan()
assert shape(d) == shape(base), (shape(d), shape(base))
assert d["stats"]["config"]["extension_overrides"] == {"ass": "other"}
assert "已加载覆盖配置" in err, err
print("  ✓ 兜底类别 other 可写:结果与无配置逐条相同,但仍然明确提示已加载")

# -- 6. skip_dirs:模式锚定在被扫描的 root(整段相对路径 或 任一级目录名)----
write_cfg(json.dumps({"skip_dirs": ["临时/*"]}))
d, _ = scan()
dp = paths(d)
assert "临时/2024/old.torrent" not in dp, sorted(dp)
# 锚定边界:临时/* 命中 临时/2024,但**不**命中 临时 自己这一层
assert "临时/loose.part" in dp, sorted(dp)
# 也不命中 深层/临时/子目录 —— 相对路径不以 临时/ 开头
assert "深层/临时/子目录/x.torrent" in dp, sorted(dp)
assert d["stats"]["config"]["skip_dirs"] == ["临时/*"]
assert d["stats"]["config"]["skipped_dirs"] == 1, d["stats"]["config"]
assert d["stats"]["files"] == n_base_files - 1, d["stats"]["files"]
print("  ✓ skip_dirs=[临时/*]:整棵子树不进入;命中 临时/2024 但不命中 临时 自身")

# -- 6b. 裸名字 临时 → 任意深度的同名目录都命中(与 6 形成对照)-------------
write_cfg(json.dumps({"skip_dirs": ["临时"]}))
d, _ = scan()
dp = paths(d)
assert not [p for p in dp if "临时" in p], sorted(dp)
# skipped_dirs 数的是**被剪掉的目录**,不是文件:临时/ 与 深层/临时/ 两个目录被剪,
# 它们下面的 3 个文件因此消失。剪掉 临时/ 之后就不再进去,所以 临时/2024 不计数。
assert d["stats"]["config"]["skipped_dirs"] == 2, d["stats"]["config"]
assert d["stats"]["files"] == n_base_files - 3, d["stats"]["files"]
print("  ✓ 裸名字 临时 在任意深度命中(剪 2 个目录 / 少 3 个文件),与 临时/* 可区分")

# -- 6c. 带 / 的模式锚定在 root:深层/临时/* 只命中那一条 --------------------
write_cfg(json.dumps({"skip_dirs": ["深层/临时/*"]}))
d, _ = scan()
dp = paths(d)
assert "深层/临时/子目录/x.torrent" not in dp, sorted(dp)
assert d["stats"]["config"]["skipped_dirs"] == 1, d["stats"]["config"]
assert d["stats"]["files"] == n_base_files - 1, d["stats"]["files"]
assert "临时/2024/old.torrent" in dp        # 反过来不命中
assert "临时/loose.part" in dp
print("  ✓ 深层/临时/* 只命中 root 下那一条路径,不波及同名的 临时/")

# -- 6d. 大小写不敏感必须**双向**成立 --------------------------------------
# 只测「目录大写 / 模式小写」测不出来:实现里相对路径总是先 .lower(),所以那个
# 方向即使忘了给模式做 lower 也照样过。反方向才是真的判据。
for pat in ("SAMPLES", "samples", "Samples"):
    write_cfg(json.dumps({"skip_dirs": [pat]}))
    d, _ = scan()
    dp = paths(d)
    assert not [x for x in dp if x.lower().startswith("samples/")], (pat, sorted(dp))
    assert d["stats"]["config"]["skipped_dirs"] == len(CASE_DIRS), \
        (pat, d["stats"]["config"], CASE_DIRS)
print(f"  ✓ 大小写不敏感双向成立:SAMPLES / samples / Samples 三种写法都命中"
      f"(本机存活 {len(CASE_DIRS)} 个目录)")

# -- 6e. skip_dirs 只作用于目录:* 也管不到直接躺在 root 下的文件 -----------
write_cfg(json.dumps({"skip_dirs": ["*"]}))
d, _ = scan()
dp = paths(d)
assert d["stats"]["dirs"] == 0, d["stats"]["dirs"]
assert d["stats"]["files"] == n_root_files, (d["stats"]["files"], n_root_files)
for root_file in ("movie.ass", "scratch.tmp", "x.part", "pack.zip", "site.iso"):
    assert root_file in dp, root_file
print("  ✓ skip_dirs 只作用于目录:* 也管不到直接躺在 root 下的文件")

# -- 7. 空对象 {}:加载并提示,但行为与无配置逐条相同 ----------------------
write_cfg("{}")
d, err = scan()
assert shape(d) == shape(base), (shape(d), shape(base))
assert "config" in d["stats"]
assert d["stats"]["config"]["skip_dirs"] == []
assert d["stats"]["config"]["extension_overrides"] == {}
assert "已加载覆盖配置" in err, err
print("  ✓ 空对象 {} 加载成功:问题清单与无配置时逐条相同,但会明确提示已加载")

# -- 8. 配置文件在,但某个键不在 → 那个键不产生任何影响 --------------------
write_cfg(json.dumps({"skip_dirs": ["SAMPLES"]}))
d, _ = scan()
dp = paths(d)
assert dp["movie.ass"]["category"] == "other", dp["movie.ass"]
assert dp["scratch.tmp"]["category"] == "junk"
assert not [x for x in dp if x.lower().startswith("samples/")], sorted(dp)
assert d["stats"]["config"]["extension_overrides"] == {}
print("  ✓ 只有 skip_dirs 时,extension_overrides 缺省 = 不改判任何扩展名")

# -- 9. 两个键同时生效,互不干扰 -------------------------------------------
write_cfg(json.dumps({"skip_dirs": ["临时"],
                      "extension_overrides": {"ass": "video"}}))
d, err = scan()
dp = paths(d)
assert dp["movie.ass"]["category"] == "video"
assert not [p for p in dp if "临时" in p], sorted(dp)
assert d["stats"]["config"]["skipped_dirs"] == 2
assert "skip_dirs 1 条" in err and "extension_overrides 1 条" in err, err
print("  ✓ 两个键同时生效;stderr 提示行分别报出各自的条数")

# -- 10. 被扫描目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)---
drop_cfg()
root_cfg = os.path.join(lib, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"extension_overrides": {"ass": "video"}, "skip_dirs": ["*"]},
              fh, ensure_ascii=False)
# 设成 800 天未动: otherwise 它是「other 且不老」→ advise 返回 keep → 根本不出
# issue,后面那句「它被当成普通文件扫到了」就没有东西可断
_old = time.time() - 800 * DAY
os.utime(root_cfg, (_old, _old))
for cwd in (src, lib, installed):        # 三种 cwd 都不能让它被读到
    d, err = scan(cwd=cwd)
    dp = paths(d)
    assert dp["movie.ass"]["category"] == "other", (cwd, dp["movie.ass"])
    assert d["stats"]["dirs"] == base["stats"]["dirs"], (cwd, d["stats"]["dirs"])
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
# 它只是被当成一个普通文件扫到了 —— 这正好证明它是数据、不是配置
d, _ = scan()
assert paths(d)["config.json"]["category"] == "other", paths(d)["config.json"]
assert paths(d)["config.json"]["action"] == "review", paths(d)["config.json"]
os.remove(root_cfg)
print("  ✓ 被扫描目录里的 config.json 被忽略(3 种 cwd 都试过),只当普通文件扫")

# -- 11. 畸形配置:一律 exit=1,指名文件与 offending 键,且不污染 --json stdout
BAD = [
    ("非法 JSON", "{not json", "不是合法 JSON"),
    ("空文件", "", "不是合法 JSON"),
    ("顶层不是对象", "[]", "顶层"),
    ("未知键(少写一个 s)", '{"skip_dir": ["临时"]}', "skip_dir"),
    ("未知键(照抄 file-sorter 的 whitelist_dirs)", '{"whitelist_dirs": ["临时"]}',
     "whitelist_dirs"),
    ("skip_dirs 不是数组", '{"skip_dirs": "临时"}', "skip_dirs"),
    ("skip_dirs 元素不是字符串", '{"skip_dirs": [1]}', "skip_dirs[0]"),
    ("skip_dirs 空字符串", '{"skip_dirs": ["  "]}', "skip_dirs[0]"),
    ("skip_dirs 绝对路径", '{"skip_dirs": ["/临时"]}', "绝对路径"),
    ("skip_dirs Windows 盘符", '{"skip_dirs": ["Z:\\\\data"]}', "绝对路径"),
    ("extension_overrides 不是对象", '{"extension_overrides": ["ass"]}',
     "extension_overrides"),
    ("类别值不是字符串", '{"extension_overrides": {"ass": 3}}', "必须是字符串类别名"),
    ("类别名不存在", '{"extension_overrides": {"ass": "subtitle"}}', "subtitle"),
    ("类别名是别的 skill 的", '{"extension_overrides": {"ass": "non_media"}}',
     "non_media"),
    ("扩展名含多个点", '{"extension_overrides": {"tar.gz": "archive"}}', "tar.gz"),
    ("扩展名归一化后为空", '{"extension_overrides": {".": "doc"}}', "空的"),
    ("两个键都拼错", '{"skip_dir": [], "extension_override": {}}',
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

# -- 12. 未知键报错里要列出**本 skill** 的可用键与可用类别 -----------------
write_cfg('{"skip_dir": ["临时"]}')
r = run(expect=1)
assert "skip_dirs" in r.stderr and "extension_overrides" in r.stderr, r.stderr
drop_cfg()
write_cfg('{"extension_overrides": {"ass": "nope"}}')
r = run(expect=1)
for cat in ("junk", "partial", "torrent", "installer", "archive",
            "video", "audio", "photo", "doc", "other"):
    assert cat in r.stderr, (cat, r.stderr)
drop_cfg()
print("  ✓ 未知键报错列出可用键名;类别名报错列出本 skill 全部 10 个类别")

# -- 13. 校验先于写入:半途失败的配置不得留下副作用(进程内直接考)--------
sys.path.insert(0, installed)
import contextlib                                          # noqa: E402
import io                                                  # noqa: E402
import download_cleaner as dc                              # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "skip_dirs": ["SAMPLES"],
    "extension_overrides": {"ass": "nope"},
}, ensure_ascii=False), encoding="utf-8")
before = {k: set(v) for k, v in dc.CONFIG_EXT_TABLES.items() if v is not None}
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        dc.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "nope" in str(e), str(e)
    assert "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()   # 失败路径不该先打印「已加载」
assert dc.CONFIG_SKIP == [], dc.CONFIG_SKIP   # skip_dirs 没被半路写进去
assert dc.CONFIG_INFO == {}, dc.CONFIG_INFO
assert {k: set(v) for k, v in dc.CONFIG_EXT_TABLES.items() if v is not None} == before, \
    "扩展名表被改了一半"

# 同一进程里再加载一份好的,确认没被上一次的失败污染
good_dir = Path(src) / "good"
good_dir.mkdir(exist_ok=True)
(good_dir / "config.json").write_text(json.dumps({
    "skip_dirs": [" 临时 "],
    "extension_overrides": {"ass": "video"},
}), encoding="utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    dc.load_config(good_dir)
assert dc.CONFIG_SKIP == ["临时"], dc.CONFIG_SKIP        # 前后空白被 strip
assert "ass" in dc.VIDEO_EXTS
assert dc.categorize("movie.ass", "ass") == "video"
assert dc.categorize("movie.srt", "srt") == "other"      # 其余扩展名不受影响
assert dc.categorize("scratch.tmp", "tmp") == "junk"     # 内置 junk 仍在
assert dc.categorize("._movie.ass", "ass") == "junk"     # 纯函数层的 AppleDouble 规则
assert dc.CONFIG_INFO["path"] == str(good_dir / "config.json"), dc.CONFIG_INFO
msg = err.getvalue()
assert "已加载覆盖配置" in msg and str(good_dir / "config.json") in msg, msg
assert "skip_dirs 1 条" in msg and "extension_overrides 1 条" in msg, msg
print("  ✓ 校验先于写入:坏配置退出后内置表与 CONFIG_* 全都没动,也不打印「已加载」")

# -- 14. 锚定规则的纯函数证据(这是 SKILL.md 那张表的来源)----------------
m = dc._cfg_match
assert m(["临时", "2024"], ["临时/*"])              # 整段相对路径命中
assert m(["临时", "2024"], ["临时"])                # 任一级目录名命中
assert not m(["临时"], ["临时/*"])                  # 临时/* 不命中 临时 自身
assert not m(["深层", "临时", "子目录"], ["临时/*"])  # 不在 root 第一层就不算
assert m(["深层", "临时", "子目录"], ["深层/临时/*"])
assert m(["深层", "临时", "子目录"], ["临时"])        # 裸名字命中任意深度
assert m(["影视", "Season 1"], ["影视/*"])           # 含空格的中文路径
assert m(["SAMPLES"], ["samples"])                  # 大小写不敏感(目录大写)
assert m(["samples"], ["SAMPLES"])                  # 反方向:模式大写
assert m(["临时", "sub"], ["临时/SUB"])              # 整段路径也要双向不敏感
assert m(["Samples", "Sub"], ["SAMPLES/*"])          # 带通配时同样双向
assert m(["临时"], ["*"])                           # * 跨 / 匹配
assert not m([], ["*"])                             # root 下的散文件不吃 skip_dirs
assert not m(["临时"], [])                          # 没有模式 = 不命中
assert not m([], [])
print("  ✓ _cfg_match 锚定规则:15 条纯函数断言")
PYEOF
rm -rf "$CFG_SRC"

echo ""
echo "🎉 所有 smoke test 通过"
