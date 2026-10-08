# File Sorter Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描任意目录(本地挂载路径),把「什么都往里丢」的乱目录
按扩展名归类,并为每个待归档文件算出 `old → new` 目标路径。
`mkdir -p` / `mv -n` 由 Agent 在用户确认整理计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

定位:family 里唯一的**类型驱动通用分类器**。其他 organizer 都是「某个领域的
合规校验」(照片按日期、音乐按歌手专辑、作品集按项目),本 skill 是「不管什么
领域,先按类型把散文件归位」,常作为进入专项 skill 之前的第一道工序。

## 目录结构

```
skills/file-sorter/
├── SKILL.md          # LLM 工作流(触发词 + 4 个场景 + 整理顺序)
├── file_sorter.py    # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh      # 烟雾测试(离线,fixture 端到端 + 合规库零误报)
└── README.md         # 本文件

# 用户可选自建(仓库里**不**放,见「配置覆盖」):
└── config.json       # 目录白名单 + 扩展名改判的覆盖层
```

## 命令

```bash
python3 skills/file-sorter/file_sorter.py scan --root /Volumes/nas/data

# 类别 + 年份,英文目录名,全部归到「整理后/」下试水
python3 skills/file-sorter/file_sorter.py scan \
  --root /Volumes/nas/data --layout type-year --naming en --dest 整理后 \
  --json --output /tmp/sort.json

# 保住项目目录,只归位散文件
python3 skills/file-sorter/file_sorter.py scan \
  --root /Volumes/nas/data --keep-dir '2024_*' --keep-dir '成品*'

# 大库分批:这轮只出图纸的计划(统计仍全量)
python3 skills/file-sorter/file_sorter.py scan \
  --root /Volumes/nas/data --only-cat cad --output /tmp/sort-cad.json
```

## 每个文件的判定阶梯(顺序即优先级)

```
0. root         --root 自己的目录名能映射到类别      → 当作所有文件的隐含祖先
                  (--root /Volumes/nas/图纸 时,里面的 dwg 视为已就位,
                   不会生成 图纸/图纸/ 这种自己套自己的计划)
1. protected    命中白名单(--keep-dir + config.json
                的 whitelist_dirs,同一个匹配函数) → 跳过,计入 stats.protected
2. compliant    祖先目录名能映射到本文件的类别      → 跳过,计入 stats.compliant
3. nested       祖先目录是「别的」类别目录          → 默认跳过(nested_other_cat)
                                                      --strict 才提议搬家
4. project      落在疑似项目目录里                 → 默认跳过(project_files),
                                                      改为一条目录级 issue
                                                      --split-project-dirs 才拆
5. filtered     类别不在 --only-cat 里             → 跳过,计入 stats.filtered_out
6. 其余                                              → 出 issue:
     junk(系统垃圾)                                action=delete
     junk(临时/残留 .log/.tmp/.bak)                action=delete-confirm
     other(扩展名不认识/无扩展名)                  action=review,target=待分类/
     重名冲突(目标已被占)                          action=review,target 加 __2
     目标目录被同名**文件**占着                     action=review,target 保留但
                                                      标记 mkdir 会失败
     正常                                           action=move,target=<类别>/…
```

第 3、4 步是**防误报的核心**:`图纸/渲染.jpg` 和 `2024_官网改版/` 里的混合文件
都是「有意的结构」,默认一律不动,只在 `stats` 里计数 + 报一条目录级 issue
让人决定。这是与「无脑按扩展名平铺」脚本的关键区别。

## 疑似项目目录判定

```
子目录 d(非类别目录、非白名单)的子树里:文件数 ≥ --project-min-files(默认 3)
                              且 跨 ≥ 2 个类别
→ d 记为疑似项目目录;只报最外层(嵌套的不重复报)
```

`stats.project_dirs` / `project_files` 给总量;每个目录一条 `is_dir: true` 的
issue(带 `files` / `categories` / `sample`),`action=review`。

## 目标路径与重名

- `target_parts(cat, layout, mtime, naming)` 决定目录层级:
  `type` → `图纸/`;`type-year` → `图纸/2024/`;`year-type` → `2024/图纸/`
- `--dest` 再加一层前缀:`整理后/图纸/`
- `occupied` 集合 = 所有现存相对路径 + 已分配的目标;撞名时依次试
  `x__2.ext` / `x__3.ext`,并把原候选写进 `conflict_with`、降级为 `review`
- **撞名封顶**(`MAX_RENAME_TRIES = 10`):同名文件多于 10 个时不再自动编号,
  返回 `target=None` + 建议保留来源目录名(`图片/d9_x.jpg`),`confidence=low`。
  两个理由:① 避免几千个同名文件时的 O(n²) 试探;② `x__4999.jpg` 这种计划
  对用户毫无价值,命名规则该由人定

## 五个会咬人的边界情况(都已处理 + 有回归测试)

| 情况 | 不处理会怎样 | 现在的行为 |
|------|--------------|-----------|
| **大小写撞名**:`a/X.jpg` 与 `b/x.jpg` 都要进 `图片/` | macOS APFS / Windows NTFS 默认**大小写不敏感**,两者是同一路径 → 第二条 `mv -n` **静默不搬**,文件留在原地,而计划显示已成功 | `_probe_case_fold()` 探测后按 `_norm()` 折叠大小写判撞名 → 第二条自动改名 `x__2.jpg` 并降级 `review`。`stats.case_insensitive_fs` 记录探测结果 |
| **目标目录被同名文件占着**:根目录有个无扩展名文件叫 `图纸`,同时 `平面.dwg` 要搬进 `图纸/` | `mkdir 图纸` 直接 `File exists`,整批执行中断在半路 | 目标的每一级都与现存文件路径比对,命中则 `action=review` + `confidence=low` + 说明「先把那个文件移走(它自己也在本计划里)」;`stats.dir_file_conflicts` 计数 |
| **`--root` 本身就是类别目录**:`--root /Volumes/nas/图纸` | 计划把里面的 dwg 搬进 `图纸/图纸/`,在自己的图纸库里再套一层 | root 的 basename 若能映射到类别,就当作所有文件的**隐含祖先** → 同类文件视为已就位。`stats.root_category` 记录 |
| **繁体目录名**:`圖紙/` `文檔/` `視頻/` | 认不出来 → 给繁体用户**另建一套简体类别目录**,等于凭空造出重复分类(本 skill 最该避免的事) | 别名表简繁都收,`_DIR_TO_CAT` 从 100 → **135** 条 |
| **文件名对 shell 不友好**:`-f.pdf`、`--force.jpg`、`price$100.xlsx` | Agent 是**照着 `target` 拼命令**执行的。实测 `mv -n "-f.pdf" 文档/` → `mv: illegal option -- .`,整条失败;`-i` 会让 mv 变交互式(非交互 Agent 里挂住);`$`/反引号即使加了双引号也会被 shell 展开 | `shell_risk()` 检出后在 `problems` 里给出**具体写法**(POSIX 加 `./` 前缀或 `--` 分隔;PowerShell 用 `-LiteralPath`),`target` 照常给出。`stats.shell_unsafe_names` 计数。含空格/中文的正常名字**不报**(双引号就够,报多了会被忽略) |

探测大小写敏感性**不写任何探针文件**(脚本必须只读):拿一个已存在的文件把名字
`swapcase()` 后 `os.path.exists()` —— 不敏感的文件系统会解析回原文件返回 True,
敏感的查无此文件返回 False。翻转名恰好也在扫描集合里时判不了,跳过换一个;
500 个都判不了就退回 `sys.platform in ("darwin", "win32")`。

`_norm()` 只在**撞名判定**时折叠,`target` 输出仍保留原始大小写。
smoke 里的大小写用例按 `stats.case_insensitive_fs` 分支断言,所以在
大小写敏感(Linux CI)和不敏感(macOS / Windows CI)两种 runner 上都成立。

## 疑似副本(不做内容判定)

只对**文件名带副本标记**(`(1)` / `[2]` / `副本` / `copy`)的文件,查是否存在
「同大小 + 去标记后同名」的另一个文件;有则加一条 problem 并计入
`stats.dup_suspects`,引用对象优先选**不带标记的那个**(本尊)。

这是命名启发式,**不是**去重:内容级判定一律交给 `dedup-finder`
(三级指纹零误报)。命中时同时累加 `stats.dup_suspect_bytes`,人类输出里给出
「占待搬体积 N%」—— 让用户**按字节数决定**要不要先去重,而不是背一条规则。

### 与 dedup-finder 的先后:是效率问题,不是正确性问题

实测(同一 fixture,3 个同字节 JPG + 1 对同字节 PDF):

| 顺序 | dedup-finder 结果 |
|------|-------------------|
| 先去重再分类 | 2 组 / 冗余 3 / 634.8 KB |
| **先分类再去重** | **2 组 / 冗余 3 / 634.8 KB(完全相同)** |

去重是内容级、与目录结构无关,分类后照样能配对(甚至跨「已分类目录 + 未分类目录」)。
先去重真正省的是两件事:① 不白搬即将删掉的字节(SMB 上搬几 GB 再删是纯浪费);
② 少产生 `__2` 撞名 —— 同名副本从两个目录搬进同一类目录会撞名,降级成 `review`,
凭空多出人工确认项。反过来先分类也有好处:重复文件并排躺在同一目录里更好认,
还能只对某一类去重(`--root <路径>/图片`)。

**所以文档里不再写「顺序不能反」**(那是早先未经验证的说法),改为给数字让用户自己判断。

<details><summary>自己复现这个实验(约 20 秒,离线)</summary>

```bash
S=skills; W=$(mktemp -d); mkdir -p "$W"/{a,b,c}
python3 - "$W" <<'EOF'
import os, sys
w = sys.argv[1]; blob = os.urandom(300000)
for p in ("a/IMG_0001.jpg", "b/照片副本.jpg", "c/IMG_0001.jpg"):   # 同字节,名字/目录都不同
    open(os.path.join(w, p), "wb").write(blob)
open(f"{w}/合同.pdf", "wb").write(os.urandom(50000))
open(f"{w}/合同 副本.pdf", "wb").write(open(f"{w}/合同.pdf", "rb").read())
open(f"{w}/图纸.dwg", "wb").write(os.urandom(80000))
EOF

# ① 未分类时先去重(基线)
python3 $S/dedup-finder/dedup_finder.py scan --root "$W" | grep 重复组

# ② 按 file-sorter 的计划真的把文件搬进类别目录
python3 $S/file-sorter/file_sorter.py scan --root "$W" --output "$W/plan.json" >/dev/null
python3 - "$W" <<'EOF'
import json, os, shutil, sys
root = sys.argv[1]; d = json.load(open(f"{root}/plan.json"))
for i in d["issues"]:
    if i["action"] == "move" and i.get("target"):
        src, dst = os.path.join(root, i["path"]), os.path.join(root, i["target"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            shutil.move(src, dst)
EOF

# ③ 分类后再去重 —— 组数/冗余数/可回收字节应与 ① 完全一致
python3 $S/dedup-finder/dedup_finder.py scan --root "$W" | grep 重复组
rm -rf "$W"
```

两次都应是 `重复组 2 | 冗余文件 3 | 可回收 634.8 KB`。注意 ② 里 `c/IMG_0001.jpg`
不会被搬走 —— 它与 `a/IMG_0001.jpg` 撞名,被降级成 `review` 交人工,这正是
「先去重能少产生撞名」的来源。
</details>

## JSON 输出

```jsonc
{
  "skill": "file-sorter", "root": "...", "generated_at": "...",
  "stats": {
    "files", "dirs", "total_size_bytes",
    "by_category": {"doc": {"count", "size"}, ...},   // 15 类,含 junk/other
    "root_files",                                      // 散在根目录的
    "compliant", "protected", "nested_other_cat",      // 三种「不动」
    "project_dirs", "project_files",
    "to_move", "to_review", "to_delete",
    "move_bytes", "junk_bytes", "kept_bytes",
    "dup_suspects", "dup_suspect_bytes", "conflicts",
    "dir_file_conflicts",                                 // 目标目录被同名文件占着
    "shell_unsafe_names",                                 // 文件名对 shell 不友好
    "stale_files",
    "case_insensitive_fs",                                // 探测结果:撞名是否折叠大小写
    "root_category",                                      // --root 自身是类别目录时为该类
    "filtered_out",                                      // --only-cat 挡掉的
    "omitted_issues", "issues_truncated",                // 计划截断(--max-issues)
    "target_dirs": {"图纸": {"count", "size"}},        // 归档后结构预览
    "unknown_exts": {".xyz": 3}, "largest": [...],
    "layout", "naming", "dest", "strict", "split_project_dirs",
    "only_cats", "max_issues",
    "sampled_out", "elapsed_sec",
    "config"?                                      // 仅当同目录 config.json 被加载
  },
  "count": 17,
  "issues": [{
    "path", "name", "is_dir", "problems": [...], "category", "category_zh",
    "action",            // move | review | delete-confirm | delete
    "target",            // old → new 的 new;junk / 撞名封顶 时为 null
    "size", "age_days", "confidence",   // high | medium | low
    "conflict_with"?, "files"?, "categories"?, "sample"?   // 目录级 issue
  }],
  "errors": [{"path", "error"}]
}
```

**`stats` 永远全量,`issues` 会被 `--max-issues`(默认 500)截断。**
理由:2 万文件的库全量计划是 2.5 MB JSON,任何 Agent 都读不进去;截断后
203 KB,而汇报用的数字(待归档多少、能回收多少)一个不少。目录级 issue
(疑似项目目录)不参与截断 —— 那是「别动」的信号,不能被淹掉。
分批取计划的正确姿势是 `--only-cat`,不是调大 `--max-issues`。

## 实测规模(本地 SSD,macOS,Python 3.9/3.11)

| 场景 | 文件数 | 扫描耗时 | JSON 大小 |
|------|-------|---------|-----------|
| 混合乱目录(4 层深、57 目录) | 20 000 | **0.28 s** | 203 KB(截断后) |
| 单层平铺照片库 | 100 000 | **1.73 s** | 187 KB(截断后) |
| 5 000 个同名文件(撞名压测) | 5 000 | **0.22 s** | — |

只 `stat`、不读文件内容,所以比 `dedup-finder`(要算 sha1)轻得多。
**注意**:以上是本地 SSD 数字;**SMB 挂载上的 stat 延迟是真正的瓶颈**,
几万文件的小库建议先 `--sample 2000` 摸底再全量。

## 类别表(15 类)

`cad`(图纸/CAD/3D)、`doc`、`design`(设计源文件)、`image`、`video`、`audio`、
`ebook`、`archive`、`installer`、`font`、`code`、`backup`(镜像)、`torrent`、
`junk`、`other`。

与 `nas-report` 的 `CATEGORIES` 有意**不完全一致**:本 skill 从 `design` 里分出
`cad`(dwg/dxf/step/sldprt/rvt/skp…),从 `doc` 里分出 `ebook`,把 `iso`/`img`
从 video 挪到 `backup`,并新增 `font`。原因:分类归档要落到**目录名**上,
「图纸」对用户是有意义的独立目录,而存储画像只需要粗粒度占比。

目录名识别(`_DIR_TO_CAT`)同时吃中英文别名:`图纸`/`CAD`/`Drawings`/`施工图`、
`文档`/`Documents`/`资料`、`图片`/`照片`/`Images`/`img`…,所以已手工分好类的库
不会被再搬一次。别名表刻意保守 —— 认错目录(把项目目录当类别目录)比不认更糟。

## 配置覆盖(`config.json`,issue #15)

可选的一层覆盖,放在**脚本自己所在目录**。**没有这个文件时输出与引入它之前
逐字节一致**(见「零破坏性怎么证的」)。完整 schema、模式锚定规则、出错行为在
`SKILL.md` 的「配置覆盖」一节 —— 那份是给 LLM 读的,这里只记实现决定。

### 为什么代码是复制的,不是共享模块

`zs skill` 的安装方式是**逐目录 `shutil.copytree`**(`cli.py` 的 `skill()`,
`--only a,b` 也一样),所以任何放在 `skills/<name>/` 外面的共享模块都**不会被装到
用户机器上**,一 import 就 ImportError。同仓库 PR #41 遇到过同一件事,当时的选择
就是把工具函数放进 `photo_organizer.py` 而不是新建框架模块 —— 这里沿用那个先例。

代价是这段代码在两个 scanner 里各存一份。为了让它可维护:

- 共享部分被切成 **PART_A(`CONFIG_*` 声明,9 行)+ PART_B(`_cfg_die` /
  `_cfg_match` / `load_config`,104 行)**,两份**逐字节相同**;唯一 per-skill 的
  是夹在中间的 `CONFIG_EXT_TABLES`(类别名 → 该 skill 的扩展名集合)。
- 有一份校验脚本比对这两段(`/tmp/check_block_identity.py`,PR 里贴了输出),
  漂移立刻可见。改动请全局搜索替换。
- `_cfg_match()` **不是新写的匹配器**:它就是原来 `Sorter._protected()` 的函数体,
  抽出来之后 `_protected()` 变成一行调用。所以配置文件里的模式与 `--keep-dir`
  走的是**同一条**代码路径 —— 不可能出现两套语义,而且既有 `--keep-dir` 测试
  自动变成对新匹配器的测试。

### 三个关键决定

| 决定 | 取值 | 理由 |
|------|------|------|
| 未知键 | **拒绝**(exit 1),不是警告后忽略 | 键名拼错一个字母(`whitelist_dir`)会让整份配置静默失效,而现象是「我明明加进白名单了它还在报」——issue 里点名的最坏失败方式。报错里列出可用键名,照着改就能修 |
| `whitelist_dirs` 合并 | **追加**到 `--keep-dir` | 替换会静默撤掉内置行为;而且这个键的定位就是「`--keep-dir` 的持久化形式」(CONTRIBUTION_IDEAS 的原话:per-library,所以每次运行都要重打) |
| `extension_overrides` 合并 | **逐扩展名改判**:先从所有内置表里摘掉,再放进指定那张 | 整表替换会把 255 个内置扩展名一次清零。摘掉再放是必要的:`categorize()` 是有序判定阶梯,`.log` 只加进 `DOC_EXTS` 而不从 `JUNK_EXTS` 摘掉的话,junk 那一级先命中,覆盖看起来完全没生效 |

### 校验先于写入

`load_config()` 把两个键**全部**校验完才开始改内置集合。半途 `SystemExit` 不会
留下一张只改了一半的表 —— 那比直接报错难查得多。smoke TEST 7 里有一条进程内断言
专门盯这个:一份「`whitelist_dirs` 合法 + `extension_overrides` 非法」的配置退出后,
`CONFIG_WHITELIST` / `CONFIG_INFO` / `DOC_EXTS` 必须分毫未动。

### 「配置到底生效没有」必须可查

加载成功时:stderr 打一行 `ℹ️ 已加载覆盖配置 <path>(whitelist_dirs N 条,
extension_overrides M 条)`,`--json` 里多一个 `stats.config`。两者都**只在真加载了
配置时出现**,所以「没有 `stats.config` 这个键」就是「没找到配置文件」的确定信号。
错误只写 stderr,`--json` 的 stdout 保持可解析(smoke 里对 15 种畸形配置逐条断言
`stdout == ""`)。

### 仓库里刻意不放 `config.json`(连 example 也不放)

`pyproject.toml` 的 `package-data` 是 `"zspace_cli.skills" = ["**/*"]`,而
`zs skill` 会把整个目录 copytree 给用户 —— 所以**任何**放进 `skills/<name>/` 的
`config.json` 都会被装到用户机器上并**立刻生效**,等于给所有用户默认开了一份覆盖。
放一个 `config.example.json` 虽然不生效,但也只是把「哪个文件名才算数」这件事
变模糊。schema 直接写在 SKILL.md 里,用户自己 `touch` 一个。

### 零破坏性怎么证的

`/tmp/identity.sh`(PR 里贴了输出):同一棵 54 文件 / 22 目录的 fixture,用
`git archive main` 取出的**改动前**脚本与改动后脚本各跑一遍,`cmp` 四样东西 ——
`--json` 的 stdout、人类可读报告的 stdout、stderr、`--output` 写出的 JSON 文件。
`generated_at` / `elapsed_sec` / fixture 绝对路径 / venv 路径先归一化。

file-sorter 覆盖 16 组参数组合(默认 / `--strict` / `--split-project-dirs` /
`--keep-dir` / 三种 `--layout` × 两种 `--naming` / `--dest` / `--only-cat` /
`--max-issues` / `--sample`+`--top` / `--max-depth` / `--stale-days` /
`--root` 指向类别目录 / `--help`),photo-organizer 覆盖 6 组(含 `--exif`),
另外 7 个未改动的 scanner 也各跑一遍做回归。**两个解释器各跑一遍**(3.9 与 3.12
都用同一个解释器跑两边,避免把解释器差异误认成改动差异):115 项 `cmp`,0 处不同。

这个 harness 自己也做了负控制(`/tmp/harness_control.sh`):6 处故意破坏
(无条件写 `stats.config`、无配置时也改内置表、白名单全命中、无配置时也打提示行、
`--keep-dir` 失效、`_walk` 的 `wl` 默认值翻转)全部被检出,且未破坏的副本报 0 处
不同(无假阳性)。第一版里有一处破坏被漏掉,查下来是**破坏本身写错了**——插到了
early return 之后,是不可达代码;修正后 6/6 检出。

## 设计原则

1. **只读** — 无 apply/mv/rm 子命令;搬家走 Agent + 用户确认
2. **零依赖** — 纯 stdlib,Python ≥3.9
3. **正向合规** — 定义「文件该在与类别匹配的目录里」,报偏离,不枚举脏模式
4. **默认保守** — 有意的结构(类别目录内、项目目录内)不动,要动得显式加 flag
5. **目标路径算好** — LLM 不用自己拼路径,直接读 `target`;重名已去重
6. **不越界** — 不做内容去重(dedup-finder)、不做版本梳理(work-organizer)、
   不做项目规范(portfolio-organizer)

## 测试

```bash
bash skills/file-sorter/tests/smoke.sh
PY=/usr/bin/python3 bash skills/file-sorter/tests/smoke.sh   # 3.9 兼容验证
```

完全离线;fixture 覆盖:14 类扩展名归类 + junk 优先、合规库**零误报**、
跨类别嵌套默认不动 / `--strict` 才动、疑似项目目录整体保留 /
`--split-project-dirs` 才拆、`--keep-dir` 白名单、重名冲突改名 `__2`、
**12 个同名文件撞名封顶**(溢出项 `target=null` + 建议来源目录名;
这条是回归测试,曾经因为把 tuple 传给 `os.path.basename` 而崩)、
疑似副本指向本尊、junk 两种 action、`--layout` 三种 × `--naming` 两种 ×
`--dest` 的目标路径、`--max-issues` 截断(stats 仍全量、目录级 issue 不被截断)、
`--only-cat` 分批。

**TEST 7(config.json 覆盖层)** 把脚本复制进临时目录来模拟「装好之后」的样子
(因为配置是按 `__file__` 解析的),覆盖:无配置时 `stats` 里没有 `config` 键、
扩展名改判只动写到的那一个、键归一化(`.XYZ` / `LOG`)、junk 被救回成正常类别、
`原盘/*` 与裸 `原盘` 与 `深层/原盘/*` 三种锚定的**互相可区分**的结果、与
`--keep-dir` 的追加合并、`*` 也管不到 root 下的散文件、空对象 `{}`、键缺省、
**被扫描目录里的 `config.json` 被忽略**(三种 cwd 都试)、15 种畸形配置逐条
exit=1 + 只写 stderr + 指名键、校验先于写入、`_cfg_match` 的 15 条纯函数断言。

这 15 条断言做过变异测试(`/tmp/mutate.py`,PR 里贴了输出):33 处故意破坏
(每个校验门、两处归一化、四处合并语义、两处「已加载」信号、五处锚定规则、
两个 per-skill 接入点)**全部被至少一条断言检出**。过程中查出 3 条自己的假断言:
① photo-organizer 的畸形配置清单是 file-sorter 的子集,漏了「空字符串」那条,
所以对应的破坏在那边活了下来;② 「类别值不是字符串」用的 needle 是
`extension_overrides`,而它下面那个「类别名不认识」的兜底检查也会产生含这个词的
报错,两者区分不开;③ 大小写不敏感只测了「目录大写 / 模式小写」一个方向 ——
实现里相对路径总是先 `.lower()`,所以那个方向即使忘了给模式做 `lower` 也照样过。
三条都已修正(补用例 / 换成只有类型检查才会产生的措辞 / 补反方向断言)。

**未覆盖**:真实 SMB 挂载上的性能、真实 CAD 库的扩展名分布
(taxonomy 是按公开格式清单写的,没跑过真实图纸库)、Windows 路径语义。

## 已知 gap

- 只按扩展名分类:不嗅探 magic bytes,不按文件名语义归项目
- 不建目录、不清空目录(搬完的空壳交给 nas-report / 手工 `rmdir`)
- 年份取 mtime,拷贝/下载会刷新 → `*-year` 布局可能把老文件归到今年
- 项目目录判定是启发式(≥3 文件 + ≥2 类),2 个文件的真项目会漏判
- ~~类别表是硬编码集合;专业软件私有格式要改脚本~~ → **已部分解决**(issue #15):
  同目录 `config.json` 的 `extension_overrides` 可以改判扩展名,`whitelist_dirs`
  可以持久化 `--keep-dir`。仍然是硬编码的:类别的**目标目录名**
  (`图纸/` `Documents/`)、目录名别名表 `_DIR_TO_CAT`、项目目录启发式的阈值
- **`config.json` 是 per-安装、不是 per-库**:一份安装一份配置。多个库想用不同
  白名单,目前只能继续用 `--keep-dir` 或装两份 skill。加一个可选的 `--config PATH`
  是自然的后续,但它会引出「两处配置是替换还是叠加」这个新问题,所以没有夹在
  这个 PR 里做
- **taxonomy 未在真实图纸库上验证过**:`.prt`/`.asm`/`.obj`/`.ts`/`.m` 这类
  跨领域扩展名的归属是按主流用法猜的,真实反馈前别说"图纸全认得"

## 参考

- 模式来源:`work-organizer` / `download-cleaner`(同仓库,只读扫描 + LLM 出计划)
- 类别表参照:`nas-report` 的 `CATEGORIES`
- 搭配:`dedup-finder`(两者谁先都对,先去重更省 I/O——见上「与 dedup-finder 的先后」)、
  `nas-report`(先画像再决定跑哪个)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
