---
name: backup-auditor
description: Use when 用户想审计 NAS 上的备份 — "我的备份还新鲜吗"、"备份作业是不是挂了"、"哪些旧备份可以轮转删掉"、"关键目录都有备份吗"、"有没有孤儿备份/空备份"。只读审计:scan 分析备份集(版本轮转/陈旧检测/空备份),coverage 核对关键源目录有无对应备份;LLM 出轮转/补备份计划,用户确认后由 Agent 执行。
  触发词:备份审计、备份检查、备份新鲜度、备份轮转、旧备份清理、备份保留策略、关键目录备份、有没有备份、孤儿备份、空备份、备份作业、Time Machine、快照审计、backup audit、check backups、backup rotation。
  不适用:内容级去重(走 dedup-finder);普通文件整理(走 photo/work/portfolio-organizer);本 skill 只审计「备份的健康度」,不做文件命名规范。
---

# Backup Auditor — 备份健康审计

## 概述

**跨 NAS 通用**:审计脚本跑在**本地挂载路径**上(SMB/NFS),纯 stdlib 零依赖。

备份最怕两件事:**该备的没备**(覆盖缺口)、**备了但早就停了/备坏了**(陈旧、空备份)。
本 skill 做**只读审计**,不碰备份数据:
- `scan` — 把备份目录里的项按「备份集」聚合(同一目标的多版本),查陈旧、单版本、空备份、可轮转旧版本
- `coverage` — 拿源数据目录对照备份目录,查哪些关键目录没备份、哪些是孤儿备份

**写操作不在脚本里** — 审计出问题后,LLM 生成轮转/补备份计划,
经用户确认后由 Agent 执行(旧版本优先 `mv` 归档而非直接 `rm`)。

## Prerequisites

```bash
open smb://<NAS_IP>/备份        # 挂载备份目录
open smb://<NAS_IP>/data        # (coverage 模式)挂载源数据目录
python3 --version               # ≥3.9,无第三方依赖
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root BACKUP` | 备份集版本/陈旧/轮转分析(只读) |
| `scan --root BACKUP --stale-days 35 --keep 3` | 自定义陈旧阈值与保留份数 |
| `coverage --source DATA --backup BACKUP` | 关键目录有无备份核对(只读) |
| `--json --output F` | JSON 输出 |
| 同目录 `config.json` | 可选覆盖层:`skip_dirs` + `extension_overrides`(见下「配置覆盖」) |

```bash
# 备份集审计:最新超过 35 天算陈旧,每组保留最近 3 份
python3 backup_auditor.py scan --root /Volumes/nas/备份

# 覆盖核对:源 data 下的关键目录是否都有备份
python3 backup_auditor.py coverage --source /Volumes/nas/data --backup /Volumes/nas/备份
```

## 配置覆盖(`config.json`,可选)

备份目录里总有**不该被审计的东西**:备份工具自己的暂存区、一个当仓库用的
`临时暂存/`、某种内部审计脚本不认识扩展名的私有归档格式。没有这个机制之前,唯一
的办法是改脚本里的 `SKIP_DIRS` / `ARCHIVE_EXTS` —— 而下次 `pip install -U` 就把
它冲掉了。

**没有这个文件时,本 skill 两个子命令的输出都与引入该机制之前逐字节一致** ——
不多一个 JSON 字段、不少一条审计项。

### 本 skill 接受哪些键

只有两个。**顶层出现第三个键 → 直接报错退出。**

| 键 | 类型 | 作用 |
|----|------|------|
| `skip_dirs` | `string[]` | **追加**到内置 `SKIP_DIRS`;命中的目录**整棵不进入**(见下「作用在哪三层」) |
| `extension_overrides` | `object` | **逐扩展名改判**:该扩展名先从 `ARCHIVE_EXTS` 里摘掉,再按你写的类别放回去 |

> **刻意没有 `whitelist_dirs`。** 那个键在 file-sorter / photo-organizer 里的语义
> 是「**视为合规 / 不要报**」—— 它喂的是那两个 skill 的**合规豁免**机制。本 skill
> 不判合规:它审的是「备份新不新鲜、有没有冗余、关键目录漏没漏」,`--keep N` 是
> **版本保留数**,不是目录白名单。同一个键名在不同 skill 里含义不同,用户把配置从
> 一个 skill 复制到另一个就会得到**静默的意外行为**,所以这里只暴露本 skill 真正有
> 的机制,并照它本来的样子命名:`skip_dirs`。
>
> 照抄别的 skill 的配置会被明确拒绝(`whitelist_dirs`、`prefer_keep_hints` 都试过),
> 报错里列出本 skill 的可用键。

### 放在哪

`config.json` 按 **脚本自己所在的目录**(`__file__`)解析,**不是** cwd、**也不是**
`--root` / `--source` / `--backup`:

```
skills/backup-auditor/         ← zs skill 装好后就是你项目里的那一份
├── backup_auditor.py
├── config.json                ← 放这里
└── SKILL.md
```

按 `__file__` 解析是刻意的:被审计的备份目录里万一躺着一个 `config.json`(备份集
本身就是一个代码目录、或者用户把配置和数据放一起了),它只会被当成一个**普通的
备份项**统计进去,不会被读成配置。配置跟着**安装**走,不跟着**数据**走。

### Schema

两个键都可选;只写一个,另一个不产生任何影响。

```jsonc
{
  // 整棵不进入的目录(fnmatch 模式,锚定在 --root / --source / --backup)
  "skip_dirs": ["临时暂存", "备份工具缓存/*"],

  // 扩展名改判。本 skill 只有两张「表」:archive 与它的兜底 non_archive
  "extension_overrides": {
    "mybak": "archive",      // 内部审计脚本不认的私有归档扩展名
    "iso":   "non_archive"   // 反过来:别再把它当归档扩展名剥掉
  }
}
```

`extension_overrides` 的可用类别名**只有两个**:

| 类别名 | 含义 |
|--------|------|
| `archive` | 进 `ARCHIVE_EXTS`,`parse_backup_name()` 会把它当归档扩展名**剥掉**再算 `base_key` |
| `non_archive` | 兜底:从 `ARCHIVE_EXTS` 里**摘掉**,扩展名留在名字里参与 `base_key` |

这直接影响**备份集怎么聚合**,所以它是本 skill 里最有用的一个覆盖:

```
数据.mybak  数据_v2.mybak
  内置:base_key = 数据mybak / 数据v2mybak  → 两个**单版本**集(各报一条「无历史冗余」)
  {"mybak": "archive"}:base_key = 数据 / 数据(version 2) → 一个**双版本**集
                                                    → --keep 1 时正确轮转出旧的那份
```

写成别的名字(比如 file-sorter 才有的 `backup`、`other`)会报错,并在报错里列出
本 skill 仅有的这两个类别。

**改不了的**:`.tar.gz` / `.tar.bz2` / `.tar.xz` / `.sparsebundle` 这四个**双扩展名**
是硬编码的、并且在 `ARCHIVE_EXTS` 之前匹配 —— 所以 `{"gz": "non_archive"}` **不会**
让 `照片备份.tar.gz` 停止被剥离。同理不可配置:`DATE_RE` / `TIME_SUFFIX_RE` /
`VERSION_RE` / `BACKUP_HINT_RE` 这四条识别备份集的正则、`--keep` 与 `--stale-days`
的默认值、`dir_size` 的 20000 文件上限。这些改动会**改变备份集怎么被聚合**,是比
「扩展名归类」大得多的设计问题,应该单独讨论而不是塞进配置层。

### `skip_dirs` 作用在哪三层

本 skill 有三个地方在遍历目录,三层都吃同一个 `skip_dirs`(否则同一个键在一个
skill 内部就有两种语义):

| 层 | 作用 | 计入 `stats.config.skipped_dirs` |
|----|------|:--:|
| `scan` 的备份项收集 | 命中的顶层目录/文件**整个不成为一个备份项** | ✅ |
| `coverage` 的源目录收集 | 命中的源顶层目录**不再要求有备份**(不计入 `source_dirs`/`missing`) | ✅ |
| `dir_size` 递归求体积 | 命中的子目录**不计入该备份项的 `size` / `file_count`** | ❌(不单独计数) |

所以 `skipped_dirs` 数的是**审计边界上**被拦下的目录数;`coverage` 模式下是源侧与
备份侧**合计**。第三层虽然不计数,但确确实实会让体积变小 —— smoke 里是按字节核对
的。

**一个必须知道的连锁反应**:在 `coverage` 里 `skip_dirs` 掉一个源目录,它对应的
备份集就失去了匹配对象,于是**变成孤儿备份**(`review-orphan`)。这是正确行为
(你说了不用管它),但汇报时要一起说,否则用户会以为凭空多出一个孤儿。

### 模式锚定规则(`skip_dirs`)

与 file-sorter / photo-organizer 的 `whitelist_dirs`、以及另两个 scanner 的
`skip_dirs` 用的是**同一个匹配函数** `_cfg_match()`,规则完全一致:

- `fnmatch` glob(`*` `?` `[seq]`),`*` **会跨过 `/`**
- **大小写不敏感**(目录名和模式两边都折叠;双向都成立)
- 匹配对象是**相对根的目录路径**,用 `/` 连接;`scan` 锚在 `--root`,`coverage`
  的源侧锚在 `--source`、备份侧锚在 `--backup`
- 命中一个目录 = 它的**整棵子树都不进入**
- 命中的两条规则是「或」:① 匹配**整段相对路径**;② 匹配**任意一级目录名**

| 模式 | `<bk>/临时暂存/子目录/` | `<bk>/临时暂存/` 自己 | `<bk>/深层/临时暂存/` |
|------|:--:|:--:|:--:|
| `临时暂存` | ✅ 规则② | ✅ 规则①② | ✅ 规则②(任意深度) |
| `临时暂存/*` | ✅ 规则① | ❌ | ❌(相对路径不以 `临时暂存/` 开头) |
| `深层/临时暂存/*` | ❌ | ❌ | ❌(它下面还得有一层才算) |
| `深层/临时暂存` | ❌ | ❌ | ✅ 规则① |
| `*` | ✅ | ✅ | ✅ |

三条边界:

1. **`skip_dirs` 只作用于目录**。直接躺在根下的文件不吃它,连 `*` 也管不到。
2. **`*` 不是子串**。`skip_dirs: ["照片"]` **不会**顺手剪掉 `照片备份_2024-01-01/`
   —— 模式是 fnmatch,不是「名字里出现过就算」。这一点在备份目录里特别容易踩,
   因为备份项的名字通常就是「源目录名 + 备份 + 日期」。
3. **绝对路径永远匹配不上**,所以写成 `/Volumes/nas/备份/临时暂存` 会**直接报错**,
   而不是让你以为它生效了。

> **与内置 `SKIP_DIRS` 的一处刻意差别**:内置那张表是**精确目录名、区分大小写**
> (`name in SKIP_DIRS`);`skip_dirs` 走的是上面那套 **fnmatch + 大小写不敏感**。
> 这是为了让整个家族的模式语义只有一套,而内置表**一个字都没动** —— 动了就会改变
> 没有配置时的输出。

### 出错行为

配置文件存在但读不通 → **exit 1**,stderr 里**指名文件路径和出错的那个键**,而
`--json` 的 stdout 保持干净(错误绝不混进 JSON,Agent 拿到的 stdout 要么是能解析
的结果、要么是空的)。`scan` 与 `coverage` 两个子命令都是这个行为。

**校验全部跑完才动手改内置集合**:一份「`skip_dirs` 合法、`extension_overrides`
非法」的配置不会留下半张改过的表,也不会打印「已加载」。

会被拒绝的写法(每一条 × 两个子命令都有 smoke 断言):非法 JSON、**空文件**、
顶层不是对象、未知键、`skip_dirs` 不是数组 / 元素不是字符串 / 空字符串 / 绝对路径
(POSIX 与 Windows 盘符两种)、`extension_overrides` 不是对象 / 类别值不是字符串 /
类别名不存在 / 扩展名含多个点(`tar.gz` —— 扩展名只取文件名最后一段,写 `gz`)/
扩展名归一化后为空。

### 怎么确认它真的生效了

加载成功时 stderr 打一行(**没有配置文件时这一行不出现**):

```
ℹ️ 已加载覆盖配置 /…/skills/backup-auditor/config.json(skip_dirs 1 条,extension_overrides 1 条)
```

`--json` 里也会多一个 `stats.config`,**只有真加载了配置才会出现**(`scan` 与
`coverage` 都有):

```jsonc
"config": {
  "path": "/…/skills/backup-auditor/config.json",
  "skip_dirs": ["临时暂存"],
  "extension_overrides": {"mybak": "archive"},
  "skipped_dirs": 2          // 审计边界上被拦下的目录数(coverage 是源+备份合计)
}
```

人类可读报告里也会印一段 `⚙ 已加载覆盖配置 …`,把生效的键值列出来。

反过来说:**结果里没有 `stats.config` 这个键 = 没找到配置文件 = 你的覆盖没生效**,
先确认它是不是真的躺在 `backup_auditor.py` 旁边(而不是备份目录底下)。

汇报时请把 `backup_items` / `backup_sets` 与 `skipped_dirs` **一起说**:被剪掉的
目录不计入前两个数字,「备份集怎么变少了」的答案就在第三个里。

## 备份集识别

脚本把备份目录下的项按 `base_key` 聚成「备份集」——剥离名字里的
**日期**(`2024-01-01`/`20240101`/`2024年01月01日`)、**版本号**(`v2`/`备份3`/`(1)`)、
**归档扩展名**(`.tar.gz`/`.zip`/`.dmg`/`.sparsebundle`…)后的公共名。

例:`照片备份_2024-01-01.tar.gz`、`照片备份_2024-02-01.tar.gz` → 同一集 `照片备份`,2 个版本。

## scan 检出项

| 项 | 判定 | action |
|----|------|--------|
| 备份陈旧 | 集内最新版本 mtime 超 `--stale-days` | `review-set`(查备份作业) |
| 单版本备份 | 集内只有 1 份(无历史冗余) | `review-set` |
| 空备份 | 0 字节 / 0 文件,**且读得到** | `investigate`(疑似失败) |
| 无法读取 | `scandir`/`stat` 抛 `OSError`(权限不足、挂载断连) | `investigate`(**体积与文件数未知**) |
| 可轮转旧版本 | 集内版本数 > `--keep`,最旧的若干 | `rotate-out`(归档/删) |

`stats.reclaimable_bytes` = 可轮转旧版本体积合计。

### 「空备份」与「无法读取」必须分开看

一个读不了的目录,`dir_size()` 只能返回 `(0, 0)` —— 数值上与真正的空目录**完全无法区分**。
所以本 skill 用 `unreadable` 标记把它们拆开,分别计入 `stats.empty_items` 与
`stats.unreadable_items`,失败明细在 JSON 顶层的 **`errors`** 键(与 file-sorter /
dedup-finder 同族约定,干净时为 `[]`),人类报告里也有一段 `⚠️ 读取失败 N 项`。

**为什么这件事要紧**:「空备份(疑似失败)」是本 skill 优先级最高的告警,会引导用户去
"修" 或删掉那个备份。如果它其实只是权限不足或挂载断连、里面有完好数据,这条告警就是
误报,而且用户看不出区别。所以拿到「空备份」结论前,先确认 `errors` 是空的。
读不了的项**不计入** `total_size_bytes`(它的大小未知,算 0 会低报,估算又会假精确)。

## coverage 检出项

| 项 | 判定 | action |
|----|------|--------|
| 关键目录无备份 | 源目录名在备份集里找不到匹配 | `add-backup` |
| 有备份但陈旧 | 匹配到但最新版本超 `--stale-days` | `refresh-backup` |
| 孤儿备份 | 备份集在源里找不到对应目录 | `review-orphan`(源已删/改名) |

匹配按归一名(base_key)双向包含,容忍 `data照片` ↔ `照片备份` 这类差异。

## 工作流

### 场景 1:用户说"我的备份还正常吗"

**步骤**:
1. **exec** `python3 backup_auditor.py scan --root <备份目录> --output /tmp/backup.json`
2. 读 JSON `stats`:备份集数、陈旧集、单版本集、空备份、可轮转数、可回收空间
3. 汇报:**重点先说陈旧集**(备份作业可能已停)和**空备份**(疑似失败)
4. **不做写操作** — 问用户要不要出轮转/修复计划

### 场景 2:用户说"旧备份太多,清理一下"

**步骤**:
1. 复用 JSON,过滤 `action == rotate-out`
2. 出轮转清单**预览**:每组保留最近 `--keep` 份,其余列出
3. 用户确认后**优先归档而非删除**:
   ```bash
   mkdir -p "/Volumes/nas/备份/_rotated"
   mv -n "/Volumes/nas/备份/照片备份_2023-01-01.tar.gz" "/Volumes/nas/备份/_rotated/"
   ```
   归档一段时间确认无需回溯,再统一删;极空间也可走 `zs mv` / `zs rm`

   **Windows(PowerShell)**:没有 `mkdir -p` / `mv -n`,等价写法——
   ```powershell
   New-Item -ItemType Directory -Force -Path "Z:\备份\_rotated" | Out-Null
   Move-Item "Z:\备份\照片备份_2023-01-01.tar.gz" "Z:\备份\_rotated\"   # 不加 -Force = 不覆盖
   ```
   中文路径先 `chcp 65001`;完整对照见 [skills/README.md](../README.md) 的
   「Windows 用户:执行阶段要换命令」。极空间用户直接用 `zs mv` 可绕开 shell 差异。
4. **删旧备份前必须确认最新版本完好** — 别把唯一可用备份轮转掉

### 场景 3:用户说"关键目录都备份了吗"

**步骤**:
1. **exec** `python3 backup_auditor.py coverage --source <数据目录> --backup <备份目录>`
2. 读 `stats`:已覆盖 / 缺失 / 陈旧 / 孤儿
3. **缺失(add-backup)优先报告** — 这是最大风险
4. 补备份计划交用户确认后由 Agent / 备份工具执行(本 skill 不做实际备份)

## 审计顺序(严格)

1. 先 `scan` 看整体健康:**`errors` 非空先处理** → 陈旧集 → 空备份 → 单版本 → 可轮转
2. **陈旧/空备份/读不了,都是「备份可能已失效」的信号,优先级最高**(先修复再谈清理)。
   注意三者要分开说:读不了的是**未知**,不是空,别据此建议删除
3. 轮转旧版本前,确认该集最新版本完好且可恢复
4. `coverage` 补缺口:关键目录无备份的,先建备份再优化保留策略
5. 孤儿备份确认源确实已删/改名后再清理
6. 重新 `scan` / `coverage` 验证

## 保留策略速查

| 备份类型 | 建议保留 | 说明 |
|----------|----------|------|
| 每日增量 | 最近 7 天 | `--keep 7` |
| 每周全量 | 最近 4 周 | `--keep 4` |
| 每月归档 | 最近 12 月 | `--keep 12` |
| 关键数据 | ≥2 份 + 异地 | 单版本集要补冗余(3-2-1 原则) |

陈旧阈值按备份频率定:每日备份 `--stale-days 2`,每周 `--stale-days 8`,每月 `--stale-days 35`。

## 关键约束

1. **脚本只读**:无 apply / rm 子命令;轮转/删除由 Agent 在用户确认后执行
2. **轮转优先归档**:旧备份先 `mv` 到 `_rotated/`,确认无需回溯再删
3. **删旧前验新**:轮转/删除任何旧版本前,确认该集最新版本完好可恢复
4. **陈旧 = 修复优先**:陈旧/空备份说明备份作业可能已坏,先修作业再清理
5. **不做实际备份**:本 skill 只审计;补备份交给 NAS 备份工具/Time Machine/rsync
6. **覆盖匹配是启发式**:按归一名双向包含,可能漏配/误配,缺失项人工复核
7. **不跟随符号链接**(顶层与嵌套都是,与其余 8 个 scanner 一致)。跟随会把 `--root`
   **之外**的数据算进 `total_size_bytes` —— 实测过一个只有 3000 字节真实数据的备份根,
   因为里面有个指向外部的链接而被报成 903000 字节;`selflink -> .` 还会重复计数。
   代价是:用符号链接组织的备份库,链接指向的内容不被审计,需要把 `--root` 指到真实路径
8. **读失败不静默**:任何 `scandir`/`stat` 失败都进 `errors`,不会伪装成「0 字节的正常项」

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 备份集拆散 | 命名不统一导致同一目标被分成多集 | base_key 已剥离日期/版本;命名差异大时需人工合并 |
| 稀疏镜像误判空 | `.sparsebundle` 是目录,size 统计可能偏差 | dir_size 递归求和;稀疏bundle 看 file_count 更可靠 |
| mtime 不等于备份时间 | SMB 拷贝可能刷新 mtime | 陈旧判定用 mtime,跨机整理时注意;必要时看名字里的日期 |
| coverage 误报缺失 | 源目录名与备份名差异大 | 双向包含匹配已容忍部分差异;仍误报则人工确认 |
| 超大备份目录卡住 | dir_size 递归慢 | 内置 20000 文件上限;巨型备份集单独审计 |
| 写了 `config.json` 但没生效 | `--json` 里没有 `stats.config`,stderr 也没有 `ℹ️ 已加载覆盖配置` | 它必须躺在 **`backup_auditor.py` 旁边**(按 `__file__` 解析),不是 cwd、也不是 `--root`/`--source`/`--backup` |
| 凭空多出一个孤儿备份 | `coverage` 的 `orphan` +1 | 你 `skip_dirs` 掉了源目录,它对应的备份集就失去了匹配对象 —— 这是正确行为,但汇报时要一起说 |
| `{"gz": "non_archive"}` 没效果 | `照片备份.tar.gz` 仍然被剥成 `照片备份` | `.tar.gz`/`.tar.bz2`/`.tar.xz`/`.sparsebundle` 是硬编码的双扩展名,并且在 `ARCHIVE_EXTS` **之前**匹配 |
| 备份项体积莫名变小 | `total_size_bytes` 下降 | `dir_size` 也吃 `skip_dirs`;对照 `stats.config.skipped_dirs` 与你写的模式 |
| `skip_dirs: ["照片"]` 没保住 `照片备份_2024/` | 备份项还在 | 模式是 fnmatch,不是子串;要写 `照片备份*` 或 `照片*` |

## 已知 gap

- ~~`SKIP_DIRS` 与 `ARCHIVE_EXTS` 是硬编码的,私有归档格式要改脚本~~ →
  **已部分解决**(issue #15):同目录 `config.json` 的 `skip_dirs` /
  `extension_overrides`。仍然硬编码的:`.tar.gz`/`.tar.bz2`/`.tar.xz`/
  `.sparsebundle` 双扩展名列表、`DATE_RE`/`TIME_SUFFIX_RE`/`VERSION_RE`/
  `BACKUP_HINT_RE` 四条识别正则、`dir_size` 的 20000 文件上限、`--keep` 与
  `--stale-days` 的默认值 —— 这些决定「备份集怎么被聚合」,是比扩展名归类大得多的
  设计问题,刻意没有塞进配置层
- **`config.json` 是 per-安装、不是 per-库**:一份安装对应一份配置。要审计不同
  备份根时用不同的 `skip_dirs`,目前只能装两份 skill。加一个 `--config PATH` 是
  自然的后续,但它会引出「两处配置是替换还是叠加」这个新问题
- 不校验备份**可恢复性**(不试解压/挂载),空备份只看 size/file_count
- 不做增量备份的链式完整性检查(需解析备份格式)
- 备份集聚合靠命名启发式,命名极不规范时可能误聚/漏聚
- coverage 的源↔备份匹配是名字包含,不理解备份工具的实际映射关系
- 不识别 NAS 厂商专有备份格式(群晖 HBB、威联通 HBS 等)的内部版本

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root/--source/--backup 不是有效目录` | 确认已挂载:`ls /Volumes/` |
| 备份集全为单版本 | 用户可能用「覆盖式」备份(无历史);确认策略后调 `--keep 1` 免报 |
| coverage 全报缺失 | 源与备份命名体系完全不同;人工建立映射或按子目录逐个核对 |
| dir_size 很慢 | 备份集内文件极多;脚本有文件数上限,超大集单独处理 |
| 报「空备份(疑似失败)」但用户说里面有数据 | 先看 `--json` 的 `errors` 键与 `stats.unreadable_items`:权限不足/挂载断连会走到「无法读取」而不是「空备份」。若 `errors` 为空才真的是空目录 |
| `total_size_bytes` 比预期大很多 | 备份根里可能有指向外部的符号链接?不会 —— 本 skill 不跟随符号链接(见「关键约束」7)。改用 `du -sh` 对照,差值通常是 `SKIP_DIRS` 与点目录被剪掉 |
| 写了 `config.json` 但行为没变 | 先看 `--json` 里有没有 `stats.config` 这个键:**没有**就是文件没躺在 `backup_auditor.py` 旁边;**有**就看 `skipped_dirs` / `extension_overrides` 是不是你写的那几条 |
