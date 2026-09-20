---
name: dedup-finder
description: Use when 用户想找 NAS 上的重复文件 — "哪些文件重复了"、"帮我找重复照片/视频/文档"、"磁盘被重复文件占满了"、"下载了好几次同一个东西"、"精确去重不要误删"。只读扫描用三级指纹(size → 头部64KB → 全量 sha1)做内容级精确去重,零误报;每组给出「保留哪个/删哪个」的启发式建议,LLM 出删除计划,用户确认后由 Agent 执行(优先 mv 到隔离目录而非直接 rm)。
  触发词:重复文件、去重、找重复、精确去重、内容去重、哪些文件一样、重复照片、重复视频、重复文档、磁盘浪费、空间回收、dedup、duplicate files、find duplicates、remove duplicates。
  不适用:文件名相似但内容不同(本 skill 只认内容 hash);照片按日期整理(走 photo-organizer);工作文件版本混乱(走 work-organizer)——那些是「组织」问题不是「内容重复」问题。
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

```bash
# 全盘找重复,只看 ≥1MB 的文件
python3 dedup_finder.py scan --root /Volumes/nas/data --min-size 1024

# 跨两个目录找重复(照片库 vs 下载区)
python3 dedup_finder.py scan --root /Volumes/nas/照片 --root /Volumes/nas/下载
```

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

**这只是给 LLM 的初排,最终保留哪个必须让用户拍板。**

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell | 任何 NAS | `mv -n` 到隔离目录(推荐)/ `rm`(确认后) |
| `zs` CLI | 极空间 | `zs mv` / `zs rm`(见 zspace-nas skill) |
| MCP tool | 极空间 + MCP | `move` / `remove`,弹 UI 二次确认 |

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
| 跨 root 相对路径 | 多 `--root` 时 path 各自相对 | JSON 每条带 fingerprint,按组处理不受影响 |

## 已知 gap

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
