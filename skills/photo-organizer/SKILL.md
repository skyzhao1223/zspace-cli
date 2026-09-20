---
name: photo-organizer
description: Use when 用户想整理 NAS/移动硬盘上的照片视频 — "帮我整理照片库"、"手机备份的照片太乱"、"截图和微信图片按日期归档"、"DCIM/IMG 散图归位"、"连拍太多帮我找出来"、"照片目录名乱七八糟"。只读扫描(scan)找出散图、可从文件名提取日期的截图/微信图/相机原图、连拍组、重复副本、垃圾文件,并给出按 YYYY/YYYY-MM 归档的建议;LLM 出 old→new 计划,用户确认后由 Agent 执行 mv/mkdir。
  触发词:照片整理、相册归档、手机照片备份整理、截图归档、微信图片整理、按日期整理照片、照片重命名、连拍精选、DCIM 整理、IMG 散图、照片库扫描、photo organizer、organize photos、sort photos by date。
  不适用:影视库命名规范(走 media-naming)、任意文件重复/孤儿诊断(走 file-organizer)、NAS 连接与通用文件操作(走 zspace-nas 或对应 NAS 的文件工具)。
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
| `zs` CLI | 极空间(免挂载) | `zs mkdir` / `zs mv` / `zs rename`(见 zspace-nas skill) |
| MCP tool | 极空间 + Agent 支持 MCP | `mkdir` / `move` / `rename`,弹 UI 二次确认 |

## 关键约束

1. **脚本只读**:无 apply / move / rename 子命令;写操作由 Agent 在用户确认后执行
2. **先预览后执行**:永远先给 `old → new` 表;`mv` 一律带 `-n`
3. **mtime 是弱证据**:SMB 拷贝可能刷新 mtime;`date_source=mtime` 的必须让用户抽查
4. **sidecar 跟随**:.aae/.xmp 与主文件同批移动,否则编辑记录丢失
5. **删除不可逆**:优先 `mv` 到 `待整理/` 暂存,人工确认后再删
6. **系统元数据静默跳过**:`@eaDir`(群晖)、`#recycle`(威联通)、`.Trashes` 等不报告

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 挂载断连 | scan 中途大量 `⚠️ 无法读取` | 重新连接 SMB 后重扫;脚本不会崩,但结果不完整 |
| AppleDouble 泛滥 | 每个目录一堆 `._xxx` | 已归类为垃圾可删;macOS 写入 SMB 产生,治本用 `defaults write com.apple.desktopservices DSDontWriteNetworkStores true` |
| mmexport 时区 | 微信导出名是 epoch 毫秒 | 脚本按本机时区换算,跨时区整理时注意 ±1 天边界 |
| 大库扫描慢 | SMB 上 stat 是网络往返 | 先 `--sample 2000` 摸底,或 `--max-depth 3` 缩小范围 |
| 相机原图无日期 | IMG_1234.jpg 提不出日期 | 退化用 mtime;要精确需读 EXIF(见已知 gap) |

## 已知 gap

- 不读 EXIF(保持零依赖);相机原图无文件名日期时只能用 mtime 弱推断,
  需要精确拍摄时间可让 Agent 对候选文件跑 `exiftool`/`mdls` 补充
- 不做内容级去重(感知哈希);连拍只按文件名连号识别
- 视频与照片默认同树;要物理分离(照片/视频两库)需用户先定结构再改校验

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认 NAS 已挂载:`ls /Volumes/`;或用实际共享路径 |
| 报告里全是「目录名不符合日期规范」 | 用户库用自定义结构(如按人物);先和用户确认约定再整理 |
| 扫描很慢 | 缩小 `--root` 到某年目录,或 `--sample` 限量 |
