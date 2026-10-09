# Dedup Finder Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描任意目录(本地挂载路径)做**内容级精确去重**。
输出重复组 + 每组保留建议;rm / mv 由 Agent 在用户确认删除计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

与 `file-organizer`(zspace_skill 仓库,size+ext 弱指纹、误报率高)的核心区别:
**三级指纹(size → 头部 64KB → 全量 sha1)零误报** — 只有字节完全相同才判重复。

## 目录结构

```
skills/dedup-finder/
├── SKILL.md          # LLM 工作流(触发词 + 场景 + 保留优先级)
├── dedup_finder.py   # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh      # 烟雾测试(离线,fixture 端到端)
└── README.md         # 本文件
```

## 命令

```bash
python3 skills/dedup-finder/dedup_finder.py scan \
  --root /Volumes/nas/data

# 跨目录找重复 + 只看大文件
python3 skills/dedup-finder/dedup_finder.py scan \
  --root /Volumes/nas/照片 --root /Volumes/nas/下载 \
  --min-size 1024 --json --output /tmp/dups.json
```

## 三级指纹

| 阶段 | 成本 | 作用 |
|------|------|------|
| 1. size 分组 | 免费(stat) | 砍掉绝大多数不可能重复的 |
| 2. 头部 64KB sha1 | 低(每文件读 64KB) | 砍掉同 size 但内容不同的 |
| 3. 全量 sha1 | 高(读完整文件) | 只对头哈希相同的极少数做,零误报 |

`stats` 里 `hashed_head` / `hashed_full` / `bytes_read` 可以看到漏斗效果。

## 保留优先级(keep_rank)

组内排序,越小越该保留:
1. 路径含 `成品/源文件/原始/master/original/import/相册`
2. 路径更浅
3. mtime 更旧
4. 名字更短(避开 `xxx 副本`、`xxx (1)`)

启发式仅供 LLM 初排,最终由用户拍板;SKILL.md 约束「先 mv 隔离,再真删」。

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

### 本 skill 的键集:`skip_dirs` + `prefer_keep_hints`

**两个键都刻意不叫别的名字。**

- **没有 `whitelist_dirs`**:那个键在 file-sorter / photo-organizer 里的语义是「视为
  合规 / 不要报」。dedup-finder 不判合规,它比的是内容 hash,「这个目录算合规」在这里
  没有意义。
- **没有 `extension_overrides`**:本 skill **根本不看扩展名** —— 没有任何 `*_EXTS`
  常量(`grep -oE '^[A-Z][A-Z_0-9]{2,}'` 的结果只有 `MAX_DEPTH` / `HEAD_SIZE` /
  `CHUNK` / `SKIP_DIRS` / `PREFER_KEEP_HINTS`)。加一个语义为空的键只会让用户以为它
  能做什么,而这正是 issue #15 最忌讳的那类静默失效。

| 决定 | 取值 | 理由 |
|------|------|------|
| 键集 | `skip_dirs` + `prefer_keep_hints` | 只暴露本 skill 真正有的两个机制:`SKIP_DIRS` 与 `PREFER_KEEP_HINTS` |
| `prefer_keep_hints` 合并 | **追加**到内置 7 条,一条都不删;与内置重复的条目不会重复出现 | 与 #47 对内置 `*_EXTS` 的处置一致。最坏情况是「多加了一个不该优先的提示词」,不是「所有内置保护都没了」 |
| 匹配方式 | **子串**匹配小写化后的相对路径,与内置**同一条**代码 | 见下「为什么没升级成 glob」 |
| 归一化 | `strip()` + `lower()` | `keep_rank()` 比的就是 `item["path"].lower()`,而内置 7 条本来就全是小写/中文,所以这不改变内置语义 |
| `PREFER_KEEP_HINTS` 类型 | tuple → **list** | 要能**原地追加**。用 list 而不是重新绑定全局名,可以少一处 `global` 声明,而且 `keep_rank()` **一行都不用改**。`any(h in path for h in …)` 对 tuple 和 list 结果相同 |
| 刻意**不可配置** | `HEAD_SIZE`(64KB)、`CHUNK`(1MB)、`MAX_DEPTH`、`--min-size` 默认值 | 它们改的是「什么叫重复」的判定精度,是比目录/提示词大得多的设计问题 |

### 安全分析:这是全家族唯一会**改变删除计划**的键

`prefer_keep_hints` 动的不是「报不报」,而是**「一组重复里建议保留哪一份」**。
`keep_rank()` 的第一级就是它:

```python
prefer = 0 if any(h in path for h in PREFER_KEEP_HINTS) else 1
return (prefer, depth, mtime, len(name))
```

`prefer` 是**排序的第一关键字**,所以一条提示词可以把深层的新副本顶到 `keep`,把浅层
的旧正主推进 `drop` —— 后面照着计划 `rm` 的人就删错了东西。实测:

```
浅层/A.bin              prefer=1  depth=1  mtime 最旧
工作/终稿/深层/A.bin     prefer=1  depth=3  mtime 最新

无配置                          keep = 浅层/A.bin            drop = 工作/终稿/深层/A.bin
{"prefer_keep_hints":["终稿"]}   keep = 工作/终稿/深层/A.bin   drop = 浅层/A.bin       ← 反了
{"prefer_keep_hints":["没有这个词"]} keep = 浅层/A.bin         drop = 工作/终稿/深层/A.bin  ← 与无配置逐条相同
```

**风险与对策一一对应:**

| 风险 | 对策 | 在哪验的 |
|---|---|---|
| 配错了但看不出来 | 生效的提示词印在**三个**地方:stderr 一行、`stats.config`(既有本次追加的 `prefer_keep_hints`,也有**实际生效全集** `prefer_keep_hints_effective` = 内置 + 追加)、以及**人类可读报告**里的一段 | smoke TEST 7 第 7 组:断言报告里含配置路径、追加项、以及内置 7 条 + 追加项的完整列表 |
| 追加变成替换,内置保护全丢 | 只 `extend`,不重新绑定 | smoke:加了 `终稿` 之后,内置 `源文件` 仍然赢下 B 组 |
| 静默失效(拼错了但没报错) | 未知键/类型错/空字符串一律 exit 1;而「合法但匹配不到任何路径」**不报错**,只是排序不变,且配置仍然出现在 `stats.config` 里 —— 所以「没生效」和「生效了但没命中」是可区分的 | smoke:第三行那个负控制,三组排序与基线**逐条**相同 |
| 子串被偷偷升级成 glob | 没有升级,而且 smoke 里**钉住了**:`终*` 必须命中不了 `终稿/` | smoke TEST 7 第 6 组 |
| 照着计划直接删 | SKILL.md 原有的「先 `mv` 到 `_dup_quarantine/`,人工核对一段时间再真删」约束不变,故障排查表里加了一条「配了这个键就必须逐组人工过 `keep`」 | 文档 |

**为什么没顺手把子串升级成 glob?** 因为 `PREFER_KEEP_HINTS` 是内置与配置**共用**的
同一个列表,`any(h in path …)` 也是同一条判定。换成 `fnmatch` 会**连内置那 7 条的
语义一起改掉**:`原始` 今天匹配路径里任何位置出现的「原始」两个字,升级之后会变成只
匹配整段路径或以 `原始/` 开头的目录。那是一次静默的行为变更,而且改的是**没有配置文件
时也会走到的代码** —— 与本机制「没有配置就逐字节一致」的前提直接冲突。如果确实想要
glob(比如「只在第一层目录名上匹配」),应该单独开 issue:得先决定内置那 7 条要不要
一起迁移、以及迁移期间老配置怎么兼容。

**路径分隔符已统一(不再是坑)**:相对路径一律用 `/`,Windows 上也是,与其余 8 个
scanner 一致。所以 `工作/终稿` 这种**含分隔符**的提示词三平台都有效,不必退化成
单级目录名。早先这里用字符串切片取相对路径(`entry.path` 来自 `os.scandir`),
Windows 上会得到 `\`,后果见 SKILL.md「路径分隔符」那段。

### 共享块的边界(比对脚本的输出摘要)

本 skill 是三个里偏差最大的一个,原因只有一个:**它没有 `extension_overrides`**。

- #47 定义的 PART_A 在这里**根本提取不出来**(它的终止锚点是 `CONFIG_EXT_TABLES`,
  而本 skill 没有这个常量)—— 这本身就是「键集不同」最直接的证据。
- #47 定义的 PART_B:104 行 → **93 行**,少了 `extension_overrides` 的 20 行校验,
  多了 `prefer_keep_hints` 的 9 行校验与 5 行注释。
- 与另两个新 scanner 相比:`load_config` 的**前 24 行逐字节相同**(文档串 → 未知键
  检查),`skip_dirs` 的 14 行校验块**逐字节相同**,`_cfg_die` / `_cfg_match`
  **逐字节相同**,头部注释与 `CONFIG_SKIP`/`CONFIG_INFO` 声明**逐字节相同**。
  不同的只有:`CONFIG_KEYS` 1 行、以及收尾的 21 行(校验 + 合并 + `CONFIG_INFO` +
  提示行)。整个加载器 120 行里 82 行相同。

也就是说:**偏差被关在「第二个键是什么」这一个区域里**,而不是散落在整个加载器。

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

1. **精确优先** — 三级指纹保证零误报,宁可多读 IO 不误删
2. **零依赖** — 纯 stdlib(hashlib),Python ≥3.9
3. **脚本只读** — 无 apply/rm 子命令;删除走 Agent + 隔离目录
4. **漏斗降 IO** — size→head→full,SMB 上尽量少读
5. **多 root** — 支持跨目录找重复(照片库 vs 下载区)

## 测试

```bash
bash skills/dedup-finder/tests/smoke.sh
```

完全离线;fixture 覆盖:真重复检出、同 size 不同内容不误报、
head 相同 full 不同(三级指纹价值)、小文件跳过、keep 优先级。

```bash
PY=/usr/bin/python3 bash skills/dedup-finder/tests/smoke.sh   # 3.9 兼容验证
```

**TEST 7(config.json 覆盖层)** 把脚本复制进临时目录来模拟「装好之后」的样子,
20 组断言,整节是围绕上面那个安全分析建的:

- **反转与负控制**:A 组的 `keep` 从 `浅层/A.bin` 翻成 `工作/终稿/深层/A.bin`,
  **整条 keep+drop 顺序**逐条断言;而 `prefer_keep_hints: ["没有这个词"]` 时三组
  顺序与基线**逐条相同**(且配置仍然出现在 `stats.config` 里,所以「没生效」与
  「生效了但没命中」可区分)。
- **追加而非替换**:加了 `终稿` 之后,内置的 `源文件` 仍然赢下 B 组。
- **C 组的升位**:命中那份从第 7 名升到第 2 名,而内置 `成品` 仍是第一 —— 说明改的
  是排序,不是「谁在就谁赢」。
- **子串不是 glob**:`终*` 必须命中不了 `终稿/`。
- **可见性**:stderr 一行、`stats.config` 的 `prefer_keep_hints` 与
  `prefer_keep_hints_effective`(= 内置 7 条 + 追加)、人类报告里的生效全集,
  三处都断言;并且**无配置时人类报告一个字都不印**。
- `skip_dirs` 的三种锚定(剪掉的目录数 1 / 1 / 1,但影响的**组数**分别是 0 / 0 / −1,
  这才能区分)、大小写双向、`*` 只扫一层(`dirs_visited == 1`)、多 `--root` 各自锚定。
- 被扫描目录里的 `config.json` 被忽略(三种 cwd)。**这一条的 fixture 是刻意设计的**:
  它必须 >1KB(否则被 `--min-size` 跳过,连「被当成数据扫到」都证明不了),而且内容
  必须是一个**真会反转 B 组**的提示词 —— 否则「被忽略了」与「根本没被读成数据」
  两种情况看不出区别。
- 15 种畸形配置逐条 exit=1 + 只写 stderr + 指名键,其中一条是**反过来的**断言:
  `prefer_keep_hints: ["/终稿/"]` **不该**报错(它是路径子串,不是路径),用来钉住
  它与 `skip_dirs` 的规则差异。
- 校验先于写入(`PREFER_KEEP_HINTS` 分毫未动),以及「与内置重复的条目不会重复出现」。

基线的三组顺序是按 `(prefer, depth, mtime, len(name))` **手推**出来写成字面量的,
所以 `keep_rank` 一改就会红。重复组数由实测的大小写撞名存活数推出(大小写不敏感的
卷上 F 组只有一份、不成组)。

fixture 里有三个坑值得记:两个大小写变体目录必须**用不同的名字**且**各有自己的
payload**(第一版复用了 A 组的 payload,两组直接并成一组,E 组的成员数还跟着文件
系统的大小写敏感性变);大小写变体目录要用三个成员,才能让「大写模式打小写目录」和
「小写模式打大写目录」**两个方向都真的翻位**(只测一个方向是测不出来的 —— 实现里
路径总是先 `.lower()`,那个方向即使忘了给模式做 `lower` 也照样过,这正是 #47 的
M21 踩过的坑)。

**未覆盖**:真实 SMB 挂载上的性能(全量 hash 是 IO 密集);百万文件级;硬链接;
Windows 路径语义(含分隔符的提示词在 Windows 上失效这一点**只有推理**,本地是 macOS)。

## 已知 gap

- ~~`SKIP_DIRS` 与 `PREFER_KEEP_HINTS` 是硬编码的~~ → **已部分解决**(issue #15):
  同目录 `config.json` 的 `skip_dirs` / `prefer_keep_hints`,两者都是**追加**。
  仍然硬编码的:`HEAD_SIZE`(64KB)、`CHUNK`(1MB)、`MAX_DEPTH`、`--min-size`
  默认值 —— 它们改的是「什么叫重复」的判定精度,是比目录/提示词大得多的设计问题
- **`prefer_keep_hints` 是子串匹配,不是 glob**:刻意的。`PREFER_KEEP_HINTS` 是内置
  与配置**共用**的同一个列表,判定也是同一条 `h in path`;换成 `fnmatch` 会连内置那
  7 条的语义一起改掉,而那是**没有配置文件时也会走到的代码**。要做 glob 得先决定内置
  提示词怎么迁移、迁移期间老配置怎么兼容 —— 建议单独开 issue 讨论
- ~~**含 `/` 的提示词不跨平台**~~ → **已解决**:相对路径在 scanner 侧统一归一成
  `/`(`entry.path[len(root)+1:].replace(os.sep, "/")`),所以 `工作/终稿` 三平台
  都命中。原先打算在 `keep_rank` 里归一,但那样只修了提示词匹配、修不了同一处的
  另一个后果:`keep_rank` 的深度这一级是 `item["path"].count("/")`,在反斜杠路径上
  恒为 0,「浅路径优先」在整个 Windows 平台上失效,排序退化成只比 mtime 与名字长度,
  于是会建议**删掉浅层那份、保留深层那份** —— 与本 skill 自己的文档和汇报文字相反。
  在 scanner 侧归一同时修好两者。POSIX 上 `os.sep` 本就是 `/`,该 replace 是空操作,
  输出逐字节不变(已用 23 个调用 × 2 个解释器复验);**Windows 上的输出因此会变**
  (`\` → `/`),这是有意的修复,不是回归
- **`keep.reason` 的文案不会跟着配置变**:它仍然写「成品/源目录优先→浅路径→旧文件
  →短名」。生效的提示词全集在 `stats.config.prefer_keep_hints_effective` 与人类报告
  里,但 JSON 的每条 issue 上没有 —— 要让 `reason` 也动态生成,就得在没有配置时保持
  原文案逐字节不变,做法上有点绕,留给后续
- **`config.json` 是 per-安装、不是 per-库**:要给不同的库用不同的保留提示词,目前
  只能装两份 skill。加 `--config PATH` 是自然的后续,但它会引出「两处配置是替换还是
  叠加」这个新问题
- 不做相似去重(感知哈希)— 连拍/转码/改尺寸抓不到
- 硬链接按普通文件比对(SMB 上少见);软链接跳过
- 全量 hash 是 IO 密集,百万文件级建议分区多次扫

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 弱指纹对照组:`file-organizer`(同仓库)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
