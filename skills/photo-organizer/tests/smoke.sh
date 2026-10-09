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
import tempfile
from datetime import datetime, timezone

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

# ── --exif:外部工具输出的日期解析(issue #14)─────────────────────────
# exiftool 会把坏值原样吐出来,所以校验必须在解析函数里做
assert po.parse_exif_datetime("2024:05:03 14:22:31") == "2024-05-03"
assert po.parse_exif_datetime("2024:05:03 14:22:31.50") == "2024-05-03"  # 带小数秒
assert po.parse_exif_datetime("0000:00:00 00:00:00") is None   # 相机时钟没设过
assert po.parse_exif_datetime("not a date at all") is None     # 实测 exiftool 会照吐
assert po.parse_exif_datetime("2024:02:30 10:00:00") is None   # 2 月 30 日
assert po.parse_exif_datetime("2024:05:03 25:61:61") is None   # 时分秒越界
bad_year = po.MIN_PLAUSIBLE_YEAR - 1
assert po.parse_exif_datetime(f"{bad_year}:06:15 10:00:00") is None
assert po.parse_exif_datetime("") is None
assert po.parse_exif_datetime(None) is None
assert po.parse_exif_datetime(20240503) is None

# mdls 给的是 UTC,要换算到本机时区
# (1) 用本机偏移量构造 → 换算回来是同一天,断言与 runner 时区无关
offset = datetime.now().astimezone().strftime("%z")            # 形如 +0800
for form in (offset, offset[:3] + ":" + offset[3:]):           # +0800 / +08:00 都要收
    assert po.parse_mdls_datetime(
        f"kMDItemContentCreationDate = 2024-05-03 14:22:31 {form}"
    ) == "2024-05-03", form
# (2) 真的在测换算:同一时刻的两种写法(+0000 与 +1000),naive 日期差一天。
#     少了 astimezone() 的话两者会给出不同结果 —— 这条在任何时区的 runner 上都成立
same_instant_utc = po.parse_mdls_datetime(
    "kMDItemContentCreationDate = 2024-05-03 23:30:00 +0000")
same_instant_au = po.parse_mdls_datetime(
    "kMDItemContentCreationDate = 2024-05-04 09:30:00 +1000")
assert same_instant_utc == same_instant_au, (same_instant_utc, same_instant_au)
# (3) 且必须等于该时刻换算到本机时区的日期(独立算一遍,不调被测函数)
expected_local = datetime(2024, 5, 3, 23, 30,
                          tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d")
assert same_instant_utc == expected_local, (same_instant_utc, expected_local)
assert len(po.parse_mdls_datetime(
    "kMDItemContentCreationDate = 2024-05-03 14:22:31 +0000") or "") == 10
assert po.parse_mdls_datetime("kMDItemContentCreationDate = (null)") is None
assert po.parse_mdls_datetime("could not find /x.jpg.") is None  # 未被 Spotlight 索引
assert po.parse_mdls_datetime("") is None
assert po.parse_mdls_datetime(None) is None

# ── 工具探测:PATH 里没有就一个都不认,且 resolve() 不抛异常(负控制)────
saved_path = os.environ.get("PATH")
with tempfile.TemporaryDirectory() as empty_dir:
    os.environ["PATH"] = empty_dir
    try:
        none_found = po.DateResolver()
        assert none_found.exiftool is None, none_found.exiftool
        assert none_found.mdls is None, none_found.mdls
        assert none_found.available is False
        assert none_found.resolve(["/nonexistent/a.jpg"]) == {}
    finally:
        if saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved_path

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
    encoding="utf-8", errors="replace",
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
echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/photoorgan-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/photo_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/photo_organizer.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
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

echo "=== TEST 7: DateResolver 契约(假的 subprocess.run,纯 python 全平台可跑)==="
# exiftool 的几个真实怪癖都是实测出来的,不是想象的:
#   * 只要有一个文件读不了它就 exit=1,但 stdout 的 JSON 对读到的文件依然有效
#   * 打不开的文件根本不出现在 JSON 里(只往 stderr 报错)
#   * 坏日期(全零 0000:00:00)会原样吐出来
# CI 的 ubuntu/windows runner 上都没装 exiftool,所以这里不依赖真二进制,
# 而是把 subprocess.run 换掉,直接考解析器的契约。
PHOTO_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import io
import json
import os
import sys

sys.path.insert(0, os.environ["PHOTO_SKILL_DIR"])
import photo_organizer as po


class FakeProc:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def paths_of(cmd):
    """从 argv 里挑出文件路径。

    不能简单按"不以 - 开头"过滤:`-charset UTF8` 的取值 UTF8 就会被误当成路径。
    这里用 fixture 路径的固定前缀来认,顺带也就不依赖 flag 的顺序和个数。
    """
    return [a for a in cmd[1:] if str(a).startswith("/lib/")]


def make_resolver():
    r = po.DateResolver()
    r.exiftool = "/fake/exiftool"     # 直接塞假路径,绕开 which()
    r.mdls = None                     # 先关掉 mdls 这一档,单独考 exiftool
    return r


def ok_json(cmd, **kw):
    body = [{"SourceFile": p, "DateTimeOriginal": "2024:05:03 14:22:31"}
            for p in paths_of(cmd)]
    return FakeProc(json.dumps(body))


real_run = po.subprocess.run
try:
    # -- mdls 只在 macOS 认:Windows/Linux 上根本没这个命令,不该去探测 --------
    # (在 macOS 上跑这条才有意义 —— 那里 which('mdls') 真的能找到)
    # 只伪装成 linux,不伪装 win32:py3.12 的 shutil.which 在 win32 分支里会去碰
    # _winapi,而它在非 Windows 上是 None,直接 AttributeError(py3.9 没这个分支,
    # 所以这个坑只在 3.12 上炸 —— 两个版本都跑才试得出来)
    from unittest import mock

    with mock.patch.object(sys, "platform", "linux"):
        assert po.DateResolver().mdls is None, "非 macOS 不该去探测 mdls"
    print("  ✓ 非 macOS 不探测 mdls")

    # -- 正常批量:450 个文件只应起 ceil(450/CHUNK) 个进程,不是 450 个 --------
    calls = []

    def counting(cmd, **kw):
        calls.append(paths_of(cmd))
        return ok_json(cmd)

    po.subprocess.run = counting
    paths = [f"/lib/IMG_{i:05d}.jpg" for i in range(450)]
    got = make_resolver().resolve(paths)
    expect_calls = -(-len(paths) // po.EXIFTOOL_CHUNK)      # 向上取整
    assert len(calls) == expect_calls, (len(calls), expect_calls)
    assert max(len(c) for c in calls) <= po.EXIFTOOL_CHUNK
    # 这条才是重点,而且刻意不引用 EXIFTOOL_CHUNK(否则改小常量它就跟着假通过):
    # 批量是 --exif 的性能前提,一张图一个进程在几万张的库上是数量级的倒退
    assert len(calls) * 10 <= len(paths), (len(calls), len(paths))
    assert len(got) == len(paths), len(got)
    assert all(v == ("2024-05-03", "exif") for v in got.values())
    print(f"  ✓ 批量:{len(paths)} 个文件只起了 {len(calls)} 个 exiftool 进程")

    # -- 同名不同目录必须各归各的日期(按 basename 匹配就会串)--------------
    # 照片库里 IMG_1234.jpg 在多个目录重名是常态,串了就会把照片搬错年份
    def distinct(cmd, **kw):
        table = {"/lib/2022/IMG_same.jpg": "2022:06:15 10:00:00",
                 "/lib/2023/IMG_same.jpg": "2023:09:24 10:00:00"}
        return FakeProc(json.dumps(
            [{"SourceFile": p, "DateTimeOriginal": table[p]} for p in paths_of(cmd)]))

    po.subprocess.run = distinct
    twins = ["/lib/2022/IMG_same.jpg", "/lib/2023/IMG_same.jpg"]
    got = make_resolver().resolve(twins)
    assert got["/lib/2022/IMG_same.jpg"] == ("2022-06-15", "exif"), got
    assert got["/lib/2023/IMG_same.jpg"] == ("2023-09-24", "exif"), got
    print("  ✓ 同名不同目录各归各的日期(不是按 basename 匹配)")

    # -- exiftool 打不开的那个同名文件,不许借用兄弟文件的日期 --------------
    # 实测:读不了的文件 exiftool 直接不输出条目。宁可丢掉这个日期退回下一档,
    # 也不能把 2022 的日期安到 2023 那张上 —— 那是会搬错目录的静默错误。
    def partial(cmd, **kw):
        return FakeProc(json.dumps(
            [{"SourceFile": "/lib/2022/IMG_same.jpg",
              "DateTimeOriginal": "2022:06:15 10:00:00"}]))

    po.subprocess.run = partial
    got = make_resolver().resolve(twins)
    assert got == {"/lib/2022/IMG_same.jpg": ("2022-06-15", "exif")}, got
    print("  ✓ 不在输出里的文件不借用同名兄弟的日期")

    # -- exit=1 但 stdout 有效 → 必须照用(否则整批日期白丢)----------------
    def rc_one(cmd, **kw):
        return FakeProc(ok_json(cmd).stdout, returncode=1)

    po.subprocess.run = rc_one
    got = make_resolver().resolve(["/lib/a.jpg", "/lib/b.jpg"])
    assert len(got) == 2, "exit=1 但 JSON 有效时必须照用"
    print("  ✓ exit=1 + 有效 JSON:不当成失败")

    # -- 各种坏输出 → 一律失败即退,返回空、不抛异常 ------------------------
    # 顺手断言"退档要说出来":静默降级会让用户以为 EXIF 生效了
    noise = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = noise
    try:
        for label, proc in (
            ("输出不是合法 JSON", FakeProc("not json {{{")),
            ("空 stdout + exit 1", FakeProc("", returncode=1)),
            ("空数组(文件不在输出里)", FakeProc("[]")),
            ("全零日期", FakeProc(json.dumps(
                [{"SourceFile": "/lib/a.jpg",
                  "DateTimeOriginal": "0000:00:00 00:00:00"}]))),
            ("SourceFile 缺失", FakeProc(json.dumps(
                [{"DateTimeOriginal": "2024:05:03 14:22:31"}]))),
        ):
            po.subprocess.run = lambda cmd, _p=proc, **kw: _p
            assert make_resolver().resolve(["/lib/a.jpg"]) == {}, label
    finally:
        sys.stderr = real_stderr
    said = noise.getvalue()
    assert "不是合法 JSON" in said, said
    print("  ✓ 坏输出(非法 JSON/空/缺条目/全零日期)全部失败即退,且退档有告警")

    # -- exiftool 直接炸(OSError)也不能把扫描带崩 --------------------------
    def boom(cmd, **kw):
        raise OSError("exiftool 其实不在")

    noise = io.StringIO()
    sys.stderr = noise
    try:
        po.subprocess.run = boom
        assert make_resolver().resolve(["/lib/a.jpg"]) == {}
    finally:
        sys.stderr = real_stderr
    assert "exiftool 调用失败" in noise.getvalue(), noise.getvalue()
    print("  ✓ exiftool 抛 OSError 时不外溢,只告警")

    # -- exiftool 拿不到 → 交给 mdls,而且必须一次一个 ----------------------
    # mdls 收多个文件时只按顺序打印值、遇到没索引的就中止整批,没法按行号回填
    seen = []

    def mixed(cmd, **kw):
        if "mdls" in str(cmd[0]):
            seen.append(cmd[-1])
            return FakeProc("kMDItemContentCreationDate = 2019-01-02 03:04:05 +0000")
        return FakeProc("[]")            # exiftool 一个都没解出来

    po.subprocess.run = mixed
    r = make_resolver()
    r.mdls = "/fake/mdls"
    got = r.resolve(["/lib/a.jpg", "/lib/b.jpg"])
    assert seen == ["/lib/a.jpg", "/lib/b.jpg"], seen        # 一次一个,可归属
    assert set(got) == {"/lib/a.jpg", "/lib/b.jpg"}, got
    assert all(v[1] == "mdls" for v in got.values()), got
    print("  ✓ exiftool 落空 → 逐个走 mdls(不批量,否则日期会安错文件)")

    # -- mdls 返回 (null)(未被 Spotlight 索引)→ 什么都没有 -----------------
    po.subprocess.run = lambda cmd, **kw: FakeProc(
        "kMDItemContentCreationDate = (null)")
    r = po.DateResolver()
    r.exiftool = None                     # 只留 mdls 这一档,单独考它
    r.mdls = "/fake/mdls"
    assert r.resolve(["/lib/a.jpg"]) == {}
    print("  ✓ mdls 返回 (null) 时不认账")
finally:
    po.subprocess.run = real_run
PYEOF

echo "=== TEST 8: --exif 端到端(exiftool → mdls → mtime 逐级回退)==="
# issue #14。fixture 里放一张**完全没有 EXIF 段**的 JPEG,断言它退回 mtime。
# 关键负控制:把 PATH 收窄到只剩解释器自己的目录,exiftool / mdls 就都不存在了
# —— 这样"回退到 mtime"在 ubuntu / windows / macOS runner 上都是确定结果,
# 不取决于机器上碰巧装了什么。
PHOTO_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime

skill = os.environ["PHOTO_SKILL_DIR"]
script = os.path.join(skill, "photo_organizer.py")
fix = tempfile.mkdtemp(prefix="photo-organizer-exif.")
root = os.path.join(fix, "照片")
os.makedirs(root)
# 把 PATH 换成一个空目录:exiftool / mdls 就都探测不到了。
# 注意不能用 os.path.dirname(sys.executable) —— macOS 的系统 python3 在 /usr/bin,
# 那里正好也有 mdls,负控制会假通过。解释器本身用绝对路径启动,不依赖 PATH。
BARE_PATH = os.path.join(fix, "no-tools")
os.makedirs(BARE_PATH)


def write_jpeg(path, when=None):
    """纯 stdlib 造一张最小 JPEG。when 给了就塞一段带 DateTimeOriginal 的 EXIF。"""
    soi = b"\xff\xd8"
    if when is None:
        with open(path, "wb") as fh:
            fh.write(soi + b"\xff\xd9")           # 没有 APP1/EXIF 段
        return
    raw = when + b"\0"
    sub = 8 + 2 + 12 * 2 + 4                      # Exif SubIFD 的偏移
    ifd0 = struct.pack("<H", 2)
    ifd0 += struct.pack("<HHII", 0x0132, 2, len(raw), sub + 2 + 12 + 4)
    ifd0 += struct.pack("<HHII", 0x8769, 4, 1, sub)
    ifd0 += struct.pack("<I", 0)
    sub_ifd = struct.pack("<H", 1)
    sub_ifd += struct.pack("<HHII", 0x9003, 2, len(raw), sub + 2 + 12 + 4)
    sub_ifd += struct.pack("<I", 0)
    tiff = b"II*\0" + struct.pack("<I", 8) + ifd0 + sub_ifd + raw
    app1 = b"Exif\0\0" + tiff
    with open(path, "wb") as fh:
        fh.write(soi + b"\xff\xe1" + struct.pack(">H", len(app1) + 2)
                 + app1 + b"\xff\xd9")


seq = [0]


def scan(extra, path=None):
    """跑一次 scan,返回 (JSON, CompletedProcess)。"""
    seq[0] += 1
    out = os.path.join(fix, "issues-%d.json" % seq[0])
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    proc = subprocess.run(
        [sys.executable, script, "scan", "--root", root, "--output", out] + extra,
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    with open(out, encoding="utf-8") as fh:
        return json.load(fh), proc


try:
    # mtime 固定成一个过去的日期(本机时区构造,round-trip 回来不会跨天)
    mtime = datetime(2021, 3, 15, 9, 30).timestamp()
    write_jpeg(os.path.join(root, "IMG_hasexif.jpg"), b"2024:05:03 14:22:31")
    write_jpeg(os.path.join(root, "IMG_noexif.jpg"))     # ← issue 要求的无 EXIF fixture
    for name in ("IMG_hasexif.jpg", "IMG_noexif.jpg"):
        os.utime(os.path.join(root, name), (mtime, mtime))
    # 一个名字排在散文件之后的坏目录:它的 issue 在遍历中就产生,而散文件的 issue
    # 在 --exif 下要等批量解析完才产生。有它才能验出"结果被插回原遍历顺序",
    # 而不是简单 append 到末尾(顺带也覆盖了 CJK 目录名)
    os.makedirs(os.path.join(root, "新建文件夹"))

    # -- 8a 负控制:exiftool / mdls 都不在 → 两张图都必须退回 mtime ------------
    fell_back, proc = scan(["--exif"], path=BARE_PATH)
    stats = fell_back["stats"]
    assert stats["date_tools"] == {"exiftool": None, "mdls": None}, stats["date_tools"]
    assert stats["date_sources"]["exif"] == 0, stats["date_sources"]
    assert stats["date_sources"]["mdls"] == 0, stats["date_sources"]
    assert stats["date_sources"]["mtime"] == 2, stats["date_sources"]
    byname = {i["name"]: i for i in fell_back["issues"] if not i["is_dir"]}
    assert set(byname) == {"IMG_hasexif.jpg", "IMG_noexif.jpg"}, byname
    for name, issue in sorted(byname.items()):
        # 有 EXIF 的那张也退回 mtime —— 证明是"工具不在",不是"文件不行"
        assert issue["date"] == "2021-03-15", (name, issue)
        assert issue["date_source"] == "mtime(弱,仅参考)", (name, issue)
        assert issue["suggested_dir"] == "2021/2021-03", (name, issue)
    # 退回 mtime 这件事必须**说出来**,不能静默(否则看起来像 EXIF 生效了)
    assert "没找到 exiftool" in proc.stderr, proc.stderr
    assert "退回了 mtime" in proc.stdout, proc.stdout
    print("  ✓ 8a exiftool/mdls 都不在 → 全部退回 mtime,且报告里明说了")

    # -- 8b 不加 --exif 时行为不变(opt-in 负控制)---------------------------
    plain, _ = scan([])
    assert "date_sources" not in plain["stats"], "默认模式不该多出 --exif 专有字段"
    assert "date_tools" not in plain["stats"], "默认模式不该多出 --exif 专有字段"
    trimmed = dict(stats)
    trimmed.pop("date_sources")
    trimmed.pop("date_tools")
    assert plain["stats"] == trimmed, "去掉 --exif 专有字段后 stats 必须与默认一致"
    # 工具都不在时,--exif 的产物应与默认模式逐条相同(含顺序)
    assert plain["issues"] == fell_back["issues"], "降级后的 issues 与默认模式不一致"
    # 自检:上面那条只有在"目录 issue 排在散文件之后"时才真的在考顺序。
    # fixture 哪天变了、两者顺序颠倒,断言就会退化成永远通过 —— 这里堵住。
    order = [i["path"] for i in plain["issues"]]
    assert order.index("IMG_noexif.jpg") < order.index("新建文件夹"), order
    print("  ✓ 8b 不加 --exif 时输出与旧版一致;工具缺失时 --exif 也只是退回原行为")

    # -- 8c --json 与 --output 不能漂移 -------------------------------------
    env = dict(os.environ, PATH=BARE_PATH)
    proc = subprocess.run(
        [sys.executable, script, "scan", "--root", root, "--json", "--exif"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    via_stdout = json.loads(proc.stdout)
    via_stdout.pop("generated_at")
    via_file = dict(fell_back)
    via_file.pop("generated_at")
    assert via_stdout == via_file, "--json 与 --output 两条路径的结果漂移了"
    print("  ✓ 8c --json 与 --output 结果一致")

    # -- 8d happy path:机器上真有 exiftool 时才跑 ---------------------------
    if shutil.which("exiftool"):
        dated, _ = scan(["--exif"])
        dstats = dated["stats"]
        assert dstats["date_tools"]["exiftool"], dstats["date_tools"]
        assert dstats["date_sources"]["exif"] >= 1, dstats["date_sources"]
        dbyname = {i["name"]: i for i in dated["issues"] if not i["is_dir"]}
        got = dbyname["IMG_hasexif.jpg"]
        # EXIF 拍摄日期必须盖过 mtime(这正是 issue #14 要修的)
        assert got["date"] == "2024-05-03", got
        assert got["date_source"] == "exif", got
        assert got["suggested_dir"] == "2024/2024-05", got
        # 强证据日期才进归档分布,mtime 不进
        assert dstats["by_month"].get("2024-05", 0) >= 1, dstats["by_month"]
        print("  ✓ 8d exiftool 在场:真 EXIF 拍摄日期盖过 mtime 并进归档分布")
    else:
        print("  ⚠️ 8d 本机没有 exiftool,跳过 happy path(只验证了 mtime 回退)")
finally:
    shutil.rmtree(fix, ignore_errors=True)
PYEOF

echo "=== TEST 9: config.json 覆盖层(issue #15) ==="
# config.json 按**脚本自己所在目录**(__file__)解析 —— 不是 cwd,也不是被扫描的
# root。所以这里把脚本复制进一个临时目录,模拟「zs skill 装好之后」的样子。
PCFG_SRC="$(mktemp -d /tmp/photoorganizer-cfg.XXXXXX)"
mkdir -p "$PCFG_SRC/installed" "$PCFG_SRC/lib"
cp "$SKILL_DIR/photo_organizer.py" "$PCFG_SRC/installed/"
PO_CFG_SRC="$PCFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
from pathlib import Path

src = os.environ["PO_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "photo_organizer.py")
cfg = os.path.join(installed, "config.json")
lib = os.path.join(src, "lib")

# ---- fixture:issue #15 点名的 原盘/VIDEO_TS + 故意的样本文件 + 归类边界 ----
for d in ("原盘/VIDEO_TS", "深层/原盘/子目录", "新建文件夹", "2024"):
    os.makedirs(os.path.join(lib, d), exist_ok=True)
FILES = {
    "原盘/VIDEO_TS/VTS_01_1.VOB": "vob-1",     # 原盘:非媒体 + 目录名不合规
    "原盘/VIDEO_TS/VTS_01_2.VOB": "vob-2",
    "原盘/说明.txt": "txt",                     # 直接在 原盘/ 下(锚定对照)
    "原盘/封面.jpg": "jpg",
    "深层/原盘/子目录/a.jpg": "jpg-deep",        # 同名的 原盘,但不在 root 第一层
    "新建文件夹/b.jpg": "jpg-bad",              # BAD_DIR
    "2024/IMG_0001.jpg": "jpg-loose",           # 年目录下的散文件
    "movie.ass": "ass",                         # issue 的例子:内置判「非媒体混入」
    "movie.srt": "srt",                         # 对照组:只改 .ass 时它不动
    "IMG_1234.aae": "aae",                      # 内置 sidecar
    "notes.txt": "txt-root",
    "IMG_20240503_120000.jpg": "jpg-dated",     # 文件名带日期 → 散文件
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


def kinds(d, needle):
    return sorted(i["path"] for i in d["issues"]
                  if any(needle in p for p in i["problems"]))


# -- 0. 基线:没有 config.json 时,stats 里连 config 这个键都不该出现 ---------
drop_cfg()
base, base_err = scan()
bp = paths(base)
s = base["stats"]
assert "config" not in s, s.get("config")
assert "已加载覆盖配置" not in base_err, base_err
assert s["dirs"] == 7 and s["files"] == 12, (s["dirs"], s["files"])
assert s["photos"] == 5 and s["videos"] == 0, s
assert s["non_media"] == 6 and s["junk"] == 0, s
assert s["loose"] == 2 and s["camera_no_date"] == 1, s
assert base["count"] == 12, base["count"]
# 目录名问题:原盘/ 深层/ 新建文件夹/(VIDEO_TS 在 depth2、2024 是年份 → 都合规)
assert sorted(i["path"] for i in base["issues"] if i["is_dir"]) == \
    ["原盘", "新建文件夹", "深层"], sorted(
        i["path"] for i in base["issues"] if i["is_dir"])
# 文件问题:非媒体 6 + sidecar 1 + 散文件 2
assert kinds(base, "非媒体文件混入") == sorted([
    "notes.txt", "movie.ass", "movie.srt",
    "原盘/VIDEO_TS/VTS_01_1.VOB", "原盘/VIDEO_TS/VTS_01_2.VOB", "原盘/说明.txt",
]), kinds(base, "非媒体文件混入")
assert kinds(base, "sidecar") == ["IMG_1234.aae"], kinds(base, "sidecar")
assert kinds(base, "散文件") == ["2024/IMG_0001.jpg", "IMG_20240503_120000.jpg"]
# 合规的照片不出 issue
for quiet in ("原盘/封面.jpg", "深层/原盘/子目录/a.jpg", "新建文件夹/b.jpg"):
    assert quiet not in bp, quiet
print("  ✓ 无 config.json:stats 无 config 键、stderr 无提示、12 条问题与旧版一致")

# -- 1. issue #15 的原话:把 .ass 当 sidecar --------------------------------
write_cfg(json.dumps({"extension_overrides": {"ass": "sidecar"}}))
d, err = scan()
dp = paths(d)
assert kinds(d, "sidecar") == ["IMG_1234.aae", "movie.ass"], kinds(d, "sidecar")
assert "movie.ass" not in kinds(d, "非媒体文件混入"), kinds(d, "非媒体文件混入")
assert d["stats"]["non_media"] == 5, d["stats"]["non_media"]
# 逐扩展名改判:.srt 没写就必须还是「非媒体混入」;内置 .aae 也没被挤掉
assert "movie.srt" in kinds(d, "非媒体文件混入")
assert "IMG_1234.aae" in dp
assert "已加载覆盖配置" in err, err
assert d["stats"]["config"]["extension_overrides"] == {"ass": "sidecar"}
assert d["stats"]["config"]["whitelist_dirs"] == []
print("  ✓ extension_overrides 把 .ass 判成 sidecar(issue 原例),且只动这一个")

# -- 2. 键归一化 + 把原盘的 .VOB 认成视频 ----------------------------------
write_cfg(json.dumps({"extension_overrides": {".VOB": "video"}}))
d, _ = scan()
assert d["stats"]["videos"] == 2, d["stats"]["videos"]
assert d["stats"]["non_media"] == 4, d["stats"]["non_media"]
assert "原盘/VIDEO_TS/VTS_01_1.VOB" not in kinds(d, "非媒体文件混入")
print("  ✓ 键归一化(.VOB → vob);原盘里的 VOB 改判为视频,不再算「非媒体混入」")

# -- 3. 内置 sidecar 也能被改判(证明「先从别的表里摘掉」那一步真的做了)---
write_cfg(json.dumps({"extension_overrides": {"aae": "photo"}}))
d, _ = scan()
assert kinds(d, "sidecar") == [], kinds(d, "sidecar")
assert d["stats"]["photos"] == 6, d["stats"]["photos"]      # 5 + IMG_1234.aae
# 它现在是 root 下的散照片 → 换成一条「散文件」issue,而不是安静地消失
assert kinds(d, "散文件") == ["2024/IMG_0001.jpg", "IMG_1234.aae",
                             "IMG_20240503_120000.jpg"], kinds(d, "散文件")
assert d["stats"]["loose"] == 3, d["stats"]["loose"]
print("  ✓ aae 从内置 sidecar 摘出来改判为 photo(不摘就会同时命中两张表)")

# -- 4. 兜底类别 non_media(表值是 None):写了等于显式维持原判 ------------
write_cfg(json.dumps({"extension_overrides": {"txt": "non_media"}}))
d, err = scan()
assert sorted(kinds(d, "非媒体文件混入")) == sorted(kinds(base, "非媒体文件混入"))
assert d["stats"]["non_media"] == 6, d["stats"]["non_media"]
assert "已加载覆盖配置" in err
print("  ✓ 兜底类别 non_media 可写:显式维持原判,不是静默无效")

# -- 5. whitelist_dirs:整棵子树跳过(目录名不判、文件不产 issue)----------
write_cfg(json.dumps({"whitelist_dirs": ["原盘"]}))
d, _ = scan()
dp = paths(d)
assert not [p for p in dp if p.startswith("原盘")], sorted(dp)
assert not [i for i in d["issues"] if i["is_dir"] and i["path"] == "原盘"]
assert d["stats"]["config"]["skipped_files"] == 5, d["stats"]["config"]
assert d["stats"]["non_media"] == 3, d["stats"]["non_media"]   # 6 - 3(原盘内)
# 裸名字在任意深度都命中:深层/原盘/子目录/a.jpg 也被跳过
assert "深层/原盘/子目录/a.jpg" not in dp
# 没写到的目录一条不少
assert d["stats"]["dirs"] == 7, d["stats"]["dirs"]      # 目录本身仍计入
assert d["stats"]["files"] == 7, d["stats"]["files"]     # 12 - 5
assert "新建文件夹" in [i["path"] for i in d["issues"] if i["is_dir"]]
print("  ✓ whitelist_dirs=[原盘]:整棵子树跳过(5 个文件),其余目录一条不少")

# -- 6. 锚定:原盘/* 命中 原盘/VIDEO_TS,但不命中 原盘 自己这一层 ----------
write_cfg(json.dumps({"whitelist_dirs": ["原盘/*"]}))
d, _ = scan()
dp = paths(d)
assert "原盘/VIDEO_TS/VTS_01_1.VOB" not in dp, sorted(dp)
assert "原盘/VIDEO_TS/VTS_01_2.VOB" not in dp, sorted(dp)
assert d["stats"]["config"]["skipped_files"] == 2, d["stats"]["config"]
# 原盘/ 这一层没被 原盘/* 命中 → 目录名问题照报,直接躺在 原盘/ 下的文件照报
assert "原盘" in [i["path"] for i in d["issues"] if i["is_dir"]], d["issues"]
assert "原盘/说明.txt" in dp, sorted(dp)
assert "原盘/封面.jpg" not in dp                 # 它本来就合规,不是因为白名单
assert d["stats"]["non_media"] == 4, d["stats"]["non_media"]   # 6 - 2 个 VOB
# 「深层/原盘/子目录」不命中 原盘/*(相对路径不是以 原盘/ 开头)→ 没被跳过。
# 它本身合规、不产 issue,所以用计数来钉:跳过数仍是 2,files 仍是 10。
assert d["stats"]["files"] == 10, d["stats"]["files"]
print("  ✓ 原盘/* 命中 原盘/VIDEO_TS(跳过 2 个),但不命中 原盘 自身那一层")

# -- 7. 带 / 的模式锚定在 root:深层/原盘/* 只命中那一条 ------------------
write_cfg(json.dumps({"whitelist_dirs": ["深层/原盘/*"]}))
d, _ = scan()
assert d["stats"]["config"]["skipped_files"] == 1, d["stats"]["config"]
assert d["stats"]["files"] == 11, d["stats"]["files"]    # 12 - 1
assert "原盘/VIDEO_TS/VTS_01_1.VOB" in paths(d), sorted(paths(d))
assert "原盘" in [i["path"] for i in d["issues"] if i["is_dir"]]
print("  ✓ 深层/原盘/* 只命中 root 下那一条,不波及同名的 原盘/")

# -- 8. "*" 也管不到直接躺在 root 下的文件(白名单只作用于目录)-----------
write_cfg(json.dumps({"whitelist_dirs": ["*"]}))
d, _ = scan()
assert d["stats"]["config"]["skipped_files"] == 7, d["stats"]["config"]
assert not [i for i in d["issues"] if i["is_dir"]], d["issues"]
dp = paths(d)
for root_file in ("movie.ass", "movie.srt", "IMG_1234.aae", "notes.txt",
                  "IMG_20240503_120000.jpg"):
    assert root_file in dp, root_file
assert d["stats"]["files"] == 5, d["stats"]["files"]     # 只剩 root 下那 5 个
print("  ✓ 白名单只作用于目录:* 也管不到直接躺在 root 下的 5 个文件")

# -- 9. 空对象 {}:加载并提示,但行为与无配置逐条相同 ----------------------
def shape(x):
    return sorted((i["path"], tuple(i["problems"])) for i in x["issues"])


write_cfg("{}")
d, err = scan()
assert shape(d) == shape(base), "空对象改变了判定"
assert d["stats"]["non_media"] == base["stats"]["non_media"]
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
assert "movie.ass" in kinds(d, "非媒体文件混入")     # 没写 extension_overrides
print("  ✓ 只有 whitelist_dirs 时,扩展名判定一条都不变")

# -- 11. 被扫描目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)---
drop_cfg()
root_cfg = os.path.join(lib, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"extension_overrides": {"ass": "sidecar"},
               "whitelist_dirs": ["*"]}, fh)
for cwd in (src, lib, installed):        # 三种 cwd 都不能让它被读到
    d, err = scan(cwd=cwd)
    assert "movie.ass" in kinds(d, "非媒体文件混入"), (cwd, kinds(d, "非媒体"))
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
    assert d["stats"]["files"] == 13, (cwd, d["stats"]["files"])  # 它自己被扫到了
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
    ("whitelist_dirs Windows 盘符", '{"whitelist_dirs": ["Z:\\\\data"]}',
     "绝对路径"),
    ("extension_overrides 不是对象", '{"extension_overrides": []}',
     "extension_overrides"),
    ("类别值不是字符串", '{"extension_overrides": {"ass": true}}',
     "必须是字符串类别名"),
    ("扩展名含多个点", '{"extension_overrides": {"tar.gz": "photo"}}', "tar.gz"),
    ("扩展名归一化后为空", '{"extension_overrides": {".": "photo"}}', "空的"),
    ("类别名不存在", '{"extension_overrides": {"ass": "subtitle"}}', "subtitle"),
    ("两个键都拼错", '{"whitelist_dir": [], "extension_override": {}}',
     "extension_override"),
]
# photo-organizer 的类别表与 file-sorter **不同**:同一份配置在那边合法、在这边
# 必须报错 —— 这条钉住「CONFIG_EXT_TABLES 是 per-skill 的」,不是全局一张表。
for label, text, needle in BAD + [
    ("file-sorter 的类别名在本 skill 不存在", '{"extension_overrides": {"ass": "doc"}}',
     "doc"),
    ("另一个 skill 的类别名", '{"extension_overrides": {"ass": "cad"}}', "cad"),
]:
    write_cfg(text)
    r = run(expect=1)
    assert r.stdout == "", (label, "错误不能污染 --json 的 stdout", r.stdout[:200])
    assert "config.json" in r.stderr, (label, r.stderr)
    assert needle in r.stderr, (label, needle, r.stderr)
    assert "❌" in r.stderr, (label, r.stderr)
# 报错里要列出**本 skill** 的可用类别,照着改就能修
write_cfg('{"extension_overrides": {"ass": "doc"}}')
r = run(expect=1)
for cat in ("photo", "video", "sidecar", "junk", "non_media"):
    assert cat in r.stderr, (cat, r.stderr)
drop_cfg()
print(f"  ✓ {len(BAD) + 2} 种畸形配置全部 exit=1、只写 stderr、指名键,"
      "并列出本 skill 的可用类别")

# -- 13. 校验先于写入 + 锚定规则的纯函数证据 ------------------------------
sys.path.insert(0, installed)
import contextlib                                           # noqa: E402
import io                                                   # noqa: E402
import photo_organizer as po                                # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "whitelist_dirs": ["原盘"],
    "extension_overrides": {"ass": "doc"},      # file-sorter 的类别,这里不存在
}), encoding="utf-8")
before = (set(po.SIDECAR_EXTS), set(po.PHOTO_EXTS), set(po.VIDEO_EXTS))
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        po.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "doc" in str(e) and "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()     # 失败路径不该先打印「已加载」
assert po.CONFIG_WHITELIST == [], po.CONFIG_WHITELIST
assert po.CONFIG_INFO == {}, po.CONFIG_INFO
assert (set(po.SIDECAR_EXTS), set(po.PHOTO_EXTS), set(po.VIDEO_EXTS)) == before

m = po._cfg_match
assert m(["原盘", "VIDEO_TS"], ["原盘/*"])              # 整段相对路径命中
assert m(["原盘", "VIDEO_TS"], ["原盘"])                # 任一级目录名命中
assert not m(["原盘"], ["原盘/*"])                      # 原盘/* 不命中 原盘 自身
assert not m(["深层", "原盘", "子目录"], ["原盘/*"])     # 不在 root 第一层就不算
assert m(["深层", "原盘", "子目录"], ["深层/原盘/*"])
assert m(["深层", "原盘", "子目录"], ["原盘"])           # 裸名字命中任意深度
assert m(["影视", "Season 1"], ["影视/*"])              # 含空格的中文路径
assert m(["SCREENSHOTS"], ["screenshots"])              # 大小写不敏感(目录大写)
assert m(["screenshots"], ["SCREENSHOTS"])              # 反方向:模式大写
assert m(["原盘", "video_ts"], ["原盘/VIDEO_TS"])        # 整段路径也要双向不敏感
assert m(["Samples", "Sub"], ["SAMPLES/*"])             # 带通配时同样双向
assert m(["原盘"], ["*"])                               # * 跨 / 匹配
assert not m([], ["*"])                                 # root 下的散文件不吃白名单
assert not m(["原盘"], [])                              # 没有模式 = 不命中
print("  ✓ 校验先于写入(内置三张表分毫未动)+ _cfg_match 锚定规则 14 条断言")

# -- 14. 与内置 WHITELIST_DIRS 的关系:是追加,不是替换 --------------------
# 内置的 截图/ 一直合规;加了 config 之后它必须**仍然**合规。
os.makedirs(os.path.join(lib, "截图"), exist_ok=True)
with open(os.path.join(lib, "截图", "S.png"), "w", encoding="utf-8") as fh:
    fh.write("png")
drop_cfg()
d0, _ = scan()
assert "截图" not in [i["path"] for i in d0["issues"] if i["is_dir"]], d0["issues"]
write_cfg(json.dumps({"whitelist_dirs": ["原盘"]}))
d1, _ = scan()
assert "截图" not in [i["path"] for i in d1["issues"] if i["is_dir"]], d1["issues"]
assert "原盘" not in [i["path"] for i in d1["issues"] if i["is_dir"]], d1["issues"]
assert "新建文件夹" in [i["path"] for i in d1["issues"] if i["is_dir"]]
print("  ✓ whitelist_dirs 是追加:内置的 截图/ 加了 config 之后仍然合规")

# -- 15. 大小写不敏感必须**双向**成立(端到端)-----------------------------
# 只测「目录大写 / 模式小写」测不出来:实现里相对路径总是先 .lower(),那个方向
# 即使忘了给模式做 lower 也照样过。所以两个方向都要跑一遍。
os.makedirs(os.path.join(lib, "samples"), exist_ok=True)
with open(os.path.join(lib, "samples", "S.mkv"), "w", encoding="utf-8") as fh:
    fh.write("mkv")
for pat in ("SAMPLES", "samples", "Samples"):
    write_cfg(json.dumps({"whitelist_dirs": [pat]}))
    d, _ = scan()
    dirs_flagged = [i["path"] for i in d["issues"] if i["is_dir"]]
    assert "samples" not in dirs_flagged, (pat, dirs_flagged)
    assert d["stats"]["config"]["skipped_files"] == 1, (pat, d["stats"]["config"])
print("  ✓ 大小写不敏感双向成立:SAMPLES / samples / Samples 都命中小写目录")
os.remove(os.path.join(lib, "截图", "S.png"))
os.rmdir(os.path.join(lib, "截图"))
os.remove(os.path.join(lib, "samples", "S.mkv"))
os.rmdir(os.path.join(lib, "samples"))
PYEOF
rm -rf "$PCFG_SRC"

echo "=== TEST 10: node_modules/.git 不入库扫描(家族审计 F3)==="
# F3:此前 photo-organizer 是 9 家里唯一不跳 node_modules 的 —— 一个带
# node_modules 的库会产出成百上千条 issue(目录名不符合日期规范/非媒体文件),
# 而其余 8 家在同一棵树上安静得多。.git 靠点前缀规则本就扫不到,这里一并钉住。
PHOTO_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import json
import os
import shutil
import subprocess
import sys
import tempfile

skill = os.environ["PHOTO_SKILL_DIR"]
script = f"{skill}/photo_organizer.py"

root = tempfile.mkdtemp(prefix="po-devdirs.")
try:
    os.makedirs(os.path.join(root, "截图"))
    os.makedirs(os.path.join(root, "node_modules", "somepkg", "lib"))
    os.makedirs(os.path.join(root, ".git"))
    for i in range(2):
        with open(os.path.join(root, "截图", f"p{i}.jpg"), "wb") as fh:
            fh.write(b"J" * 2000)
    for i in range(20):
        with open(os.path.join(root, "node_modules", "somepkg", "lib", f"mod{i}.js"), "wb") as fh:
            fh.write(b"x" * 400)
    with open(os.path.join(root, "node_modules", "somepkg", "asset.jpg"), "wb") as fh:
        fh.write(b"x" * 300)
    with open(os.path.join(root, ".git", "config"), "w") as fh:
        fh.write("[core]")
    r = subprocess.run([sys.executable, script, "scan", "--root", root, "--json"],
                       capture_output=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    d = json.loads(r.stdout)
    s = d["stats"]
    blob = json.dumps(d, ensure_ascii=False)
    assert "node_modules" not in blob, blob[:300]
    assert ".git" not in blob, blob[:300]
    assert s["files"] == 2, s            # 只有 截图/ 下两张
    assert s["photos"] == 2, s
    assert s["non_media"] == 0, s        # 20 个 .js 不再算非媒体噪音
    assert s["total_size_bytes"] == 4000, s
    assert d["count"] == 0, d["issues"]  # 截图/ 是白名单目录,零 issue
    print("  ✓ node_modules/.git 被跳过:21 个开发文件不入库,零噪音 issue")
finally:
    shutil.rmtree(root, ignore_errors=True)
PYEOF

echo ""
echo "🎉 所有 smoke test 通过"
