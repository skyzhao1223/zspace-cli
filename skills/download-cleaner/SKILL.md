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
| `zs` CLI | 极空间 | `zs mv` / `zs rm`(见 zspace-nas skill) |
| MCP tool | 极空间 + MCP | `move` / `remove`,弹 UI 二次确认 |

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

## 已知 gap

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
