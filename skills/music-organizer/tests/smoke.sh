#!/bin/bash
# music-organizer skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
MU_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["MU_SKILL_DIR"]
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
if name != "music-organizer":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=music-organizer")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/music_organizer.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(ID3 解析/曲目号/水印/标签对照) ==="
MU_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import struct
import sys

sys.path.insert(0, os.environ["MU_SKILL_DIR"])
import music_organizer as mo

# 构造一个 ID3v2.3 标签:TIT2=简单爱, TPE1=周杰伦, TALB=范特西, TRCK=02
def frame(fid, text):
    body = b"\x00" + text.encode("utf-16")[2:]  # enc=1(UTF-16) 去 BOM? 用 enc=0 latin 不行中文
    # 改用 enc=3 (UTF-8) 更稳
    body = b"\x03" + text.encode("utf-8")
    return fid.encode() + struct.pack(">I", len(body)) + b"\x00\x00" + body

frames = frame("TIT2", "简单爱") + frame("TPE1", "周杰伦") + frame("TALB", "范特西")
header = b"ID3" + bytes([3, 0, 0]) + bytes([
    (len(frames) >> 21) & 0x7F, (len(frames) >> 14) & 0x7F,
    (len(frames) >> 7) & 0x7F, len(frames) & 0x7F,
])
tags = mo.parse_id3(header + frames + b"\x00" * 20)
assert tags.get("TIT2") == "简单爱", tags
assert tags.get("TPE1") == "周杰伦", tags
assert tags.get("TALB") == "范特西", tags
# 非 ID3 数据返回空
assert mo.parse_id3(b"not an id3 tag at all") == {}

# 曲目号
assert mo.TRACK_NO_OK.match("01 爱在西元前")
assert mo.TRACK_NO_OK.match("01 - 简单爱")
assert mo.TRACK_NO_OK.match("1-01 忍者")
assert not mo.TRACK_NO_OK.match("简单爱")
assert mo.TRACK_NO_TAIL.search("简单爱 02")

# 水印
assert mo.WATERMARK_RE.search("歌曲【FLAC】")
assert mo.WATERMARK_RE.search("song [320K]")
assert mo.WATERMARK_RE.search("www.music.com 曲目")
assert not mo.WATERMARK_RE.search("01 普通曲目名")

# 标签对照
assert mo.tag_matches("周杰伦", "周杰伦")
assert mo.tag_matches("范特西 (2001)", "范特西")   # 路径带年份也能匹配
assert not mo.tag_matches("周杰伦", "林俊杰")

# 整轨镜像判定
alb = mo.Album("某专辑", "歌手/某专辑")
alb.has_cue = True
alb.audio = [{"ext": "flac", "stem": "whole", "name": "whole.flac",
              "rel": "whole.flac", "size": 1, "abs": "", "full": ""}]
assert alb.is_cue_sheet

print("  ✓ 纯函数用例通过(含 ID3v2 解析)")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/music-organizer-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/音乐"
# 合规专辑(带封面、曲目号)
mkdir -p "$ROOT/周杰伦/范特西 (2001)"
touch "$ROOT/周杰伦/范特西 (2001)/01 爱在西元前.mp3" \
      "$ROOT/周杰伦/范特西 (2001)/02 简单爱.mp3" \
      "$ROOT/周杰伦/范特西 (2001)/cover.jpg"
# 问题专辑:无封面、无曲目号、水印名、同曲多格式、歌词不配对
mkdir -p "$ROOT/歌手B/乱专辑"
touch "$ROOT/歌手B/乱专辑/简单爱.mp3" \
      "$ROOT/歌手B/乱专辑/简单爱.flac" \
      "$ROOT/歌手B/乱专辑/歌曲【FLAC】.mp3" \
      "$ROOT/歌手B/乱专辑/不存在的歌.lrc"
# 歌手层散曲
touch "$ROOT/歌手B/流浪.mp3"
# 缺歌手层的伪专辑(根级目录直接装音频)
mkdir -p "$ROOT/某张专辑"
touch "$ROOT/某张专辑/01 曲.mp3" "$ROOT/某张专辑/cover.jpg"
# 整轨镜像 + CUE(合法)
mkdir -p "$ROOT/歌手C/镜像专辑"
touch "$ROOT/歌手C/镜像专辑/whole.flac" "$ROOT/歌手C/镜像专辑/album.cue"
# 根目录散曲 + 垃圾
touch "$ROOT/散落.mp3" "$ROOT/.DS_Store"

MU_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["MU_SKILL_DIR"]
root = os.environ["FIXTURE_ROOT"]
out = os.path.join(tempfile.mkdtemp(), "issues.json")
r = subprocess.run(
    [sys.executable, f"{skill}/music_organizer.py", "scan",
     "--root", root, "--output", out],
    capture_output=True, text=True,
    encoding="utf-8", errors="replace",
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())
blob = json.dumps(data, ensure_ascii=False)
s = data["stats"]

assert data["skill"] == "music-organizer"
assert s["albums"] >= 4, s                     # 范特西/乱专辑/某张专辑/镜像专辑
assert s["albums_no_cover"] >= 1, s            # 乱专辑无封面
assert s["tracks_no_number"] >= 1, s           # 简单爱.mp3 无编号
assert s["tracks_watermark"] >= 1, s           # 歌曲【FLAC】
assert s["multi_format"] >= 1, s               # 简单爱 mp3+flac
assert s["lrc_mismatch"] >= 1, s               # 不存在的歌.lrc
assert s["loose_artist"] >= 1, s               # 歌手B/流浪.mp3
assert s["loose_root"] >= 1, s                 # 散落.mp3
assert s["misplaced_albums"] >= 1, s           # 某张专辑
assert s["cue_sheets"] >= 1, s                 # 镜像专辑
assert "缺专辑封面" in blob
assert "缺曲目号" in blob
assert "水印/音质标签" in blob
assert "同曲多格式共存" in blob
assert "歌词文件不配对" in blob
assert "歌手层散曲" in blob
assert "散曲(未归入" in blob
assert "缺歌手层" in blob
assert "整轨镜像+CUE" in blob
assert "垃圾" in blob
# 合规的范特西专辑不应有曲目/封面类问题
assert "范特西" not in [i["path"] for i in data["issues"]
                        if any("缺专辑封面" in p for p in i["problems"])], "范特西被误报缺封面"
print("  ✓ fixture scan 检出全部预期问题,合规专辑零误报")
PYEOF

echo "=== TEST 5: --help / --read-tags 参数 ==="
"$PY" "$SKILL_DIR/music_organizer.py" --help >/dev/null
"$PY" "$SKILL_DIR/music_organizer.py" scan --help | grep -q -- "--read-tags"
"$PY" "$SKILL_DIR/music_organizer.py" scan --help | grep -q -- "--tag-limit"
echo "  ✓ CLI help 可用"

echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/musicorgan-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/music_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/music_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
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
echo ""
echo "=== TEST 7: config.json 覆盖层(issue #15) ==="
# config.json 按**脚本自己所在的目录**(__file__)解析 —— 不是 cwd,也不是被扫描的
# root。所以这里把脚本复制进一个临时目录,模拟「zs skill 装好之后」的样子。
MUCFG_SRC="$(mktemp -d /tmp/musicorganizer-cfg.XXXXXX)"
mkdir -p "$MUCFG_SRC/installed" "$MUCFG_SRC/lib"
cp "$SKILL_DIR/music_organizer.py" "$MUCFG_SRC/installed/"
MU_CFG_SRC="$MUCFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
from pathlib import Path

src = os.environ["MU_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "music_organizer.py")
cfg = os.path.join(installed, "config.json")
lib = os.path.join(src, "lib")

# ---- fixture:歌手/专辑 三层 + 内置白名单功能区 + 归类边界 + 大小写 ----
FILES = [
    "周杰伦/范特西 (2001)/01 爱在西元前.mp3",   # 合规曲目号
    "周杰伦/范特西 (2001)/02 简单爱.mp3",
    "周杰伦/范特西 (2001)/cover.jpg",           # 封面(IMAGE_EXTS)
    "周杰伦/范特西 (2001)/专辑.log",            # ALLOWED_EXTS,静默放过
    "周杰伦/叶惠美/03 晴天.mp3",                # 缺封面专辑
    "周杰伦/叶惠美/extra.shn",                  # 非音频混入({"shn":"audio"} 的目标)
    "周杰伦/叶惠美/disc.zip",                   # 非音频混入(对照组,不该跟着变)
    "周杰伦/叶惠美/notes.bak",                  # JUNK_EXTS({"bak":"audio"} 的目标)
    "原盘/IMAGE/track.shn",                     # 原盘:非白名单目录名 → 当成歌手
    "原盘/IMAGE/cover.jpg",
    "原盘/说明.txt",                            # 歌手层的 txt:静默忽略
    "深层/原盘/子目录/x.mp3",                   # 同名的 原盘,但不在 root 第一层
    "新建文件夹/y.mp3",                         # BAD_DIR + 缺歌手层
    "合辑/群星/z.flac",                         # 内置 WHITELIST_DIRS(depth1 → 整棵跳过)
    "samples/t.mp3",                            # 大小写不敏感的目标目录
    "loose.mp3",                                # root 直下散曲
]
for rel in FILES:
    p = Path(lib) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


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


def dirs(d):
    return sorted(i["path"] for i in d["issues"] if i["is_dir"])


# -- 0. 基线:没有 config.json 时,stats 里连 config 这个键都不该出现 ---------
drop_cfg()
base, base_err = scan()
s = base["stats"]
assert "config" not in s, s.get("config")
assert "已加载覆盖配置" not in base_err, base_err
assert (s["artists"], s["albums"], s["audio_files"]) == (5, 6, 7), s
assert s["junk"] == 1 and s["non_audio_mixed"] == 3, s
assert s["loose_root"] == 1 and s["loose_artist"] == 2, s
assert s["misplaced_albums"] == 2 and s["albums_no_cover"] == 4, s
assert s["tracks_no_number"] == 3 and s["by_ext"] == {"mp3": 7}, s
assert base["count"] == 18, base["count"]
assert kinds(base, "非音频文件混入") == sorted([
    "周杰伦/叶惠美/disc.zip", "周杰伦/叶惠美/extra.shn", "原盘/IMAGE/track.shn",
]), kinds(base, "非音频文件混入")
assert kinds(base, "垃圾/临时文件") == ["周杰伦/叶惠美/notes.bak"]
assert kinds(base, "空专辑目录") == ["原盘/IMAGE"], kinds(base, "空专辑目录")
# 内置 WHITELIST_DIRS:合辑/ 整棵不扫,它里面的 z.flac 一条都不该出现
assert "合辑" not in s.get("by_ext", {}) and "flac" not in s["by_ext"], s["by_ext"]
assert not [i for i in base["issues"] if "合辑" in i["path"]], base["issues"]
print("  ✓ 无 config.json:stats 无 config 键、stderr 无提示、18 条问题与旧版一致")

# -- 1. extension_overrides 把 .shn 认成音频(issue #15 那类专有扩展名)------
write_cfg(json.dumps({"extension_overrides": {"shn": "audio"}}))
d, err = scan()
assert d["stats"]["audio_files"] == 9, d["stats"]["audio_files"]     # 7 + 2 个 .shn
assert d["stats"]["by_ext"] == {"mp3": 7, "shn": 2}, d["stats"]["by_ext"]
assert d["stats"]["non_audio_mixed"] == 1, d["stats"]["non_audio_mixed"]
assert kinds(d, "非音频文件混入") == ["周杰伦/叶惠美/disc.zip"], \
    kinds(d, "非音频文件混入")                       # 逐扩展名:.zip 没写就不动
# 认成音频之后它要接受曲目号检查,而 原盘/IMAGE 也不再是「空专辑目录」
assert sorted(kinds(d, "缺曲目号")) == sorted([
    "samples/t.mp3", "新建文件夹/y.mp3", "周杰伦/叶惠美/extra.shn",
    "原盘/IMAGE/track.shn", "深层/原盘/子目录/x.mp3"]), kinds(d, "缺曲目号")
assert kinds(d, "空专辑目录") == [], kinds(d, "空专辑目录")
assert "已加载覆盖配置" in err, err
assert d["stats"]["config"]["extension_overrides"] == {"shn": "audio"}
assert d["stats"]["config"]["whitelist_dirs"] == []
print("  ✓ extension_overrides 把 .shn 认成音频(专有格式),且只动这一个扩展名")

# -- 2. 键归一化 + junk 可以被救回成正常类别(证明「先从别的表里摘掉」)-----
write_cfg(json.dumps({"extension_overrides": {".BAK": "audio"}}))
d, _ = scan()
assert d["stats"]["junk"] == 0, d["stats"]["junk"]                   # 从 JUNK_EXTS 摘掉
assert d["stats"]["audio_files"] == 8, d["stats"]["audio_files"]
assert d["stats"]["by_ext"] == {"mp3": 7, "bak": 1}, d["stats"]["by_ext"]
assert kinds(d, "垃圾/临时文件") == [], kinds(d, "垃圾/临时文件")
assert "周杰伦/叶惠美/notes.bak" in kinds(d, "缺曲目号"), kinds(d, "缺曲目号")
print("  ✓ 键归一化(.BAK → bak);junk 里的 .bak 被救回成音频(不摘掉就永远先是垃圾)")

# -- 3. 从**更早命中**的表里摘掉:.jpg 改判 allowed 之后专辑就没封面了 ------
# 专辑分支的判定阶梯是 IMAGE_EXTS → lrc → cue → ALLOWED_EXTS → 非音频混入。
# 只往 ALLOWED_EXTS 里加 jpg 而不从 IMAGE_EXTS 里摘掉,这一级会先命中,覆盖等于没写。
write_cfg(json.dumps({"extension_overrides": {"jpg": "allowed"}}))
d, _ = scan()
assert d["stats"]["albums_no_cover"] == 5, d["stats"]["albums_no_cover"]   # 4 + 范特西
assert "周杰伦/范特西 (2001)" in kinds(d, "缺专辑封面"), kinds(d, "缺专辑封面")
assert d["stats"]["non_audio_mixed"] == 3, d["stats"]["non_audio_mixed"]
print("  ✓ jpg 从 IMAGE_EXTS 摘出来改判 allowed:范特西 立刻失去封面(阶梯顺序被尊重)")

# -- 4. 兜底类别 non_audio(表值是 None):写了等于显式维持原判 ------------
write_cfg(json.dumps({"extension_overrides": {"zip": "non_audio"}}))
d, err = scan()
assert kinds(d, "非音频文件混入") == kinds(base, "非音频文件混入")
assert d["stats"]["non_audio_mixed"] == 3, d["stats"]["non_audio_mixed"]
assert "已加载覆盖配置" in err
print("  ✓ 兜底类别 non_audio 可写:显式维持原判,不是静默无效")

# -- 5. whitelist_dirs:整棵子树跳过(不注册歌手/专辑、不产任何问题)--------
write_cfg(json.dumps({"whitelist_dirs": ["原盘"]}))
d, _ = scan()
assert d["stats"]["config"]["skipped_files"] == 4, d["stats"]["config"]
assert (d["stats"]["artists"], d["stats"]["albums"]) == (4, 4), d["stats"]
assert d["stats"]["audio_files"] == 6, d["stats"]["audio_files"]
assert d["stats"]["non_audio_mixed"] == 2, d["stats"]["non_audio_mixed"]
assert not [i for i in d["issues"] if i["path"].startswith("原盘")], d["issues"]
# 裸名字在任意深度都命中:深层/原盘/子目录/x.mp3 也被跳过
assert "深层" not in [i["path"] for i in d["issues"] if i["is_dir"]], dirs(d)
assert "深层/原盘/子目录/x.mp3" not in [i["path"] for i in d["issues"]]
# 没写到的目录一条不少
assert "新建文件夹" in dirs(d), dirs(d)
assert kinds(d, "垃圾/临时文件") == ["周杰伦/叶惠美/notes.bak"]
# 内置的 合辑/ 仍然被跳过(whitelist_dirs 是**追加**,不是替换)—— 它里面的
# z.flac 既没被扫到、也没被算进 skipped_files(4 = 原盘 3 + 深层/原盘 1)
assert "flac" not in d["stats"]["by_ext"], d["stats"]["by_ext"]
print("  ✓ whitelist_dirs=[原盘]:整棵子树跳过(4 个文件),内置的 合辑/ 仍然跳过")

# -- 6. 锚定:原盘/* 命中 原盘/IMAGE,但不命中 原盘 自己这一层 --------------
write_cfg(json.dumps({"whitelist_dirs": ["原盘/*"]}))
d, _ = scan()
assert d["stats"]["config"]["skipped_files"] == 2, d["stats"]["config"]
assert (d["stats"]["artists"], d["stats"]["albums"]) == (5, 5), d["stats"]
assert "原盘/IMAGE/track.shn" not in [i["path"] for i in d["issues"]], d["issues"]
assert kinds(d, "空专辑目录") == [], kinds(d, "空专辑目录")   # IMAGE 被整棵跳过
# 深层/原盘 不命中 原盘/*(相对路径不以 原盘/ 开头)→ 照报
assert "深层/原盘" in kinds(d, "缺专辑封面"), kinds(d, "缺专辑封面")
assert "深层/原盘/子目录/x.mp3" in kinds(d, "缺曲目号")
print("  ✓ 原盘/* 命中 原盘/IMAGE(跳过 2 个),但不命中 原盘 自身那一层")

# -- 7. 带 / 的模式锚定在 root:深层/原盘/* 只命中那一条 --------------------
write_cfg(json.dumps({"whitelist_dirs": ["深层/原盘/*"]}))
d, _ = scan()
assert d["stats"]["config"]["skipped_files"] == 1, d["stats"]["config"]
assert (d["stats"]["artists"], d["stats"]["albums"]) == (5, 6), d["stats"]
assert "原盘/IMAGE/track.shn" in [i["path"] for i in d["issues"]], d["issues"]
# 深层/原盘 现在是张没有音频的专辑(它唯一的曲目被跳过了)
assert kinds(d, "空专辑目录") == sorted(["深层/原盘", "原盘/IMAGE"]), \
    kinds(d, "空专辑目录")
print("  ✓ 深层/原盘/* 只命中 root 下那一条,不波及同名的 原盘/")

# -- 8. "*" 也管不到直接躺在 root 下的文件(白名单只作用于目录)-------------
write_cfg(json.dumps({"whitelist_dirs": ["*"]}))
d, _ = scan()
assert d["stats"]["config"]["skipped_files"] == 15, d["stats"]["config"]
assert (d["stats"]["artists"], d["stats"]["albums"]) == (0, 0), d["stats"]
assert d["stats"]["audio_files"] == 1 and d["stats"]["loose_root"] == 1, d["stats"]
assert [i["path"] for i in d["issues"]] == ["loose.mp3"], d["issues"]
print("  ✓ 白名单只作用于目录:* 也管不到直接躺在 root 下的 loose.mp3")

# -- 9. 空对象 {}:加载并提示,但行为与无配置逐条相同 ----------------------
def shape(x):
    return sorted((i["path"], tuple(i["problems"])) for i in x["issues"])


write_cfg("{}")
d, err = scan()
assert shape(d) == shape(base), "空对象改变了判定"
assert d["stats"]["audio_files"] == base["stats"]["audio_files"]
assert "config" in d["stats"]
assert d["stats"]["config"]["whitelist_dirs"] == []
assert d["stats"]["config"]["extension_overrides"] == {}
assert d["stats"]["config"]["skipped_files"] == 0
assert "已加载覆盖配置" in err, err
print("  ✓ 空对象 {} 加载成功:问题清单与无配置时逐条相同,但会明确提示已加载")

# -- 10. 配置文件在,但某个键不在 → 那个键不产生任何影响 ------------------
write_cfg(json.dumps({"whitelist_dirs": ["原盘"]}))
d, _ = scan()
assert d["stats"]["config"]["extension_overrides"] == {}
assert "周杰伦/叶惠美/extra.shn" in kinds(d, "非音频文件混入")   # 没写 ext 覆盖
print("  ✓ 只有 whitelist_dirs 时,扩展名判定一条都不变")

# -- 11. 被扫描目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)---
drop_cfg()
root_cfg = os.path.join(lib, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"extension_overrides": {"shn": "audio"},
               "whitelist_dirs": ["*"]}, fh)
for cwd in (src, lib, installed):        # 三种 cwd 都不能让它被读到
    d, err = scan(cwd=cwd)
    assert "周杰伦/叶惠美/extra.shn" in kinds(d, "非音频文件混入"), \
        (cwd, kinds(d, "非音频文件混入"))
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
    assert d["stats"]["audio_files"] == 7, (cwd, d["stats"]["audio_files"])
os.remove(root_cfg)
print("  ✓ 被扫描目录里的 config.json 被忽略(3 种 cwd 都试过),只当普通文件扫")

# -- 12. 畸形配置:一律 exit=1,指名文件与键,且不污染 --json stdout --------
BAD = [
    ("非法 JSON", "{not json", "不是合法 JSON"),
    ("空文件", "", "不是合法 JSON"),
    ("顶层不是对象", '"just a string"', "顶层"),
    ("未知键(少写一个 s)", '{"whitelist_dir": ["原盘"]}', "whitelist_dir"),
    ("whitelist_dirs 不是数组", '{"whitelist_dirs": "原盘"}', "whitelist_dirs"),
    ("whitelist_dirs 元素不是字符串", '{"whitelist_dirs": [null]}',
     "whitelist_dirs[0]"),
    ("whitelist_dirs 空字符串", '{"whitelist_dirs": ["  "]}', "whitelist_dirs[0]"),
    ("whitelist_dirs 绝对路径", '{"whitelist_dirs": ["/原盘"]}', "绝对路径"),
    ("whitelist_dirs Windows 盘符", '{"whitelist_dirs": ["Z:\\\\music"]}',
     "绝对路径"),
    ("extension_overrides 不是对象", '{"extension_overrides": []}',
     "extension_overrides"),
    ("类别值不是字符串", '{"extension_overrides": {"shn": true}}',
     "必须是字符串类别名"),
    ("扩展名含多个点", '{"extension_overrides": {"tar.gz": "audio"}}', "tar.gz"),
    ("扩展名归一化后为空", '{"extension_overrides": {".": "audio"}}', "空的"),
    ("类别名不存在", '{"extension_overrides": {"shn": "lossless"}}', "lossless"),
    ("两个键都拼错", '{"whitelist_dir": [], "extension_override": {}}',
     "extension_override"),
]
# 本 skill 的类别表与另外四个 scanner **不同**:同一份配置在别处合法、在这里必须
# 报错 —— 这条钉住「CONFIG_EXT_TABLES 是 per-skill 的」,不是全局一张表。
for label, text, needle in BAD + [
    ("photo-organizer 的类别名", '{"extension_overrides": {"shn": "photo"}}',
     "photo"),
    ("file-sorter / work-organizer 的类别名",
     '{"extension_overrides": {"shn": "doc"}}', "doc"),
    ("portfolio-organizer 的类别名", '{"extension_overrides": {"shn": "source"}}',
     "source"),
]:
    write_cfg(text)
    r = run(expect=1)
    assert r.stdout == "", (label, "错误不能污染 --json 的 stdout", r.stdout[:200])
    assert "config.json" in r.stderr, (label, r.stderr)
    assert needle in r.stderr, (label, needle, r.stderr)
    assert "❌" in r.stderr, (label, r.stderr)
# 报错里要列出**本 skill** 的可用类别,照着改就能修
write_cfg('{"extension_overrides": {"shn": "doc"}}')
r = run(expect=1)
for cat in ("audio", "image", "allowed", "junk", "non_audio"):
    assert cat in r.stderr, (cat, r.stderr)
drop_cfg()
print(f"  ✓ {len(BAD) + 3} 种畸形配置全部 exit=1、只写 stderr、指名键,"
      "并列出本 skill 的可用类别")

# -- 13. 校验先于写入 + 锚定规则的纯函数证据 ------------------------------
sys.path.insert(0, installed)
import contextlib                                           # noqa: E402
import io                                                   # noqa: E402
import music_organizer as mo                                # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "whitelist_dirs": ["原盘"],
    "extension_overrides": {"shn": "photo"},     # photo-organizer 的类别,这里没有
}), encoding="utf-8")
before = (set(mo.AUDIO_EXTS), set(mo.IMAGE_EXTS), set(mo.ALLOWED_EXTS),
          set(mo.JUNK_EXTS), set(mo.CUE_IMAGE_EXTS))
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        mo.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "photo" in str(e) and "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()     # 失败路径不该先打印「已加载」
assert mo.CONFIG_WHITELIST == [], mo.CONFIG_WHITELIST
assert mo.CONFIG_INFO == {}, mo.CONFIG_INFO
assert (set(mo.AUDIO_EXTS), set(mo.IMAGE_EXTS), set(mo.ALLOWED_EXTS),
        set(mo.JUNK_EXTS), set(mo.CUE_IMAGE_EXTS)) == before

# 合法配置生效之后:CUE_IMAGE_EXTS 必须**分毫不动** —— 它刻意不在
# CONFIG_EXT_TABLES 里(它是 AUDIO_EXTS 的派生子集,摘掉会静默改变 is_cue_sheet)。
ok_dir = Path(src) / "ok"
ok_dir.mkdir(exist_ok=True)
(ok_dir / "config.json").write_text(
    json.dumps({"extension_overrides": {"flac": "allowed"}}), encoding="utf-8")
with contextlib.redirect_stderr(io.StringIO()):
    mo.load_config(ok_dir)
assert "flac" not in mo.AUDIO_EXTS, mo.AUDIO_EXTS
assert "flac" in mo.ALLOWED_EXTS, mo.ALLOWED_EXTS
assert set(mo.CUE_IMAGE_EXTS) == {"ape", "flac", "wav", "wv", "tta"}, \
    mo.CUE_IMAGE_EXTS
assert set(mo.CONFIG_EXT_TABLES) == {
    "audio", "image", "allowed", "junk", "non_audio"}, set(mo.CONFIG_EXT_TABLES)

m = mo._cfg_match
assert m(["原盘", "IMAGE"], ["原盘/*"])               # 整段相对路径命中
assert m(["原盘", "IMAGE"], ["原盘"])                 # 任一级目录名命中
assert not m(["原盘"], ["原盘/*"])                    # 原盘/* 不命中 原盘 自身
assert not m(["深层", "原盘", "子目录"], ["原盘/*"])   # 不在 root 第一层就不算
assert m(["深层", "原盘", "子目录"], ["深层/原盘/*"])
assert m(["深层", "原盘", "子目录"], ["原盘"])         # 裸名字命中任意深度
assert m(["周杰伦", "范特西 (2001)"], ["周杰伦/*"])    # 含空格与括号的路径
assert m(["SAMPLES"], ["samples"])                    # 大小写不敏感(目录大写)
assert m(["samples"], ["SAMPLES"])                    # 反方向:模式大写
assert m(["原盘", "image"], ["原盘/IMAGE"])            # 整段路径也要双向不敏感
assert m(["Samples", "Sub"], ["SAMPLES/*"])           # 带通配时同样双向
assert m(["原盘"], ["*"])                             # * 跨 / 匹配
assert not m([], ["*"])                               # root 下的散文件不吃白名单
assert not m(["原盘"], [])                            # 没有模式 = 不命中
print("  ✓ 校验先于写入(内置五张表分毫未动)+ CUE_IMAGE_EXTS 不受覆盖影响"
      " + _cfg_match 锚定规则 14 条断言")
PYEOF
rm -rf "$MUCFG_SRC"

echo "=== TEST 8: @Recycle(极空间回收站)不当活库扫描 ==="
# 家族审计 F4:此前 @Recycle 只在 photo-organizer 的 SKIP_DIRS 里,
# music-organizer 会把回收站里的目录注册成「歌手」、把已删除的歌当库内曲目。
MU_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import json
import os
import shutil
import subprocess
import sys
import tempfile

skill = os.environ["MU_SKILL_DIR"]
script = f"{skill}/music_organizer.py"

root = tempfile.mkdtemp(prefix="mu-recycle.")
try:
    os.makedirs(os.path.join(root, "周杰伦"))
    os.makedirs(os.path.join(root, "@Recycle", "回收歌手"))
    with open(os.path.join(root, "周杰伦", "晴天.mp3"), "wb") as fh:
        fh.write(b"M" * 3000)
    with open(os.path.join(root, "@Recycle", "回收歌手", "被删的歌.mp3"), "wb") as fh:
        fh.write(b"M" * 2000)
    r = subprocess.run([sys.executable, script, "scan", "--root", root, "--json"],
                       capture_output=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    d = json.loads(r.stdout)
    s = d["stats"]
    blob = json.dumps(d, ensure_ascii=False)
    # 回收站在整份报告里必须完全不可见
    assert "@Recycle" not in blob, blob[:300]
    assert "回收歌手" not in blob, blob[:300]
    assert "被删的歌" not in blob, blob[:300]
    assert s["artists"] == 1, s              # 只有 周杰伦
    assert s["audio_files"] == 1, s
    assert s["audio_size_bytes"] == 3000, s  # 被删的 2000B 不计入
    print("  ✓ @Recycle 被跳过:回收站目录不再是「歌手」,已删除的歌不再入库统计")
finally:
    shutil.rmtree(root, ignore_errors=True)
PYEOF

echo "🎉 所有 smoke test 通过"
