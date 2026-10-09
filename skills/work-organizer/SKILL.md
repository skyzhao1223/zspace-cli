---
name: work-organizer
description: Use when 用户想整理 NAS 上的工作文件 — "帮我整理工作目录"、"根目录一堆散文件"、"文档版本太乱(最终版/final/副本)"、"找临时文件和安装包"、"几年没动的文件归档"、"办公文档按项目/年份归位"。只读扫描(scan)找出根目录散文件、版本标记混乱、同名多版本共存、副本、临时/锁定文件、安装包、过期归档候选;LLM 出 old→new 计划,用户确认后由 Agent 执行。
  触发词:工作文件整理、工作目录归档、文档整理、文件版本混乱、最终版 final、副本文件清理、办公文档归档、按项目整理文件、按年份整理、临时文件清理、安装包清理、过期文件归档、work organizer、organize work files。
  不适用:照片视频按日期整理(走 photo-organizer)、作品集整理(走 portfolio-organizer)、影视命名(走 media-naming)、任意类型混合目录按类型分类归档(走 file-sorter)、内容级重复文件(走 dedup-finder)。
---

# Work Organizer — 工作文件库正向合规整理

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS 挂载的 NAS 共享、
外置硬盘、本地同步目录都行),纯 stdlib 零依赖,不绑定任何品牌 API。

对工作文件库做**正向合规扫描**:定义合规结构(年份/项目 + 规范命名)→ 不匹配即报问题。
**写操作不在脚本里** — 扫描出问题后,LLM 生成 `old → new` 整理计划,
经用户确认后由 Agent 执行(挂载盘 `mkdir`/`mv -n`,极空间也可走 `zs` CLI / MCP tool)。

## Prerequisites

```bash
# macOS 挂载 NAS 共享(Finder → 前往 → 连接服务器)
open smb://<NAS_IP>/工作        # 极空间/群晖/威联通/绿联等均支持 SMB
# 挂载后路径形如 /Volumes/工作

python3 --version               # ≥3.9 即可,无第三方依赖
```

## 快速验证

```bash
python3 work_organizer.py scan --root /Volumes/nas/工作 --sample 200
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 正向合规扫描(只读) |
| `scan --root PATH --json --output F` | JSON 输出 |
| `--strict-naming` | 加查:办公文档需带日期前缀 `YYYYMMDD_` |
| `--archive-years N` | 加查:mtime 超过 N 年 → 归档候选(默认关) |
| `--max-depth N` / `--sample N` / `--top N` | 深度 / 文件数上限 / 每类显示条数 |
| (同目录 `config.json`) | 可选覆盖层:目录白名单 + 扩展名改判,见下「配置覆盖」。**没有这个文件时行为与旧版逐字节一致** |

期望目录结构:

```
工作/
  2024/                                    ← 年份
    官网改版/                              ← 项目
      20240501_官网改版_需求文档_v2.docx    ← 日期_项目_主题_vN
      20240515_官网改版_设计稿_v1.pdf
  合同/                                    ← 职能目录(白名单)
  模板/
  归档/                                    ← 过期文件去向
    2022/…
```

## 配置覆盖(`config.json`,可选)

工作目录里有大量**故意的例外**:一个明知道乱、但就是要原样留着的 `客户资料 副本/`、
一个不属于「年份/项目」结构的 `待办/`,以及内置表里没有的专有扩展名 —— BIM 的
`.rfa` / `.pln`、GIS 的 `.shp`、EDA 的 `.brd`,现在统统落进 `by_type.other`,
`--strict-naming` 也管不到它们。没有这个机制之前,唯一的办法是改脚本里的
`WHITELIST_DIRS` / `DOC_EXTS`,而下次 `pip install -U` 就把它冲掉了。

**没有这个文件时,本 skill 的行为与引入该机制之前逐字节一致** —— 不多一个 JSON
字段、不少一条问题。

### 放在哪

`config.json` 按 **脚本自己所在的目录**(`__file__`)解析,**不是** cwd、**也不是**
`--root`:

```
skills/work-organizer/      ← zs skill 装好后就是你项目里的那一份
├── work_organizer.py
├── config.json             ← 放这里
└── SKILL.md
```

按 `__file__` 解析是刻意的:`--root` 底下万一躺着一个 `config.json`,它只会被当成
一个普通的 `.json` 文件(归 `code` 类,在 root 下还会被报一条「根目录散文件」),
不会被读成配置。配置跟着**安装**走,不跟着**数据**走。

### Schema

两个键都可选;只写一个,另一个不产生任何影响。顶层出现第三个键 → 直接报错退出。

```jsonc
{
  // 目录白名单:命中的目录**不再判目录名**(临时/无语义、副本),子树里的文件照扫照报
  "whitelist_dirs": ["客户资料 副本", "待办", "旧项目/*"],

  // 扩展名改判:键是扩展名(前导点可写可不写、大小写都行),值是本 skill 的类别名
  "extension_overrides": {
    ".rfa": "design",   // Revit:内置表没有,现在落进 other、--strict-naming 也管不到
    "pln":  "design",   // ArchiCAD
    "shp":  "other",    // GIS:明确不当办公文档
    "bak":  "doc"       // 反向也行:把 .bak 从「垃圾可删」救回成正常文档
  }
}
```

| 键 | 类型 | 合并语义 |
|----|------|---------|
| `whitelist_dirs` | `string[]` | **追加**到内置的 `WHITELIST_DIRS`(`合同/` `财务/` `归档/` `模板/` …)。内置条目一条都不删 |
| `extension_overrides` | `object` | **逐扩展名改判**,不是整表替换。该扩展名先从**所有**内置 `DOC_EXTS` / `SHEET_EXTS` / `PPT_EXTS` / `PDF_EXTS` / `ARCHIVE_EXTS` / `INSTALLER_EXTS` / `DESIGN_EXTS` / `CODE_EXTS` / `MEDIA_EXTS` / `JUNK_EXTS` 里摘掉,再放进指定的那一张;**没写到的扩展名完全不受影响** |

`extension_overrides` 的可用类别名就是 `classify_ext()` 的返回值,共 **11 个**,
与 file-sorter 的 15 类、music-organizer 的 5 类**不通用**:

| 值 | 对应的内置集合 | 备注 |
|----|---------------|------|
| `doc` | `DOC_EXTS` | 参与 `--strict-naming` 的日期前缀检查、参与同名多版本分组 |
| `sheet` | `SHEET_EXTS` | 同上 |
| `ppt` | `PPT_EXTS` | 同上 |
| `pdf` | `PDF_EXTS` | 同上 |
| `archive` | `ARCHIVE_EXTS` | |
| `installer` | `INSTALLER_EXTS` | **同时**决定「安装包/镜像混在工作区」那条问题 |
| `design` | `DESIGN_EXTS` | 参与同名多版本分组 |
| `code` | `CODE_EXTS` | |
| `media` | `MEDIA_EXTS` | |
| `junk` | `JUNK_EXTS` | 在 `classify_ext()` **之前**就命中,直接报「可删」 |
| `other` | (无) | 兜底:不属于任何集合 |

写成别的名字(比如 photo-organizer 才有的 `sidecar`、music-organizer 才有的
`audio`)会**报错**,并在报错里列出上面这 11 个。同一份配置在别的 skill 里合法、
在这里非法是**故意的** —— 类别体系本来就不同,静默接受一个本 skill 不认识的类别名
等于静默失效。

**「先摘掉再放进」不是洁癖,是必要的。**`classify_ext()` 是**有序判定阶梯**:

```python
if ext in DOC_EXTS:       return "doc"      # ← doc 在最前面
if ext in SHEET_EXTS:     return "sheet"
…
if ext in CODE_EXTS:      return "code"
if ext in MEDIA_EXTS:     return "media"
return "other"
```

只往 `CODE_EXTS` 里加 `md` 而不从 `DOC_EXTS` 里摘掉,`doc` 那一级先命中,覆盖
**看起来完全没生效**。smoke TEST 7 里这条是真断言的:`{"md": "code"}` 之后
`by_type.doc` 从 17 变 14、`by_type.code` 从 0 变 3,而且 `--strict-naming` 的
问题数从 34 变 33(`2024/官网改版/笔记.md` 不再被要求带日期前缀 —— 那条检查只
对 `doc`/`sheet`/`ppt`/`pdf` 生效)。

`JUNK_EXTS` 比阶梯更靠前(在 `_check_file()` 里第一个判),所以 `{"bak": "doc"}`
是把一个文件从「垃圾可删」救回成正常文档:`stats.junk` 5→4、`stats.files` 24→25、
`by_type.doc` 17→18,并且它开始被当根目录散文件报。反过来 `{"dmg": "junk"}` 让
`installer.dmg` 从「安装包混在工作区」变成垃圾。

### 改不了的

- `JUNK_NAMES` 里的**文件名**(`.DS_Store` / `Thumbs.db`)、`LOCK_PREFIXES`
  (`~$` / `.~` / `~` 开头的锁定文件)、`._*` AppleDouble。
- `BAD_DIR` / `YEAR_DIR_OK` / `COPY_MARK_RE` / `VERSION_CHAOS_RE` / `VN_OK_RE` /
  `DATE_PREFIX_RE` / `DATE_ANYWHERE_RE` 这些**正则**:「最终版/final/定稿 算版本
  混乱」是硬编码的,只能用 `whitelist_dirs` 豁免它所在的**目录名**,豁免不了文件。
- `ARCHIVE_DIR_RE`(名字里含「归档/存档/archive/backup」的目录不算归档候选)。
- `base_stem()` 的归一规则,以及同名多版本的阈值(≥3 个)。

`extension_overrides` 只作用于扩展名。

### 模式锚定规则(`whitelist_dirs`)

与 photo-organizer / music-organizer 用的是**同一个匹配函数** `_cfg_match()`(每个
scanner 里逐字重复,因为 `zs skill` 是逐目录 copytree 安装的,共享模块装不进用户
目录):

- `fnmatch` glob(`*` `?` `[seq]`),`*` **会跨过 `/`**
- **大小写不敏感**(双向:模式大写、目录小写也命中)
- 匹配对象是**相对 `--root` 的目录路径**,用 `/` 连接;永远不含绝对路径
- 命中一个目录 = 命中它的**整棵子树**(下面每一级都命中)
- 命中的两条规则是「或」:① 匹配**整段相对路径**;② 匹配**任意一级目录名**

| 模式 | `<root>/2024/temp/` | `<root>/2024/` 自己 | `<root>/深层/temp/子/` |
|------|:--:|:--:|:--:|
| `temp` | ✅ 规则② | ❌ | ✅ 规则②(任意深度) |
| `2024/temp` | ✅ 规则① | ❌ | ❌(相对路径不是这一段) |
| `2024/*` | ✅ 规则① | ❌ | ❌ |
| `*` | ✅ | ✅ | ✅ |

裸名字与带 `/` 的模式**效果可区分**,这在 smoke 里是两条不同的断言:
`["temp"]` 一次豁免 3 个同名目录(问题数 23→20),`["2024/temp"]` 只豁免 1 个
(23→22)。所以那个必须有个确定答案的问题:**「`2024/*` 匹配 `/vol/2024/x` 吗?」**
(设 `--root /vol`)

- `x` 是**目录** → 匹配,`2024/x/` 及其子树的目录名都不再判。
- 模式永远**锚定在 `--root`**。`/vol/2024`、`Z:\work` 这种绝对路径匹配不上任何
  东西,所以会**直接报错**,而不是让你以为它生效了。

### `whitelist_dirs` 在本 skill 里的语义:**只豁免目录名**

这是与 photo-organizer / music-organizer 的实质差别,来源是内置机制本来就不同 ——
本 skill 的 `WHITELIST_DIRS` 只在 `dir_problems()` 里被读一次:

```python
def dir_problems(name, depth):
    if name.lower() in WHITELIST_DIRS or YEAR_DIR_OK.match(name):
        return []                       # ← 唯一的作用点:豁免目录名
```

所以配置层就是**在这个判定点上追加**,而不是发明一套「别扫这块」的新语义:

| | 本 skill(work-organizer) | photo / music-organizer |
|---|---|---|
| 命中的目录 | 不再报「临时/无语义目录名」「副本目录」 | 同(还额外不注册歌手/专辑) |
| 子树里的文件 | **照扫照报**:根目录散文件、副本、版本混乱、安装包、垃圾、`--archive-years` 全部照常 | **整棵跳过**,不计入 `stats` |
| `stats.dirs` / `stats.files` | **一个都不变** | `files` 会变小 |
| `stats.config.skipped_files` | **没有这个字段**(没有跳过任何文件) | 有 |

也就是说:**把 `temp/` 加进白名单,`temp/b.tmp` 仍然会被报「临时/锁定/垃圾文件」**。
这是刻意的 —— 工作目录里的白名单表达的是「这个目录名我知道不规范,别念叨」,
不是「这块别检查」。要连文件一起免检,目前只能改脚本(记在「已知 gap」里)。

smoke TEST 7 把这条钉住了:`{"whitelist_dirs": ["temp"]}` 之后目录名问题从 6 条
降到 2 条,而 `stats.dirs` / `stats.files` 仍是 16 / 24,`stats.junk` 仍是 5,
且 `temp/b.tmp` 还在垃圾清单里。

### 出错行为

配置文件存在但读不通 → **exit 1**,stderr 里**指名文件路径和出错的那个键**,
而 `--json` 的 stdout 保持干净(错误绝不混进 JSON)。

宁可报错也不「警告后忽略」:键名拼错一个字母(`whitelist_dir`)就会让整份配置
静默失效,而用户看到的现象是「我明明把 `客户资料 副本/` 加进白名单了,它还在报」
—— 这是本机制最坏的失败方式,所以未知键一律拒绝。

**校验全部跑完才动手改内置集合**:一份「`whitelist_dirs` 合法、
`extension_overrides` 非法」的配置不会留下半张改过的表(smoke 里是进程内直接
import 断言的:10 张扩展名表 + `WHITELIST_DIRS` 分毫未动,`CONFIG_WHITELIST`
仍是 `[]`,而且失败路径不会先打印「已加载」)。

会被拒绝的写法(每一条都有 smoke 断言,共 25 种):非法 JSON、**空文件**、顶层
不是对象(字符串/数组两种)、未知键(单键、双键拼错各一种)、`whitelist_dirs`
不是数组 / 元素不是字符串(第 0、第 1 个各一种)/ 空字符串 / 绝对路径(POSIX
`/temp`、反斜杠 `\temp`、Windows 盘符 `Z:\work` 三种)、`extension_overrides`
不是对象 / 类别值不是字符串(`true`、`null` 两种)/ 类别名不存在 / 扩展名含多个点
(`tar.gz` —— 扩展名只取文件名最后一段,写 `gz`)/ 扩展名归一化后为空(`.`、`""`
两种)。

### 怎么确认它真的生效了

加载成功时 stderr 打一行(**没有配置文件时这一行不出现**):

```
ℹ️ 已加载覆盖配置 /…/skills/work-organizer/config.json(whitelist_dirs 1 条,extension_overrides 2 条)
```

`--json` 里也会多一个 `stats.config`,**只有真加载了配置才会出现**:

```jsonc
"config": {
  "path": "/…/skills/work-organizer/config.json",
  "whitelist_dirs": ["客户资料 副本"],
  "extension_overrides": {"rfa": "design", "pln": "design"}
}
```

注意本 skill 的 `stats.config` **没有** `skipped_files`(它不跳过任何文件)。

反过来说:**结果里没有 `stats.config` 这个键 = 没找到配置文件 = 你的覆盖没生效**,
先确认它是不是真的躺在 `work_organizer.py` 旁边(而不是 `--root` 底下)。

## 工作流

### 场景 1:用户说"帮我整理工作目录"

**步骤**:
1. **exec** `python3 work_organizer.py scan --root <挂载路径> --output /tmp/work-issues.json`
   (库很旧时加 `--archive-years 3` 一并找归档候选)
2. 读 JSON:先看 `stats`(散文件/副本/版本混乱/垃圾计数)+ `by_type` + `largest`
3. 汇报问题分布,**不做任何写操作** — 问用户从哪类开始

### 场景 2:用户说"按规范整理"

**步骤**:
1. 复用上一轮 JSON(或重新 scan)
2. 按下方「整理顺序」生成 `old → new` 映射表,**先预览给用户**
   - 散文件的项目归属由 LLM 从文件名推断,拿不准的**单独列出让用户裁决**
   - 版本混乱文件:给出规范新名(如 `最终版` → `_v3` 或日期后缀),逐条确认
3. 用户确认后批量执行(挂载模式):
   ```bash
   mkdir -p "/Volumes/nas/工作/2024/官网改版"
   mv -n "/Volumes/nas/工作/需求文档.docx" \
         "/Volumes/nas/工作/2024/官网改版/20240501_官网改版_需求文档_v1.docx"
   ```
   - **必须 `mv -n`**(不覆盖);同名冲突列出来交用户裁决
   - 极空间用户也可不挂载,改走 zspace-nas skill 的 `zs mkdir` / `zs mv` / `zs rename`
4. 再跑一遍 `scan`,问题数下降才算完成

### 场景 3:用户说"清掉垃圾和没用的"

**步骤**:
1. scan 后过滤「临时/锁定/垃圾文件」「安装包/镜像」两类
2. 删除清单**逐类预览确认**;工作文件删除不可逆,建议先 `mv` 到 `归档/_待删/` 暂存
3. `~$xxx.docx` 是 Office 锁定文件 — 确认没有文档正被打开再删

## 整理顺序(严格)

1. 删除临时/锁定/垃圾(`~$*`、`.tmp`、`.bak`、`.DS_Store`、`._*`)
2. 副本文件 → 与原件比对大小/mtime 后建议删除或转正
3. 根目录散文件 → 按 mtime 年份 + 文件名推断项目,生成归档计划
4. 版本标记规范化:`最终/final/修改版` → 统一 `_vN` 或 `YYYYMMDD_` 前缀
5. 同名多版本组 → 保留最新,旧版移入 `归档/`
6. 安装包/镜像移出工作区(软件目录或删除)
7. 过期文件(`--archive-years`)→ 移入 `归档/YYYY/`
8. 重新 `scan` 验证

每一步都先出映射表预览,确认后再批量执行。

## 命名速查

| 对象 | 规范 | 示例 |
|------|------|------|
| 年份目录 | `YYYY` | `2024` |
| 项目目录 | 简短项目名(可带年份前缀) | `官网改版`、`2024_年度审计` |
| 办公文档 | `YYYYMMDD_项目_主题_vN.ext` | `20240501_官网改版_需求文档_v2.docx` |
| 交付快照 | `YYYYMMDD_项目_主题_已交付.pdf` | `20240601_官网改版_合同_已交付.pdf` |
| 版本 | 只用 `v1`/`v2`/`v2.1` | ❌ `最终版` ❌ `final` ❌ `真的最终版` |
| 过期文件 | `归档/YYYY/…` | `归档/2022/旧项目/…` |

- 禁止:`副本`、`(1)`、`最终版`、`打死不改`、`新建文件夹`
- 白名单职能目录(合同/财务/模板/归档/…)不强制年份结构

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell | 任何 NAS(SMB/NFS 挂载) | `mkdir -p` + `mv -n`(同共享内移动=服务端移动) |
| PowerShell(Windows) | 任何 NAS | `New-Item -ItemType Directory -Force` 建目录 + `Move-Item`(**不加 `-Force`** = 不覆盖);批量 `robocopy /MOV /XC /XN /XO` |
| `zs` CLI | 极空间(免挂载) | `zs mkdir` / `zs mv` / `zs rename` / `zs rm`(见 zspace-nas skill) |
| MCP tool | 极空间 + Agent 支持 MCP | `mkdir` / `move` / `rename` / `remove`,弹 UI 二次确认 |

> **Windows**:上表第一行是 POSIX 命令,PowerShell 里没有 `mv -n` / `mkdir -p`,照着执行会直接失败。完整对照(含中文路径要先 `chcp 65001`、用隔离目录代替删除)见 [skills/README.md](../README.md) 的「Windows 用户:执行阶段要换命令」;极空间用户可直接用 `zs mv` 绕开 shell 差异。

## 关键约束

1. **脚本只读**:无 apply / move / rename / rm 子命令;写操作由 Agent 在用户确认后执行
2. **先预览后执行**:永远先给 `old → new` 表;`mv` 一律带 `-n`
3. **删除必须暂存**:工作文件优先 `mv` 到 `归档/_待删/`,人工确认一段时间后再真删
4. **项目归属拿不准就问**:LLM 不要强行猜项目名,单独列出让用户裁决
5. **锁定文件**:删除 `~$*`/`.~*` 前确认文档没有在别的机器上打开
6. **mtime 是弱证据**:归档判断只看 mtime,重要文件可能被动过但没改 — 归档不是删除
7. **系统/开发目录静默跳过**:`node_modules`、`.git`、`@eaDir`、`#recycle` 等不报告

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| Office 临时文件洪水 | 每个目录都有 `~$xxx` | 已归垃圾类;确认无打开会话后批量删 |
| 同名多版本误合并 | `合同.docx` 在不同项目目录各有一份 | 分组键含目录,跨目录不聚合 |
| 日期前缀误报 | 文档名里带 `2024` 就算有日期 | `--strict-naming` 默认关,开启后宽松匹配全年份 |
| SMB 上 stat 慢 | 大库扫描久 | 先 `--sample 2000` 摸底;`--max-depth 3` 缩小范围 |
| 挂载断连 | 大量 `⚠️ 无法读取` | 重连 SMB 后重扫 |
| 专有扩展名落进 `other` | `.rfa` / `.pln` / `.shp` 不进任何类别,`--strict-naming` 也管不到 | 同目录 `config.json` 写 `"extension_overrides": {"rfa": "design"}`,别改脚本(下次升级会被冲掉) |
| 白名单只免目录名,不免文件 | 把 `temp/` 加进 `whitelist_dirs` 之后 `temp/b.tmp` 仍然报「垃圾可删」 | **这是刻意的**,与内置 `WHITELIST_DIRS` 的语义一致(见「配置覆盖」);要连文件一起免检目前做不到 |
| `config.json` 里写了别的 skill 的类别名 | `❌ 配置文件 … 无效:… 的类别 'audio' 本 skill 不认识` | 本 skill 有 11 个类别(`classify_ext()` 的返回值 + `junk`);别的 skill 的类别名在这里**故意**不通用 |

## 已知 gap

- 项目归属推断靠文件名 + LLM,无语义索引;文件多的库建议分批整理
- 不做内容级去重(hash);同名多版本只按文件名分组
- 白名单职能目录名写死常见集合,公司特有目录名现在可以用同目录 `config.json` 的
  `whitelist_dirs` 追加(issue #15),不必再改脚本;但它**只豁免目录名**,子树里的
  文件照扫照报 —— 要「这块整体别检查」得先决定语义,目前没有
- **`config.json` 改不了的**:`BAD_DIR` / `VERSION_CHAOS_RE` / `COPY_MARK_RE` /
  `DATE_*_RE` 这些正则、`LOCK_PREFIXES`、`ARCHIVE_DIR_RE`、`base_stem()` 的归一
  规则与同名多版本阈值(≥3)
- **`config.json` 是 per-安装、不是 per-库**:一份安装对应一份配置。多个工作库想用
  不同白名单,目前只能装两份 skill。加一个 `--config PATH` 是自然的后续(要先定
  「与旁边那份是替换还是叠加」)
- 版本组「保留最新」按文件名排序,不保证真语义最新 — 计划里带 mtime 供 LLM 判断

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认 NAS 已挂载:`ls /Volumes/`;或用实际共享路径 |
| 报告里「缺日期前缀」刷屏 | 那是 `--strict-naming` 的效果;不需要就关掉该 flag |
| 归档候选太多 | 调大 `--archive-years`(如 5),或只对 `归档/` 外目录处理 |
| 写了 `config.json` 但没生效 | `--json` 结果里没有 `stats.config` 这个键、stderr 也没有 `ℹ️ 已加载覆盖配置` 那一行 | 它必须躺在 **`work_organizer.py` 旁边**(按 `__file__` 解析),不是 cwd、也不是 `--root`;`--root` 底下的 `config.json` 只会被当成一个普通的 `.json` 文件(归 `code`) |
| `❌ 配置文件 … 无效:未知键 whitelist_dir` | 键名拼错了(少个 `s`)。**刻意报错而不是警告后忽略** —— 拼错会让整份配置静默失效,而现象是「我明明加了白名单它还在报」。报错里会列出可用键名 |
