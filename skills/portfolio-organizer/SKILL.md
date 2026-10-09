---
name: portfolio-organizer
description: Use when 用户想整理 NAS 上的作品集 — "帮我整理作品集"、"设计项目文件太乱"、"源文件和成品混在一起"、"项目缺封面/说明"、"成品版本太多不知道哪个是最终版"、"大体积 psd/工程文件占空间"。只读扫描(scan)按项目校验:命名年份、封面、说明文件、成品/源文件分离、多版本共存、空项目、大文件;LLM 出 old→new 计划,用户确认后由 Agent 执行。
  触发词:作品集整理、设计作品归档、项目文件整理、成品源文件分离、psd 归档、工程文件整理、项目封面、作品集规范、设计师文件整理、剪辑工程整理、portfolio organizer、organize portfolio。
  不适用:照片视频按日期整理(走 photo-organizer)、办公文档整理(走 work-organizer)、影视命名(走 media-naming)。
---

# Portfolio Organizer — 作品集合规整理

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS 挂载的 NAS 共享、
外置硬盘、本地同步目录都行),纯 stdlib 零依赖,不绑定任何品牌 API。

对作品集做**正向合规扫描**:以「项目」为单位校验结构完整性(命名/封面/说明/
成品源文件分离),不匹配即报问题。
**写操作不在脚本里** — 扫描出问题后,LLM 生成 `old → new` 整理计划,
经用户确认后由 Agent 执行(挂载盘 `mkdir`/`mv -n`,极空间也可走 `zs` CLI / MCP tool)。

## Prerequisites

```bash
# macOS 挂载 NAS 共享(Finder → 前往 → 连接服务器)
open smb://<NAS_IP>/作品集      # 极空间/群晖/威联通/绿联等均支持 SMB
# 挂载后路径形如 /Volumes/作品集

python3 --version               # ≥3.9 即可,无第三方依赖
```

## 快速验证

```bash
python3 portfolio_organizer.py scan --root /Volumes/nas/作品集
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 按项目合规扫描(只读) |
| `scan --root PATH --json --output F` | JSON 输出 |
| `--large-gb N` | 源文件超过 N GB 提示压缩归档(默认 2) |
| `--max-depth N` / `--sample N` / `--top N` | 深度 / 文件数上限 / 每类显示条数 |
| (同目录 `config.json`) | 可选覆盖层:目录白名单 + 扩展名改判,见下「配置覆盖」。**没有这个文件时行为与旧版逐字节一致** |

期望目录结构(每个项目自包含,方便整体拷贝/展示/交付):

```
作品集/
  2024_品牌设计/                 ← 年份_项目名
    cover.jpg                    ← 封面(或 封面.png / preview.jpg)
    README.md                    ← 项目说明(或 说明.txt)
    成品/                        ← 只放导出交付物(jpg/png/pdf/mp4)
      logo_横版_v3.png
    源文件/                      ← 只放工程(psd/ai/fig/blend/prproj)
      logo.psd
  字体/                          ← 白名单功能目录,不做项目校验
```

## 配置覆盖(`config.json`,可选)

作品集里有大量**故意的例外**:一个 root 下的 `客户往来/` 或 `灵感收集/`(它不是
项目,但内置 `WHITELIST_DIRS` 里没有这个名字,于是被当成项目、报「项目名缺年份」
+「缺封面图」+「缺项目说明」三条),以及内置表里没有的工程格式 —— ZBrush 的
`.ztl`、3D 交换的 `.fbx` / `.obj`、Substance 的 `.sbsar`、iPhone 导出的 `.heic`。
没有这个机制之前,唯一的办法是改脚本里的 `WHITELIST_DIRS` / `SOURCE_EXTS`,而下次
`pip install -U` 就把它冲掉了。

**没有这个文件时,本 skill 的行为与引入该机制之前逐字节一致** —— 不多一个 JSON
字段、不少一条问题。

### 放在哪

`config.json` 按 **脚本自己所在的目录**(`__file__`)解析,**不是** cwd、**也不是**
`--root`:

```
skills/portfolio-organizer/      ← zs skill 装好后就是你项目里的那一份
├── portfolio_organizer.py
├── config.json                  ← 放这里
└── SKILL.md
```

按 `__file__` 解析是刻意的:`--root` 底下万一躺着一个 `config.json`,它只会被当成
一个普通文件(`json` 不在任何表里 → `other` 类,在 root 下还会被报一条「根目录
散文件」),不会被读成配置。配置跟着**安装**走,不跟着**数据**走。

### Schema

两个键都可选;只写一个,另一个不产生任何影响。顶层出现第三个键 → 直接报错退出。

```jsonc
{
  // 目录白名单:命中的 root 一级目录**不算项目**(不做任何项目级校验)
  "whitelist_dirs": ["客户往来", "灵感*", "DRAFTS"],

  // 扩展名改判:键是扩展名(前导点可写可不写、大小写都行),值是本 skill 的类别名
  "extension_overrides": {
    ".ztl":  "source",   // ZBrush:内置 SOURCE_EXTS 没有,现在落进 other
    "fbx":   "source",   // 3D 交换格式
    "sbsar": "source",   // Substance 材质
    "heic":  "export",   // 它已在 IMAGE_EXTS 里(能当封面),但 classify_ext 判 other
    "bak":   "source"    // 反向也行:把 .bak 从「垃圾可删」救回成源文件
  }
}
```

| 键 | 类型 | 合并语义 |
|----|------|---------|
| `whitelist_dirs` | `string[]` | **追加**到内置的 `WHITELIST_DIRS`(`素材库/` `模板/` `归档/` `练习/` `assets/` …)。内置条目一条都不删 |
| `extension_overrides` | `object` | **逐扩展名改判**,不是整表替换。该扩展名先从**所有**内置 `SOURCE_EXTS` / `EXPORT_EXTS` / `JUNK_EXTS` 里摘掉,再放进指定的那一张;**没写到的扩展名完全不受影响** |

`extension_overrides` 的可用类别名就是 `classify_ext()` 的返回值 + `junk`,共
**4 个**,与 file-sorter 的 15 类、work-organizer 的 11 类**不通用**:

| 值 | 含义 | 对应的内置集合 |
|----|------|---------------|
| `source` | 源文件/工程:计入 `stats.source_files` + `source_size_bytes`,参与「大体积源文件」「成品目录混入源文件」「成品与源文件混放」 | `SOURCE_EXTS` |
| `export` | 成品:计入 `stats.export_files` + `export_size_bytes`,参与「成品多版本共存」分组 | `EXPORT_EXTS` |
| `junk` | 垃圾/临时文件,在 `classify_ext()` **之前**就命中,报「可删」 | `JUNK_EXTS` |
| `other` | 兜底:不属于任何集合 | (无) |

写成别的名字(photo-organizer 的 `sidecar`、work-organizer 的 `doc`、music-organizer
的 `audio`,甚至**本 skill 有但不开放的** `image`)会**报错**,并在报错里列出上面
这 4 个。静默接受一个本 skill 不认识的类别名等于静默失效。

**「先摘掉再放进」不是洁癖,是必要的。**`classify_ext()` 是**有序判定阶梯**:

```python
if ext in SOURCE_EXTS: return "source"     # ← source 在最前面
if ext in EXPORT_EXTS: return "export"
return "other"
```

只往 `EXPORT_EXTS` 里加 `psd` 而不从 `SOURCE_EXTS` 里摘掉,`source` 那一级先命中,
覆盖**看起来完全没生效**。smoke TEST 7 里这条是真断言的:`{"psd": "export"}` 之后
`source_files` 8→4、`export_files` 10→14,而「成品目录混入源文件」那条问题**消失**
(成品目录里的 `.psd` 现在算成品了);反向 `{"pdf": "source"}` 让
`空格 项目 2020/交付/终稿.pdf` **新出现**在同一条问题里(17 vs 15 条)。
`{"c4d": "export"}` 更能说明它触达的是真判定:`--large-gb 0.000001` 下
「大体积源文件」和「成品与源文件混放」**两条一起翻**。

### 改不了的:`IMAGE_EXTS` 刻意不在覆盖范围内

`IMAGE_EXTS` 与 `EXPORT_EXTS` 有 **8 个扩展名是故意重叠的**
(`jpg` `jpeg` `png` `webp` `gif` `tif` `tiff` `bmp`:一张 png 既算成品、又算封面
候选),而改判语义要求「先从**所有**别的表里摘掉」。把 `image` 放进
`CONFIG_EXT_TABLES` 会造出两个静默陷阱:

| 写法 | 如果 `image` 可覆盖 | 后果 |
|---|---|---|
| `{"png": "export"}` | png 从 `IMAGE_EXTS` 被摘掉 | `cover.png` **静默失去封面资格** → 项目突然多报「缺封面图」 |
| `{"png": "image"}` | png 从 `EXPORT_EXTS` 被摘掉 | `classify_ext("png")` 变 `other` → 成品多版本分组、成品/源文件混放全部失效 |

两个都是「用户以为在细化分类,实际把另一套判定拆了」,正是 issue #15 最忌讳的
静默失效。所以本 skill 只开放 `classify_ext()` 真正返回的那几个类别 + `junk`,
`IMAGE_EXTS`(封面判定 `is_cover()` 用的)保持硬编码。smoke 里两层都钉住了:
`{"png": "export"}` / `{"jpg": "source"}` 之后 `stats.with_cover` 仍是 3、
`is_cover("cover.png")` 仍为 True;进程内 import 也断言 `IMAGE_EXTS` 九个元素
分毫未动。

代价是:**封面格式不能通过配置扩展**(比如 `.avif` 封面)。这记在「已知 gap」里,
要改得动脚本 —— 那属于「内置表的内容」,不是覆盖机制。

另外这些也改不了:`JUNK_NAMES`(`.DS_Store` / `Thumbs.db`)、`._*` / `~$` 前缀、
`FINAL_DIR_NAMES`(哪些目录名算「成品」)、`SOURCE_DIR_NAMES`、`COVER_PAT` /
`README_PAT` / `YEAR_IN_NAME_RE` / `COPY_MARK_RE` 这些**正则**、`base_stem()` 的
归一规则与成品多版本阈值(≥3)。大文件阈值用 `--large-gb` 调,不走配置。

### 模式锚定规则(`whitelist_dirs`)

匹配函数与其他四个 scanner 是**同一个** `_cfg_match()`(每个 scanner 里逐字重复,
因为 `zs skill` 是逐目录 copytree 安装的,共享模块装不进用户目录):

- `fnmatch` glob(`*` `?` `[seq]`),`*` **会跨过 `/`**
- **大小写不敏感**(双向:模式大写、目录小写也命中)
- 匹配对象是**相对 `--root` 的目录路径**,用 `/` 连接;永远不含绝对路径
- 命中的两条规则是「或」:① 匹配**整段相对路径**;② 匹配**任意一级目录名**

**但本 skill 只在 root 下一级问这个问题**(内置 `WHITELIST_DIRS` 的既有语义就是
「root 下的非项目功能目录」),所以判定拿到的相对路径**只有一级**:

| 模式 | 效果 |
|------|------|
| `客户往来` / `DRAFTS` / `drafts` | ✅ 该 root 一级目录不算项目 |
| `灵感*` / `2024_*` | ✅ glob 可用 |
| `*` | ✅ 所有 root 一级目录都不算项目(`stats.projects` 归 0) |
| `练习稿/logo.sketch`、`2023_品牌视觉/成品`、`深层/*` | ❌ **含 `/` 的模式永远不命中** |

含 `/` 的模式**加载成功、会在 stderr 提示、会出现在 `stats.config.whitelist_dirs`
里,但一条都不会命中** —— 因为单级相对路径配不上带 `/` 的模式。这一点 smoke TEST 7
用 4 个这样的模式钉住了(`stats.projects` 仍是 8、问题数与基线逐条相同)。要
「项目内某个子目录别检查」目前做不到,记在「已知 gap」里;**不要**写这种模式然后
以为它生效了。

模式永远**锚定在 `--root`**。`/vol/客户往来`、`Z:\art` 这种绝对路径匹配不上任何
东西,所以会**直接报错**,而不是让你以为它生效了。

### `whitelist_dirs` 在本 skill 里的语义:**这个目录不是项目**

| | 本 skill(portfolio-organizer) | photo / music-organizer | work-organizer |
|---|---|---|---|
| 命中的目录 | **不注册为项目**:不做缺年份/缺封面/缺说明/成品混放/多版本任何一项校验 | 不判目录名(music 还不注册歌手/专辑) | 不判目录名 |
| 子树里的文件 | **照扫、照计入 `stats.files` / `total_size_bytes` / `largest`**,只是不进 `proj.files` | **整棵跳过**,不计入 `stats` | 照扫照报 |
| `stats.source_files` / `export_files` | **会变小**(白名单目录里的文件不再计入源/成品) | — | — |
| `stats.config.skipped_files` | **没有这个字段**(没有跳过任何文件) | 有 | 没有 |
| 生效范围 | **只有 root 下一级** | 任意深度 | 任意深度 |

也就是说:`{"whitelist_dirs": ["练习稿"]}` 之后 `stats.projects` 8→7、问题数
16→15、`source_files` 8→7,但 `stats.files` **仍是 28**、`stats.dirs` **仍是 13**、
垃圾与根目录散文件一条不少。这与内置 `WHITELIST_DIRS` 对 `素材库/` 的处理**完全
一致**(它本来就只 `return`、不减少 `stats.files`),不是新发明的语义。

### 出错行为

配置文件存在但读不通 → **exit 1**,stderr 里**指名文件路径和出错的那个键**,
而 `--json` 的 stdout 保持干净(错误绝不混进 JSON)。

宁可报错也不「警告后忽略」:键名拼错一个字母(`whitelist_dir`)就会让整份配置
静默失效,而用户看到的现象是「我明明把 `客户往来/` 加进白名单了,它还在报缺封面」
—— 这是本机制最坏的失败方式,所以未知键一律拒绝。

**校验全部跑完才动手改内置集合**:一份「`whitelist_dirs` 合法、
`extension_overrides` 非法」的配置不会留下半张改过的表(smoke 里是进程内直接
import 断言的:`SOURCE_EXTS` / `EXPORT_EXTS` / `JUNK_EXTS` / `IMAGE_EXTS` /
`WHITELIST_DIRS` 五个集合分毫未动,`CONFIG_WHITELIST` 仍是 `[]`,而且失败路径
不会先打印「已加载」)。

会被拒绝的写法(每一条都有 smoke 断言,共 26 种):非法 JSON、**空文件**、顶层
不是对象(字符串/数组两种)、未知键(单键、双键拼错各一种)、`whitelist_dirs`
不是数组 / 元素不是字符串(第 0、第 1 个各一种)/ 空字符串 / 绝对路径(POSIX、
反斜杠、Windows 盘符三种)、`extension_overrides` 不是对象 / 类别值不是字符串
(`true`、`null` 两种)/ 类别名不存在(含**本 skill 刻意不开放的** `image`)/
扩展名含多个点(`tar.gz` —— 扩展名只取文件名最后一段,写 `gz`)/ 扩展名归一化后
为空(`.`、`""` 两种)。

### 怎么确认它真的生效了

加载成功时 stderr 打一行(**没有配置文件时这一行不出现**):

```
ℹ️ 已加载覆盖配置 /…/skills/portfolio-organizer/config.json(whitelist_dirs 1 条,extension_overrides 2 条)
```

`--json` 里也会多一个 `stats.config`,**只有真加载了配置才会出现**:

```jsonc
"config": {
  "path": "/…/skills/portfolio-organizer/config.json",
  "whitelist_dirs": ["客户往来"],
  "extension_overrides": {"ztl": "source", "fbx": "source"}
}
```

注意本 skill 的 `stats.config` **没有** `skipped_files`(它不跳过任何文件)。

反过来说:**结果里没有 `stats.config` 这个键 = 没找到配置文件 = 你的覆盖没生效**,
先确认它是不是真的躺在 `portfolio_organizer.py` 旁边(而不是 `--root` 底下)。
如果 `stats.config` 在、但效果没有,先看模式里是不是**含 `/`**(本 skill 只认
root 一级),再看类别名是不是**本 skill 的** 4 个之一。

## 工作流

### 场景 1:用户说"帮我整理作品集"

**步骤**:
1. **exec** `python3 portfolio_organizer.py scan --root <挂载路径> --output /tmp/pf-issues.json`
2. 读 JSON `stats`:项目数、封面/说明覆盖率、源文件与成品体积占比、最大文件
3. 按项目汇报缺口(缺封面 N 个、混放 M 个、多版本 K 组),**不做写操作** —
   问用户从哪个项目开始

### 场景 2:用户说"按规范整理项目 X"

**步骤**:
1. 复用上一轮 JSON,过滤该项目 issues
2. 生成 `old → new` 计划,**先预览**:
   - 混放文件 → `mkdir 成品/ 源文件/` + 按扩展名分拣
   - 多版本 → 保留最终版,旧版移入 `源文件/历史版本/` 或删除(用户选)
   - 缺封面 → 建议从成品里挑一张复制/改名为 `cover.jpg`(列候选让用户挑)
   - 缺说明 → LLM 按项目内容起草 README.md 给用户过目
3. 用户确认后执行(挂载模式):
   ```bash
   mkdir -p "/Volumes/nas/作品集/2024_品牌设计/成品"
   mv -n "/Volumes/nas/作品集/2024_品牌设计/logo.png" \
         "/Volumes/nas/作品集/2024_品牌设计/成品/"
   ```
   - **必须 `mv -n`**;极空间用户也可走 `zs mkdir` / `zs mv`(见 zspace-nas skill)
4. 重扫该项目目录验证

### 场景 3:用户说"哪些工程文件太占空间"

**步骤**:
1. `scan --large-gb 1`(或更小)看「大体积源文件」列表 + `stats.largest`
2. 建议:旧项目工程压缩(`zip -r` 后删原目录)或移到冷存储/外置盘
3. 压缩/删除清单逐项确认 — 工程文件不可再生,禁止自动删

## 整理顺序(严格)

1. **`errors` 非空先处理**(权限/挂载断连 —— 先恢复可读,再谈清理)
2. 删除垃圾(`.DS_Store`、`._*`、`.tmp`、锁定文件)
3. 根目录散文件 → 归入对应项目(拿不准就问用户)
4. 空项目目录(**读得到且确实无文件**;读不了的在 `errors` 里,别删)→ 确认后删除或补内容
5. 项目改名:补年份前缀 `YYYY_项目名`(改名前列对照表)
6. 每个项目内拆分 `成品/` 与 `源文件/`(按扩展名分拣,预览后执行)
7. 成品多版本 → 用户选定最终版,其余移入 `源文件/历史版本/`
8. 补封面(从成品挑)、补说明(LLM 起草,用户过目)
9. 大体积源文件 → 压缩或外置归档
10. 重新 `scan` 验证

每一步都先出映射表预览,确认后再批量执行。

## 命名速查

| 对象 | 规范 | 示例 |
|------|------|------|
| 项目目录 | `YYYY_项目名` | `2024_品牌设计` |
| 封面 | `cover.*` / `封面.*` / `preview.*` | `cover.jpg` |
| 说明 | `README.md` / `说明.txt` | `README.md` |
| 成品子目录 | `成品/`(或 final/export/output) | — |
| 源文件子目录 | `源文件/`(或 source/工程/素材) | — |
| 成品文件 | `项目_内容_规格_vN.ext` | `logo_横版_3000px_v3.png` |
| 禁止 | `最终版`、`(1)`、`副本`、无年份项目名 | — |

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell | 任何 NAS(SMB/NFS 挂载) | `mkdir -p` + `mv -n`(同共享内移动=服务端移动) |
| PowerShell(Windows) | 任何 NAS | `New-Item -ItemType Directory -Force` 建目录 + `Move-Item`(**不加 `-Force`** = 不覆盖);批量 `robocopy /MOV /XC /XN /XO` |
| `zs` CLI | 极空间(免挂载) | `zs mkdir` / `zs mv` / `zs rename`(见 zspace-nas skill) |
| MCP tool | 极空间 + Agent 支持 MCP | `mkdir` / `move` / `rename`,弹 UI 二次确认 |

> **Windows**:上表第一行是 POSIX 命令,PowerShell 里没有 `mv -n` / `mkdir -p`,照着执行会直接失败。完整对照(含中文路径要先 `chcp 65001`、用隔离目录代替删除)见 [skills/README.md](../README.md) 的「Windows 用户:执行阶段要换命令」;极空间用户可直接用 `zs mv` 绕开 shell 差异。

## 关键约束

1. **脚本只读**:无 apply 子命令;写操作由 Agent 在用户确认后执行
2. **先预览后执行**:永远先给 `old → new` 表;`mv` 一律带 `-n`
3. **工程文件不可再生**:源文件(psd/blend/prproj)禁止自动删;压缩前确认已导出成品
4. **封面/说明是建议不是强制**:用户库有自己的展示习惯时,先确认约定再整理
5. **项目级判定只看一级结构**:成品/源文件目录名用白名单匹配(中英双语),
   自定义目录名需改 `FINAL_DIR_NAMES` / `SOURCE_DIR_NAMES`
6. **白名单功能目录**(字体/素材库/模板…)跳过项目校验,避免误报
7. **读失败不静默**:任何 `scandir`/`stat` 失败都进顶层 `errors` 键(干净时 `[]`),
   并单列 `unreadable_projects`/`unreadable_files`;读不了的项目**不会**伪装成「空项目目录」

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 目录名语言混用 | `成品`/`Final`/`导出` 并存 | 白名单已含中英常见名;报告仍提示统一 |
| 版本判定 | `logo_v1/v2/v3` 全报多版本 | ≥3 个同 base 才报;保留策略交用户 |
| 大文件误报 | 视频素材当源文件 | `素材` 目录在白名单功能目录/源目录名下,不做大文件提示 |
| SMB 上 stat 慢 | 大库扫描久 | `--sample 2000` 摸底;`--max-depth 3` 缩小范围 |
| 挂载断连 | 大量 `⚠️ 无法读取`,JSON 顶层 `errors` 非空 | 重连 SMB 后重扫;这些目录单列 `unreadable_projects`,**不会**被报成「空项目目录(建议删除)」 |
| 冷门工程格式落进 `other` | `.ztl` / `.fbx` / `.sbsar` 不算源文件,不计入 `source_size_bytes` | 同目录 `config.json` 写 `"extension_overrides": {"ztl": "source"}`,别改脚本(下次升级会被冲掉) |
| root 下的非项目目录被当成项目 | `客户往来/` 报「项目名缺年份 + 缺封面 + 缺说明」三条 | `config.json` 写 `"whitelist_dirs": ["客户往来"]`(与内置 `素材库/` 同一个机制) |
| 白名单模式里带了 `/` | 配置加载成功、`stats.config` 也在,但一条都不命中 | **本 skill 只在 root 下一级问这个问题**,单级相对路径配不上带 `/` 的模式。写裸名字或 glob(`灵感*`) |
| `{"png": "image"}` 报错 | `❌ … 的类别 'image' 本 skill 不认识` | 刻意不开放:`IMAGE_EXTS` 与 `EXPORT_EXTS` 有 8 个扩展名**故意重叠**,可覆盖会让 `cover.png` 静默失去封面资格。可用类别只有 `source` `export` `junk` `other` |

## 读失败:「无法读取」≠「空项目」

**为什么这件事要紧**:「空项目目录(建议删除或补充内容)」是删除引导。一个因权限或
挂载断连而暂时读不到的项目,如果被判成「空」,用户会据此删掉一个其实完好的项目。
修复前两者的 JSON 输出**逐字节相同**(`scandir` 失败只打一行 stderr,JSON 里没有
`errors` 键);file-sorter / dedup-finder / backup-auditor 早就有 errors 通道,
本 skill 是家族里最后一个补上的(跨 scanner 一致性审计 F5,与 backup-auditor F1 同族)。

现在的行为:

- root 直下的项目读不了 → 专门 issue「无法读取(权限不足或挂载断连),内容未知 ——
  不等于空项目,别据此删除」,`stats.unreadable_projects` 单列;**不再**判「空项目目录」
- 任意层级目录/文件读不了 → 顶层 `errors` 键(`{path, error}`,最多 50 条);
  文件读不到大小时**不计入** `total_size_bytes`(按 0 计会低报),`stats.unreadable_files` 单列
- 干净树 `errors` 恒为 `[]`(键恒在,家族约定)
- 拿到「空项目目录」结论前,先确认 `errors` 是空的

## 已知 gap

- 不识别设计软件版本兼容(如 PSD 是否含分层),只做结构校验
- 「最终版」语义靠用户确认,脚本只按文件名分组
- 白名单目录名与源文件/成品扩展名集合有限,冷门软件格式现在可以用同目录
  `config.json` 的 `whitelist_dirs` / `extension_overrides` 追加(issue #15),不必再
  改脚本;**但 `IMAGE_EXTS`(封面判定)刻意不可覆盖** —— 它与 `EXPORT_EXTS` 有 8 个
  扩展名故意重叠,「先摘掉再放进」的语义会让 `cover.png` 静默失去封面资格。所以
  `.avif` 封面这类需求仍然得改脚本
- **白名单只作用于 root 一级**(继承内置 `WHITELIST_DIRS` 的既有语义):「项目内某个
  子目录别做校验」做不到,含 `/` 的模式永远不命中
- **`config.json` 是 per-安装、不是 per-库**:一份安装对应一份配置。多个作品集想用
  不同白名单,目前只能装两份 skill。加一个 `--config PATH` 是自然的后续(要先定
  「与旁边那份是替换还是叠加」)
- 项目跨年改版(2023 → 2024 迭代)视为两个项目,合并策略交用户

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认 NAS 已挂载:`ls /Volumes/`;或用实际共享路径 |
| 所有项目都报「缺封面」 | 用户用子目录放封面(如 `预览/`);确认约定后调整白名单或跳过该项 |
| 扫描很慢 | 缩小 `--root` 到单个项目,或 `--sample` 限量 |
| 写了 `config.json` 但没生效 | `--json` 结果里没有 `stats.config` 这个键、stderr 也没有 `ℹ️ 已加载覆盖配置` 那一行 | 它必须躺在 **`portfolio_organizer.py` 旁边**(按 `__file__` 解析),不是 cwd、也不是 `--root`;`--root` 底下的 `config.json` 只会被当成一个普通文件(归 `other`) |
| `stats.config` 在、但白名单没效果 | 模式里含 `/`,或者写的是项目**内部**的子目录 | 本 skill 只认 root 一级目录名(裸名字或 glob);要豁免整个项目就写它在 root 下的那一层名字 |
| 某项目报「无法读取(权限不足或挂载断连)」 | 不等于空项目:内容未知。先恢复可读(chmod / 重连 SMB)再重扫,**别据此删除**;明细在 `--json` 顶层 `errors` 键 |
