# Download Cleaner Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描 NAS 下载目录,按类别**分诊**并给每文件一个建议动作。
rm / mv / 解压由 Agent 在用户确认清理计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):
脚本只读 + 判断交回 LLM + 先预览后执行 + 删除先隔离。

## 目录结构

```
skills/download-cleaner/
├── SKILL.md              # LLM 工作流(触发词 + 场景 + 分诊表)
├── download_cleaner.py   # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh          # 烟雾测试(离线,fixture 端到端)
└── README.md             # 本文件
```

## 命令

```bash
python3 skills/download-cleaner/download_cleaner.py scan \
  --root /Volumes/nas/下载

python3 skills/download-cleaner/download_cleaner.py scan \
  --root /Volumes/nas/下载 --stale-days 180 --json --output /tmp/dl.json
```

## 分诊模型

`categorize(name, ext)` → 类别(纯函数),`advise(category, age, extracted, stale)` →
(问题, 动作)(纯函数)。两函数无副作用,smoke.sh 直接单测。

| action | 含义 |
|--------|------|
| `delete` | 垃圾,可直接删 |
| `delete-confirm` | 种子/未完成/已解压包/老旧安装包,逐项确认后删 |
| `extract-or-review` | 未解压压缩包,确认内容 |
| `move-to-library` | 媒体/文档,分流到对应库 |
| `review` | 杂项或新安装包,人工看 |
| `keep` | 无需处理 |

「可回收空间」= junk + partial + torrent + 已解压 archive + 老旧 installer 的体积。

## 配置覆盖(`config.json`,issue #15)

可选的一层覆盖,放在**脚本自己所在目录**。**没有这个文件时输出与引入它之前
逐字节一致**(见下「零破坏性怎么证的」)。完整 schema、模式锚定规则、出错行为在
`SKILL.md` 的「配置覆盖」一节 —— 那份是给 LLM 读的,这里只记实现决定。

### 为什么代码是复制的,不是共享模块

`zs skill` 的安装方式是**逐目录 `shutil.copytree`**(`cli.py` 的 `skill()`,
`--only a,b` 也一样),所以任何放在 `skills/<name>/` 外面的共享模块都**不会被装到
用户机器上**,一 import 就 ImportError。PR #41 遇到过同一件事,当时的选择就是把工具
函数放进 `photo_organizer.py` 而不是新建框架模块;PR #47 沿用,本 PR 再沿用一次:
**没有** `skills/_shared/`,**没有**任何包级 import。

代价是这段代码现在在**五个** scanner 里各存一份。为了让它可维护:

- 共享文本是从**单一来源**生成的(本 PR 的 `apply_config.py`,PR 里贴了全文),所以
  逐字节相同是**构造出来的**,不是靠三次小心的复制粘贴。
- 有一份独立的比对脚本(`check_block_identity.py`,PR 里贴了输出)把加载器切成 10 个
  区域,逐区域报告「几个变体、哪些文件属于哪个变体、与 #47 的参考文件差哪几行」。
  它自己带负控制:`--self-test` 会往其中一份里改一个字符,然后要求脚本报告漂移 ——
  第一版因为把 `lambda L: has(a) and has(b)` 当成了谓词(那返回的是**函数对象**,
  恒真),所有区域都在自己的第一行就"匹配"了,于是五份文件一律报 1 行 / 144 字节。
  改不出的漂移比漂移本身更危险,所以这个自测留着。
- 改动请全局搜索替换,然后重跑比对脚本。

### 本 skill 的键集:`skip_dirs` + `extension_overrides`

**刻意没有 `whitelist_dirs`。** 那个键在 file-sorter / photo-organizer 里的语义是
「视为合规 / 不要报」,喂的是它们的**合规豁免**机制。download-cleaner 不判合规:它对
看见的每个文件都做分诊,没有「这个目录算合规」这个概念。同一个键名在不同 skill 里
含义不同,用户把配置复制过来就会得到**静默的意外行为** —— 所以只暴露本 skill 真正有
的机制:`SKIP_DIRS`(→ `skip_dirs`)与 9 张扩展名表(→ `extension_overrides`)。

| 决定 | 取值 | 理由 |
|------|------|------|
| 键集 | `skip_dirs` + `extension_overrides`,**没有** `whitelist_dirs` | 见上。照抄 file-sorter 的配置会被**指名拒绝**,报错里列出本 skill 的可用键 |
| `skip_dirs` 合并 | **追加**到内置 `SKIP_DIRS`;命中的目录整棵不进入 | 替换会静默撤掉 18 条内置元数据目录;而「不进入」正是内置 `SKIP_DIRS` 的既有语义 |
| `skip_dirs` 匹配器 | 复用 #47 的 `_cfg_match`(fnmatch、大小写不敏感、锚定在被扫描的根、整段相对路径或任意一级目录名) | 家族里只该有一套模式语义。**代价**:内置 `SKIP_DIRS` 是精确名 + 区分大小写,配置是 fnmatch + 不区分 —— 这个差别在 SKILL.md 里明写了,内置表一个字没动(动了就会改变无配置时的输出) |
| `extension_overrides` 合并 | 逐扩展名改判:先从**所有**内置表里摘掉,再放进指定那张 | 与 #47 完全相同。整表替换会把 100 多个内置扩展名一次清零 |
| `CONFIG_EXT_TABLES` | 10 个类别 = `CATEGORIES` 那 10 个,兜底 `other` → `None` | 直接从 `categorize()` 的判定阶梯与 `CATEGORIES` 常量取,不是凭印象写的 |

### 为了让 `junk` 也能被覆盖,动了一处既有代码

`categorize()` 是**有序判定阶梯**,`junk` 在第一级。而那三个 junk 扩展名原本写成
函数体里的**内联字面量**:

```python
if name in JUNK_NAMES or name.startswith("._") or ext in ("tmp", "temp", "bak"):
    return "junk"
```

内联字面量**没有名字可以摘**,所以 `{"tmp": "doc"}` 会是彻底的静默失效:junk 那一级
先命中,覆盖看起来完全没生效 —— 正是 issue #15 最忌讳的失败方式。因此把它提成
`JUNK_EXTS = {"tmp", "temp", "bak"}`(与 file-sorter / photo-organizer 同名同义),
`categorize()` 改成读这个常量。**行为一个字节都没变**(`ext in` 对 tuple 和 set 的
结果相同),identity harness 也证明了这一点;smoke TEST 7 里有一条断言专门盯住
「junk 可以被救回来」,在提成常量之前那条是红的。

### `ARCHIVE_EXTS` 是共享的:一处覆盖,两处后果

`ARCHIVE_EXTS` 同时喂 ① 类别判定 和 ② 「压缩包是否已解压」的启发式(同目录有没有
去掉扩展名的同名目录)。所以 `{"zip": "other"}` 之后 `pack.zip` 既不再是压缩包,
也**不再参与已解压判定**。这不是 bug,是「一个扩展名只属于一个类别」的必然结果,
但它反直觉,所以 SKILL.md 里单开了一节,smoke 里也钉住了。

### 共享块的边界(比对脚本的输出摘要)

10 个区域里,本 skill 与其他四个 scanner 的关系:

| 区域 | 变体数 | 说明 |
|---|:--:|---|
| R7 `load_config` 文档串 → 未知键检查 | **1** | 24 行 / 1356 字节,**五个 scanner 逐字节相同** |
| R9 `extension_overrides` 校验 | **1** | 20 行 / 1092 字节,**四个有的 scanner 逐字节相同**(dedup-finder 没有这个键)。第一版这里是手抄的,续行缩进差了一个空格,被比对脚本抓出来;现在改成从 file-sorter 里**读出来**,同一性是结构性的 |
| R5 `_cfg_die` / R6 `_cfg_match` | 各 2 | 只差文档串(2 行 / 3 行):`白名单`→`skip_dirs`、示例目录名 `原盘`→`临时`。**函数体逐字节相同** |
| R1 头部注释 / R3 声明 / R8 列表键校验 / R10 收尾 | 各 2~3 | 键名不同导致的必然差异;R8 的 14 行里有 9 行是 `whitelist_dirs`→`skip_dirs` 的改名 |
| R2 `CONFIG_KEYS` / R4 `CONFIG_EXT_TABLES` | per-skill | 本来就该不同 |

**与 backup-auditor 相比:整个加载器 120 行里 117 行逐字节相同,只有 4 行不同 ——
全部是 `CONFIG_EXT_TABLES` 的表体**(3 行 dc 的 10 类 vs 1 行 ba 的 2 类)。
与 #47 的 file-sorter 相比:122 行里 94 行相同。dedup-finder 因为没有
`extension_overrides`,偏差更大(见它自己的 README)。

### 校验先于写入

`load_config()` 把两个键**全部**校验完才开始改内置集合。半途 `SystemExit` 不会留下
一张只改了一半的表 —— 那比直接报错难查得多。smoke TEST 7 里有一条进程内断言专门盯
这个:一份「`skip_dirs` 合法 + 第二个键非法」的配置退出后,`CONFIG_SKIP` /
`CONFIG_INFO` / 内置表必须分毫未动,而且 stderr 不能已经打过「已加载」。

### 「配置到底生效没有」必须可查

加载成功时三处都有信号:

1. stderr 一行 `ℹ️ 已加载覆盖配置 <path>(skip_dirs N 条,<第二个键> M 条)`
2. `--json` 里多一个 `stats.config`(含 `skipped_dirs`)
3. **人类可读报告**里一段 `⚙ 已加载覆盖配置 …`,把生效的键值列出来

三者都**只在真加载了配置时出现**,所以「没有 `stats.config` 这个键」就是「没找到
配置文件」的确定信号。错误只写 stderr,`--json` 的 stdout 保持可解析(smoke 里对每
一种畸形配置逐条断言 `stdout == ""`)。

### 仓库里刻意不放 `config.json`(连 example 也不放)

`pyproject.toml` 的 `package-data` 是 `"zspace_cli.skills" = ["**/*"]`,而 `zs skill`
会把整个目录 copytree 给用户 —— 所以**任何**放进 `skills/<name>/` 的 `config.json`
都会被装到用户机器上并**立刻生效**,等于给所有用户默认开了一份覆盖。放一个
`config.example.json` 虽然不生效,但也只是把「哪个文件名才算数」这件事变模糊。
schema 直接写在 SKILL.md 里,用户自己 `touch` 一个。

### 零破坏性怎么证的

`identity.py`(PR 里贴了输出):同一棵 **99 文件 / 51 目录**的 fixture,用
`git archive origin/main` 取出的**改动前**脚本与改动后脚本各跑一遍,比较**四样东西**
—— `--json` 的 stdout、人类可读报告的 stdout、stderr、`--output` 写出的 JSON 文件。
42 组调用(download-cleaner 12 / backup-auditor 18 / dedup-finder 12)× 4 = **168 项**
比较,**两个解释器各跑一遍**(3.9.25 与 3.12.9,每遍两边都用同一个解释器,免得把解释
器差异误认成改动差异):**各 168 项,0 处不同**。

只归一化**真正不确定**的字段,一共 10 条规则,每条的实际命中次数都印出来(全 0 就说明
归一化器根本没接上):`generated_at`、`elapsed_sec` / `耗时 Ns`、`age_days` 与所有由它
派生的「N 天…」文案(跨日界会 +1)。**没有**归一化 mtime、mtime_date、size、任何计数、
任何路径 —— 两边用的是同一棵 fixture 和同一个相对 `--output` 名,所以路径本来就相等。

fixture 刻意堆满代码路径:CJK 名、shell 危险名(前导 `-`、首尾空格、引号、`$`、反引号、
`*`、`;`、`&`、`|`)、大小写撞名对、AppleDouble `._`、内置 `SKIP_DIRS`/点目录、
download-cleaner 的 10 个类别全部非空 + 已解压/未解压两种压缩包 + 陈旧文件、
backup-auditor 的多版本集/陈旧集/空备份/孤儿/双扩展名/`--keep` 1~5、dedup-finder 的
跨 0~3 层同内容副本 + 同 size 不同内容(不得误报)+ 头哈希相同全量不同(三级指纹的
第二级)+ 低于 `--min-size` 的文件 + `成品/` 里的副本(让 keep 排序可观测)。

**这个 harness 自己也做了负控制**:17 处故意破坏 + 3 处「未破坏的副本必须报 0 处不同」
(查假阳性),其中 4 处还带**可达性探针**(直接 import 改过的模块,断言常量真的变了)。
全部检出,`harness negative controls missed: 0` → `HARNESS IS NOT VACUOUS`。
过程中查出 **4 处自己写错的破坏**,值得记一句:

| 写错的破坏 | 为什么检不出 | 换成 |
|---|---|---|
| 往 `ARCHIVE_EXTS` 里**加** `iso2` | fixture 里没有 `*.iso2`,输出一个字节都没变 | **摘掉** `iso`,`网站备份.iso` 就不再被剥扩展名 |
| 从 `PREFER_KEEP_HINTS` 里摘掉 `相册` | fixture 里没有含 `相册` 的路径 | 摘掉 `成品`,它正是基线的 keep |
| `dir_size` 里 `child_rel = [e.name]` | 没有配置时 `CONFIG_SKIP` 是空的,`_cfg_match` 的卫语句直接返回 False —— 在**无配置模式下原理上不可观测** | 改打内置 `SKIP_DIRS` 的剪枝(那是今天就活着的代码) |
| 要求锚点「恰好匹配 1 次」 | backup-auditor 的 `if CONFIG_INFO:` 有**两处**(scan 与 coverage),于是报成 anchor error | 改成「至少 1 次,全部替换」,并把处数印出来 |
### 变异测试:每一处行为都改坏一次

`mutate.py`(PR 里贴了全文与输出):对配置代码实现的**每一处**行为各改坏一次,跑对应
skill 的 `smoke.sh`,只有 smoke **非零退出**才算抓到。共享块的破坏**同时打进三个
文件**,并且要求**三个都**抓到(块是复制的,每份都得有自己的覆盖)。
**py3.9 与 py3.12 双解释器各跑一遍,两边逐条相同:52 tried / 52 caught /
0 survived / 0 anchor errors。** 跑完之后 `git diff HEAD` 对三个脚本必须是干净的 ——
脚本自己断言这一点(原因见本节末尾那个 SIGTERM 的教训)。

覆盖范围:15 处共享的 `load_config` 校验门与「已加载」信号、5 处 `_cfg_match`
锚定规则、8 处 `extension_overrides`(仅 download-cleaner / backup-auditor)、
11 处 `prefer_keep_hints`(仅 dedup-finder),以及每个 skill 自己的接入点
(download-cleaner 5 处 / backup-auditor 6 处 / dedup-finder 2 处)。

**变异测试反过来抓出了我自己 2 条假断言**(与 #47 查出 3 条同一类问题):

1. **M22(`_cfg_match` 不再锚定,模式被加上 `*` 前缀)第一轮在 backup-auditor 与
   dedup-finder 都活了下来**,只有 download-cleaner 抓到。原因不是实现有问题,是
   那两个 skill 的纯函数清单里只有 `not m(["深层","临时暂存"], ["临时暂存/*"])`
   这条**两层**的断言 —— 模式变成 `*临时暂存/*` 之后它照样匹配不上,区分不出锚定
   与不锚定。真正有判据的是**三层**那条(`深层/临时暂存/子目录` 会被不锚定的模式
   误命中),download-cleaner 的清单里正好有。已给另外两个补齐,现在三个 skill 都抓。
2. **M27(没有配置时 `_print_config` 也打印)第一轮在 dedup-finder 活了下来**:
   它的人类报告断言只在**有配置**时跑过,没有「无配置时报告里不该出现 ⚙ 那一段」
   的负向断言。已补。

另外这次还有一个**工具级**的教训值得记:第一轮跑变异之前,上一次运行被 SIGTERM
打断,把 `if False:` 留在了三个 `.py` 里;下一次运行于是把**那份被改坏的文件**当成
了 pristine 基线。现在 `mutate.py` 开始前会 `git diff HEAD` 检查三个脚本是否干净,
不干净就直接拒绝运行,结束时再检查一次并打印 `post-run tree state`。

## 设计原则

1. **分诊不裁决** — 每文件给类别 + 建议动作,删/留由用户拍板
2. **零依赖** — 纯 stdlib,Python ≥3.9
3. **脚本只读** — 无 apply/rm;删除先 `mv` 隔离再真删
4. **归档不越权** — 只指出媒体该去哪个库,规范化交给对应 skill
5. **可回收估算保守** — 只统计高置信度可删类别

## 测试

```bash
bash skills/download-cleaner/tests/smoke.sh
```

完全离线;TEST 3 单测 categorize/advise 纯函数,TEST 4 fixture 覆盖
各类别 + 已解压/未解压 + 重复下载 + 老旧文件(`touch -t` 造旧 mtime)。

```bash
PY=/usr/bin/python3 bash skills/download-cleaner/tests/smoke.sh   # 3.9 兼容验证
```

**TEST 7(config.json 覆盖层)** 把脚本复制进临时目录来模拟「装好之后」的样子
(因为配置是按 `__file__` 解析的),19 组断言:无配置时 `stats` 里没有 `config` 键、
人类报告里没有 ⚙ 那一段;`extension_overrides` 只动写到的那一个扩展名;**junk 可以
被救回来**(这条在 `JUNK_EXTS` 提成常量之前是红的);键归一化(`.XYZ` / `ISO`);
`ARCHIVE_EXTS` 与「已解压」启发式的耦合;兜底类别 `other` 可写;`临时/*` 与裸 `临时`
与 `深层/临时/*` 三种锚定**互相可区分**(剪掉的目录数分别是 1 / 2 / 1,少掉的文件数
分别是 1 / 3 / 1);大小写不敏感双向;`*` 也管不到 root 下的散文件;空对象 `{}`;
键缺省;**被扫描目录里的 `config.json` 被忽略**(三种 cwd 都试,并断言它被当成一个
普通 `other` 文件扫出来);17 种畸形配置逐条 exit=1 + 只写 stderr + 指名键;报错里
列出本 skill 全部 10 个类别;校验先于写入;`_cfg_match` 的 15 条纯函数断言。

fixture 里有两处必须**实测**而不能假定:大小写撞名对(`CASE.bin` / `case.bin`)与
大小写变体目录(`SAMPLES/` 与 `samples/`)在大小写不敏感的卷上(macOS 默认 /
Windows)会各自合并成一个,所以断言跟着实际存活的数量走;另外 root 下那份用来验证
「被忽略」的 `config.json` 必须设成 800 天未动 —— 本 skill 对「other 且不老」的文件
根本不出 issue,不设旧就没有东西可断。

**未覆盖**:真实 SMB 挂载上的性能;真实下载区的扩展名分布;Windows 路径语义
(本地是 macOS,以 CI 的 windows-latest 日志为准)。

## 已知 gap

- ~~`SKIP_DIRS` 与 9 张扩展名表是硬编码的~~ → **已部分解决**(issue #15):同目录
  `config.json` 的 `skip_dirs` / `extension_overrides`。仍然硬编码的:`JUNK_NAMES`、
  `DUP_MARK_RE`、`ARCHIVE_HINT` 的目标库文案、`MAX_DEPTH`、`--stale-days` 默认值
- **`config.json` 是 per-安装、不是 per-库**:要给不同下载区用不同的 `skip_dirs`,
  目前只能装两份 skill。加 `--config PATH` 是自然的后续,但它会引出「两处配置是替换
  还是叠加」这个新问题,所以没有夹在这个 PR 里做
- ~~**AppleDouble 文件根本进不了统计**~~ → **已修**(#58):`_check_file` 原先写的是
  `name.startswith(".") and name not in JUNK_NAMES` → return,于是 `.DS_Store`(在
  `JUNK_NAMES` 里)能过、`._xxx`(不在)被提前 return,永远走不到 `categorize()` 里那条
  `startswith("._")` → junk。**那条规则经 CLI 是死代码**,只有直接调纯函数才看得到,
  所以纯函数测试一直是绿的、掩盖了这个问题。现在与 file-sorter 同序:先算
  `is_junk_name = name in JUNK_NAMES or name.startswith("._")`,再对「点开头且非 junk」
  早退,并且 junk 名不参与扩展名推断(`._pack.zip` 不该拿到 `zip`)。
  **这会改变既有输出**:`stats.files`、`by_category.junk`、`reclaimable_bytes` 都会
  把 `._*` 算进去 —— 这是修复的目的,不是回归。SKILL.md 那张用户可见的表本来就承诺
  「垃圾 = `.DS_Store`、`._*`、`.tmp` → delete」,修复让它成真(此前表格与实现矛盾)。
  smoke.sh 里原先钉住错误行为的那条 `assert "._movie.ass" not in bp` 已反转,并补了
  负控制:`.hidden.torrent` / `.ignored.part` 两个**普通** dotfile 必须继续被忽略
  (扩展名刻意选会产生 issue 的,否则「把早退整个删掉」也能通过 —— 第一版负控制用的
  是 `.hidden_config`,它会被判成 other 且没有 problems、压根不出现在 issues 里,
  实测无效)
- 「已解压」靠同名目录启发式,不比对压缩包内容
- 不解析压缩包内文件列表
- 重复下载只按名字识别,内容级重复走 dedup-finder
- 归档目标是类别建议,实际库路径需用户指定

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 下游归档:media-naming / music-organizer / photo-organizer / work-organizer
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
