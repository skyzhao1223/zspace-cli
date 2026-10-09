---
name: music-organizer
description: Use when 用户想整理 NAS 上的音乐库 — "帮我整理音乐"、"歌曲散落一地"、"专辑没有封面"、"文件名带【FLAC】/320K 水印"、"曲目没有编号"、"路径和 ID3 标签对不上"。只读扫描按「歌手/专辑/曲目」三层结构做正向合规校验,内置最小 ID3v2 解析器可选对照路径与标签;LLM 出 old→new 计划,用户确认后由 Agent 执行。
  触发词:音乐整理、音乐库归档、歌曲整理、专辑整理、曲目编号、专辑封面、ID3 标签、音乐文件名、水印文件名、FLAC 标签、歌手专辑结构、整轨镜像、CUE 分轨、music organizer、organize music、fix id3。
  不适用:影视命名(走 media-naming)、照片视频按日期(走 photo-organizer)、内容级去重(走 dedup-finder)、下载区清理(走 download-cleaner)。
---

# Music Organizer — 音乐库正向合规整理

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS),纯 stdlib 零依赖,
不绑定任何品牌 API。内置**最小 ID3v2 解析器**(纯 Python,读 mp3 标签),
无需 mutagen/eyeD3 等第三方库。

对音乐库做**正向合规扫描**:定义合规结构(歌手/专辑/曲目)→ 不匹配即报问题。
**写操作不在脚本里** — 扫描出问题后,LLM 生成 `old → new` 计划,
经用户确认后由 Agent 执行(挂载盘 `mkdir`/`mv -n`,极空间也可走 `zs` CLI / MCP tool)。

## Prerequisites

```bash
open smb://<NAS_IP>/音乐        # 极空间/群晖/威联通/绿联等均支持 SMB
python3 --version               # ≥3.9 即可,无第三方依赖
```

## 快速验证

```bash
python3 music_organizer.py scan --root /Volumes/nas/音乐 --sample 500
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 正向合规扫描(只读) |
| `scan --root PATH --json --output F` | JSON 输出 |
| `--read-tags` | 读 mp3 ID3v2 标签,对照路径与标签一致性(较慢,默认最多 100 文件) |
| `--tag-limit N` | `--read-tags` 时最多读取的文件数 |
| `--max-depth N` / `--sample N` / `--top N` | 深度 / 文件数上限 / 每类显示条数 |
| (同目录 `config.json`) | 可选覆盖层:目录白名单 + 扩展名改判,见下「配置覆盖」。**没有这个文件时行为与旧版逐字节一致** |

期望目录结构:

```
音乐/
  周杰伦/                          ← 歌手
    范特西 (2001)/                 ← 专辑(建议带年份)
      01 爱在西元前.mp3            ← 曲目号前缀
      02 简单爱.mp3
      cover.jpg                    ← 专辑封面
      范特西.cue                   ← 整轨镜像的 CUE(合法)
  合辑/                            ← 白名单功能区(不做歌手/专辑校验)
```

## 配置覆盖(`config.json`,可选)

音乐库里有大量**故意的例外**:一个不按「歌手/专辑」组织的 `播客/` 或 `原声带/`
树、故意留着的整轨镜像 + CUE、以及内置表里没有的无损格式(`.shn` / `.tak` /
`.ofr`)—— 后者现在每个都会被报一条「非音频文件混入专辑」。没有这个机制之前,
唯一的办法是改脚本里的 `WHITELIST_DIRS` / `AUDIO_EXTS`,而下次 `pip install -U`
就把它冲掉了。

**没有这个文件时,本 skill 的行为与引入该机制之前逐字节一致** —— 不多一个 JSON
字段、不少一条问题。

### 放在哪

`config.json` 按 **脚本自己所在的目录**(`__file__`)解析,**不是** cwd、**也不是**
`--root`:

```
skills/music-organizer/      ← zs skill 装好后就是你项目里的那一份
├── music_organizer.py
├── config.json              ← 放这里
└── SKILL.md
```

按 `__file__` 解析是刻意的:`--root` 底下万一躺着一个 `config.json`,它只会被当成
一个普通文件(它在专辑目录里还会被报一条「非音频文件混入专辑」),不会被读成配置。
配置跟着**安装**走,不跟着**数据**走。

### Schema

两个键都可选;只写一个,另一个不产生任何影响。顶层出现第三个键 → 直接报错退出。

```jsonc
{
  // 目录白名单:命中的目录不判合规、不注册歌手/专辑,且**整棵子树跳过**
  "whitelist_dirs": ["播客", "原声带/*", "DJ 混音"],

  // 扩展名改判:键是扩展名(前导点可写可不写、大小写都行),值是本 skill 的类别名
  "extension_overrides": {
    ".shn": "audio",     // Shorten 无损:内置表没有,现在每个都报「非音频混入专辑」
    "tak":  "audio",
    "ofr":  "audio",
    "jpg":  "allowed"    // 反向也行:把封面图降级成「允许的附属文件」
  }
}
```

| 键 | 类型 | 合并语义 |
|----|------|---------|
| `whitelist_dirs` | `string[]` | **追加**到内置的 `WHITELIST_DIRS`(`合辑/` `群星/` `播客/` `有声书/` …)。内置条目一条都不删 |
| `extension_overrides` | `object` | **逐扩展名改判**,不是整表替换。该扩展名先从**所有**内置 `AUDIO_EXTS` / `IMAGE_EXTS` / `ALLOWED_EXTS` / `JUNK_EXTS` 里摘掉,再放进指定的那一张;**没写到的扩展名完全不受影响** |

`extension_overrides` 的可用类别名是**本 skill 自己的** 5 个,与 file-sorter 的
15 类、work-organizer 的 11 类**不通用**:

| 值 | 含义 | 对应的内置集合 |
|----|------|---------------|
| `audio` | 算音频曲目:计入 `stats.audio_files` / `by_ext`,参与曲目号、水印、同曲多格式、歌词配对全部检查 | `AUDIO_EXTS` |
| `image` | 算专辑封面图 | `IMAGE_EXTS` |
| `allowed` | 专辑目录内允许的非音频附属文件,静默放过 | `ALLOWED_EXTS` |
| `junk` | 垃圾/临时文件,报「可删」 | `JUNK_EXTS` |
| `non_audio` | 兜底:不属于任何集合 → 在专辑目录里报「非音频文件混入专辑」 | (无) |

写成别的名字(比如 photo-organizer 才有的 `photo`、work-organizer 才有的 `doc`)
会**报错**,并在报错里列出上面这 5 个。同一份配置在别的 skill 里合法、在这里非法
是**故意的** —— 类别体系本来就不同,静默接受一个本 skill 不认识的类别名等于静默失效。

**「先摘掉再放进」不是洁癖,是必要的。**专辑目录里的判定是**有序阶梯**:

```python
if name in JUNK_NAMES or ext in JUNK_EXTS or name.startswith("._"): …   # junk 最先
if ext in AUDIO_EXTS: …                                                 # 然后音频
if ext in IMAGE_EXTS: …                                                 # 封面
elif ext == "lrc": …
elif ext == "cue": …
elif ext in ALLOWED_EXTS: pass                                          # 静默放过
else: 非音频文件混入专辑
```

只往 `AUDIO_EXTS` 里加 `bak` 而不从 `JUNK_EXTS` 里摘掉,junk 那一级先命中,覆盖
**看起来完全没生效**。smoke TEST 7 里这条是真断言的:`{".BAK": "audio"}` 之后
`notes.bak` 的 `stats.junk` 从 1 变 0、`audio_files` 从 7 变 8、`by_ext` 多出
`bak: 1`,并且它开始被要求有曲目号。

同理 `{"jpg": "allowed"}` 会让 `范特西 (2001)/cover.jpg` 失去封面资格,那张专辑
立刻多报一条「缺专辑封面」—— 这正是「摘掉」那一步在起作用的证据。

### 改不了的

- **`CUE_IMAGE_EXTS` 不在覆盖范围内**(刻意)。它是 `AUDIO_EXTS` 的一个**派生子集**
  (`ape` `flac` `wav` `wv` `tta`,整轨镜像常见容器),而改判语义要求「先从别的表里
  摘掉」—— 那会把 `flac` 从 `is_cue_sheet` 的判定里静默摘走,整轨镜像专辑会突然
  开始被要求逐轨编号。smoke 里断言了:任何合法配置加载之后 `CUE_IMAGE_EXTS`
  分毫不动。
- 专辑分支里的 `lrc` / `cue` 还有**硬编码字面量**(在 `ALLOWED_EXTS` 之前),所以
  `{"lrc": "allowed"}` 是显式无变化;要把 `.lrc` 当曲目得写 `{"lrc": "audio"}`
  (`is_audio` 在专辑分支之前就 return 了)。
- `JUNK_NAMES` 里的**文件名**(`.DS_Store` / `Thumbs.db` / `._*`),以及 `BAD_DIR` /
  `CD_DIR` / `TRACK_NO_OK` / `TRACK_NO_TAIL` / `WATERMARK_RE` 这些**正则**、
  同曲多格式的阈值(≥2 种扩展名)。`extension_overrides` 只作用于扩展名。

### 模式锚定规则(`whitelist_dirs`)

与 photo-organizer / file-sorter 用的是**同一个匹配函数** `_cfg_match()`(每个
scanner 里逐字重复,因为 `zs skill` 是逐目录 copytree 安装的,共享模块装不进用户
目录):

- `fnmatch` glob(`*` `?` `[seq]`),`*` **会跨过 `/`**
- **大小写不敏感**(双向:模式大写、目录小写也命中)
- 匹配对象是**相对 `--root` 的目录路径**,用 `/` 连接;永远不含绝对路径
- 命中一个目录 = 命中它的**整棵子树**
- 命中的两条规则是「或」:① 匹配**整段相对路径**;② 匹配**任意一级目录名**

| 模式 | `<root>/原盘/IMAGE/` | `<root>/原盘/` 自己 | `<root>/深层/原盘/子目录/` |
|------|:--:|:--:|:--:|
| `原盘` | ✅ 规则② | ✅ 规则①② | ✅ 规则②(任意深度) |
| `原盘/*` | ✅ 规则① | ❌ | ❌(相对路径不以 `原盘/` 开头) |
| `深层/原盘/*` | ❌ | ❌ | ✅ 规则① |
| `*` | ✅ | ✅ | ✅ |

所以那个必须有个确定答案的问题:**「`原盘/*` 匹配 `/vol/原盘/x` 吗?」**
(设 `--root /vol`)

- `x` 是**目录** → 匹配,`原盘/x/` 整棵子树被跳过。
- `x` 是**文件** → **不**匹配。文件判定拿到的是它的**父目录层级** `["原盘"]`,
  不含 `/`。也就是说 **`原盘/*` 保不住直接躺在 `原盘/` 里的文件**。
- 想连 `原盘/` 自己一起保住,写 **`原盘`**(不带 `/*`)。
- 模式永远**锚定在 `--root`**。`/vol/原盘`、`Z:\music` 这种绝对路径匹配不上任何
  东西,所以会**直接报错**,而不是让你以为它生效了。

### `whitelist_dirs` 在五个 scanner 里效果**不同**(刻意的,已明写)

同一个 `whitelist_dirs`,效果继承自各自**既有**的内置机制,不是新发明的:

| | file-sorter | photo-organizer | **music-organizer(本 skill)** | work-organizer | portfolio-organizer |
|---|---|---|---|---|---|
| 追加到 | `--keep-dir` | `WHITELIST_DIRS` | `WHITELIST_DIRS` | `WHITELIST_DIRS` | `WHITELIST_DIRS` |
| 命中的目录 | 不出搬家计划 | 不判目录名 | **不判目录名、不注册歌手/专辑** | 不判目录名 | **不算项目** |
| 子树里的文件 | 仍计入 `stats` | **整棵跳过** | **整棵跳过** | **照扫照报** | **照扫照计入 `stats`** |
| 被跳过的文件数 | `stats.protected` | `stats.config.skipped_files` | `stats.config.skipped_files` | (无,不跳过) | (无,不跳过) |
| root 下的散文件 | 不吃白名单 | 不吃白名单 | 不吃白名单 | 不吃白名单 | 不吃白名单 |

本 skill 与 photo-organizer 同属「**这块别扫**」一派:内置 `WHITELIST_DIRS` 在
`depth == 1` 命中时就是一个 `continue`(`合辑/` `歌单/` 里的东西从来不进
`stats.audio_files`),配置层沿用同一个语义,只是**不限层数**、支持 glob、大小写
不敏感。所以配了白名单之后 `stats.artists` / `albums` / `audio_files` 都会**变小**
—— 这是有意的,`skipped_files` 告诉你少算了多少个文件,汇报时请两个数字一起给。

内置 `WHITELIST_DIRS` 命中的目录仍然走原来那个 `continue`(整棵**不走进去**),
所以它里面的文件**不计入** `skipped_files` —— 那个数字只统计**配置**白名单跳过的。

### 出错行为

配置文件存在但读不通 → **exit 1**,stderr 里**指名文件路径和出错的那个键**,
而 `--json` 的 stdout 保持干净(错误绝不混进 JSON)。

宁可报错也不「警告后忽略」:键名拼错一个字母(`whitelist_dir`)就会让整份配置
静默失效,而用户看到的现象是「我明明把 `播客/` 加进白名单了,它还在报」——
这是本机制最坏的失败方式,所以未知键一律拒绝。

**校验全部跑完才动手改内置集合**:一份「`whitelist_dirs` 合法、
`extension_overrides` 非法」的配置不会留下半张改过的表(smoke 里是进程内直接
import 断言的:`AUDIO_EXTS` / `IMAGE_EXTS` / `ALLOWED_EXTS` / `JUNK_EXTS` /
`CUE_IMAGE_EXTS` 五个集合分毫未动,`CONFIG_WHITELIST` 仍是 `[]`,而且失败路径
不会先打印「已加载」)。

会被拒绝的写法(每一条都有 smoke 断言,共 18 种):非法 JSON、**空文件**、顶层
不是对象、未知键、`whitelist_dirs` 不是数组 / 元素不是字符串 / 空字符串 / 绝对
路径(`/播客`、`Z:\music`)、`extension_overrides` 不是对象 / 类别值不是字符串 /
类别名不存在 / 扩展名含多个点(`tar.gz` —— 扩展名只取文件名最后一段,写 `gz`)/
扩展名归一化后为空。

### 怎么确认它真的生效了

加载成功时 stderr 打一行(**没有配置文件时这一行不出现**):

```
ℹ️ 已加载覆盖配置 /…/skills/music-organizer/config.json(whitelist_dirs 1 条,extension_overrides 2 条)
```

`--json` 里也会多一个 `stats.config`,**只有真加载了配置才会出现**:

```jsonc
"config": {
  "path": "/…/skills/music-organizer/config.json",
  "whitelist_dirs": ["播客"],
  "extension_overrides": {"shn": "audio", "tak": "audio"},
  "skipped_files": 12         // 被配置白名单整棵跳过的文件数
}
```

反过来说:**结果里没有 `stats.config` 这个键 = 没找到配置文件 = 你的覆盖没生效**,
先确认它是不是真的躺在 `music_organizer.py` 旁边(而不是 `--root` 底下)。

## 工作流

### 场景 1:用户说"帮我整理音乐库"

**步骤**:
1. **exec** `python3 music_organizer.py scan --root <挂载路径> --output /tmp/music-issues.json`
2. 读 JSON `stats`:歌手/专辑/曲目数、格式分布、缺封面专辑数、缺曲目号、水印名、散曲数
3. 汇报问题分布,**不做写操作** — 问用户从哪类开始

### 场景 2:用户说"按规范整理"

**步骤**:
1. 复用上一轮 JSON(或重新 scan)
2. 按下方「整理顺序」生成 `old → new` 计划,**先预览**:
   - 散曲 → 建 `歌手/专辑/` 结构后归位(歌手/专辑名从标签或文件名推断,拿不准就问)
   - 缺曲目号 → 按专辑曲目顺序补 `NN - ` 前缀
   - 水印名 → 去掉 `【FLAC】`/`320K`/站点名等
3. 用户确认后执行(挂载模式):
   ```bash
   mkdir -p "/Volumes/nas/音乐/周杰伦/范特西 (2001)"
   mv -n "/Volumes/nas/音乐/爱在西元前.mp3" \
         "/Volumes/nas/音乐/周杰伦/范特西 (2001)/01 爱在西元前.mp3"
   ```
   - **必须 `mv -n`**;极空间用户也可走 `zs mkdir` / `zs mv` / `zs rename`
4. 重扫验证

### 场景 3:用户说"路径和标签对不上 / 帮我核对 ID3"

**步骤**:
1. **exec** `scan --root <路径> --read-tags --output /tmp/tags.json`
   (标签对照只读 mp3;`--tag-limit` 控制读取量,SMB 上逐个读文件头较慢)
2. 过滤「路径与标签不符」「缺 ID3 标签」两类
3. **标签与路径冲突时以哪个为准,必须问用户** — 脚本只报差异,不猜
4. 需要写标签(补 TIT2/TPE1/TALB)时:本 skill 不写标签,
   建议用户用 `eyeD3`/`mutagen`/MusicBrainz Picard(见已知 gap)

## 整理顺序(严格)

1. 删除垃圾(`.DS_Store`、`._*`、`.tmp`、下载残留)
2. 移出非音频文件(专辑目录里的 mp4/pdf 等,除非是封面/歌词/CUE/log)
3. 散曲归位:建 `歌手/专辑/` 结构(歌手/专辑从标签或文件名推断,拿不准问用户)
4. 缺歌手层的「伪专辑」→ 补一层歌手目录,或归入 `合辑/`
5. 曲目号规范化:补 `NN - ` 前缀(按专辑顺序;整轨镜像+CUE 跳过)
6. 清理水印/音质标签(`【FLAC】`、`320K`、站点名)
7. 同曲多格式 → 保留最高音质(无损 > 320K > 其他),其余用户确认去留
8. 补专辑封面(cover.jpg 或内嵌 APIC)
9. 重新 `scan` 验证

每一步都先出映射表预览,确认后再批量执行。

## 命名速查

| 对象 | 规范 | 示例 |
|------|------|------|
| 歌手目录 | 歌手名 | `周杰伦` |
| 专辑目录 | `专辑名 (年份)` | `范特西 (2001)` |
| 曲目文件 | `NN 标题.ext` 或 `NN - 标题.ext` | `01 爱在西元前.mp3` |
| 多碟 | `CD1/` `CD2/` 子目录,曲目号各自从 01 起 | `CD1/01 ….mp3` |
| 碟-轨合编 | `D-TT 标题.ext` | `1-01 爱在西元前.flac` |
| 封面 | `cover.jpg` / `folder.jpg` / 内嵌 APIC | — |
| 整轨镜像 | 单 `.flac/.ape/.wv` + `.cue`(合法,不强制分轨) | — |

- 禁止:文件名带 `【】`、`[FLAC]`、`320K`、`www.xxx.com`、站点/公众号水印
- 曲目号在前缀,不在结尾(`简单爱 01.mp3` → `01 简单爱.mp3`)

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell | 任何 NAS | `mkdir -p` + `mv -n`(同共享内移动=服务端移动) |
| PowerShell(Windows) | 任何 NAS | `New-Item -ItemType Directory -Force` 建目录 + `Move-Item`(**不加 `-Force`** = 不覆盖);批量 `robocopy /MOV /XC /XN /XO` |
| `zs` CLI | 极空间(免挂载) | `zs mkdir` / `zs mv` / `zs rename`(见 zspace-nas skill) |
| MCP tool | 极空间 + MCP | `mkdir` / `move` / `rename`,弹 UI 二次确认 |
| 标签写入 | 任意 | **本 skill 不写标签**;用 `eyeD3`/`mutagen`/Picard |

> **Windows**:上表第一行是 POSIX 命令,PowerShell 里没有 `mv -n` / `mkdir -p`,照着执行会直接失败。完整对照(含中文路径要先 `chcp 65001`、用隔离目录代替删除)见 [skills/README.md](../README.md) 的「Windows 用户:执行阶段要换命令」;极空间用户可直接用 `zs mv` 绕开 shell 差异。

## 关键约束

1. **脚本只读**:无 apply / mv / rename 子命令;写操作由 Agent 在用户确认后执行
2. **先预览后执行**:永远先给 `old → new` 表;`mv` 一律带 `-n`
3. **标签只读且仅 mp3**:`--read-tags` 只解析 ID3v2(mp3);FLAC/M4A 标签见已知 gap
4. **标签 vs 路径冲突不自动裁决**:脚本只报差异,以哪个为准由用户定
5. **整轨镜像+CUE 合法**:不强制分轨,只做提示
6. **合辑/歌单等白名单目录**跳过歌手/专辑校验,避免误报

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| ID3v2 版本差异 | v2.2 帧头 3 字节 / v2.3-4 是 4 字节,v2.4 用 synchsafe | 解析器已按版本分支处理 |
| 标签编码混乱 | latin-1/UTF-16/UTF-8 混用,中文乱码 | 按帧 encoding 字节解码;解不出返回空跳过 |
| APIC 大封面 | 内嵌封面几百 KB,`--read-tags` 读得慢 | `--tag-limit` 控制读取量;封面判定目录图片优先 |
| 曲目号误判 | `2001 太空漫游` 被当缺曲目号 | `TRACK_NO_OK` 只认 1-3 位纯数字前缀;年份开头会误报,人工复核 |
| 整轨镜像误报 | 单 flac+cue 被当「缺曲目号」 | `is_cue_sheet` 检测后跳过逐轨校验 |
| 内置表没有的无损格式 | `.shn` / `.tak` / `.ofr` 每个都报「非音频文件混入专辑」 | 同目录 `config.json` 写 `"extension_overrides": {"shn": "audio"}`,别改脚本(下次升级会被冲掉) |
| `config.json` 里写了 `image` 之外的类别名 | `❌ 配置文件 … 无效:… 的类别 'photo' 本 skill 不认识` | 本 skill 只有 5 个类别:`audio` `image` `allowed` `junk` `non_audio`;别的 skill 的类别名在这里**故意**不通用 |

## 已知 gap

- **标签只支持 mp3(ID3v2)**:FLAC(Vorbis Comment)、M4A(MP4 atom)、APE 标签未解析 —
  需第三方库(mutagen),与「零依赖」冲突;要全格式标签用 Picard/eyeD3
- **不写标签**:本 skill 只读+报告;补标签/改标签交给专用工具
- **不联网查专辑信息**:歌手/专辑归类、年份补全靠文件名+标签+LLM 知识,冷门曲目需用户确认
- **曲目顺序**:补 `NN` 前缀的顺序靠标签 TRCK 或 LLM 推断,无权威曲目表时会不准
- **`config.json` 只能改扩展名归类与目录白名单**:`CUE_IMAGE_EXTS`(整轨镜像容器)、
  `BAD_DIR` / `TRACK_NO_OK` / `TRACK_NO_TAIL` / `WATERMARK_RE` 这些正则、专辑分支里
  `lrc`/`cue` 的硬编码字面量、同曲多格式阈值都还是硬编码的,要改得动脚本
- **`config.json` 是 per-安装、不是 per-库**:一份安装对应一份配置。多个音乐库想用
  不同白名单,目前只能装两份 skill。加一个 `--config PATH` 是自然的后续(要先定
  「与旁边那份是替换还是叠加」)

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认已挂载:`ls /Volumes/` |
| `--read-tags` 很慢 | SMB 上逐文件读头;减小 `--tag-limit` 或不开该 flag |
| 大量「缺曲目号」误报 | 库里多用「标题 01」尾缀式;确认约定后按「曲目号在结尾」类批量改前缀 |
| 合辑被拆成一堆歌手 | `合辑/群星/VA` 已在内置白名单;自定义合辑目录名写进同目录 `config.json` 的 `whitelist_dirs`(整棵子树跳过,不必再改脚本) |
| 写了 `config.json` 但没生效 | `--json` 结果里没有 `stats.config` 这个键、stderr 也没有 `ℹ️ 已加载覆盖配置` 那一行 | 它必须躺在 **`music_organizer.py` 旁边**(按 `__file__` 解析),不是 cwd、也不是 `--root`;`--root` 底下的 `config.json` 只会被当成一个普通文件 |
| 加了白名单但 `audio_files` 变小了 | 本 skill 的白名单是「**这块别扫**」(与内置 `合辑/` 相同) | 有意的;`stats.config.skipped_files` 给出被跳过的文件数,汇报时两个数字一起给 |
