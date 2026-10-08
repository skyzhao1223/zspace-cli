---
name: photo-organizer
description: Use when 用户想整理 NAS/移动硬盘上的照片视频 — "帮我整理照片库"、"手机备份的照片太乱"、"截图和微信图片按日期归档"、"DCIM/IMG 散图归位"、"连拍太多帮我找出来"、"照片目录名乱七八糟"。只读扫描(scan)找出散图、可从文件名提取日期的截图/微信图/相机原图、连拍组、重复副本、垃圾文件,并给出按 YYYY/YYYY-MM 归档的建议;LLM 出 old→new 计划,用户确认后由 Agent 执行 mv/mkdir。
  触发词:照片整理、相册归档、手机照片备份整理、截图归档、微信图片整理、按日期整理照片、照片重命名、连拍精选、DCIM 整理、IMG 散图、照片库扫描、photo organizer、organize photos、sort photos by date。
  不适用:影视库命名规范(走 media-naming)、任意类型混合目录按类型分类归档(走 file-sorter)、内容级重复文件(走 dedup-finder)、NAS 连接与通用文件操作(走 zspace-nas 或对应 NAS 的文件工具)。
---

# Photo Organizer — 照片/视频库正向合规整理

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS 挂载的 NAS 共享、外置硬盘、
本地同步目录都行),纯 stdlib 零依赖,不绑定任何品牌 API。

对照片库做**正向合规扫描**:定义合规结构(年/月或日期_事件)→ 不匹配即报问题。
**写操作不在脚本里** — 扫描出问题后,LLM 生成 `old → new` 归档计划,
经用户确认后由 Agent 执行(挂载盘 `mkdir`/`mv -n`,极空间也可走 `zs` CLI / MCP tool)。

## Prerequisites

```bash
# macOS 挂载 NAS 共享(Finder → 前往 → 连接服务器)
open smb://<NAS_IP>/照片        # 极空间/群晖/威联通/绿联等均支持 SMB
# 挂载后路径形如 /Volumes/照片

python3 --version               # ≥3.9 即可,无第三方依赖

# 可选:只有用 --exif 读拍摄日期时才需要(没装也能跑,会退回 mtime)
exiftool -ver                   # macOS: brew install exiftool
```

## 快速验证

```bash
python3 photo_organizer.py scan --root /Volumes/nas/照片 --sample 200
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 正向合规扫描(只读) |
| `scan --root PATH --json` | stdout JSON |
| `scan --root PATH --output /tmp/issues.json` | 写 JSON 文件 |
| `scan --root PATH --exif` | 用 EXIF 拍摄日期定档(可选,见下「拍摄日期来源」) |
| `--max-depth N` / `--sample N` / `--top N` | 深度 / 文件数上限 / 每类显示条数 |

期望目录结构:

```
照片/
  2024/                              ← 年
    2024-05/                         ← 月(日常照片按月归)
      IMG_1234.HEIC                  ← 相机原始名保留,不强制改
      2024-05-14_101530.jpg
    2024-05-01 五一杭州行/            ← 事件目录(日期前缀 + 事件名)
      IMG_5678.jpg
  截图/                              ← 白名单功能区(可选,不强制按日期)
```

## 拍摄日期来源(--exif)

默认按**文件名**提取日期,提不出就退到 **mtime**。问题在于文件名没日期的相机原图
(`IMG_1234.jpg`)只能拿到 mtime,而 **mtime 会被复制、重新下载、rsync 刷新** ——
这正是"照片被归到错误年月"的主因。

`--exif` 打开一条更强的证据链。**逐级回退,每一级都失败即退,绝不中断扫描**:

| 顺序 | 来源 | 说明 | `date_source` |
|------|------|------|---------------|
| 1 | 文件名 | 始终最优先(纯字符串,不起子进程) | `filename` |
| 2 | `exiftool -DateTimeOriginal` | 真 EXIF 拍摄时间,最可信 | `exif` |
| 3 | `mdls -name kMDItemContentCreationDate` | **仅 macOS**;Spotlight 的内容创建时间 | `mdls` |
| 4 | mtime | 兜底,弱证据 | `mtime(弱,仅参考)` |

第 2/3 级只处理**文件名提不出日期**的文件,而且 exiftool 是**批量**调用的
(一次 200 个路径 + `-json` 输出),不是一张图起一个进程。

> **不加 `--exif` 时行为与旧版逐字节一致**:第 2、3 级根本不会执行,JSON 里也不会
> 多出 `date_sources` / `date_tools` 字段。这是 opt-in,不是默认升级。

### exiftool 是可选的外部依赖

脚本本身仍是**纯 stdlib 零依赖**(skill 硬规则,见 CONTRIBUTING.md);`--exif` 只是
shell out 到系统里已有的工具。**没装不会报错**,只是退回 mtime:

| 系统 | 安装 |
|------|------|
| macOS | `brew install exiftool` |
| Debian / Ubuntu | `sudo apt install libimage-exiftool-perl` |
| Fedora / RHEL | `sudo dnf install perl-Image-ExifTool` |
| Arch | `sudo pacman -S perl-image-exiftool` |
| Windows | `winget install exiftool` 或 `choco install exiftool`;也可从 [exiftool.org](https://exiftool.org/) 下 zip,把 `exiftool.exe` 放进 PATH |

> **Windows / Linux**:`mdls` 是 macOS 独有的,第 3 级在这两个平台上自动跳过。
> 所以非 macOS 想让 `--exif` 真的生效,**必须装 exiftool**,否则等于没开。

### 报告里怎么看

`--exif` 时人类可读输出多出来源统计,每条日期后面也挂一个来源标签:

```
日期来源(--exif): filename 412 | exif 1803 | mdls 0 | mtime 96
外部工具: exiftool(缺失时只能退回 mtime)
⚠️ 有 96 个文件没读到 EXIF,退回了 mtime —— 弱证据,归档前请让用户抽查
...
  IMG_1234.jpg  → 2024-05-03 [exif] (建议 2024/2024-05/)
```

**`mtime` 那一栏是关键**:它是"这个功能其实没生效"的唯一信号。`--exif` 跑完先看这个数
——数字大就说明 exiftool 没装好、或这批图本来就没写 EXIF,别把 mtime 当拍摄日期用。

JSON 侧对应两个字段(同样只在 `--exif` 时出现):

- `stats.date_sources`:四种来源各命中多少
- `stats.date_tools`:探测到的 exiftool / mdls 路径,`null` = 没找到

## 工作流

### 场景 1:用户说"帮我整理照片库"

**步骤**:
1. **exec** `python3 photo_organizer.py scan --root <挂载路径> --output /tmp/photo-issues.json`
2. 读 JSON:先看 `stats.by_month`(可自动归档的月份分布)和问题分类计数
3. 汇报:散文件 N 个、其中 X 个可从文件名提取日期、连拍组 Y 组、垃圾 Z 个
4. **不做任何写操作** — 问用户要不要出归档计划

### 场景 2:用户说"按日期归档"

**步骤**:
1. 复用上一轮 `/tmp/photo-issues.json`(或重新 scan)
2. 按下方「整理顺序」生成 `old → new` 映射表,**先预览给用户**
   - 每条散文件 issue 自带 `date` / `date_source` / `suggested_dir` 字段,直接引用
   - `date_source` 是 `mtime(弱,仅参考)` 的,**单独列出让用户抽查确认**
   - 库里 `IMG_xxxx` 这类无日期相机原图多时,**先加 `--exif` 重扫一轮**再出计划:
     `exif` 可以直接采信,`mdls` 需要抽查,`mtime` 必须逐条确认
3. 用户确认后批量执行(挂载模式):
   ```bash
   mkdir -p "/Volumes/nas/照片/2024/2024-05"
   mv -n "/Volumes/nas/照片/IMG_1234.jpg" "/Volumes/nas/照片/2024/2024-05/"
   ```
   - **必须 `mv -n`**(不覆盖);同名冲突列出来交用户裁决
   - 极空间用户也可不挂载,改走 zspace-nas skill 的 `zs mkdir` / `zs mv`
4. 再跑一遍 `scan`,散文件数明显下降才算完成

### 场景 3:用户说"连拍/重复的帮我找出来"

**步骤**:
1. scan 后过滤「疑似连拍组」「疑似重复副本」两类 issue
2. 列出连号区间(如 IMG_1234-1248 共 15 张)让用户决定精选策略
3. **不自动删** — 删除清单逐组确认,建议先 `mv` 到 `待整理/连拍候选/` 再人工筛

## 整理顺序(严格)

1. 删除垃圾(`.DS_Store` 之外的 `._*` AppleDouble、`.part`/`.td` 下载残留)
2. 移出非媒体文件(混进照片库的 pdf/zip/doc)
3. 散文件按 `suggested_dir` 归档(同月一批;mtime 弱证据的单独确认)
4. 相机原始卷目录(DCIM/100APPLE)内文件逐个提取日期后重组
5. 截图/微信图归档后,可选统一改名 `YYYY-MM-DD_HHMMSS_描述.ext`
6. 连拍组 → 用户精选;重复副本 → 比对后去留
7. 重命名时 **sidecar(.aae/.xmp)必须跟随主文件同批移动**
8. 重新 `scan` 验证

每一步都先出映射表预览,确认后再批量执行。

## 命名速查

| 对象 | 规范 | 示例 |
|------|------|------|
| 年目录 | `YYYY` | `2024` |
| 月目录 | `YYYY-MM` | `2024-05` |
| 事件目录 | `YYYY-MM-DD 事件名` | `2024-05-01 五一杭州行` |
| 相机原图 | 保留原始名 | `IMG_1234.HEIC` |
| 截图/微信图(改名时) | `YYYY-MM-DD_HHMMSS_描述` | `2024-05-01_101530_订单页.png` |
| 视频 | 与照片同树按日期,或独立 `视频/` | — |

- 层级最多三层:年 / 月(或事件)/ 文件,不再深
- HEIC+JPG 双格式、Live Photo、.aae:成组移动,禁止拆散

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell | 任何 NAS(SMB/NFS 挂载) | `mkdir -p` + `mv -n`(同共享内移动=服务端移动,不耗本机带宽) |
| PowerShell(Windows) | 任何 NAS | `New-Item -ItemType Directory -Force` 建目录 + `Move-Item`(**不加 `-Force`** = 不覆盖);批量 `robocopy /MOV /XC /XN /XO` |
| `zs` CLI | 极空间(免挂载) | `zs mkdir` / `zs mv` / `zs rename`(见 zspace-nas skill) |
| MCP tool | 极空间 + Agent 支持 MCP | `mkdir` / `move` / `rename`,弹 UI 二次确认 |

> **Windows**:上表第一行是 POSIX 命令,PowerShell 里没有 `mv -n` / `mkdir -p`,照着执行会直接失败。完整对照(含中文路径要先 `chcp 65001`、用隔离目录代替删除)见 [skills/README.md](../README.md) 的「Windows 用户:执行阶段要换命令」;极空间用户可直接用 `zs mv` 绕开 shell 差异。

## 关键约束

1. **脚本只读**:无 apply / move / rename 子命令;写操作由 Agent 在用户确认后执行
2. **先预览后执行**:永远先给 `old → new` 表;`mv` 一律带 `-n`
3. **mtime 是弱证据**:SMB 拷贝/重新下载都会刷新 mtime;`date_source=mtime` 的必须让用户
   抽查。要更准就加 `--exif`,但 `mdls` 给的也不是 EXIF(见下条)
4. **`mdls` ≠ EXIF**:第 3 级拿到的是 Spotlight 的**内容创建时间**。本机拍/导出的文件它很准,
   拷进 NAS 的文件它可能只是"拷贝那一刻"。比 mtime 强,但**弱于 `exif`**,批量归档前要抽查
5. **sidecar 跟随**:.aae/.xmp 与主文件同批移动,否则编辑记录丢失
6. **删除不可逆**:优先 `mv` 到 `待整理/` 暂存,人工确认后再删
7. **系统元数据静默跳过**:`@eaDir`(群晖)、`#recycle`(威联通)、`.Trashes` 等不报告

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 挂载断连 | scan 中途大量 `⚠️ 无法读取` | 重新连接 SMB 后重扫;脚本不会崩,但结果不完整 |
| AppleDouble 泛滥 | 每个目录一堆 `._xxx` | 已归类为垃圾可删;macOS 写入 SMB 产生,治本用 `defaults write com.apple.desktopservices DSDontWriteNetworkStores true` |
| mmexport 时区 | 微信导出名是 epoch 毫秒 | 脚本按本机时区换算,跨时区整理时注意 ±1 天边界 |
| 大库扫描慢 | SMB 上 stat 是网络往返 | 先 `--sample 2000` 摸底,或 `--max-depth 3` 缩小范围 |
| 相机原图无日期 | IMG_1234.jpg 提不出日期 | 加 `--exif`;没装 exiftool 时看 `date_sources.mtime` 那一栏 |
| `--exif` 没生效 | `date_sources` 里 `exif` 是 0、`mtime` 很大 | exiftool 不在 PATH 里(报告会打 `⚠️ 没找到 exiftool`);按上面的表装一个 |
| `--exif` 在 macOS 上很慢 | 每文件一次 `mdls`,实测约 24ms/张 | mdls 是逐个调的(批量会安错日期,见 photo_organizer.py 注释)。**装 exiftool 就快了**:批量 exiftool 实测约 0.9ms/张,快 27 倍 |
| EXIF 日期是坏的 | `0000:00:00 00:00:00` / 1899 年 | 相机时钟没设过。脚本会判为无效并退到下一档,不会把 0000 年写进归档路径 |

## 已知 gap

- **`--exif` 依赖外部二进制**:脚本自己不解析 EXIF(那是纯 stdlib 硬规则),只 shell out
  到 exiftool / mdls。两者都不在时就只有 mtime,`--exif` 等于没开
- **`mdls` 不是 EXIF**:它给的是 Spotlight 内容创建时间;而且 NAS 挂载盘上的文件通常
  **没被 Spotlight 索引**,mdls 会返回空 → 直接退到 mtime。所以挂载盘场景基本只有
  「exiftool 或 mtime」两档,别指望第 3 级
- **只读 `DateTimeOriginal`**:不回退到 `CreateDate` / `ModifyDate`,也不看 XMP sidecar;
  相机时钟没设过(全零日期)的图只能退档
- 不做内容级去重(感知哈希);连拍只按文件名连号识别
- 视频与照片默认同树;要物理分离(照片/视频两库)需用户先定结构再改校验

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认 NAS 已挂载:`ls /Volumes/`;或用实际共享路径 |
| 报告里全是「目录名不符合日期规范」 | 用户库用自定义结构(如按人物);先和用户确认约定再整理 |
| 扫描很慢 | 缩小 `--root` 到某年目录,或 `--sample` 限量 |
| 加了 `--exif` 但日期没变 | 看 `stats.date_tools`:两个都是 `null` 就是工具没装/不在 PATH。报告会打一行 `⚠️ 没找到 exiftool` |
| `--exif` 报 `⚠️ exiftool 输出不是合法 JSON` | exiftool 版本过旧或被别的东西遮蔽了(`which -a exiftool`);脚本已退档,不影响扫描 |
