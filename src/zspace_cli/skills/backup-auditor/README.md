# Backup Auditor Skill(开发者文档)

## 这是什么

Agent skill:**只读**审计 NAS 备份目录的健康度。两个模式:
- `scan` — 备份集版本分析(陈旧 / 单版本 / 空备份 / 可轮转旧版本)
- `coverage` — 源数据目录 ↔ 备份目录 覆盖核对(缺失 / 陈旧 / 孤儿备份)

轮转/删除/补备份由 Agent 在用户确认计划后执行(本 skill 不做实际备份)。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能审计。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):脚本只读 + 判断交回 LLM + 先预览后执行。

## 目录结构

```
skills/backup-auditor/
├── SKILL.md            # LLM 工作流(触发词 + 场景 + 保留策略)
├── backup_auditor.py   # CLI:scan / coverage(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh        # 烟雾测试(离线,fixture 端到端,含两模式)
└── README.md           # 本文件
```

## 命令

```bash
python3 skills/backup-auditor/backup_auditor.py scan \
  --root /Volumes/nas/备份 --stale-days 35 --keep 3

python3 skills/backup-auditor/backup_auditor.py coverage \
  --source /Volumes/nas/data --backup /Volumes/nas/备份
```

## 核心逻辑

| 函数 | 作用 |
|------|------|
| `strip_archive_ext` | 剥离 `.tar.gz`/`.zip`/`.sparsebundle` 等(含双扩展名) |
| `parse_backup_name` | 名字 → `(base_key, date, version)`;剥日期/版本后聚成备份集 |
| `dir_size` | 递归求目录大小(20000 文件上限防卡死) |
| `Auditor.scan` | 备份集分析:陈旧 / 单版本 / 空 / 可轮转 |
| `Auditor.coverage` | 源↔备份归一名双向包含匹配,查缺失/陈旧/孤儿 |

`parse_backup_name` / `strip_archive_ext` 是纯函数,smoke.sh TEST 3 直接单测。

## 检出项与 action

| mode | 问题 | action |
|------|------|--------|
| scan | 备份陈旧(最新超阈值) | `review-set` |
| scan | 单版本备份(无冗余) | `review-set` |
| scan | 空备份(0 字节/0 文件) | `investigate` |
| scan | 可轮转旧版本(超 keep) | `rotate-out` |
| coverage | 关键目录无备份 | `add-backup` |
| coverage | 有备份但陈旧 | `refresh-backup` |
| coverage | 孤儿备份(源已删/改名) | `review-orphan` |

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
「视为合规 / 不要报」。backup-auditor 不判合规,它审的是「备份新不新鲜、有没有冗余、
关键目录漏没漏」;`--keep N` 是**版本保留数**,不是目录白名单(#47 的表格里这一行
写得很清楚)。给本 skill 一个 `whitelist_dirs` 只会让用户以为它能豁免什么。

| 决定 | 取值 | 理由 |
|------|------|------|
| 键集 | `skip_dirs` + `extension_overrides`,**没有** `whitelist_dirs` | 见上。照抄 file-sorter 的 `whitelist_dirs`、照抄 dedup-finder 的 `prefer_keep_hints`,都会被**指名拒绝** |
| `CONFIG_EXT_TABLES` | **只有两个类别**:`archive` → `ARCHIVE_EXTS`,兜底 `non_archive` → `None` | 本 skill 里 `*_EXTS` 常量**只有一张**(`grep -oE '^[A-Z][A-Z_0-9]{2,}' ` 的结果就在 PR 里)。没有第二张表,就不编一个出来 |
| `extension_overrides` 的意义 | 改的是**备份集怎么聚合**,不是「文件该去哪」 | `ARCHIVE_EXTS` 只被 `strip_archive_ext()` 用,而它决定 `base_key`。所以 `{"mybak": "archive"}` 能把 `数据.mybak` / `数据_v2.mybak` 从两个「单版本集」合成一个「双版本集」,`--keep 1` 于是正确轮转出旧的那份 —— 这是本 skill 里最有用的一个覆盖 |
| `skip_dirs` 作用层 | **三层**:备份项收集、coverage 的源目录收集、`dir_size` 递归求体积 | 内置 `SKIP_DIRS` 本来就在这三层都生效;配置项要是只在前两层生效,同一个键在**一个 skill 内部**就有两种语义 |
| `dir_size` 签名 | 新增可选参数 `rel_parts` | 配置模式要锚定在**备份根**,而 `dir_size` 原本拿不到相对层级。默认值 `None` → 老调用方式行为不变(smoke 里进程内直接断言了这一点) |
| 刻意**不可配置** | `DATE_RE` / `TIME_SUFFIX_RE` / `VERSION_RE` / `BACKUP_HINT_RE`、四个硬编码双扩展名、`dir_size` 的 20000 上限、`--keep`/`--stale-days` 默认值 | 这些决定「什么算一个备份集」「什么算陈旧」,改它们等于换掉审计的定义,是比扩展名归类大得多的设计问题 |

### 两个必须写进文档的副作用

1. **双扩展名在 `ARCHIVE_EXTS` 之前匹配。**`strip_archive_ext()` 先试
   `(".tar.gz", ".tar.bz2", ".tar.xz", ".sparsebundle")` 这四个硬编码字面量,命中就
   直接返回,**根本不看 `ARCHIVE_EXTS`**。所以 `{"gz": "non_archive"}` 不会让
   `照片备份.tar.gz` 停止被剥离。这是既有结构,配置层不去动它 —— 但用户一定会踩,
   所以 SKILL.md 的踩坑表里有一行。
2. **`skipped_dirs` 只数审计边界上的两层。**`dir_size` 内部剪掉的子目录不计数(它是
   一个模块级函数,没有实例可以累加),但确确实实会让 `size` / `file_count` 变小。
   smoke 里是按**字节**核对的(`total_size_bytes` 精确少掉那个文件的大小),不是只看
   计数变没变。

### coverage 模式下的连锁反应

`skip_dirs` 掉一个**源**目录,它对应的备份集就失去匹配对象,于是从「已覆盖」变成
「孤儿备份」。这是正确行为(你说了不用管它),但汇报时必须一起说,否则用户会以为
凭空多出一个孤儿。smoke TEST 7 里这条是显式断言的(`covered -1`、`orphan +1`、
`backup_sets` 不变)。

同一节还钉住了一条容易搞错的语义:**模式是 fnmatch,不是子串**。
`skip_dirs: ["照片"]` **不会**顺手剪掉 `照片备份_2024-01-01/` —— 在备份目录里这一点
特别容易踩,因为备份项的名字通常就是「源目录名 + 备份 + 日期」。

### 共享块的边界(比对脚本的输出摘要)

与 download-cleaner 相比:**整个加载器 118 行里 117 行逐字节相同,只有 4 行不同 ——
全部是 `CONFIG_EXT_TABLES` 的表体**。`load_config` 的收尾注释也是共用的(故意不写
`categorize()` 这个函数名,因为本 skill 里那条判定阶梯叫 `strip_archive_ext()`)。

与 #47 的两个 scanner 相比,偏差只落在:头部注释 1 行、`CONFIG_KEYS` 1 行、
`CONFIG_SKIP` 声明 1 行、`_cfg_die`/`_cfg_match` 的文档串 2+3 行、`skip_dirs`
校验块 9 行(纯改名)、收尾 5 行。`load_config` 的前半段(24 行)与
`extension_overrides` 的校验(20 行)**逐字节相同**。

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

1. **审计不碰数据** — 全程只读;轮转/删除交 Agent + 用户确认
2. **零依赖** — 纯 stdlib,Python ≥3.9
3. **陈旧=修复优先** — 陈旧/空备份是「备份可能已失效」信号,优先级高于清理
4. **轮转先归档** — 旧备份 `mv` 到 `_rotated/`,删前确认最新版本完好
5. **保留策略可配** — `--keep` / `--stale-days` 按备份频率调

## 测试

```bash
bash skills/backup-auditor/tests/smoke.sh
```

完全离线;TEST 3 单测名字解析,TEST 4 fixture 覆盖 scan(多版本轮转/陈旧/空备份/单版本)
+ coverage(缺失/孤儿),用 `touch -t` 造旧 mtime 验证陈旧检测。

```bash
PY=/usr/bin/python3 bash skills/backup-auditor/tests/smoke.sh   # 3.9 兼容验证
```

**TEST 7(config.json 覆盖层)** 把脚本复制进临时目录来模拟「装好之后」的样子,
18 组断言,**scan 与 coverage 两个子命令都覆盖**:无配置时两边的 `stats` 都没有
`config` 键、人类报告都没有 ⚙ 那一段;`{"mybak": "archive"}` 把两个「单版本集」合成
一个「双版本集」并且 `--keep 1` 时正确轮转出旧的那份;`{"iso": "non_archive"}` 反过来
让 `网站备份.iso` 不再被剥扩展名;键归一化;`skip_dirs` 在三种锚定下的**体积差是按
字节核对**的(`临时暂存` 剪掉顶层项 + `dir_size` 里的深层同名目录;`临时暂存/*` 只剪
子目录,顶层项还在;`深层/临时暂存/*` 与 `深层/临时暂存` 可区分);coverage 里同一个
`skip_dirs` 同时作用于 `--source` 与 `--backup`(`skipped_dirs` = 1+1);跳过源目录
会让对应备份集**变孤儿**、而 `照片` **不会**误剪 `照片备份_*`;空对象 `{}`;
被审计目录里的 `config.json` 被忽略(三种 cwd,并断言它被当成第 23 个备份项);
18 种畸形配置 × **两个子命令**逐条 exit=1 + 只写 stderr + 指名键;`dir_size` 的
`rel_parts` 锚定(含「省略 rel_parts 时旧行为不变」);`_cfg_match` 的 16 条纯函数断言。

基线数字是从 fixture 结构**推出来**的(22 个顶层备份项 = 从文件系统独立数出来、
16 个集、13 个陈旧、`total_size_bytes` 由各 payload 长度求和),不是从上一次运行抄的,
所以断言真的会红。

fixture 里有两个坑值得记:**目录 mtime 必须在里面所有文件写完之后才设** —— 往目录里
写文件会把它的 mtime 刷成现在,先设后写等于没设,第一版就这么把整个陈旧判定路径丢掉
了(coverage 的 `stale` 一直是 0 却没人发现);**两个大小写变体目录必须用不同的名字**
(`SAMPLES` 与 `lowcase`),因为同名不同写法在大小写不敏感的卷上会合并成一个目录,那样
就只剩一个方向的大小写折叠可测。

**未覆盖**:真实 SMB 挂载上的性能;真实备份集的命名分布(`base_key` 启发式是按常见
命名写的);Windows 路径语义;`dir_size` 的 20000 文件上限触顶时的行为。

## 已知 gap

- ~~`SKIP_DIRS` 与 `ARCHIVE_EXTS` 是硬编码的,私有归档格式要改脚本~~ →
  **已部分解决**(issue #15):同目录 `config.json` 的 `skip_dirs` /
  `extension_overrides`。仍然硬编码的:`.tar.gz`/`.tar.bz2`/`.tar.xz`/
  `.sparsebundle` 四个双扩展名(而且它们在 `ARCHIVE_EXTS` **之前**匹配,所以
  `{"gz": "non_archive"}` 不会让 `.tar.gz` 停止被剥离)、`DATE_RE`/`TIME_SUFFIX_RE`/
  `VERSION_RE`/`BACKUP_HINT_RE` 四条识别正则、`dir_size` 的 20000 文件上限、
  `--keep` 与 `--stale-days` 的默认值
- **`stats.config.skipped_dirs` 只数审计边界上的两层**(备份项、coverage 的源顶层
  目录);`dir_size` 内部剪掉的子目录不计数,虽然它确实会让 `size`/`file_count` 变小。
  要让第三层也计数,得给 `dir_size` 加一个出参或者把计数器做成模块级的,两者都比
  现在这个签名侵入性大
- **`config.json` 是 per-安装、不是 per-库**:要审计不同备份根时用不同的 `skip_dirs`,
  目前只能装两份 skill。加 `--config PATH` 是自然的后续,但它会引出「两处配置是替换
  还是叠加」这个新问题
- 不校验备份**可恢复性**(不试解压/挂载),空备份只看 size/file_count
- 不做增量备份链式完整性检查
- 备份集聚合靠命名启发式,极不规范命名可能误聚/漏聚
- coverage 匹配是名字双向包含,不理解备份工具的实际映射
- 不识别厂商专有格式(群晖 HBB / 威联通 HBS)内部版本

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 3-2-1 备份原则、ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
