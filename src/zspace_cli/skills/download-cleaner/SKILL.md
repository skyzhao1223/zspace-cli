---
name: download-cleaner
description: Use when 用户想清理 NAS 下载目录 — "下载区太乱帮我清一下"、"哪些下载可以删了"、"安装包/种子/压缩包占空间"、"下载完的视频没归档"、"半截下载残留"、"重复下载了好几次"。只读扫描按类别分诊(未完成/种子/安装包/压缩包/待归档媒体文档),给每文件一个建议动作(delete/extract/move-to-library/review);LLM 出清理计划,用户确认后由 Agent 执行。
  触发词:下载清理、下载区整理、下载目录、清理下载、安装包清理、种子清理、压缩包解压、下载归档、半截下载、重复下载、下载残留、download cleaner、clean downloads。
  不适用:内容级去重(走 dedup-finder);归档后的影视/音乐/照片/工作文件整理(分别走 media-naming / music-organizer / photo-organizer / work-organizer)——本 skill 只负责把下载区「分诊 + 分流」。
---

# Download Cleaner — 下载区分诊清理

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS),纯 stdlib 零依赖。

下载区是 NAS 最脏的地方:半截下载、用完的安装包、种子、解压后忘删的压缩包、
下载完没归档的影视/音乐/文档混在一起。本 skill 做**分诊**:给每个文件打类别 +
建议动作,不自动删。
**写操作不在脚本里** — LLM 按 `action` 分组出清理计划,用户确认后由 Agent 执行
(优先 `mv` 到隔离/归档目录,人工核对后再真删)。

## Prerequisites

```bash
open smb://<NAS_IP>/下载        # 极空间/群晖/威联通/绿联等均支持 SMB
python3 --version               # ≥3.9,无第三方依赖
```

## 快速验证

```bash
python3 download_cleaner.py scan --root /Volumes/nas/下载 --sample 500
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 下载区分诊扫描(只读) |
| `scan --root PATH --json --output F` | JSON 输出 |
| `--stale-days N` | 超过 N 天视为久未处理(默认 365) |
| `--max-depth N` / `--sample N` / `--top N` | 深度 / 文件数上限 / 每类显示条数 |
| 同目录 `config.json` | 可选覆盖层:`skip_dirs` + `extension_overrides`(见下「配置覆盖」) |

## 配置覆盖(`config.json`,可选)

真实下载区里总有**故意的例外**:一个当仓库用的 `临时/` 目录、某款下载器的私有
半截文件扩展名、公司内部的 `.iso` 交付物。没有这个机制之前,唯一的办法是改脚本
里的 `SKIP_DIRS` / `*_EXTS` 集合 —— 而下次 `pip install -U` 就把它冲掉了。

**没有这个文件时,本 skill 的输出与引入该机制之前逐字节一致** —— 不多一个 JSON
字段、不少一条问题。

### 本 skill 接受哪些键

只有两个。**顶层出现第三个键 → 直接报错退出。**

| 键 | 类型 | 作用 |
|----|------|------|
| `skip_dirs` | `string[]` | **追加**到内置 `SKIP_DIRS`;命中的目录**整棵不进入**,里面的文件既不计入 `stats` 也不出任何问题 |
| `extension_overrides` | `object` | **逐扩展名改判**:该扩展名先从**所有**内置表里摘掉,再放进指定的那一张 |

> **刻意没有 `whitelist_dirs`。** 那个键在 file-sorter / photo-organizer 里的
> 语义是「**视为合规 / 不要报**」—— 它喂的是那两个 skill 的**合规豁免**机制
> (`--keep-dir` 与 `WHITELIST_DIRS`)。本 skill 不做合规判定:它对看见的每个
> 文件都做分诊,没有「这个目录算合规」这个概念。同一个键名在不同 skill 里含义
> 不同,用户把配置从一个 skill 复制到另一个就会得到**静默的意外行为**,所以这里
> 只暴露本 skill 真正有的机制,并照它本来的样子命名:`skip_dirs`。
>
> 照抄 file-sorter 的配置会被明确拒绝,报错里列出本 skill 的可用键:
> ```
> ❌ 配置文件 /…/config.json 无效:未知键 whitelist_dirs;可用键只有 skip_dirs,
>    extension_overrides。宁可报错也不警告后忽略 —— 键名拼错一个字母就会让整份
>    配置静默失效,而用户看不出任何区别
> ```

### 放在哪

`config.json` 按 **脚本自己所在的目录**(`__file__`)解析,**不是** cwd、**也不是**
`--root`:

```
skills/download-cleaner/       ← zs skill 装好后就是你项目里的那一份
├── download_cleaner.py
├── config.json                ← 放这里
└── SKILL.md
```

按 `__file__` 解析是刻意的:`--root` 底下万一躺着一个 `config.json`(下载区里
什么都有),它只会被当成一个普通文件扫出来(归入 `other`),不会被读成配置。
配置跟着**安装**走,不跟着**数据**走。

### Schema

两个键都可选;只写一个,另一个不产生任何影响。

```jsonc
{
  // 整棵不进入的目录(fnmatch 模式,锚定在 --root)
  "skip_dirs": ["临时", "下载器缓存/*", "PT 做种中"],

  // 扩展名改判:键是扩展名(前导点可写可不写、大小写都行),
  // 值是本 skill 的类别名
  "extension_overrides": {
    "iso":  "archive",   // 公司交付物是 iso,我想让它进「压缩包」流程
    "ass":  "video",     // 内置判 other,字幕其实该跟着视频走
    "tmp":  "doc"        // 内置判 junk(建议删),可我的 .tmp 是数据
  }
}
```

`extension_overrides` 的可用类别名就是分诊表那 10 个:

```
junk  partial  torrent  installer  archive  video  audio  photo  doc  other
```

`other` 是兜底(= 不属于任何一张表),写它等于把某个扩展名从现有类别里**摘出来**
变成杂项。写成别的名字(比如 file-sorter 才有的 `cad`、photo-organizer 才有的
`sidecar`)会报错,并在报错里列出本 skill 的可用类别。

「逐扩展名」这一点是关键:写 `{"ass": "video"}` 只会让 `.ass` 变视频,`.srt`
仍然是杂项,`.part` 仍然是未完成下载。替换整张表会静默撤掉内置行为 —— 那不是
用户想要的。

**`junk` 也能被覆盖。**`categorize()` 是**有序判定阶梯**,junk 在第一级;所以
`{"tmp": "doc"}` 必须先把 `tmp` 从 `JUNK_EXTS` 里摘掉才有效,否则 junk 那一级
先命中、覆盖看起来完全没生效。脚本就是这么做的(smoke 里有真断言)。

改不了的:`JUNK_NAMES` 里的**文件名**(`.DS_Store` / `Thumbs.db`)、`._*`
AppleDouble 规则、重复下载的正则(`(1)` / `副本` / `copy`)。`extension_overrides`
只作用于扩展名。

### 一个必须知道的耦合:`ARCHIVE_EXTS` 是**共享**的

`ARCHIVE_EXTS` 同时被两处用:① 判类别;② 判「压缩包是否已解压」(同目录有没有
去掉扩展名的同名目录)。所以:

- `{"zip": "other"}` 之后,`pack.zip` 不再算压缩包,**也就不再参与「已解压」判定**
  —— 即使 `pack/` 就在旁边。
- `{"myext": "archive"}` 之后,`foo.myext` 旁边有个 `foo/` 就会判「已解压」。

这不是 bug,是「一个扩展名只属于一个类别」的必然结果。smoke 里把这条钉住了。

### 模式锚定规则(`skip_dirs`)

与 file-sorter 的 `whitelist_dirs`、photo-organizer 的 `whitelist_dirs` 用的是
**同一个匹配函数** `_cfg_match()`,规则完全一致:

- `fnmatch` glob(`*` `?` `[seq]`),`*` **会跨过 `/`**
- **大小写不敏感**(目录名和模式两边都折叠;双向都成立)
- 匹配对象是**相对 `--root` 的目录路径**,用 `/` 连接;永远不含绝对路径
- 命中一个目录 = 它的**整棵子树都不进入**
- 命中的两条规则是「或」:① 匹配**整段相对路径**;② 匹配**任意一级目录名**

| 模式 | `<root>/临时/2024/` | `<root>/临时/` 自己 | `<root>/深层/临时/` |
|------|:--:|:--:|:--:|
| `临时` | ✅ 规则② | ✅ 规则①② | ✅ 规则②(任意深度) |
| `临时/*` | ✅ 规则① | ❌ | ❌(相对路径不以 `临时/` 开头) |
| `深层/临时/*` | ❌ | ❌ | ❌(它下面还得有一层才算) |
| `深层/临时` | ❌ | ❌ | ✅ 规则① |
| `*` | ✅ | ✅ | ✅ |

三条边界:

1. **`skip_dirs` 只作用于目录**。直接躺在 `--root` 下的文件不吃它,连 `*` 也管不到
   (判定时拿到的父目录层级是空的)。
2. **`*` 不是子串**。`skip_dirs: ["照片"]` 不会顺手剪掉 `照片备份_2024/` —— 模式
   是 fnmatch,不是「名字里出现过就算」。
3. **绝对路径永远匹配不上**,所以写成 `/Volumes/nas/下载/临时` 或 `Z:\下载\临时`
   会**直接报错**,而不是让你以为它生效了。

> **与内置 `SKIP_DIRS` 的一处刻意差别**:内置那张表是**精确目录名、区分大小写**
> (`name in SKIP_DIRS`,所以 `@eaDir` 与 `@eadir` 不是一回事);`skip_dirs` 走的是
> 上面那套 **fnmatch + 大小写不敏感**。这是为了让整个 skill 家族的模式语义只有一
> 套(用户在 file-sorter 学会的写法在这里一样管用),而内置表**一个字都没动**
> —— 动了就会改变没有配置时的输出。

### 出错行为

配置文件存在但读不通 → **exit 1**,stderr 里**指名文件路径和出错的那个键**,而
`--json` 的 stdout 保持干净(错误绝不混进 JSON,Agent 拿到的 stdout 要么是能解析
的结果、要么是空的)。

**校验全部跑完才动手改内置集合**:一份「`skip_dirs` 合法、`extension_overrides`
非法」的配置不会留下半张改过的表,也不会打印「已加载」。

会被拒绝的写法(每一条都有 smoke 断言):非法 JSON、**空文件**、顶层不是对象、
未知键、`skip_dirs` 不是数组 / 元素不是字符串 / 空字符串 / 绝对路径(POSIX 与
Windows 盘符两种)、`extension_overrides` 不是对象 / 类别值不是字符串 / 类别名不
存在 / 扩展名含多个点(`tar.gz` —— 扩展名只取文件名最后一段,写 `gz`)/ 扩展名
归一化后为空。

### 怎么确认它真的生效了

加载成功时 stderr 打一行(**没有配置文件时这一行不出现**):

```
ℹ️ 已加载覆盖配置 /…/skills/download-cleaner/config.json(skip_dirs 2 条,extension_overrides 3 条)
```

`--json` 里也会多一个 `stats.config`,**只有真加载了配置才会出现**:

```jsonc
"config": {
  "path": "/…/skills/download-cleaner/config.json",
  "skip_dirs": ["临时", "PT 做种中"],
  "extension_overrides": {"iso": "archive", "ass": "video"},
  "skipped_dirs": 3          // 因 skip_dirs 而没有进入的目录数
}
```

人类可读报告里也会印一段 `⚙ 已加载覆盖配置 …`,把生效的键值列出来。

反过来说:**结果里没有 `stats.config` 这个键 = 没找到配置文件 = 你的覆盖没生效**,
先确认它是不是真的躺在 `download_cleaner.py` 旁边(而不是 `--root` 底下)。

`skipped_dirs` 与 `stats.dirs` 要**一起汇报**:被剪掉的目录不计入 `dirs`,也不计入
`files`,所以「文件数怎么变少了」的答案就在这两个数字里。

## 分诊类别与建议动作

| 类别 | 判定 | 建议 action |
|------|------|-------------|
| 垃圾 | `.DS_Store`、`._*`、`.tmp` | `delete` |
| 未完成下载 | `.part`/`.crdownload`/`.td`/`.aria2`/`.bt.td` | `delete-confirm`(无法续传则删) |
| 种子 | `.torrent` | `delete-confirm`(任务完成后可删) |
| 安装包 | `.dmg`/`.exe`/`.apk`/`.pkg`… | `review`;超 `--stale-days` → `delete-confirm` |
| 压缩包 | `.zip`/`.rar`/`.7z`… | 已解压(同名目录在)→ `delete-confirm`;未解压 → `extract-or-review` |
| 视频/音频/图片/文档 | 对应扩展名 | `move-to-library`(归档到影视/音乐/照片/工作库) |
| 重复下载 | 名字带 `(1)`/`副本`/`copy` | 附加「二选一」提示 |
| 杂项 | 其余 | 超期 → `review`;否则 `keep` |

「可回收空间」= 垃圾 + 未完成 + 种子 + 已解压压缩包 + 老旧安装包 的体积合计。

## 工作流

### 场景 1:用户说"帮我清理下载目录"

**步骤**:
1. **exec** `python3 download_cleaner.py scan --root <挂载路径> --output /tmp/downloads.json`
2. 读 JSON `stats`:总大小、可回收空间、各类别计数、重复下载数、最大文件 Top10
3. 汇报:可立即回收约 X GB;按 action 分组列待处理项
4. **不做写操作** — 问用户先处理哪类(建议从 `delete` / `delete-confirm` 开始)

### 场景 2:用户说"把能删的删了"

**步骤**:
1. 复用 JSON,过滤 `action` ∈ {`delete`, `delete-confirm`}
2. 出删除清单**预览**;`delete-confirm` 类(种子/未完成/已解压包)逐项让用户过目
3. 用户确认后**优先隔离**:
   ```bash
   mkdir -p "/Volumes/nas/_trash_review"
   mv -n "/Volumes/nas/下载/xxx.torrent" "/Volumes/nas/_trash_review/"
   ```
   隔离一段时间没问题再统一 `rm`;极空间也可走 `zs mv` / `zs rm`

### 场景 3:用户说"下载的影视/音乐帮我归位"

**步骤**:
1. 过滤 `action == move-to-library` 的文件,按 `category` 分流
2. **本 skill 只负责指出该去哪**,具体归档命名走对应 skill:
   - video → media-naming / media-manager-skill
   - audio → music-organizer
   - photo → photo-organizer
   - doc → work-organizer
3. 生成 `下载区 → 目标库` 的 `old → new` 计划,预览确认后 `mv -n`

## 清理顺序(严格)

1. 删垃圾(`delete`:系统文件/临时文件)
2. 清未完成下载 + 种子(`delete-confirm`,逐项过目)
3. 已解压的压缩包 → 删原包(`delete-confirm`)
4. 未解压压缩包 → 确认内容后解压归档或删(`extract-or-review`)
5. 老旧安装包 → 确认已装后删(`delete-confirm`)
6. 媒体/文档 → 分流到对应库(`move-to-library`,交给专门 skill 规范化)
7. 重复下载 → 二选一
8. 重新 `scan` 验证可回收空间下降

每一步先出清单预览,确认后再执行;删除一律先隔离。

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell | 任何 NAS | `mv -n` 隔离 / 归档,确认后 `rm` |
| PowerShell(Windows) | 任何 NAS | `New-Item -ItemType Directory -Force` 建目录 + `Move-Item`(**不加 `-Force`** = 不覆盖);批量 `robocopy /MOV /XC /XN /XO` |
| `zs` CLI | 极空间 | `zs mv` / `zs rm`(见 zspace-nas skill) |
| MCP tool | 极空间 + MCP | `move` / `remove`,弹 UI 二次确认 |

> **Windows**:上表第一行是 POSIX 命令,PowerShell 里没有 `mv -n` / `mkdir -p`,照着执行会直接失败。完整对照(含中文路径要先 `chcp 65001`、用隔离目录代替删除)见 [skills/README.md](../README.md) 的「Windows 用户:执行阶段要换命令」;极空间用户可直接用 `zs mv` 绕开 shell 差异。

## 关键约束

1. **脚本只读**:无 apply / rm 子命令;删除/移动由 Agent 在用户确认后执行
2. **先隔离后删除**:所有 `delete-confirm` 优先 `mv` 到隔离目录,人工核对再真删
3. **删种子前确认任务状态**:做种中的 `.torrent` 删了会影响 PT 分享率
4. **压缩包「已解压」是启发式**:靠同目录同名目录判定,可能漏判/误判,逐项确认
5. **未完成任务**:`.part`/`.crdownload` 可能还能续传,删前确认下载器里没有活动任务
6. **归档不越权**:媒体文件只指出「该去哪个库」,实际命名规范交给对应 skill

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 已解压误判 | `foo.zip` 有 `foo/` 就判已解压 | 可能同名但内容不同;删包前扫一眼目录 |
| 下载器活动文件 | `.aria2`/`.part` 正在写入 | 删前确认下载器无活动任务,否则中断下载 |
| 做种中的种子 | 删 `.torrent` 掉分享率 | PT 用户保留活跃种子;只清完成任务的 |
| 嵌套压缩 | 包里还有包 | `--max-depth` 保证扫到;逐层确认 |
| SMB stat 慢 | 大下载区扫描久 | `--sample` 摸底;下载区通常文件数不多,可接受 |
| 写了 `config.json` 但没生效 | `--json` 里没有 `stats.config`,stderr 也没有 `ℹ️ 已加载覆盖配置` | 它必须躺在 **`download_cleaner.py` 旁边**(按 `__file__` 解析),不是 cwd、也不是 `--root`;`--root` 底下的 `config.json` 只会被当成普通文件扫出来 |
| `skip_dirs` 直接报错退出 | stderr 说「写成了绝对路径」 | 模式锚定在 `--root`,只能写相对路径(`临时`、`下载器缓存/*`) |
| 改判出 `archive` 之后「已解压」不判了 | `pack.zip` 从 `delete-confirm` 变成 `review` | `ARCHIVE_EXTS` 同时喂类别判定与「已解压」启发式,见「配置覆盖」里那条耦合说明 |
| 文件数莫名变少 | `stats.files` 比 `ls` 数出来的少 | 看 `stats.config.skipped_dirs`;被 `skip_dirs` 剪掉的目录整棵不计入 |
| 照抄别的 skill 的配置报错 | 「未知键 whitelist_dirs」 | 本 skill 只认 `skip_dirs` 与 `extension_overrides`;报错里已列出可用键,照着改 |

## 已知 gap

- ~~`SKIP_DIRS` 与 9 张扩展名表是硬编码的,私有格式要改脚本~~ → **已部分解决**
  (issue #15):同目录 `config.json` 的 `skip_dirs` / `extension_overrides`。
  仍然硬编码的:`JUNK_NAMES`(文件名而非扩展名)、`DUP_MARK_RE`(重复下载后缀)、
  `ARCHIVE_HINT` 里的目标库文案、`MAX_DEPTH`、`--stale-days` 默认值
- **`config.json` 是 per-安装、不是 per-库**:一份安装对应一份配置。要给不同的
  下载区用不同的 `skip_dirs`,目前只能装两份 skill。加一个 `--config PATH` 是自然
  的后续,但它会引出「两处配置是替换还是叠加」这个新问题
- **AppleDouble 文件根本进不了统计**(既有行为,本次没改):`_check_file` 对
  「以 `.` 开头且不在 `JUNK_NAMES` 里」的文件提前 return,所以 `._xxx` 既不计入
  `stats.files` 也不出 issue,而 `categorize()` 里那条 `startswith("._")` → junk
  的规则经 CLI 走不到。要不要统一,交维护者决定
- 「已解压」靠同名目录启发式,不比对压缩包内容与目录
- 不解析压缩包内文件列表(需 zipfile 逐个读,大压缩包慢)
- 重复下载只按名字 `(1)`/副本 识别,内容级重复走 dedup-finder
- 归档目标是建议(按类别),实际目标库路径需用户指定

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认已挂载:`ls /Volumes/`;下载区可能是 PT/BT 客户端的独立目录 |
| 可回收空间为 0 但很乱 | 多是待归档媒体(move-to-library);走场景 3 分流 |
| 扫描慢 | `--sample 2000` 摸底 |
| 写了 `config.json` 但行为没变 | 先看 `--json` 里有没有 `stats.config` 这个键:**没有**就是文件没躺在 `download_cleaner.py` 旁边;**有**就看 `skipped_dirs` / `extension_overrides` 是不是你写的那几条 |
