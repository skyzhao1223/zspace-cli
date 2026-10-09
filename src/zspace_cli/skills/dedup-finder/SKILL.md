---
name: dedup-finder
description: Use when 用户想找 NAS 上的重复文件 — "哪些文件重复了"、"帮我找重复照片/视频/文档"、"磁盘被重复文件占满了"、"下载了好几次同一个东西"、"精确去重不要误删"。只读扫描用三级指纹(size → 头部64KB → 全量 sha1)做内容级精确去重,零误报;每组给出「保留哪个/删哪个」的启发式建议,LLM 出删除计划,用户确认后由 Agent 执行(优先 mv 到隔离目录而非直接 rm)。
  触发词:重复文件、去重、找重复、精确去重、内容去重、哪些文件一样、重复照片、重复视频、重复文档、磁盘浪费、空间回收、dedup、duplicate files、find duplicates、remove duplicates。
  不适用:文件名相似但内容不同(本 skill 只认内容 hash);照片按日期整理(走 photo-organizer);工作文件版本混乱(走 work-organizer);混合目录按类型分类归档(走 file-sorter — 两者谁先跑都能检出同样的重复,先去重只是省掉白搬的字节)——那些是「组织」问题不是「内容重复」问题。
---

# Dedup Finder — 内容级精确去重

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS),纯 stdlib 零依赖。

与其他「弱指纹」去重不同,本 skill 用**三级指纹做内容级精确去重**:

```
size 分组  →  头部 64KB sha1  →  全量 sha1
(免费)        (只读开头)         (零误报)
```

只有三级全部相同才判为重复 — **不会误删内容不同的文件**。
**写操作不在脚本里** — 扫描出重复组后,LLM 生成删除计划(每组保留哪个),
经用户确认后由 Agent 执行(优先 `mv` 到隔离目录,人工核对后再真删)。

## Prerequisites

```bash
open smb://<NAS_IP>/data        # 挂载要扫描的共享
python3 --version               # ≥3.9,无第三方依赖
```

## 快速验证

```bash
python3 dedup_finder.py scan --root /Volumes/nas/data --max-files 2000
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 三级指纹去重扫描(只读);`--root` 可重复传多个跨目录找重复 |
| `scan --root PATH --json --output F` | JSON 输出 |
| `--min-size N` | 忽略小于 N KB 的文件(默认 1) |
| `--max-files N` | 最多比对文件数(0=不限,大库先摸底) |
| `--max-depth N` / `--top N` | 递归深度 / 显示前 N 组 |
| 同目录 `config.json` | 可选覆盖层:`skip_dirs` + `prefer_keep_hints`(见下「配置覆盖」) |

```bash
# 全盘找重复,只看 ≥1MB 的文件
python3 dedup_finder.py scan --root /Volumes/nas/data --min-size 1024

# 跨两个目录找重复(照片库 vs 下载区)
python3 dedup_finder.py scan --root /Volumes/nas/照片 --root /Volumes/nas/下载
```

## 配置覆盖(`config.json`,可选)

「哪一份是正主」是**每个库自己的事**:有人把成品放 `终稿/`,有人放 `交付/`,有人
放 `Final/`。内置的 7 条提示词(`成品/源文件/原始/master/original/import/相册`)
覆盖不到所有习惯,而改脚本里的 `PREFER_KEEP_HINTS` 下次 `pip install -U` 就被冲掉。

**没有这个文件时,本 skill 的输出与引入该机制之前逐字节一致** —— 不多一个 JSON
字段、删除计划里一份都不换。

### 本 skill 接受哪些键

只有两个。**顶层出现第三个键 → 直接报错退出。**

| 键 | 类型 | 作用 |
|----|------|------|
| `skip_dirs` | `string[]` | **追加**到内置 `SKIP_DIRS`;命中的目录**整棵不进入**,不参与任何重复组 |
| `prefer_keep_hints` | `string[]` | **追加**到内置 `PREFER_KEEP_HINTS`;命中的路径在重复组里**优先被建议保留** |

> **刻意没有 `whitelist_dirs`,也没有 `extension_overrides`。**
>
> - `whitelist_dirs` 在 file-sorter / photo-organizer 里的语义是「**视为合规 /
>   不要报**」,喂的是那两个 skill 的**合规豁免**机制。本 skill 不判合规 —— 它比
>   的是内容 hash,「这个目录算合规」在这里没有意义。同一个键名在不同 skill 里
>   含义不同,用户把配置从一个 skill 复制到另一个就会得到**静默的意外行为**,所以
>   这里只暴露本 skill 真正有的机制,并照它本来的样子命名。
> - `extension_overrides` 也**没有**:本 skill 是内容级去重,**根本不看扩展名**
>   (没有任何 `*_EXTS` 表)。加一个语义为空的键只会让用户以为它能做什么。想按
>   扩展名筛选,用 `--root` 指到具体目录,或者先走 file-sorter。
>
> 照抄别的 skill 的配置会被明确拒绝,报错里列出本 skill 的可用键:
> ```
> ❌ 配置文件 /…/config.json 无效:未知键 extension_overrides;可用键只有
>    skip_dirs, prefer_keep_hints。宁可报错也不警告后忽略 —— 键名拼错一个字母
>    就会让整份配置静默失效,而用户看不出任何区别
> ```

### ⚠️ 这是全家族里唯一会**改变删除计划**的键

`prefer_keep_hints` 动的不是「报不报」,而是**「一组重复里建议保留哪一份」**。
写错一条,原本排在删除列表末尾的散落副本会被顶到 `keep`,而**正主掉进 `drop`**
—— 后面照着计划 `rm` 的人就删错了东西。所以:

1. **加载成功必须看得见**,三个地方都印:
   - stderr 一行 `ℹ️ 已加载覆盖配置 <path>(skip_dirs N 条,prefer_keep_hints M 条)`
   - `--json` 的 `stats.config`,里面既有你**追加的**(`prefer_keep_hints`),也有
     **实际生效的全集**(`prefer_keep_hints_effective` = 内置 7 条 + 你追加的)
   - **人类可读报告**里一段 `⚙ 已加载覆盖配置 …`,把生效全集逐条列出来
2. `keep.reason` 仍然写着启发式依据,但**它不会自动跟着配置变** —— 要看生效的
   提示词,看上面第 3 处。
3. **删之前一定要人工过一遍 `keep`**,这是 SKILL.md 本来就有的约束(「先 `mv` 到
   `_dup_quarantine/`,人工核对一段时间再真删」),配了 `prefer_keep_hints` 之后
   更是如此。
4. 只**追加**,不替换:内置那 7 条一条都不删。所以最坏情况是「多加了一个不该优先
   的提示词」,不是「所有内置保护都没了」。

汇报时请把 `keep` 与生效的提示词**一起**给用户,不要只给删除清单。

### 放在哪

`config.json` 按 **脚本自己所在的目录**(`__file__`)解析,**不是** cwd、**也不是**
`--root`:

```
skills/dedup-finder/           ← zs skill 装好后就是你项目里的那一份
├── dedup_finder.py
├── config.json                ← 放这里
└── SKILL.md
```

按 `__file__` 解析是刻意的:被扫描的目录里万一躺着一个 `config.json`(扫的正是
一个代码目录、或者用户把配置和数据放一起了),它只会被当成一个**普通文件**参与
比对(超过 `--min-size` 就进 `files_scanned`),不会被读成配置。配置跟着**安装**
走,不跟着**数据**走。

### Schema

两个键都可选;只写一个,另一个不产生任何影响。

```jsonc
{
  // 整棵不进入的目录(fnmatch 模式,锚定在各自的 --root)
  "skip_dirs": ["临时", "安装包缓存/*"],

  // 追加的「优先保留」提示词。**子串**匹配,不是 glob
  "prefer_keep_hints": ["终稿", "交付", "final"]
}
```

### `prefer_keep_hints` 的确切语义

内置的判定是这一行,一个字都没改:

```python
path = item["path"].lower()
prefer = 0 if any(h in path for h in PREFER_KEEP_HINTS) else 1
```

所以配置项:

- **是子串匹配**,作用在**小写化后的相对路径**上(`工作/终稿/深层/A.bin` 含
  `终稿` → 命中)
- 加载时会 `strip()` 前后空白并 `lower()`,和内置提示词的处理保持一致(内置 7 条
  本来就全是小写/中文,所以这不改变它们的语义)
- 与内置重复的条目**不会重复出现**在生效全集里
- **不是 glob**:`终*` 里的 `*` 就是字面的星号,命中不了 `终稿/`

> **为什么没顺手升级成 glob?** 因为 `PREFER_KEEP_HINTS` 是内置与配置**共用**的
> 同一个列表,`any(h in path …)` 也是同一条判定。换成 `fnmatch` 会**连内置那 7 条
> 的语义一起改掉**:`原始` 会变成只匹配整段路径或以 `原始/` 开头的目录,而今天它
> 匹配路径里任何位置出现的「原始」两个字。那是一次静默的行为变更,而且改的是没有
> 配置文件时也会走到的代码 —— 与本机制「没有配置就逐字节一致」的前提直接冲突。
> 如果确实想要 glob(比如「只在第一层目录名上匹配」),建议单独开 issue:那需要先
> 决定内置提示词要不要一起迁移,以及迁移之后老配置的兼容期怎么算。

**路径分隔符**:相对路径**统一用 `/`**(与其余 8 个 scanner 一致),Windows 上也
一样。所以 `工作/终稿` 这种**含分隔符**的提示词三平台都有效,不必退化成单级目录名。

> 这里曾经不是这样,记下来免得有人"顺手改回去":本 skill 早先用字符串切片取相对
> 路径(`entry.path[len(root)+1:]`),Windows 上于是得到 `工作\终稿`。后果不止是
> 汇报里混着两种分隔符 —— `keep_rank()` 的深度这一级是 `item["path"].count("/")`,
> 在反斜杠路径上恒为 0,「浅路径优先」在整个 Windows 平台上是失效的,排序退化成
> 只比 mtime 与名字长度,于是会建议**删掉浅层那份、保留深层那份**,与本 skill 自己
> 的文档和汇报文字相反。单元级实测:`keep_rank(shallow) < keep_rank(deep)` 对
> `a/b/c/G.bin` 为 True、对 `a\b\c\G.bin` 为 False。现已在 scanner 侧统一成
> 正斜杠(POSIX 上 `os.sep` 本就是 `/`,该 replace 是空操作,输出逐字节不变)。

### 模式锚定规则(`skip_dirs`)

与 file-sorter / photo-organizer 的 `whitelist_dirs`、以及另两个 scanner 的
`skip_dirs` 用的是**同一个匹配函数** `_cfg_match()`,规则完全一致:

- `fnmatch` glob(`*` `?` `[seq]`),`*` **会跨过 `/`**
- **大小写不敏感**(目录名和模式两边都折叠;双向都成立)
- 匹配对象是**相对该 `--root` 的目录路径**,用 `/` 连接;多个 `--root` 时**各自
  锚定**
- 命中一个目录 = 它的**整棵子树都不进入**(里面的文件连 size 分组都不参与)
- 命中的两条规则是「或」:① 匹配**整段相对路径**;② 匹配**任意一级目录名**

| 模式 | `<root>/临时/2024/` | `<root>/临时/` 自己 | `<root>/深层/临时/` |
|------|:--:|:--:|:--:|
| `临时` | ✅ 规则② | ✅ 规则①② | ✅ 规则②(任意深度) |
| `临时/*` | ✅ 规则① | ❌ | ❌(相对路径不以 `临时/` 开头) |
| `深层/临时/*` | ❌ | ❌ | ❌(它下面还得有一层才算) |
| `深层/临时` | ❌ | ❌ | ✅ 规则① |
| `*` | ✅ | ✅ | ✅ |

三条边界:

1. **`skip_dirs` 只作用于目录**。直接躺在 `--root` 下的文件不吃它,连 `*` 也管不到。
2. **剪掉一份副本会改变重复组本身**:一组 3 份被剪掉 1 份就变 2 份,被剪到只剩
   1 份就**不再成组**。`duplicate_groups` / `redundant_files` / `wasted_bytes`
   都会跟着变 —— 这是对的(那些字节本来就不在你关心的范围里),但汇报时要说清楚。
3. **绝对路径永远匹配不上**,所以写成 `/Volumes/nas/data/临时` 会**直接报错**,
   而不是让你以为它生效了。

> **与内置 `SKIP_DIRS` 的一处刻意差别**:内置那张表是**精确目录名、区分大小写**
> (`name in SKIP_DIRS`);`skip_dirs` 走的是上面那套 **fnmatch + 大小写不敏感**。
> 这是为了让整个家族的模式语义只有一套,而内置表**一个字都没动** —— 动了就会改变
> 没有配置时的输出。

### 出错行为

配置文件存在但读不通 → **exit 1**,stderr 里**指名文件路径和出错的那个键**,而
`--json` 的 stdout 保持干净(错误绝不混进 JSON,Agent 拿到的 stdout 要么是能解析
的结果、要么是空的)。

**校验全部跑完才动手改内置集合**:一份「`skip_dirs` 合法、`prefer_keep_hints`
非法」的配置不会让 `PREFER_KEEP_HINTS` 被改一半,也不会打印「已加载」。

会被拒绝的写法(每一条都有 smoke 断言):非法 JSON、**空文件**、顶层不是对象、
未知键、`skip_dirs` 不是数组 / 元素不是字符串 / 空字符串 / 绝对路径(POSIX 与
Windows 盘符两种)、`prefer_keep_hints` 不是数组 / 元素不是字符串 / 空字符串。

`prefer_keep_hints` **不做**绝对路径检查 —— 它匹配的是路径**子串**,`/终稿/` 是一
个完全合法的提示词(而且比 `终稿` 更精确)。这与 `skip_dirs` 不同,别把两者的规则
搞混。

### 怎么确认它真的生效了

加载成功时 stderr 打一行(**没有配置文件时这一行不出现**):

```
ℹ️ 已加载覆盖配置 /…/skills/dedup-finder/config.json(skip_dirs 1 条,prefer_keep_hints 2 条)
```

`--json` 里也会多一个 `stats.config`,**只有真加载了配置才会出现**:

```jsonc
"config": {
  "path": "/…/skills/dedup-finder/config.json",
  "skip_dirs": ["临时"],
  "prefer_keep_hints": ["终稿", "交付"],                    // 本次追加的
  "prefer_keep_hints_effective": [                          // 实际生效的全集
    "成品", "源文件", "原始", "master", "original", "import", "相册",
    "终稿", "交付"
  ],
  "skipped_dirs": 1          // 因 skip_dirs 而没有进入的目录数
}
```

人类可读报告里也会印出来,**包括生效全集** —— 因为这份报告旁边就是一张删除清单:

```
⚙ 已加载覆盖配置 /…/skills/dedup-finder/config.json
  skip_dirs: 临时
  prefer_keep_hints: 终稿, 交付
  保留提示词生效全集(内置 + 本次追加,决定重复组里先保留哪个):
    成品, 源文件, 原始, master, original, import, 相册, 终稿, 交付
  因 skip_dirs 未纳入的目录: 1 个
```

反过来说:**结果里没有 `stats.config` 这个键 = 没找到配置文件 = 你的覆盖没生效**,
先确认它是不是真的躺在 `dedup_finder.py` 旁边(而不是 `--root` 底下)。

### 一个实测过的例子

```
浅层/A.bin              (prefer=1,深度 1,mtime 最旧)
工作/终稿/深层/A.bin     (prefer=1,深度 3,mtime 最新)

无配置      keep = 浅层/A.bin            drop = 工作/终稿/深层/A.bin
{"prefer_keep_hints": ["终稿"]}
            keep = 工作/终稿/深层/A.bin   drop = 浅层/A.bin          ← 整个反过来了
{"prefer_keep_hints": ["没有这个词"]}
            keep = 浅层/A.bin            drop = 工作/终稿/深层/A.bin ← 与无配置逐条相同
```

第二行就是上面那个警告的具体样子:**一条配置把删除计划的第一名换掉了**。第三行是
它的负控制 —— 匹配不到任何东西的提示词不会悄悄改变排序,而配置本身仍然出现在
`stats.config` 里,所以「没生效」和「生效了但没命中」是**可以区分**的。

## 工作流

### 场景 1:用户说"帮我找重复文件"

**步骤**:
1. **exec** `python3 dedup_finder.py scan --root <挂载路径> --output /tmp/dups.json`
   (大库先 `--max-files 5000` 摸底,看耗时再决定要不要全量)
2. 读 JSON `stats`:重复组数、冗余文件数、可回收空间、耗时、读取字节数
3. 汇报:发现 N 组重复,M 个冗余文件,可回收 X GB;列前 20 组最大的
4. **不做任何写操作** — 问用户要不要出清理计划

### 场景 2:用户说"清理重复的"

**步骤**:
1. 复用上一轮 `/tmp/dups.json`
2. 每组已给 `keep`(建议保留)和 `drop`(建议删除);**先预览整个计划**给用户
   - `keep.reason` 说明启发式依据(成品/源目录优先 → 浅路径 → 旧文件 → 短名)
   - **启发式只是建议**,LLM 要复核:如果用户库结构特殊,逐组确认保留哪个
3. 用户确认后,**优先隔离而非删除**:
   ```bash
   mkdir -p "/Volumes/nas/_dup_quarantine"
   mv -n "/Volumes/nas/data/xxx 副本.mp4" "/Volumes/nas/_dup_quarantine/"
   ```
   - 隔离一段时间(如 2 周)用户没反馈问题,再统一 `rm`
   - 极空间用户也可走 zspace-nas skill 的 `zs mv` / `zs rm`
4. 重扫验证重复组数下降

### 场景 3:用户说"我只想找重复的照片/视频"

**步骤**:
1. 把 `--root` 指到照片/视频目录(而非全盘),减少无关比对
2. 提示:内容级去重会抓到「改了文件名的同一张图」,这正是它比按名去重强的地方
3. 若用户想要「相似但不同」(连拍/微调版)— 本 skill 做不到(见已知 gap),
   建议走 photo-organizer 的连拍识别

## 保留优先级(启发式)

重复组内 `keep` 的选择顺序(越小越该保留):

1. 路径含 `成品/源文件/原始/master/original/import/相册` 等提示词
2. 路径更浅(层级少)— 通常是「正主」,深层是散落副本
3. mtime 更旧 — 原始文件通常更早
4. 名字更短 — 避开 `xxx 副本`、`xxx (1)` 这类派生名

第 1 条那张提示词表可以用同目录 `config.json` 的 `prefer_keep_hints` **追加**
(见下「配置覆盖」)—— 内置 7 条一条都不会被删。**这是全家族里唯一会改变删除计划的
配置键**,所以生效的全集会同时印在 stderr、`stats.config` 和人类报告里。

**这只是给 LLM 的初排,最终保留哪个必须让用户拍板。**

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell | 任何 NAS | `mv -n` 到隔离目录(推荐)/ `rm`(确认后) |
| PowerShell(Windows) | 任何 NAS | `New-Item -ItemType Directory -Force` 建目录 + `Move-Item`(**不加 `-Force`** = 不覆盖);批量 `robocopy /MOV /XC /XN /XO` |
| `zs` CLI | 极空间 | `zs mv` / `zs rm`(见 zspace-nas skill) |
| MCP tool | 极空间 + MCP | `move` / `remove`,弹 UI 二次确认 |

> **Windows**:上表第一行是 POSIX 命令,PowerShell 里没有 `mv -n` / `mkdir -p`,照着执行会直接失败。完整对照(含中文路径要先 `chcp 65001`、用隔离目录代替删除)见 [skills/README.md](../README.md) 的「Windows 用户:执行阶段要换命令」;极空间用户可直接用 `zs mv` 绕开 shell 差异。

## 关键约束

1. **脚本只读**:无 apply / rm 子命令;删除由 Agent 在用户确认后执行
2. **先隔离后删除**:重复文件优先 `mv` 到 `_dup_quarantine/`,人工核对一段时间再真删
3. **保留项必须确认**:启发式 `keep` 只是建议,LLM 逐组复核 + 用户拍板
4. **删除不可逆**:`rm` 前确认隔离副本已就位;`mv` 一律带 `-n`
5. **内容级 ≠ 相似级**:只抓字节完全相同的;连拍/转码/改尺寸的抓不到
6. **大库耗时**:全量 hash 要读完整文件;SMB 上受网络带宽限制,先 `--max-files` 摸底

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| SMB 全量 hash 慢 | 大库扫描很久 | 三级指纹已尽量减少全量 hash;先 `--max-files` / `--min-size 1024` 缩小 |
| 稀疏文件 | size 相同但实际占用不同 | hash 按逻辑内容,重复判定不受影响;空间回收估算按 size |
| 硬链接 | 同 inode 多路径,hash 相同 | 删其一不影响数据;SMB 上少见,如遇需先 `ls -i` 确认 |
| 挂载断连 | 读取错误进 `errors` | 脚本不崩,errors 列出失败项;重连后重扫 |
| 跨 root 相对路径 | 多 `--root` 时 path 各自相对 | JSON 每条带 fingerprint,按组处理不受影响;`skip_dirs` 也在每个 root 上各自锚定 |
| 写了 `config.json` 但没生效 | `--json` 里没有 `stats.config`,stderr 也没有 `ℹ️ 已加载覆盖配置` | 它必须躺在 **`dedup_finder.py` 旁边**(按 `__file__` 解析),不是 cwd、也不是 `--root`;`--root` 底下的 `config.json` 只会被当成普通文件参与比对 |
| `prefer_keep_hints` 里的 `*` 不起作用 | `终*` 命中不了 `终稿/` | 它是**子串**匹配,不是 glob(刻意保持与内置提示词同一条规则);写 `终稿` |
| `keep` 换了一份但不知道为什么 | 删除计划第一名变了 | 看人类报告里 `⚙ 已加载覆盖配置` 那段的「保留提示词生效全集」,或 `stats.config.prefer_keep_hints_effective` |
| 重复组数变少了 | `duplicate_groups` 下降 | `skip_dirs` 剪掉副本之后,一组不足 2 份就不成组;对照 `stats.config.skipped_dirs` |

## 已知 gap

- ~~`SKIP_DIRS` 与 `PREFER_KEEP_HINTS` 是硬编码的~~ → **已部分解决**(issue #15):
  同目录 `config.json` 的 `skip_dirs` / `prefer_keep_hints`,两者都是**追加**。
  仍然硬编码的:`HEAD_SIZE`(64KB)、`CHUNK`(1MB)、`MAX_DEPTH`、`--min-size`
  默认值 —— 它们改的是「什么叫重复」的判定精度,是比目录/提示词大得多的设计问题,
  刻意没有塞进配置层
- **`prefer_keep_hints` 是子串匹配,不是 glob**:刻意的。`PREFER_KEEP_HINTS` 是
  内置与配置**共用**的同一个列表,判定也是同一条 `h in path`;换成 `fnmatch` 会连
  内置那 7 条的语义一起改掉,而那是没有配置文件时也会走到的代码。要做 glob 得先
  决定内置提示词怎么迁移,建议单独开 issue
- **`config.json` 是 per-安装、不是 per-库**:一份安装对应一份配置。要给不同的库
  用不同的保留提示词,目前只能装两份 skill。加一个 `--config PATH` 是自然的后续,
  但它会引出「两处配置是替换还是叠加」这个新问题
- 不做「相似」去重(感知哈希/编辑距离)— 连拍、转码版、改尺寸抓不到,那是 photo-organizer 的活
- 不识别硬链接/软链接关系(软链接已跳过,硬链接按普通文件比对)
- `keep` 启发式对特殊库结构可能选错,必须人工复核
- 全量 hash 是 IO 密集;超大库(百万文件)建议分区多次扫

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认已挂载:`ls /Volumes/` |
| 扫描很慢 | `--min-size 1024`(只比 ≥1MB)+ `--max-files 10000` 分批 |
| `errors` 里一堆读失败 | 权限或挂载问题;检查共享读权限,重连后重扫 |
| 重复组太多看不过来 | JSON 已按浪费空间降序;`--top 50` 或让 LLM 分批出计划 |
| 写了 `config.json` 但 `keep` 没变 | 先看 `--json` 里有没有 `stats.config`:**没有**就是文件没躺在 `dedup_finder.py` 旁边;**有**就把 `prefer_keep_hints_effective` 与重复组的实际路径对一遍 —— 提示词是路径**子串**,而且比的是小写化后的相对路径 |
| 配了 `prefer_keep_hints` 之后要删东西 | —— | **逐组人工过 `keep`**,并先 `mv` 到 `_dup_quarantine/`。这个键能反转「建议保留哪一份」,配错了就会把正主排进删除清单 |
