#!/bin/bash
# dedup-finder skill 烟雾测试(离线,不需要 NAS — 用本地 fixture 目录)
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
PY="${PY:-python3}"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
DD_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import re
import sys

skill_dir = os.environ["DD_SKILL_DIR"]
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
if name != "dedup-finder":
    print(f"❌ name 错: {name!r}")
    sys.exit(1)
if "触发词" not in block or "不适用" not in block:
    print("❌ description 缺少 触发词/不适用 段落")
    sys.exit(1)
print("  ✓ SKILL.md 合法,name=dedup-finder")
PYEOF

echo "=== TEST 2: 模块可语法检查 ==="
"$PY" -m py_compile "$SKILL_DIR/dedup_finder.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: 纯函数(hash/保留优先级) ==="
DD_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYEOF'
import os
import sys
import tempfile

sys.path.insert(0, os.environ["DD_SKILL_DIR"])
import dedup_finder as dd

# hash 一致性 + 头部/全量
d = tempfile.mkdtemp()
a = os.path.join(d, "a.bin")
b = os.path.join(d, "b.bin")
payload = os.urandom(200_000)
open(a, "wb").write(payload)
open(b, "wb").write(payload)
assert dd.full_hash(a) == dd.full_hash(b)
assert dd.head_hash(a) == dd.head_hash(b)
# 尾部不同 → head 相同但 full 不同(三级指纹的价值)
c = os.path.join(d, "c.bin")
open(c, "wb").write(payload[:100_000] + os.urandom(100_000))
assert dd.head_hash(a) == dd.head_hash(c)      # 头部 64KB 一样
assert dd.full_hash(a) != dd.full_hash(c)      # 全量不一样
# 不存在的文件返回 None 而非崩
assert dd.full_hash(os.path.join(d, "nope")) is None

# 保留优先级:成品目录 > 浅路径 > 旧文件 > 短名
items = [
    {"path": "data/x/xxx 副本.mp4", "name": "xxx 副本.mp4", "mtime": 200},
    {"path": "成品/xxx.mp4", "name": "xxx.mp4", "mtime": 300},
    {"path": "data/xxx.mp4", "name": "xxx.mp4", "mtime": 100},
]
ranked = sorted(items, key=dd.keep_rank)
assert ranked[0]["path"] == "成品/xxx.mp4", ranked  # 成品优先
assert ranked[-1]["path"] == "data/x/xxx 副本.mp4", ranked  # 深+副本名 最后

print("  ✓ 纯函数用例通过")
PYEOF

echo "=== TEST 4: fixture 端到端 scan ==="
FIXTURE="$(mktemp -d /tmp/dedup-finder-fixture.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT

ROOT="$FIXTURE/data"
mkdir -p "$ROOT/照片" "$ROOT/照片/子目录" "$ROOT/下载"
DD_SKILL_DIR="$SKILL_DIR" FIXTURE_ROOT="$ROOT" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile

root = os.environ["FIXTURE_ROOT"]
# 3 份完全相同(应判重复),1 份同 size 但内容不同(不应判重复)
payload = os.urandom(5000)
for p in ["照片/a.jpg", "照片/子目录/a 副本.jpg", "下载/a(1).jpg"]:
    open(os.path.join(root, p), "wb").write(payload)
open(os.path.join(root, "照片/unique.jpg"), "wb").write(os.urandom(5000))
# 小文件(< min-size 默认 1KB)应被跳过
open(os.path.join(root, "照片/tiny.jpg"), "wb").write(payload[:100])

skill = os.environ["DD_SKILL_DIR"]
out = os.path.join(tempfile.mkdtemp(), "dups.json")
r = subprocess.run(
    [sys.executable, f"{skill}/dedup_finder.py", "scan",
     "--root", root, "--output", out],
    capture_output=True, text=True,
    encoding="utf-8", errors="replace",
)
assert r.returncode == 0, r.stderr
data = json.loads(open(out, encoding="utf-8").read())

assert data["skill"] == "dedup-finder"
s = data["stats"]
assert s["files_skipped_small"] >= 1, s       # tiny.jpg 被跳过
assert s["duplicate_groups"] == 1, s          # 只有一组真重复
assert s["redundant_files"] == 2, s           # 3 份 → 2 个冗余
g = data["issues"][0]
assert g["count"] == 3, g
assert g["wasted_bytes"] == 5000 * 2, g
# keep 应选中「照片/a.jpg」(最浅+短名),drop 含副本和 (1)
assert g["keep"]["path"].endswith("a.jpg") and "副本" not in g["keep"]["path"], g
drop_paths = [d["path"] for d in g["drop"]]
assert any("副本" in p for p in drop_paths), g
assert any("(1)" in p for p in drop_paths), g
# unique.jpg 不在任何重复组里
assert "unique" not in json.dumps(data["issues"]), "内容不同的文件被误判重复!"
print("  ✓ fixture scan:精确去重、零误报、保留优先级正确")
PYEOF

echo "=== TEST 5: --help / 多 root ==="
"$PY" "$SKILL_DIR/dedup_finder.py" --help >/dev/null
"$PY" "$SKILL_DIR/dedup_finder.py" scan --help | grep -q -- "--root"
"$PY" "$SKILL_DIR/dedup_finder.py" scan --help | grep -q -- "--min-size"
echo "  ✓ CLI help 可用"

echo "=== TEST 6: 非 UTF-8 stdout 不崩(Windows / Agent 捕获输出场景) ==="
# Windows 上 stdout 被重定向时 Python 用 locale 编码(cp1252/GBK)而非 UTF-8,
# 报告里的中文会 UnicodeEncodeError 让整个扫描中断 —— AI Agent 捕获输出正是此场景。
ENC_DIR="$(mktemp -d /tmp/dedupfinde-enc.XXXXXX)"
mkdir -p "$ENC_DIR/图纸"
printf 'x' > "$ENC_DIR/图纸/平面.dwg"
printf 'y' > "$ENC_DIR/合同.pdf"
rc_utf8=0; rc_ascii=0
out_utf8=$(PYTHONIOENCODING=utf-8 "$PY" "$SKILL_DIR/dedup_finder.py" scan --root "$ENC_DIR" 2>&1) || rc_utf8=$?
out_ascii=$(PYTHONIOENCODING=ascii "$PY" "$SKILL_DIR/dedup_finder.py" scan --root "$ENC_DIR" 2>&1) || rc_ascii=$?
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
# 本 skill 的输出是**删除计划**,而 prefer_keep_hints 能反转「建议保留哪一份」,
# 所以这一节除了功能断言,还专门盯着「生效的提示词必须在输出里看得见」。
# config.json 按**脚本自己所在目录**(__file__)解析 —— 不是 cwd,也不是 --root。
CFG_SRC="$(mktemp -d /tmp/dedupfinde-cfg.XXXXXX)"
mkdir -p "$CFG_SRC/installed" "$CFG_SRC/lib"
cp "$SKILL_DIR/dedup_finder.py" "$CFG_SRC/installed/"
DD_CFG_SRC="$CFG_SRC" "$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import time
from pathlib import Path

src = os.environ["DD_CFG_SRC"]
installed = os.path.join(src, "installed")
script = os.path.join(installed, "dedup_finder.py")
cfg = os.path.join(installed, "config.json")
lib = os.path.join(src, "lib")
DAY = 86400
BUILTIN_HINTS = ["成品", "源文件", "原始", "master", "original", "import", "相册"]

PA = bytes((i * 7 + 13) % 256 for i in range(4000))    # A 组
PB = bytes((i * 11 + 29) % 256 for i in range(4000))   # B 组
PC = bytes((i * 5 + 41) % 256 for i in range(4000))    # C 组
PD = bytes((i * 3 + 57) % 256 for i in range(4000))    # D 组
PE = bytes((i * 13 + 71) % 256 for i in range(4000))   # E 组
PG = bytes((i * 19 + 83) % 256 for i in range(4000))   # G 组
PF = bytes((i * 23 + 97) % 256 for i in range(4000))   # F 组(大小写撞名对)


def w(rel, body, age_days=0):
    p = os.path.join(lib, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(body)
    if age_days:
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))


# ---- A 组:反转靶子。两份都不含任何内置提示词,基线靠「浅路径」定胜负 ----
w("浅层/A.bin", PA, 500)                  # 浅 + 旧 → 基线的 keep
w("工作/终稿/深层/A.bin", PA, 100)         # 深 + 新 → 基线的 drop;加 终稿 后翻上来

# ---- B 组:证明 prefer_keep_hints 是**追加**而不是替换内置 ----
w("源文件/B.bin", PB, 100)                 # 内置提示词 + 浅 + 新
w("散落/深/更深/B.bin", PB, 900)            # 无提示词 + 深 + 旧 → 基线就是 drop

# ---- C 组:7 份,跨 0~3 层,用来断言完整排序而不只是第一名 ----
# 年龄**刻意与深度打架**:临时/2024(depth 2)比 临时(depth 1)更旧,
# 工作/终稿/深层(depth 3)是全局最新。所以 keep_rank 的 depth 那一层是**载荷性**的
# —— 它一旦失效(相对路径退回反斜杠时就是如此,见 SKILL.md「路径分隔符」),
# 下面手推的 EXPECT_C 立刻就不对。**不要把年龄改成"越浅越旧"来让它看起来更整齐**,
# 那会让 depth 层变得不可观测,顺带把那个分隔符修复的唯一非 Windows 护栏拆掉。
w("成品/C.bin", PC, 500)                   # 内置提示词,depth 1
w("照片/C.bin", PC, 400)                   # depth 1
w("照片/子目录/C 副本.bin", PC, 300)         # depth 2
w("下载/C(1).bin", PC, 200)                 # depth 1,这一层里最新
w("工作/终稿/深层/C.bin", PC, 100)           # depth 3,全局最新
w("临时/C.bin", PC, 600)                   # depth 1,全局最旧(skip_dirs 靶子)
w("临时/2024/C.bin", PC, 610)               # depth 2,比 临时/C.bin 更旧(锚定靶子)

# ---- D 组:大小写不敏感要**双向**测,所以用两个不同名字的目录 ----
w("SAMPLES/D.bin", PD, 300)
w("lowcase/D.bin", PD, 200)

# ---- E 组:shell 危险名 ----
w("-odd/E.bin", PE, 300)
w("  spaced name/E.bin", PE, 200)

# ---- G 组:大小写双向靶子。三份都不含任何提示词,基线纯按 mtime 排 ----
# 刻意用三个不同名字且互不为子串:两个「非第一名」各当一次靶子,这样一个方向用
# 「小写模式打大写目录」、另一个方向用「大写模式打小写目录」,两次都真的翻位。
# (第一版把这两份建在测试中途,而且用了 A 组的 payload —— 它们直接并进了 A 组。)
w("OLD/G.bin", PG, 300)
w("target1/G.bin", PG, 200)
w("TARGET2/G.bin", PG, 100)

# ---- 不该被报成重复的 ----
w("照片/unique.bin", bytes((i * 17 + 3) % 256 for i in range(4000)), 350)  # 同 size 不同内容
w("照片/tiny.bin", PA[:100], 350)          # 低于 --min-size 默认 1KB
big = bytes((i * 29 + 7) % 256 for i in range(200000))
w("big1.bin", big, 800)
w("deep/big1 copy.bin", big, 700)          # 真重复(全量 hash 相同)
w("big2.bin", big[:100000] + bytes((i * 31 + 11) % 256 for i in range(100000)), 600)
# AppleDouble 与系统垃圾:内置就跳过
w("._A.bin", b"a" * 40)
w(".DS_Store", b"s" * 40)
w("@eaDir/A.bin", PA)
w(".git/A.bin", PA)
# 大小写撞名对:大小写不敏感的卷(macOS 默认 / Windows)上第二个会覆盖第一个,
# 所以断言跟着实际存活的数量走,不假定文件系统行为
w("CASE.bin", PF, 150)
w("case.bin", PF, 140)
CASE_PAIR = sorted(n for n in os.listdir(lib) if n.lower() == "case.bin")
assert 1 <= len(CASE_PAIR) <= 2, CASE_PAIR

# 基线排序是按 (prefer, depth, mtime, len(name)) 手推出来的,不是从上一次运行抄的:
#   A 组:两份都 prefer=1 → 浅层/A.bin(0 个 /)在前
#   B 组:源文件/B.bin 命中内置提示词 prefer=0 → 在前,与深度/新旧无关
#   C 组:成品/C.bin prefer=0 独占第一;其余 prefer=1,按深度 1→2→3,
#        同深度按 mtime 旧→新:临时(600) → 照片(400) → 下载(200);
#        第二层:临时/2024(610) → 照片/子目录(300);第三层:工作/终稿/深层(100)
EXPECT_A = ["浅层/A.bin", "工作/终稿/深层/A.bin"]
EXPECT_B = ["源文件/B.bin", "散落/深/更深/B.bin"]
EXPECT_C = ["成品/C.bin", "临时/C.bin", "照片/C.bin", "下载/C(1).bin",
            "临时/2024/C.bin", "照片/子目录/C 副本.bin", "工作/终稿/深层/C.bin"]
# 加上 终稿 之后:工作/终稿/深层/C.bin 从 prefer=1 变 prefer=0,升到第二名
EXPECT_C_HINTED = ["成品/C.bin", "工作/终稿/深层/C.bin", "临时/C.bin", "照片/C.bin",
                   "下载/C(1).bin", "临时/2024/C.bin", "照片/子目录/C 副本.bin"]
#   G 组:三份都 prefer=1、同为 1 层 → 纯按 mtime 旧→新
EXPECT_G = ["OLD/G.bin", "target1/G.bin", "TARGET2/G.bin"]
# 顶层目录数(独立数出来的,不读被测实现):skip_dirs=["*"] 时被剪掉的就是这些
TOP_DIRS = len([n for n in os.listdir(lib)
                if os.path.isdir(os.path.join(lib, n))
                and not n.startswith(".") and n != "@eaDir"])


def write_cfg(text):
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(text)


def drop_cfg():
    if os.path.exists(cfg):
        os.remove(cfg)


def run(*extra, expect=0, cwd=None, json_out=True):
    args = [sys.executable, script, "scan", "--root", lib]
    if json_out:
        args.append("--json")
    # cwd 故意设成 src(既不是脚本目录也不是被扫描目录):证明解析与 cwd 无关
    r = subprocess.run(args + list(extra), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd or src)
    assert r.returncode == expect, (r.returncode, r.stdout[-400:], r.stderr)
    return r


def scan(*extra, cwd=None):
    r = run(*extra, cwd=cwd)
    return json.loads(r.stdout), r.stderr


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


def all_paths(d):
    """所有出现在重复组里的路径。

    **不要**拿子串去搜 `json.dumps(issues)`:每条 issue 的 `keep.reason` 是
    "启发式:成品/源目录优先→浅路径→旧文件→短名",里面就含 `成品/`。第一版
    `assert "成品/" not in blob` 在本机(大小写不敏感 → F 组不成组 → issues 为空)
    是绿的,在 ubuntu CI 上因为多出一组、reason 里那个 `成品/` 而红 —— 典型的
    「needle 被另一条路径也产生」。
    """
    return [p for g in d["issues"]
            for p in [g["keep"]["path"]] + [x["path"] for x in g["drop"]]]


def order(d, member):
    """某一组的完整保留顺序:keep 在前,drop 按脚本给出的顺序在后。"""
    for g in d["issues"]:
        if member in [g["keep"]["path"]] + [x["path"] for x in g["drop"]]:
            return [g["keep"]["path"]] + [x["path"] for x in g["drop"]]
    raise AssertionError(f"找不到含 {member} 的重复组:{json.dumps(d['issues'], ensure_ascii=False)}")


# -- 0. 基线:没有 config.json 时,stats 里连 config 这个键都不该出现 ---------
drop_cfg()
base, base_err = scan()
assert "config" not in base["stats"], base["stats"].get("config")
assert "已加载覆盖配置" not in base_err, base_err
bs = base["stats"]
assert bs["files_skipped_small"] == 1, bs         # tiny.bin
# A B C D E G + big1 = 7 组;F 组(大小写撞名对)只有在大小写**敏感**的卷上才是
# 两份、才成一组,所以这一项由实测的存活数推出来,不写死。
EXPECT_GROUPS = 7 + (1 if len(CASE_PAIR) == 2 else 0)
assert bs["duplicate_groups"] == EXPECT_GROUPS, (bs, EXPECT_GROUPS, CASE_PAIR)
assert order(base, "OLD/G.bin") == EXPECT_G, order(base, "OLD/G.bin")
assert order(base, "浅层/A.bin") == EXPECT_A, order(base, "浅层/A.bin")
assert order(base, "源文件/B.bin") == EXPECT_B, order(base, "源文件/B.bin")
assert order(base, "成品/C.bin") == EXPECT_C, order(base, "成品/C.bin")
blob = json.dumps(base["issues"], ensure_ascii=False)
assert "unique" not in blob, "同 size 不同内容的文件被误判重复!"
assert "big2" not in blob, "头部相同、尾部不同的文件被误判重复!"
assert "._A.bin" not in blob and ".DS_Store" not in blob
assert "@eaDir" not in blob and ".git/" not in blob
assert bs["hashed_full"] < bs["hashed_head"], bs   # 三级指纹确实在第二级砍掉了一些
print(f"  ✓ 无 config.json:stats 无 config 键、stderr 无提示、{bs['duplicate_groups']} 组重复;"
      f"零误报(unique/big2 都没被报),三组排序与手推一致(大小写撞名存活 {len(CASE_PAIR)} 个)")

# -- 0b. 人类可读报告:没有配置时一个字都不印(有配置时的断言在第 7 组)----
r = run(json_out=False)
assert "已加载覆盖配置" not in r.stdout, r.stdout
assert "⚙" not in r.stdout, r.stdout
assert "保留提示词生效全集" not in r.stdout, r.stdout
print("  ✓ 人类报告:无配置时不印任何覆盖层信息(有配置时必须印,见第 7 组)")

# -- 1. 头条:prefer_keep_hints 反转「建议保留哪一份」----------------------
# 这一条是本 skill 的安全要点 —— 一份配置就能把删除计划的第一名换掉。
write_cfg(json.dumps({"prefer_keep_hints": ["终稿"]}))
d, err = scan()
after = order(d, "浅层/A.bin")
assert after == ["工作/终稿/深层/A.bin", "浅层/A.bin"], after
assert after[0] == EXPECT_A[1] and after[1] == EXPECT_A[0], (after, EXPECT_A)
assert after != EXPECT_A, "加了提示词但排序没变 —— 断言是假的"
assert d["stats"]["config"]["prefer_keep_hints"] == ["终稿"]
assert d["stats"]["config"]["prefer_keep_hints_effective"] == BUILTIN_HINTS + ["终稿"], \
    d["stats"]["config"]
assert "已加载覆盖配置" in err and "prefer_keep_hints 1 条" in err, err
print("  ✓ prefer_keep_hints=[终稿]:A 组 keep 从 浅层/A.bin 反转成 工作/终稿/深层/A.bin"
      "(前后顺序逐条断言)")

# -- 2. C 组:同一条配置只把命中那份**升位**,第一名不动 --------------------
assert order(d, "成品/C.bin") == EXPECT_C_HINTED, order(d, "成品/C.bin")
assert order(d, "成品/C.bin") != EXPECT_C, "C 组排序没变"
print("  ✓ C 组:工作/终稿/深层/C.bin 从第 7 升到第 2,内置 成品 仍是第一名")

# -- 3. 是**追加**不是替换:内置提示词照样生效 ----------------------------
assert order(d, "源文件/B.bin") == EXPECT_B, order(d, "源文件/B.bin")
print("  ✓ 追加而非替换:加了 终稿 之后,内置的 源文件 仍然赢下 B 组")

# -- 4. 匹配不到任何东西的提示词 → 排序与基线逐条相同(负控制)------------
write_cfg(json.dumps({"prefer_keep_hints": ["zzz-没有这个目录"]}))
d, _ = scan()
for member, expect in (("浅层/A.bin", EXPECT_A), ("源文件/B.bin", EXPECT_B),
                       ("成品/C.bin", EXPECT_C)):
    assert order(d, member) == expect, (member, order(d, member))
assert d["stats"]["config"]["prefer_keep_hints"] == ["zzz-没有这个目录"]
assert d["stats"]["config"]["prefer_keep_hints_effective"] == \
    BUILTIN_HINTS + ["zzz-没有这个目录"]
print("  ✓ 匹配不到任何路径的提示词:三组排序与基线逐条相同(不是静默失效,配置仍在 stats 里)")

# -- 5. 归一化:前后空白 strip、大写折成小写(比的就是 path.lower())-------
write_cfg(json.dumps({"prefer_keep_hints": ["  终稿  "]}))
d, _ = scan()
assert order(d, "浅层/A.bin") == ["工作/终稿/深层/A.bin", "浅层/A.bin"]
assert d["stats"]["config"]["prefer_keep_hints"] == ["终稿"]
print("  ✓ 提示词被 strip;子串匹配跑在 path.lower() 上,与内置提示词同一条规则")

# -- 5b. 大小写:目录大写 / 提示词小写,与目录小写 / 提示词大写,双向都成立 --
# 只测一个方向是测不出来的:实现里路径总是先 .lower(),所以「目录大写 / 模式小写」
# 即使忘了给模式做 lower 也照样过。两个方向各挑一个基线里不是第一名的当靶子。
for pat, target in (("TARGET1", "target1/G.bin"), ("target2", "TARGET2/G.bin")):
    write_cfg(json.dumps({"prefer_keep_hints": [pat]}))
    d, _ = scan()
    g = order(d, "OLD/G.bin")
    assert g[0] == target, (pat, target, g)
    assert g != EXPECT_G, (pat, g)          # 必须真的翻位,不是「本来就第一」
print("  ✓ 大小写双向成立:TARGET1→target1/ 与 target2→TARGET2/ 都把那份顶到第一")

# -- 6. 是**子串**匹配,不是 glob:`终*` 不该命中 终稿/ -------------------
# 刻意不升级成 fnmatch —— 那会连内置提示词的语义一起改掉(见 PR 说明)。
write_cfg(json.dumps({"prefer_keep_hints": ["终*"]}))
d, _ = scan()
assert order(d, "浅层/A.bin") == EXPECT_A, order(d, "浅层/A.bin")
assert order(d, "成品/C.bin") == EXPECT_C, order(d, "成品/C.bin")
print("  ✓ 子串语义钉住:`终*` 不是通配,命中不了 终稿/(没有偷偷升级成 glob)")

# -- 6b. 含 / 的提示词也能用(它就是路径子串)---------------------------
write_cfg(json.dumps({"prefer_keep_hints": ["工作/终稿"]}))
d, _ = scan()
assert order(d, "浅层/A.bin") == ["工作/终稿/深层/A.bin", "浅层/A.bin"]
print("  ✓ 含 / 的提示词按路径子串生效(可移植性提醒已写进 SKILL.md)")

# -- 7. 生效的提示词必须**看得见**:人类报告里也要有 ----------------------
r = run("--top", "50", json_out=False)
assert "已加载覆盖配置" in r.stdout, r.stdout
assert_cfg_path_in(r.stdout)
assert "保留提示词生效全集" in r.stdout, r.stdout
for h in BUILTIN_HINTS + ["终稿"]:
    assert h in r.stdout, (h, r.stdout[-800:])
assert "✓ 保留: 工作/终稿/深层/A.bin" in r.stdout, r.stdout
print("  ✓ 人类报告印出配置路径、追加的提示词、以及「内置 + 追加」的生效全集")

# -- 8. skip_dirs:命中的目录整棵不进入 ----------------------------------
write_cfg(json.dumps({"skip_dirs": ["临时"]}))
d, _ = scan()
c = order(d, "成品/C.bin")
assert "临时/C.bin" not in c and "临时/2024/C.bin" not in c, c
assert d["stats"]["config"]["skipped_dirs"] == 1, d["stats"]["config"]
assert d["stats"]["files_scanned"] == bs["files_scanned"] - 2, d["stats"]
# C 组少两份,剩下的相对顺序不变
assert c == [x for x in EXPECT_C if not x.startswith("临时/")], (c, EXPECT_C)
print("  ✓ skip_dirs=[临时]:C 组从 7 份变 5 份,其余顺序不变,skipped_dirs=1")

# -- 8b. 锚定边界:临时/* 不命中 临时 自己,只命中它的子目录 --------------
write_cfg(json.dumps({"skip_dirs": ["临时/*"]}))
d, _ = scan()
c = order(d, "成品/C.bin")
assert "临时/C.bin" in c, c                       # 直接躺在 临时/ 里的还在
assert "临时/2024/C.bin" not in c, c               # 子目录被剪
assert d["stats"]["config"]["skipped_dirs"] == 1, d["stats"]["config"]
assert d["stats"]["files_scanned"] == bs["files_scanned"] - 1, d["stats"]
print("  ✓ 临时/* 命中 临时/2024 但不命中 临时 自身(与裸名字 临时 可区分)")

# -- 8c. 带 / 的模式锚定在 root -----------------------------------------
write_cfg(json.dumps({"skip_dirs": ["工作/终稿/*"]}))
d, _ = scan()
gp = all_paths(d)
assert not [p for p in gp if "工作/终稿" in p], gp
# A 组被剪掉一份后只剩 浅层/A.bin → 不再是重复组;C 组只是少一个成员,仍然成组。
# 所以组数恰好 -1,这个数字能区分「剪对了那一层」与「剪多了」。
assert "浅层/A.bin" not in gp, gp
assert order(d, "成品/C.bin") == [x for x in EXPECT_C if "工作/终稿" not in x], blob
assert d["stats"]["duplicate_groups"] == bs["duplicate_groups"] - 1, d["stats"]
assert d["stats"]["config"]["skipped_dirs"] == 1, d["stats"]["config"]
print("  ✓ 工作/终稿/* 只剪 root 下那一条:A 组因此不再成组(组数 -1),C 组仍在")

# -- 8d. 大小写不敏感双向 ------------------------------------------------
for pat, gone in (("samples", "SAMPLES/D.bin"), ("LOWCASE", "lowcase/D.bin")):
    write_cfg(json.dumps({"skip_dirs": [pat]}))
    d, _ = scan()
    assert gone not in json.dumps(d["issues"], ensure_ascii=False), (pat, gone)
    assert d["stats"]["config"]["skipped_dirs"] == 1, (pat, d["stats"]["config"])
print("  ✓ skip_dirs 大小写双向成立:samples→SAMPLES/ 与 LOWCASE→lowcase/")

# -- 8e. skip_dirs 只作用于目录:* 也管不到 root 下的散文件 --------------
write_cfg(json.dumps({"skip_dirs": ["*"]}))
d, _ = scan()
assert d["stats"]["dirs_visited"] == 1, d["stats"]
gp = all_paths(d)
# 剪掉所有目录之后,还能成组的只可能是 root 下的散文件 → 路径里不该再有 "/"
assert not [p for p in gp if "/" in p], gp
assert "deep/big1 copy.bin" not in gp, gp
assert d["stats"]["config"]["skipped_dirs"] == TOP_DIRS, \
    (d["stats"]["config"], TOP_DIRS)
print(f"  ✓ skip_dirs=[*]:只扫 root 一层(dirs_visited=1),剪掉 "
      f"{d['stats']['config']['skipped_dirs']} 个目录(= 实测顶层目录数);散文件仍在")

# -- 9. 多 root:每个 root 各自锚定 -------------------------------------
lib2 = os.path.join(src, "lib2")
os.makedirs(os.path.join(lib2, "临时"), exist_ok=True)
with open(os.path.join(lib2, "临时", "A.bin"), "wb") as fh:
    fh.write(PA)
write_cfg(json.dumps({"skip_dirs": ["临时"]}))
r = subprocess.run([sys.executable, script, "scan", "--root", lib, "--root", lib2,
                    "--json"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", cwd=src)
assert r.returncode == 0, r.stderr
d = json.loads(r.stdout)
assert d["stats"]["config"]["skipped_dirs"] == 2, d["stats"]["config"]
assert not [p for p in all_paths(d) if "临时" in p], all_paths(d)
print("  ✓ 多 --root:skip_dirs 在每个 root 上各自锚定(两边各剪 1 个)")

# -- 10. 空对象 {}:加载并提示,但结果与无配置逐条相同 --------------------
def shape(x):
    return sorted((g["keep"]["path"], tuple(y["path"] for y in g["drop"]))
                  for g in x["issues"])


write_cfg("{}")
d, err = scan()
assert shape(d) == shape(base), (shape(d), shape(base))
NOISY = ("config", "elapsed_sec")     # elapsed_sec 是墙钟,两边不可能相等
assert {k: v for k, v in d["stats"].items() if k not in NOISY} == \
    {k: v for k, v in bs.items() if k not in NOISY}, d["stats"]
assert d["stats"]["config"]["skip_dirs"] == []
assert d["stats"]["config"]["prefer_keep_hints"] == []
assert d["stats"]["config"]["prefer_keep_hints_effective"] == BUILTIN_HINTS
assert "已加载覆盖配置" in err, err
print("  ✓ 空对象 {} 加载成功:删除计划与无配置时逐组相同,生效全集 = 内置 7 条")

# -- 11. 被扫描目录里的 config.json 必须被**忽略**(证明按 __file__ 解析)---
# 这里刻意让它「如果被读到就一定看得出来」:内容会把 B 组的 keep 反转,并且
# 体积超过 --min-size,所以它自己也会被当成数据扫进去。
drop_cfg()
root_cfg = os.path.join(lib, "config.json")
with open(root_cfg, "w", encoding="utf-8") as fh:
    json.dump({"prefer_keep_hints": ["散落", "padding" + "x" * 2000]},
              fh, ensure_ascii=False)
assert os.path.getsize(root_cfg) > 1024, os.path.getsize(root_cfg)
for cwd in (src, lib, installed):
    d, err = scan(cwd=cwd)
    assert order(d, "源文件/B.bin") == EXPECT_B, (cwd, order(d, "源文件/B.bin"))
    assert "config" not in d["stats"], (cwd, d["stats"].get("config"))
    assert "已加载覆盖配置" not in err, (cwd, err)
# 它被当成一个普通文件扫到了(files_scanned 多 1)—— 这正好证明它是数据、不是配置
d, _ = scan()
assert d["stats"]["files_scanned"] == bs["files_scanned"] + 1, d["stats"]
os.remove(root_cfg)
print("  ✓ 被扫描目录里的 config.json 被忽略(3 种 cwd 都试过),只当普通文件扫")

# -- 12. 畸形配置:一律 exit=1,指名文件与 offending 键,且不污染 --json stdout
BAD = [
    ("非法 JSON", "{not json", "不是合法 JSON"),
    ("空文件", "", "不是合法 JSON"),
    ("顶层不是对象", "[]", "顶层"),
    ("未知键(少写一个 s)", '{"skip_dir": ["临时"]}', "skip_dir"),
    ("未知键(照抄 file-sorter 的 whitelist_dirs)", '{"whitelist_dirs": ["临时"]}',
     "whitelist_dirs"),
    ("未知键(照抄另两个 skill 的 extension_overrides)",
     '{"extension_overrides": {"bin": "other"}}', "extension_overrides"),
    ("skip_dirs 不是数组", '{"skip_dirs": "临时"}', "skip_dirs"),
    ("skip_dirs 元素不是字符串", '{"skip_dirs": [1]}', "skip_dirs[0]"),
    ("skip_dirs 空字符串", '{"skip_dirs": ["  "]}', "skip_dirs[0]"),
    ("skip_dirs 绝对路径", '{"skip_dirs": ["/临时"]}', "绝对路径"),
    ("skip_dirs Windows 盘符", '{"skip_dirs": ["Z:\\\\data"]}', "绝对路径"),
    ("prefer_keep_hints 不是数组", '{"prefer_keep_hints": "终稿"}',
     "prefer_keep_hints"),
    ("prefer_keep_hints 元素不是字符串", '{"prefer_keep_hints": [1]}',
     "prefer_keep_hints[0]"),
    ("prefer_keep_hints 空字符串", '{"prefer_keep_hints": ["  "]}',
     "prefer_keep_hints[0]"),
    ("prefer_keep_hints 允许绝对路径(它是子串不是路径)——所以这条**不该**报错",
     '{"prefer_keep_hints": ["/终稿/"]}', None),
    ("两个键都拼错", '{"skip_dir": [], "prefer_keep_hint": []}', "prefer_keep_hint"),
]
for label, text, needle in BAD:
    write_cfg(text)
    if needle is None:
        r = run(expect=0)
        assert json.loads(r.stdout)["stats"]["config"]["prefer_keep_hints"] == ["/终稿/"], \
            (label, r.stdout[:200])
        continue
    r = run(expect=1)
    assert r.stdout == "", (label, "错误不能污染 --json 的 stdout", r.stdout[:200])
    assert "config.json" in r.stderr, (label, r.stderr)
    assert needle in r.stderr, (label, needle, r.stderr)
    assert "❌" in r.stderr, (label, r.stderr)
drop_cfg()
print(f"  ✓ {len(BAD) - 1} 种畸形配置全部 exit=1、只写 stderr、指名文件与键;"
      "并钉住 prefer_keep_hints 不做绝对路径检查(它是路径子串)")

# -- 13. 未知键报错里要列出**本 skill** 的可用键 -------------------------
write_cfg('{"extension_overrides": {}}')
r = run(expect=1)
assert "skip_dirs" in r.stderr and "prefer_keep_hints" in r.stderr, r.stderr
assert "extension_overrides" in r.stderr, r.stderr
drop_cfg()
print("  ✓ 未知键报错列出本 skill 的可用键(照抄别的 skill 的键会被明确拒绝)")

# -- 14. 校验先于写入:半途失败的配置不得留下副作用(进程内直接考)--------
sys.path.insert(0, installed)
import contextlib                                          # noqa: E402
import io                                                  # noqa: E402
import dedup_finder as dfmod                               # noqa: E402

bad_dir = Path(src) / "bad"
bad_dir.mkdir(exist_ok=True)
(bad_dir / "config.json").write_text(json.dumps({
    "skip_dirs": ["临时"],
    "prefer_keep_hints": [3],
}, ensure_ascii=False), encoding="utf-8")
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        dfmod.load_config(bad_dir)
    raise AssertionError("坏配置本该 SystemExit")
except SystemExit as e:
    assert "prefer_keep_hints[0]" in str(e), str(e)
    assert "config.json" in str(e), str(e)
assert err.getvalue() == "", err.getvalue()   # 失败路径不该先打印「已加载」
assert dfmod.CONFIG_SKIP == [], dfmod.CONFIG_SKIP     # skip_dirs 没被半路写进去
assert dfmod.CONFIG_INFO == {}, dfmod.CONFIG_INFO
assert dfmod.PREFER_KEEP_HINTS == BUILTIN_HINTS, dfmod.PREFER_KEEP_HINTS

good_dir = Path(src) / "good"
good_dir.mkdir(exist_ok=True)
(good_dir / "config.json").write_text(json.dumps({
    "skip_dirs": [" 临时 "],
    "prefer_keep_hints": [" 终稿 ", "成品"],     # 成品 是内置的,不该重复出现
}, ensure_ascii=False), encoding="utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    dfmod.load_config(good_dir)
assert dfmod.CONFIG_SKIP == ["临时"], dfmod.CONFIG_SKIP        # 前后空白被 strip
assert dfmod.PREFER_KEEP_HINTS == BUILTIN_HINTS + ["终稿"], dfmod.PREFER_KEEP_HINTS
assert dfmod.CONFIG_INFO["prefer_keep_hints"] == ["终稿", "成品"], dfmod.CONFIG_INFO
assert dfmod.CONFIG_INFO["prefer_keep_hints_effective"] == BUILTIN_HINTS + ["终稿"]
# keep_rank 直接吃这个全局:命中提示词的那份排到最前
ranked = sorted([{"path": "工作/终稿/深层/A.bin", "name": "A.bin", "mtime": 900},
                 {"path": "浅层/A.bin", "name": "A.bin", "mtime": 100}],
                key=dfmod.keep_rank)
assert ranked[0]["path"] == "工作/终稿/深层/A.bin", ranked

# keep_rank 的 depth 层数的是 "/",所以 _walk 里那句 .replace(os.sep, "/") 是
# **载荷性的**,不是美化:少了它,Windows 上每条路径的 depth 都是 0,「浅路径优先」
# 整条规则失效。这里把两边的契约钉在一起 —— 直接喂路径给纯函数,**不依赖 runner**,
# 所以在 ubuntu / macOS 上也验得到,不用等 windows-latest 才发现。
BSL = chr(92)
assert dfmod.keep_rank({"path": "a/b/c.bin", "name": "c.bin", "mtime": 1})[1] == 2
assert dfmod.keep_rank({"path": f"a{BSL}b{BSL}c.bin", "name": "c.bin", "mtime": 1})[1] == 0
# 正斜杠时浅的赢,反斜杠时深的赢 —— 后者就是修复前 Windows 上的实际行为
assert dfmod.keep_rank({"path": "G.bin", "name": "G.bin", "mtime": 9}) \
    < dfmod.keep_rank({"path": "a/b/c/G.bin", "name": "G.bin", "mtime": 1})
assert dfmod.keep_rank({"path": "G.bin", "name": "G.bin", "mtime": 9}) \
    > dfmod.keep_rank({"path": f"a{BSL}b{BSL}c{BSL}G.bin", "name": "G.bin", "mtime": 1})
msg = err.getvalue()
assert "已加载覆盖配置" in msg and str(good_dir / "config.json") in msg, msg
assert "skip_dirs 1 条" in msg and "prefer_keep_hints 2 条" in msg, msg
print("  ✓ 校验先于写入:坏配置退出后 PREFER_KEEP_HINTS 与 CONFIG_* 全都没动;"
      "追加时与内置重复的条目不会重复出现")
print("  ✓ 契约钉住:keep_rank 的 depth 只数 '/',所以 _walk 里的 os.sep 归一是"
      "载荷性的(这条在任何 runner 上都验,不必等 windows-latest)")

# -- 15. 锚定规则的纯函数证据(SKILL.md 那张表的来源)--------------------
m = dfmod._cfg_match
assert m(["临时", "2024"], ["临时/*"])
assert m(["临时", "2024"], ["临时"])
assert not m(["临时"], ["临时/*"])
assert not m(["深层", "临时"], ["临时/*"])
# 三层那条才真能区分「锚定」与「不锚定」(变异测试 M22 的判据,见上)
assert not m(["深层", "临时", "子目录"], ["临时/*"])
assert m(["深层", "临时", "子目录"], ["深层/临时/*"])
assert m(["深层", "临时"], ["临时"])
assert m(["  spaced name  "], ["*spaced*"])            # 含空格
assert m(["SAMPLES"], ["samples"])
assert m(["lowcase"], ["LOWCASE"])
assert m(["临时", "sub"], ["临时/SUB"])
assert m(["Samples", "Sub"], ["SAMPLES/*"])
assert m(["临时"], ["*"])
assert not m([], ["*"])
assert not m(["临时"], [])
assert not m([], [])
print("  ✓ _cfg_match 锚定规则:16 条纯函数断言")
PYEOF
rm -rf "$CFG_SRC"

echo ""
echo "🎉 所有 smoke test 通过"
