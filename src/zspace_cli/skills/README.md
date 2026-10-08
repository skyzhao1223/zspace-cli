# Skills（Agent 工作流）

> 👋 第一次接触「挂载」「AI 助手」「skill」这些词？先读[《新手指南》](../docs/beginner-guide.zh.md)——零代码基础，手把手装好（[English](../docs/beginner-guide.md)）。

把这些 Skill 复制到你的 Agent 项目后，用自然语言就能整理你的 NAS。两类定位：

- **`zspace-nas`**：极空间**零配置底座**——读桌面客户端登录态直连 API，无需挂载、无需密码
- **其余 9 个整理/审计 skill**：**跨 NAS 通用**——扫描脚本纯 stdlib 零依赖，跑在**挂载路径**（SMB/NFS）上，极空间 / 群晖 / 威联通 / 绿联等都能用；写操作极空间可走 `zs` 命令，其他品牌走挂载盘 `mv`

## 30 秒上手

### 极空间用户（零配置，免挂载）

```bash
# 1. 前提：macOS 极空间桌面客户端已登录并运行
pip install zspace-cli
zs check

# 2. 复制 skill 到你的项目
zs skill --list                            # 先看有哪些
zs skill ~/your-project/skills/            # 全装（--only a,b 可选装）

# 3. 对你的 Agent 说
# 「列出我 NAS 上 /sata11/my/data 的文件」
```

### 其他 NAS 用户（群晖 / 威联通 / 绿联…）

```bash
# 1. 挂载 NAS 共享（macOS Finder → 前往 → 连接服务器）
open smb://<NAS_IP>/照片                    # 挂载后即 /Volumes/照片

# 2. 复制 skill 到你的项目
#    （zspace-cli 只当安装器用；9 个整理 skill 不调极空间 API、不需要客户端）
pip install zspace-cli && zs skill ~/your-project/skills/
#    或直接 clone 本仓库，复制 skills/<name>/ 目录

# 3. 对你的 Agent 说
# 「帮我整理 /Volumes/照片 里的照片，按日期归档」
```

## 对 Agent 说什么（触发示例）

| 你说 | 触发 |
|------|------|
| 「给我出个 NAS 存储报告」「空间都被什么占了」 | **nas-report**（入口，会推荐下一步） |
| 「这目录什么都往里丢，帮我按类型分个类」「照片视频文档安装包全混在一层」 | **file-sorter** |
| 「帮我整理照片库，按拍摄日期归档」「截图太多了」 | photo-organizer |
| 「整理音乐库」「歌曲文件名带水印」「补曲目号」 | music-organizer |
| 「工作目录太乱帮我归档」「文档全是最终版final」 | work-organizer |
| 「整理我的作品集」「设计项目缺封面」 | portfolio-organizer |
| 「清理下载目录」「安装包种子太多了」 | download-cleaner |
| 「找找重复文件」「回收点空间」 | dedup-finder |
| 「我的备份还新鲜吗」「哪些旧备份能删」 | backup-auditor |
| 「列出 / 移动 / 重命名 NAS 上的文件」（极空间） | zspace-nas |

所有整理 skill 都是同一套安全模式：**只读扫描 → LLM 出 old→new 计划 → 你确认 → Agent 执行**，删除一律先隔离再真删。

> 💡 **「清重复 + 分类」组合拳**（最常见的需求）：[dedup-finder](dedup-finder/SKILL.md) 内容级去重并隔离副本 + [file-sorter](file-sorter/SKILL.md) 按类型归档。**推荐先去重**——省掉白搬那些即将删掉的字节，也少产生撞名待确认项；但**反过来也不会漏检**，去重是内容级的、与目录结构无关（实测两种顺序检出同样的重复组）。file-sorter 会报 `疑似副本共 X MB（占待搬体积 N%）`，按这个数字决定值不值得先跑一趟去重。

### Windows 用户：执行阶段要换命令

**扫描阶段跨平台**——9 个整理 skill 的脚本都是纯 stdlib，只要 NAS 挂载成本地盘（`Z:\` 这样的映射盘）就能跑，`python xxx.py scan --root Z:\data` 即可。CI 的 smoke 测试在 **ubuntu + windows 双跑**（Python 3.9），脚本自身会强制 stdout 为 UTF-8，所以 Windows 上被 AI 助手捕获输出时，中文报告不会触发 `UnicodeEncodeError`。

**执行阶段**（Agent 按计划搬文件）用的是 POSIX 的 `mv -n` / `mkdir -p`，PowerShell 里没有这两个命令——每个 SKILL.md 的「写操作通道」都给了 Windows 对照行，汇总如下：

| 意图 | macOS / Linux | Windows PowerShell |
|------|---------------|--------------------|
| 建目录 | `mkdir -p "图纸"` | `New-Item -ItemType Directory -Force "Z:\data\图纸"` |
| 移动且不覆盖 | `mv -n "x.dwg" "图纸/"` | `Move-Item "Z:\data\x.dwg" "Z:\data\图纸\"`（**不要加 `-Force`**，加了就是覆盖） |
| 删除＝先隔离 | `mv -n "x" "_quarantine/"` | `Move-Item "Z:\data\x" "Z:\data\_quarantine\"` |
| 批量同类 | `mv -n *.dwg "图纸/"` | `robocopy "Z:\data" "Z:\data\图纸" *.dwg /MOV /XC /XN /XO`（先 `/L` 空跑看清单） |

中文路径先切 UTF-8，否则目录名会乱码：`chcp 65001 > $null`。

> 极空间用户在 Windows 上更省事：直接用 `zs mv` / `zs mkdir`（走 API，不依赖映射盘和 shell 转义）。注意 `zs` 的**零配置登录态读取**在 macOS 上最稳，Windows/Linux 是尽力支持（见 [issue #7](https://github.com/skyzhao1223/zspace-cli/issues/7)）；整理 skill 本身不需要登录态。

## Skill 清单

**先跑 [nas-report](nas-report/SKILL.md) 看清全局，它会告诉你该用哪个专项 skill。**

### 底座 / 入口

| Skill | 能做什么 |
|-------|----------|
| **zspace-nas** | 通用文件管理（列目录 / 重命名 / 移动 / 复制 / 删除 / 搜索）——极空间零配置底座 |
| **nas-report** | 🧭 **元技能/入口**：全盘存储画像（类别体积、冷热分层、大文件榜）+ 按发现路由到专项 skill |

### 专项整理

| Skill | 整理对象 | 能做什么 |
|-------|---------|----------|
| **file-sorter** | 任意混合目录 | 🧹 **通用第一道工序**：认 **255 种扩展名、分 15 类**（图片 / 视频 / 音频 / 文档 / 电子书 / 压缩包 / 安装包 / 字体 / 代码 / 设计源文件 / 图纸CAD / 备份镜像 / 种子 / 垃圾 / 待分类），算好每个文件的 `old → new`；认 **100 个目录名别名**，默认不打散项目目录、不重搬已归类的文件 |
| **photo-organizer** | 照片/视频 | 按拍摄日期归档、散图归位、截图/微信图识别、连拍去重、非媒体混入 |
| **music-organizer** | 音乐库 | 歌手/专辑/曲目三层结构、曲目号、封面、水印名、内置 ID3v2 解析对照路径与标签 |
| **work-organizer** | 工作文件 | 散文件归档、版本混乱（最终版/final）、同名多版本、副本、临时文件、过期归档 |
| **portfolio-organizer** | 作品集 | 项目结构、封面/说明、成品与源文件分离、多版本、大体积工程文件 |
| **download-cleaner** | 下载区 | 分诊清理：未完成/种子/安装包/压缩包/待归档媒体，给每文件一个建议动作 |

### 审计 / 去重

| Skill | 能做什么 |
|-------|----------|
| **dedup-finder** | 内容级**精确**去重（三级指纹 size→头部→全量 sha1，零误报），给保留/删除建议 |
| **backup-auditor** | 备份健康审计：版本轮转、陈旧检测、空备份、关键目录覆盖核对 |

### 外部关联

| Skill | 来源 | 能做什么 |
|-------|------|----------|
| **media-manager-skill** | [media-manager-skill](https://github.com/skyzhao1223/media-manager-skill) | 影视库命名扫描与整理（依赖 [media-naming-guide](https://github.com/skyzhao1223/media-naming-guide)） |

> 每个 skill 目录都含：`SKILL.md`（触发词 + 场景工作流）、`*.py`（只读扫描 CLI）、`tests/smoke.sh`（离线端到端测试）、`README.md`（开发者文档）。

## 可选：MCP（极空间）

想通过 MCP 协议使用极空间底层能力（不依赖 Agent 的 skill 机制）：

```bash
pip install "zspace-cli[mcp]"
```

配置见[仓库根 README](../README.md)（或[中文版](../docs/README.zh.md)）。

## 与「填密码直连 NAS」方案的区别（极空间）

| | zspace-cli | 账号密码直连方案 |
|---|-----------|------------------|
| 鉴权 | 桌面客户端已登录即可 | `.env` 里写账号密码 |
| 平台 | macOS（当前） | 更广 |
| 适合 | 本机 Agent / 日常整理 | 无桌面客户端的服务器 |

两者可以并存；zspace-cli 主打**零配置**。
