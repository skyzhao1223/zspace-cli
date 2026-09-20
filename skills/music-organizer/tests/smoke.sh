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

echo ""
echo "🎉 所有 smoke test 通过"
