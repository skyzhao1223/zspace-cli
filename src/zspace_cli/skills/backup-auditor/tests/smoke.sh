#!/bin/bash
# backup-auditor skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
BA_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["BA_SKILL_DIR"]
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
if name != "backup-auditor":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=backup-auditor")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/backup_auditor.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(备份名解析) ==="
BA_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["BA_SKILL_DIR"])
import backup_auditor as ba

# 归档扩展名剥离(含双扩展名)
assert ba.strip_archive_ext("照片备份.tar.gz") == "照片备份"
assert ba.strip_archive_ext("db.zip") == "db"
assert ba.strip_archive_ext("vol.sparsebundle") == "vol"
assert ba.strip_archive_ext("目录名") == "目录名"

# 日期提取
assert ba.parse_backup_name("照片备份_2024-01-01.tar.gz") == ("照片备份", "2024-01-01", None)
assert ba.parse_backup_name("照片备份_20240101") [0] == "照片备份"
assert ba.parse_backup_name("照片备份_20240101")[1] == "2024-01-01"
assert ba.parse_backup_name("docs 2024年03月05日")[1] == "2024-03-05"

# 版本号提取
assert ba.parse_backup_name("db_v2.tar.gz") == ("db", None, 2)
assert ba.parse_backup_name("网站备份3")[2] == 3

# 同一目标多版本聚到同一 base_key
k1 = ba.parse_backup_name("照片备份_2024-01-01.tar.gz")[0]
k2 = ba.parse_backup_name("照片备份_2024-02-01.tar.gz")[0]
assert k1 == k2 == "照片备份", (k1, k2)

# 无日期无版本
assert ba.parse_backup_name("misc") == ("misc", None, None)

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan + coverage ==="
FIXTURE="$(mktemp -d /tmp/backup-auditor-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

BK="$FIXTURE/备份"
SRC="$FIXTURE/data"
mkdir -p "$BK" "$SRC/照片" "$SRC/文档" "$SRC/项目X"

# 备份集:照片备份 4 个版本(超过 keep=3,最旧 1 个可轮转)
for d in 2024-01-01 2024-02-01 2024-03-01 2024-04-01; do
  mkdir -p "$BK/照片备份_$d"
  echo "photo data $d" > "$BK/照片备份_$d/img.jpg"
done
# 单版本 + 陈旧备份(mtime 设为 2 年前)
mkdir -p "$BK/文档备份_20230101"
echo "old doc" > "$BK/文档备份_20230101/a.txt"
touch -t 202301010000 "$BK/文档备份_20230101" "$BK/文档备份_20230101/a.txt"
# 空备份项
mkdir -p "$BK/空备份_2024-05-01"
# 孤儿备份(源里没有 老项目)
mkdir -p "$BK/老项目备份_2024-01-01"
echo "x" > "$BK/老项目备份_2024-01-01/f.bin"
# 源里 项目X 无对应备份(缺失)

BA_SKILL_DIR="$SKILL_DIR" BK="$BK" SRC="$SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

skill = os.environ["BA_SKILL_DIR"]
bk, src = os.environ["BK"], os.environ["SRC"]
tmpd = tempfile.mkdtemp()

# --- scan ---
out = os.path.join(tmpd, "scan.json")
r = subprocess.run(
    [sys.executable, f"{skill}/backup_auditor.py", "scan",
     "--root", bk, "--stale-days", "35", "--keep", "3", "--output", out],
    capture_output=True, text=True,
    encoding="utf-8", errors="replace",
)
assert r.returncode == 0, r.stderr
scan = json.loads(open(out, encoding="utf-8").read())
sb = json.dumps(scan, ensure_ascii=False)
s = scan["stats"]
assert scan["skill"] == "backup-auditor" and scan["mode"] == "scan"
assert s["backup_sets"] >= 4, s                 # 照片备份/文档备份/空备份/老项目备份
assert s["rotatable_versions"] >= 1, s          # 照片备份 4 版 > keep 3
assert s["single_version_sets"] >= 2, s         # 文档/空/老项目 都是单版本
assert s["stale_sets"] >= 1, s                  # 文档备份 2023 陈旧
assert s["empty_items"] >= 1, s                 # 空备份
assert "可轮转旧版本" in sb
assert "备份陈旧" in sb
assert "空备份项" in sb
assert "单版本备份" in sb
actions = {i["action"] for i in scan["issues"]}
assert {"rotate-out", "review-set", "investigate"} <= actions, actions

# --- coverage ---
out2 = os.path.join(tmpd, "cov.json")
r2 = subprocess.run(
    [sys.executable, f"{skill}/backup_auditor.py", "coverage",
     "--source", src, "--backup", bk, "--stale-days", "35", "--output", out2],
    capture_output=True, text=True,
    encoding="utf-8", errors="replace",
)
assert r2.returncode == 0, r2.stderr
cov = json.loads(open(out2, encoding="utf-8").read())
cb = json.dumps(cov, ensure_ascii=False)
cs = cov["stats"]
assert cov["mode"] == "coverage"
assert cs["source_dirs"] == 3, cs               # 照片/文档/项目X
assert cs["missing"] >= 1, cs                   # 项目X 无备份
assert cs["orphan"] >= 1, cs                    # 老项目备份 是孤儿
assert "关键目录无对应备份" in cb
assert "孤儿备份" in cb
cactions = {i["action"] for i in cov["issues"]}
assert "add-backup" in cactions and "review-orphan" in cactions, cactions

print("  ✓ scan + coverage 检出全部预期项")
PYEOF

echo "=== TEST 5: --help(scan + coverage) ==="
"$PY" "$SKILL_DIR/backup_auditor.py" --help >/dev/null
"$PY" "$SKILL_DIR/backup_auditor.py" scan --help | grep -q -- "--keep"
"$PY" "$SKILL_DIR/backup_auditor.py" coverage --help | grep -q -- "--source"
echo "  ✓ CLI help 可用"

echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/backupaudi-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/backup_auditor.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/backup_auditor.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
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
# config.json 按**脚本自己所在目录**(__file__)解析 —— 不是 cwd,也不是 --root /
# --source / --backup。所以这里把脚本复制进一个临时目录,模拟「zs skill 装好之后」
# 的样子;顺便也就证明了放在被审计目录里的 config.json 不会被误读。
CFG_SRC="$(mktemp -d /tmp/backupaudi-cfg.XXXXXX)"
mkdir -p "$CFG_SRC/installed" "$CFG_SRC/bk" "$CFG_SRC/data"
cp "$SKILL_DIR/backup_auditor.py" "$CFG_SRC/installed/"
BA_CFG_SRC="$CFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import time
from pathlib import Path

src = os.environ["BA_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "backup_auditor.py")
cfg = os.path.join(installed, "config.json")
bk = os.path.join(src, "bk")
data = os.path.join(src, "data")
DAY = 86400


def w(root, rel, body, age_days=0):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    if age_days:
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))
    return len(body.encode("utf-8"))


def d(root, rel, age_days=0):
    p = os.path.join(root, rel)
    os.makedirs(p, exist_ok=True)
    if age_days:
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))


# 目录年龄必须在**里面所有文件写完之后**再设:往目录里写文件会把它的 mtime 刷成
# 现在,先设后写等于没设(第一版就是这么把陈旧判定整个丢掉的)。
DIR_AGES = []


def dd(root, rel, age_days):
    d(root, rel)
    DIR_AGES.append((os.path.join(root, rel), age_days))


# ---- 备份树:多版本 / 陈旧 / 空 / 孤儿 / 私有扩展名 / 危险名 / AppleDouble ----
for i, day in enumerate(("2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01")):
    dd(bk, f"照片备份_{day}", 400 - i * 30)
    w(bk, f"照片备份_{day}/img.jpg", "p" * 2000, 400 - i * 30)
    w(bk, f"照片备份_{day}/._img.jpg", "a" * 40, 400 - i * 30)   # AppleDouble
dd(bk, "文档备份_20230101", 730)                   # 陈旧 + 单版本
w(bk, "文档备份_20230101/a.txt", "o" * 500, 730)
dd(bk, "空备份_2024-05-01", 100)                    # 空备份项 → investigate
dd(bk, "老项目备份_2024-01-01", 300)                 # 孤儿(源里没有)
w(bk, "老项目备份_2024-01-01/f.bin", "f" * 1500, 300)
for v in (1, 2, 3, 4):
    w(bk, f"db_v{v}.tar.gz", "d" * (1000 + v * 10), 200 - v * 10)
# 私有扩展名 .mybak:内置不认识 → base_key 带着扩展名,两个版本被拆成两组
w(bk, "数据.mybak", "m" * 1200, 90)
w(bk, "数据_v2.mybak", "m" * 1300, 60)
w(bk, "网站备份.iso", "i" * 6000, 120)              # 内置 ARCHIVE_EXTS 认识 iso
dd(bk, "vol.sparsebundle", 150)                    # 双扩展名 + 目录型备份项
w(bk, "vol.sparsebundle/band1", "b" * 4000, 150)
# dir_size 内部也吃内置 SKIP_DIRS:下面这两个不该被算进 vol.sparsebundle 的体积
d(bk, "vol.sparsebundle/@eaDir")
w(bk, "vol.sparsebundle/@eaDir/SYNOPHOTO", "e" * 5000)
d(bk, "vol.sparsebundle/.git")
w(bk, "vol.sparsebundle/.git/index", "e" * 6000)
# skip_dirs 的三个锚定层次:临时暂存/ 自己、它的子目录、以及更深处的同名目录
dd(bk, "临时暂存", 90)
SUB_SIZE = w(bk, "临时暂存/子目录/y.bin", "y" * 1200, 90)
TOP_SIZE = w(bk, "临时暂存/x.bin", "x" * 1100, 90)
dd(bk, "深层", 80)
DEEP_SIZE = w(bk, "深层/临时暂存/z.bin", "z" * 1400, 80)
dd(bk, "-weird-name-backup", 60)                   # shell 危险名:前导 -
w(bk, "-weird-name-backup/y.bin", "z" * 1300, 60)
# 尾随空格刻意不留:Windows 的 Win32 路径规范化会把目录名的尾随空格与点剥掉,
# 那个目录根本建不出来(CI 的 windows-latest 上就是这么炸的)。前导空格保留,
# 一样是 shell 危险名、一样走同一条代码路径。
dd(bk, " spaced backup 2024-06-01", 50)             # shell 危险名:前导空格
w(bk, " spaced backup 2024-06-01/z.bin", "z" * 1400, 50)
# 大小写不敏感要**双向**测,所以刻意用两个不同名字:大写目录配小写模式、
# 小写目录配大写模式。同名不同写法在大小写不敏感的卷上会合并成一个目录,
# 那样就只剩一个方向可测了。
dd(bk, "SAMPLES", 5)
w(bk, "SAMPLES/s.bin", "s" * 900, 5)
dd(bk, "lowcase", 5)
w(bk, "lowcase/s2.bin", "s" * 950, 5)
# 大小写撞名对放在一个备份项**里面**:在大小写不敏感的卷(macOS 默认 / Windows)
# 上 case.bin 会覆盖 CASE.bin,所以断言跟着实际存活的数量走,不假定文件系统。
dd(bk, "撞名备份", 5)
w(bk, "撞名备份/CASE.bin", "c" * 1700, 5)
w(bk, "撞名备份/case.bin", "c" * 1701, 5)
CASE_PAIR = sorted(n for n in os.listdir(os.path.join(bk, "撞名备份"))
                   if n.lower() == "case.bin")
assert 1 <= len(CASE_PAIR) <= 2, CASE_PAIR
w(bk, "._网站备份.iso", "a" * 40)                   # AppleDouble(点开头 → 内置就跳过)
w(bk, ".DS_Store", "s" * 40)
d(bk, "@eaDir")
w(bk, "@eaDir/junk", "j" * 40)
now = time.time()
for p, age in sorted(DIR_AGES, key=lambda x: -len(Path(x[0]).parts)):
    os.utime(p, (now - age * DAY, now - age * DAY))

# ---- 源树(coverage 用)----
for name, age in (("照片", 30), ("文档", 730), ("项目X", 10), ("临时暂存", 20),
                  ("-odd-dir", 15), (" spaced dir", 12)):
    d(data, name, age)
    w(data, f"{name}/payload.bin", "s" * 2000, age)
d(data, "@eaDir")
w(data, "@eaDir/x", "x")
d(data, ".hidden")
w(data, ".hidden/y", "y")

# 顶层备份项 = bk 下「不以 . 开头、且不在内置 SKIP_DIRS 里」的条目数。这是从
# 文件系统**独立**数出来的,不是从被测实现读的;下面的 assert 因此不是同义反复。
EXPECT_ITEMS = len([n for n in os.listdir(bk)
                    if not n.startswith(".") and n != "@eaDir"])


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


def run(cmd, *extra, expect=0, cwd=None):
    # cwd 故意设成 src(既不是脚本目录也不是被审计目录):证明解析与 cwd 无关
    r = subprocess.run(
        [sys.executable, script, cmd, *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd or src)
    assert r.returncode == expect, (r.returncode, r.stdout[-400:], r.stderr)
    return r


def scan(*extra, cwd=None):
    r = run("scan", "--root", bk, "--json", *extra, cwd=cwd)
    return json.loads(r.stdout), r.stderr


def cover(*extra, cwd=None):
    r = run("coverage", "--source", data, "--backup", bk, "--json", *extra, cwd=cwd)
    return json.loads(r.stdout), r.stderr


def names_of(d):
    return sorted({i["name"] for i in d["issues"] if i["action"] == "review-set"})


def shape(d):
    return sorted((i["path"], i["action"], tuple(i["problems"])) for i in d["issues"])


# -- 0. 基线:两个子命令都不该有 config 键,也不该打提示行 -------------------
drop_cfg()
b_scan, b_scan_err = scan()
b_cov, b_cov_err = cover()
assert "config" not in b_scan["stats"], b_scan["stats"].get("config")
assert "config" not in b_cov["stats"], b_cov["stats"].get("config")
assert "已加载覆盖配置" not in b_scan_err, b_scan_err
assert "已加载覆盖配置" not in b_cov_err, b_cov_err
bs, cs = b_scan["stats"], b_cov["stats"]
assert bs["backup_items"] == EXPECT_ITEMS == 22, (bs, EXPECT_ITEMS)
assert bs["backup_sets"] == 16, bs
assert bs["multi_version_sets"] == 2, bs        # 照片备份(4 版)、db(4 版)
assert bs["single_version_sets"] == 14, bs
assert bs["stale_sets"] == 13, bs               # SAMPLES/lowcase/撞名备份 是新鲜的
assert bs["empty_items"] == 1, bs               # 空备份_2024-05-01
assert bs["rotatable_versions"] == 2, bs        # 4>3 的两组各轮转出 1 个
assert {"rotate-out", "review-set", "investigate"} <= {i["action"] for i in b_scan["issues"]}
assert "网站备份" in names_of(b_scan), names_of(b_scan)     # iso 被剥掉 → 网站备份
assert "数据mybak" in names_of(b_scan), names_of(b_scan)    # mybak 不被剥 → 拆成两组
assert "数据v2mybak" in names_of(b_scan), names_of(b_scan)
assert cs["source_dirs"] == 6, cs
assert cs["covered"] == 3 and cs["missing"] == 3 and cs["stale"] == 3, cs
assert cs["orphan"] == 13, cs
assert cs["backup_sets"] == 16, cs
BASE_SIZE = bs["total_size_bytes"]
# 撞名那一对的体积**实测**,不推算:大小写不敏感的卷上第二份会连内容一起覆盖第一份
# (只剩 1701 字节的一份),大小写敏感的卷上两份都在(1700 + 1701)。第一版用
# `n.isupper()` 猜哪份活下来了 —— 而 "CASE.bin".isupper() 是 **False**("bin" 是
# 小写),于是在本机碰巧对上、在 ubuntu CI 上差 1 字节。
CASE_SIZE = sum(os.path.getsize(os.path.join(bk, "撞名备份", n)) for n in CASE_PAIR)
# vol.sparsebundle 的体积不含 @eaDir/(5000)与 .git/(6000):dir_size 的内置剪枝;
# 而 ._img.jpg 这类 AppleDouble **是**算进去的(dir_size 不看文件名)
assert BASE_SIZE == (
    4 * (2000 + 40)                            # 照片备份_*:img.jpg + ._img.jpg
    + 500                                      # 文档备份_20230101
    + 0                                        # 空备份_2024-05-01
    + 1500                                     # 老项目备份_2024-01-01
    + sum(1000 + v * 10 for v in (1, 2, 3, 4))  # db_v1..v4.tar.gz
    + 1200 + 1300                              # 数据.mybak / 数据_v2.mybak
    + 6000                                     # 网站备份.iso
    + 4000                                     # vol.sparsebundle(只有 band1)
    + TOP_SIZE + SUB_SIZE                      # 临时暂存
    + DEEP_SIZE                                # 深层/临时暂存
    + 1300 + 1400                              # -weird-name-backup / " spaced … "
    + 900 + 950                                # SAMPLES / lowcase
    + CASE_SIZE                                # 撞名备份
), (BASE_SIZE, CASE_SIZE)
print(f"  ✓ 无 config.json:scan/coverage 都无 config 键、无提示行;"
      f"{bs['backup_items']} 项 / {bs['backup_sets']} 集 / {bs['stale_sets']} 陈旧,"
      f"coverage {cs['covered']}覆盖 {cs['missing']}缺失 {cs['stale']}陈旧 {cs['orphan']}孤儿")

# -- 0b. 人类可读报告:两个子命令都要「无配置不印、有配置印出来」 ----------
for cmd, extra in (("scan", ["--root", bk]),
                   ("coverage", ["--source", data, "--backup", bk])):
    drop_cfg()                      # 上一轮循环写下的配置必须先清掉
    r = run(cmd, *extra)
    assert "已加载覆盖配置" not in r.stdout, (cmd, r.stdout)
    write_cfg(json.dumps({"skip_dirs": ["临时暂存"]}))
    r = run(cmd, *extra)
    assert "⚙ 已加载覆盖配置" in r.stdout, (cmd, r.stdout)
    assert_cfg_path_in(r.stdout, cmd)
    assert "skip_dirs: 临时暂存" in r.stdout, (cmd, r.stdout)
    assert "因 skip_dirs 未纳入的目录:" in r.stdout, (cmd, r.stdout)
drop_cfg()
print("  ✓ 人类报告:scan 与 coverage 都是无配置不印、有配置印出路径与生效键值")

# -- 1. extension_overrides:把私有扩展名 .mybak 认成归档 → 两版本合成一集 ----
write_cfg(json.dumps({"extension_overrides": {"mybak": "archive"}}))
d, err = scan()
assert d["stats"]["backup_sets"] == bs["backup_sets"] - 1, d["stats"]
assert d["stats"]["multi_version_sets"] == bs["multi_version_sets"] + 1, d["stats"]
assert d["stats"]["single_version_sets"] == bs["single_version_sets"] - 2, d["stats"]
assert "数据" in names_of(d), names_of(d)
assert "数据mybak" not in names_of(d) and "数据v2mybak" not in names_of(d)
# 逐扩展名改判:其余一律不动
assert "网站备份" in names_of(d), names_of(d)
assert d["stats"]["backup_items"] == bs["backup_items"], d["stats"]
assert d["stats"]["total_size_bytes"] == BASE_SIZE, d["stats"]
assert d["stats"]["config"]["extension_overrides"] == {"mybak": "archive"}
assert d["stats"]["config"]["skip_dirs"] == []
assert d["stats"]["config"]["skipped_dirs"] == 0
assert "已加载覆盖配置" in err, err
# --keep 1 时这一集现在有两版 → 多出一条 rotate-out(与保留策略联动)
d1, _ = scan("--keep", "1")
rot = [i["path"] for i in d1["issues"] if i["action"] == "rotate-out"]
assert "数据.mybak" in rot, rot
print("  ✓ extension_overrides {mybak: archive}:两版本合成一集,--keep 联动出轮转项")

# -- 2. 反方向:把内置认识的 iso 改判出去 → base_key 不再被剥离 -------------
write_cfg(json.dumps({"extension_overrides": {"iso": "non_archive"}}))
d, _ = scan()
assert "网站备份iso" in names_of(d), names_of(d)
assert "网站备份" not in names_of(d), names_of(d)
assert d["stats"]["config"]["extension_overrides"] == {"iso": "non_archive"}
print("  ✓ 兜底类别 non_archive 可写:网站备份.iso 不再被剥扩展名,自成一集")

# -- 3. 键归一化(前导点 / 大小写)----------------------------------------
write_cfg(json.dumps({"extension_overrides": {".MYBAK": "archive"}}))
d, _ = scan()
assert "数据" in names_of(d), names_of(d)
print("  ✓ 键归一化:.MYBAK 与 mybak 等价")

# -- 4. skip_dirs 在**审计边界**上生效:备份项整个不纳入 -------------------
write_cfg(json.dumps({"skip_dirs": ["临时暂存"]}))
d, _ = scan()
# _collect_items 只看顶层,所以只有 临时暂存/ 这一项消失;深层/临时暂存/ 是在
# dir_size 里被剪的(同一个裸名字模式在任意深度都命中),它属于 深层 这一项的体积。
assert d["stats"]["backup_items"] == bs["backup_items"] - 1, d["stats"]
assert d["stats"]["config"]["skipped_dirs"] == 1, d["stats"]["config"]
assert "临时暂存" not in names_of(d), names_of(d)
assert "深层" in names_of(d), names_of(d)
assert d["stats"]["total_size_bytes"] == BASE_SIZE - TOP_SIZE - SUB_SIZE - DEEP_SIZE, \
    (d["stats"]["total_size_bytes"], BASE_SIZE)
print("  ✓ skip_dirs=[临时暂存]:顶层那项不纳入,深层同名目录在 dir_size 里也被剪"
      "(体积按字节核对)")

# -- 5. 锚定边界:临时暂存/* 不命中 临时暂存 自己,只命中它的子目录 ---------
write_cfg(json.dumps({"skip_dirs": ["临时暂存/*"]}))
d, _ = scan()
assert d["stats"]["backup_items"] == bs["backup_items"], d["stats"]
assert "临时暂存" in names_of(d), names_of(d)          # 自己那一层还在
assert d["stats"]["config"]["skipped_dirs"] == 0, d["stats"]["config"]
assert d["stats"]["total_size_bytes"] == BASE_SIZE - SUB_SIZE, \
    (d["stats"]["total_size_bytes"], BASE_SIZE)
print("  ✓ 临时暂存/* 不命中 临时暂存 自身,只剪掉 临时暂存/子目录(体积精确少一份)")

# -- 6. 带 / 的模式锚定在 root:深层/临时暂存/* 只命中那一条 ---------------
write_cfg(json.dumps({"skip_dirs": ["深层/临时暂存/*"]}))
d, _ = scan()
assert d["stats"]["backup_items"] == bs["backup_items"], d["stats"]
assert "临时暂存" in names_of(d) and "深层" in names_of(d), names_of(d)
assert d["stats"]["config"]["skipped_dirs"] == 0, d["stats"]["config"]
# 深层/临时暂存/ 自己那一层不被 深层/临时暂存/* 命中,z.bin 是直接躺在里面的文件,
# 所以体积一分不少 —— 与 5 形成对照:同一个模式形状,少不少字节取决于树形状
assert d["stats"]["total_size_bytes"] == BASE_SIZE, d["stats"]["total_size_bytes"]
write_cfg(json.dumps({"skip_dirs": ["深层/临时暂存"]}))
d, _ = scan()
assert d["stats"]["total_size_bytes"] == BASE_SIZE - DEEP_SIZE, \
    (d["stats"]["total_size_bytes"], BASE_SIZE)
print("  ✓ 深层/临时暂存/* 与 深层/临时暂存 可区分:前者不剪自己那层,后者剪")

# -- 7. 大小写不敏感必须**双向**成立 --------------------------------------
# 只测「目录大写 / 模式小写」测不出来:实现里相对路径总是先 .lower(),那个方向
# 即使忘了给模式做 lower 也照样过。所以两个方向各用一个独立目录。
for pat, target in (("samples", "SAMPLES"), ("SAMPLES", "SAMPLES"),
                    ("LOWCASE", "lowcase"), ("LowCase", "lowcase")):
    write_cfg(json.dumps({"skip_dirs": [pat]}))
    d, _ = scan()
    assert d["stats"]["config"]["skipped_dirs"] == 1, (pat, d["stats"]["config"])
    assert d["stats"]["backup_items"] == bs["backup_items"] - 1, (pat, d["stats"])
    assert target.lower() not in [n.lower() for n in names_of(d)], (pat, names_of(d))
print("  ✓ 大小写不敏感双向成立:samples→SAMPLES 与 LOWCASE→lowcase 都命中")

# -- 8. coverage:源侧与备份侧都吃同一个 skip_dirs ------------------------
write_cfg(json.dumps({"skip_dirs": ["临时暂存"]}))
d, err = cover()
c0 = d["stats"]
assert c0["source_dirs"] == cs["source_dirs"] - 1, c0    # 源里的 临时暂存/ 不再要求备份
assert c0["backup_sets"] == cs["backup_sets"] - 1, c0     # 备份侧顶层那项被剪
assert c0["missing"] == cs["missing"], (c0, cs)           # 它本来就有备份,不影响缺失数
assert d["stats"]["config"]["skipped_dirs"] == 2, c0      # 源 1 + 备份 1
assert "已加载覆盖配置" in err, err
print("  ✓ coverage:同一个 skip_dirs 同时作用于 --source 与 --backup(1+1=2)")

# -- 8b. 模式是 fnmatch 不是子串:照片 不会顺手剪掉 照片备份_* -------------
write_cfg(json.dumps({"skip_dirs": ["照片"]}))
d, _ = cover()
c1 = d["stats"]
assert c1["source_dirs"] == cs["source_dirs"] - 1, c1
assert c1["covered"] == cs["covered"] - 1, (c1, cs)
assert c1["orphan"] == cs["orphan"] + 1, (c1, cs)
assert c1["backup_sets"] == cs["backup_sets"], (c1, cs)   # 备份项一个没少!
orphans = [i["name"] for i in d["issues"] if i["action"] == "review-orphan"]
assert any("照片备份" in n for n in orphans), orphans
print("  ✓ 连锁反应:源目录不再核对 → 对应备份集变孤儿;而 照片 不会误剪 照片备份_*")

# -- 9. 空对象 {}:加载并提示,但两个子命令都与无配置逐条相同 --------------
write_cfg("{}")
d, err = scan()
assert shape(d) == shape(b_scan), "scan 的空配置结果与基线不同"
assert {k: v for k, v in d["stats"].items() if k != "config"} == bs, d["stats"]
assert "已加载覆盖配置" in err, err
d2, err2 = cover()
assert shape(d2) == shape(b_cov), "coverage 的空配置结果与基线不同"
assert {k: v for k, v in d2["stats"].items() if k != "config"} == cs, d2["stats"]
assert "已加载覆盖配置" in err2, err2
print("  ✓ 空对象 {} 加载成功:scan 与 coverage 的审计项与统计都与无配置时相同")

# -- 10. 被审计目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)---
drop_cfg()
root_cfg = os.path.join(bk, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"extension_overrides": {"iso": "non_archive"},
               "skip_dirs": ["*"]}, fh, ensure_ascii=False)
for cwd in (src, bk, installed):
    d, err = scan(cwd=cwd)
    assert "网站备份" in names_of(d), (cwd, names_of(d))
    assert d["stats"]["backup_items"] == bs["backup_items"] + 1, (cwd, d["stats"])
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
# 它被当成一个普通备份项扫到了(22 → 23)—— 这正好证明它是数据、不是配置
os.remove(root_cfg)
print("  ✓ 被审计目录里的 config.json 被忽略(3 种 cwd 都试过),只当普通备份项扫")

# -- 11. 畸形配置:一律 exit=1,指名文件与 offending 键,且不污染 --json stdout
BAD = [
    ("非法 JSON", "{not json", "不是合法 JSON"),
    ("空文件", "", "不是合法 JSON"),
    ("顶层不是对象", "[]", "顶层"),
    ("未知键(少写一个 s)", '{"skip_dir": ["临时暂存"]}', "skip_dir"),
    ("未知键(照抄 file-sorter 的 whitelist_dirs)", '{"whitelist_dirs": ["x"]}',
     "whitelist_dirs"),
    ("未知键(照抄 dedup-finder 的 prefer_keep_hints)",
     '{"prefer_keep_hints": ["x"]}', "prefer_keep_hints"),
    ("skip_dirs 不是数组", '{"skip_dirs": "临时暂存"}', "skip_dirs"),
    ("skip_dirs 元素不是字符串", '{"skip_dirs": [1]}', "skip_dirs[0]"),
    ("skip_dirs 空字符串", '{"skip_dirs": ["  "]}', "skip_dirs[0]"),
    ("skip_dirs 绝对路径", '{"skip_dirs": ["/临时暂存"]}', "绝对路径"),
    ("skip_dirs Windows 盘符", '{"skip_dirs": ["Z:\\\\bak"]}', "绝对路径"),
    ("extension_overrides 不是对象", '{"extension_overrides": ["gz"]}',
     "extension_overrides"),
    ("类别值不是字符串", '{"extension_overrides": {"iso": 3}}', "必须是字符串类别名"),
    ("类别名不存在", '{"extension_overrides": {"iso": "backup"}}', "backup"),
    ("类别名是别的 skill 的", '{"extension_overrides": {"iso": "other"}}', "other"),
    ("扩展名含多个点", '{"extension_overrides": {"tar.gz": "archive"}}', "tar.gz"),
    ("扩展名归一化后为空", '{"extension_overrides": {".": "archive"}}', "空的"),
    ("两个键都拼错", '{"skip_dir": [], "extension_override": {}}',
     "extension_override"),
]
for label, text, needle in BAD:
    write_cfg(text)
    for args in (("scan", "--root", bk), ("coverage", "--source", data, "--backup", bk)):
        r = run(*args, "--json", expect=1)
        assert r.stdout == "", (label, args[0], "错误不能污染 --json 的 stdout",
                                r.stdout[:200])
        assert "config.json" in r.stderr, (label, args[0], r.stderr)
        assert needle in r.stderr, (label, args[0], needle, r.stderr)
        assert "❌" in r.stderr, (label, args[0], r.stderr)
drop_cfg()
print(f"  ✓ {len(BAD)} 种畸形配置 × 2 个子命令全部 exit=1、只写 stderr、指名文件与键")

# -- 12. 报错里列出**本 skill** 的可用键与可用类别 -------------------------
write_cfg('{"skip_dir": ["x"]}')
r = run("scan", "--root", bk, "--json", expect=1)
assert "skip_dirs" in r.stderr and "extension_overrides" in r.stderr, r.stderr
drop_cfg()
write_cfg('{"extension_overrides": {"iso": "nope"}}')
r = run("scan", "--root", bk, "--json", expect=1)
assert "archive" in r.stderr and "non_archive" in r.stderr, r.stderr
drop_cfg()
print("  ✓ 未知键报错列出可用键名;类别名报错列出本 skill 仅有的 2 个类别")

# -- 13. 校验先于写入:半途失败的配置不得留下副作用(进程内直接考)--------
sys.path.insert(0, installed)
import contextlib                                          # noqa: E402
import io                                                  # noqa: E402
import backup_auditor as ba                                # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "skip_dirs": ["临时暂存"],
    "extension_overrides": {"iso": "nope"},
}, ensure_ascii=False), encoding="utf-8")
before = set(ba.ARCHIVE_EXTS)
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        ba.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "nope" in str(e), str(e)
    assert "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()   # 失败路径不该先打印「已加载」
assert ba.CONFIG_SKIP == [], ba.CONFIG_SKIP
assert ba.CONFIG_INFO == {}, ba.CONFIG_INFO
assert set(ba.ARCHIVE_EXTS) == before, "ARCHIVE_EXTS 被改了一半"

good_dir = Path(src) / "good"
good_dir.mkdir(exist_ok=True)
(good_dir / "config.json").write_text(json.dumps({
    "skip_dirs": [" 临时暂存 "],
    "extension_overrides": {".MYBAK": "archive"},
}), encoding="utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    ba.load_config(good_dir)
assert ba.CONFIG_SKIP == ["临时暂存"], ba.CONFIG_SKIP     # 前后空白被 strip
assert "mybak" in ba.ARCHIVE_EXTS
assert ba.strip_archive_ext("数据_v2.mybak") == "数据_v2"
assert ba.strip_archive_ext("照片备份.tar.gz") == "照片备份"   # 双扩展名仍走硬编码那条
assert ba.parse_backup_name("数据_v2.mybak") == ("数据", None, 2)
assert ba.CONFIG_INFO["path"] == str(good_dir / "config.json"), ba.CONFIG_INFO
msg = err.getvalue()
assert "已加载覆盖配置" in msg and str(good_dir / "config.json") in msg, msg
assert "skip_dirs 1 条" in msg and "extension_overrides 1 条" in msg, msg
print("  ✓ 校验先于写入:坏配置退出后 ARCHIVE_EXTS 与 CONFIG_* 全都没动")

# -- 14. dir_size 的 rel_parts 锚定(纯函数级)---------------------------
ba.CONFIG_SKIP.clear()
ba.CONFIG_INFO.clear()
ba.ARCHIVE_EXTS.discard("mybak")
TMP = os.path.join(bk, "临时暂存")
tot, cnt = ba.dir_size(os.path.join(bk, "vol.sparsebundle"),
                       rel_parts=["vol.sparsebundle"])
assert (tot, cnt) == (4000, 1), (tot, cnt)     # @eaDir/ 与 .git/ 被内置规则剪掉
tot, cnt = ba.dir_size(os.path.join(bk, "撞名备份"))
assert cnt == len(CASE_PAIR), (cnt, CASE_PAIR)  # 大小写撞名:存活几个算几个
# 没有配置时:临时暂存/ 下 x.bin(1100)+ 子目录/y.bin(1200)
assert ba.dir_size(TMP, rel_parts=["临时暂存"]) == (TOP_SIZE + SUB_SIZE, 2)
ba.CONFIG_SKIP.extend(["临时暂存/*"])
# 配置剪枝按**备份根**锚定:子目录 的相对路径是 临时暂存/子目录 → 命中
assert ba.dir_size(TMP, rel_parts=["临时暂存"]) == (TOP_SIZE, 1)
# 同一个模式,但锚定在别处(比如把 临时暂存 当成根)就不命中 —— 证明锚定不是糊的
assert ba.dir_size(TMP, rel_parts=[]) == (TOP_SIZE + SUB_SIZE, 2)
ba.CONFIG_SKIP.clear()
ba.CONFIG_SKIP.extend(["临时暂存"])
# 裸名字在任意深度都命中。两项分工:自己那一层由 _collect_items 在调用 dir_size
# 之前就拒掉(dir_size 只检查子层,不检查自己的根),而 dir_size 里 子目录 的相对
# 路径含 临时暂存 这一级,所以同样被剪。
assert ba._cfg_match(["临时暂存"], ba.CONFIG_SKIP) is True
assert ba._cfg_match(["临时暂存", "子目录"], ba.CONFIG_SKIP) is True
assert ba.dir_size(TMP, rel_parts=["临时暂存"]) == (TOP_SIZE, 1)
ba.CONFIG_SKIP.clear()
tot4, cnt4 = ba.dir_size(os.path.join(bk, "照片备份_2024-01-01"))
assert (tot4, cnt4) == (2040, 2), (tot4, cnt4)  # 不传 rel_parts 时旧行为不变
print("  ✓ dir_size:内置剪枝照旧、配置按备份根锚定、自己那层交给 _collect_items、"
      "省略 rel_parts 时行为不变")

# -- 15. 锚定规则的纯函数证据(SKILL.md 那张表的来源)--------------------
m = ba._cfg_match
assert m(["临时暂存", "子目录"], ["临时暂存/*"])
assert m(["临时暂存", "子目录"], ["临时暂存"])
assert not m(["临时暂存"], ["临时暂存/*"])
assert not m(["深层", "临时暂存"], ["临时暂存/*"])
# 三层那条才真能区分「锚定」与「不锚定」:模式被偷偷加上 * 前缀之后,
# 两层的 深层/临时暂存 仍然匹配不上,三层的 深层/临时暂存/子目录 却会匹配上。
# 变异测试 M22 就是靠这一条才在三个 skill 里都被抓到(第一版只有两层那条)。
assert not m(["深层", "临时暂存", "子目录"], ["临时暂存/*"])
assert m(["深层", "临时暂存", "子目录"], ["深层/临时暂存/*"])
assert not m(["深层", "临时暂存"], ["深层/临时暂存/*"])   # /* 需要下面还有一层
assert m(["深层", "临时暂存"], ["临时暂存"])
assert m(["spaced backup 2024-06-01 "], ["spaced*"])       # 含空格
assert m(["SAMPLES"], ["samples"])
assert m(["lowcase"], ["LOWCASE"])
assert m(["临时暂存", "sub"], ["临时暂存/SUB"])
assert m(["Samples", "Sub"], ["SAMPLES/*"])
assert m(["临时暂存"], ["*"])
assert not m([], ["*"])
assert not m(["临时暂存"], [])
assert not m([], [])
print("  ✓ _cfg_match 锚定规则:17 条纯函数断言")
PYEOF
rm -rf "$CFG_SRC"

echo ""
echo "🎉 所有 smoke test 通过"
