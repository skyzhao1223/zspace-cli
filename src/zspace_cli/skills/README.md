# Skills（Agent 工作流）

把这些 Skill 复制到你的 Agent 项目后，用自然语言就能管理你的极空间 NAS。底层能力来自 **`zspace-cli`**（读桌面客户端登录态，无需填密码）。

## 30 秒上手

```bash
# 1. 安装底座（macOS + 极空间桌面客户端已登录）
pip install zspace-cli
zs check

# 2. 复制 skill 到你的项目
zs skill --list                            # 先看有哪些
zs skill ~/your-project/.cursor/skills/    # 全装(Cursor)
# 或
zs skill ~/your-project/skills/            # 全装(Claude Code 等)
# 或按需选装
zs skill ~/your-project/skills/ --only nas-report,photo-organizer,dedup-finder

# 3. 对你的 Agent 说
# 「列出我 NAS 上 /sata11/my/data 的文件」
```

> 也可以直接从仓库源码复制 `skills/zspace-nas/`。

## Skill 清单

**先跑 [`nas-report`](#nas-report) 看清全局,它会告诉你该用下面哪个专项 skill。**

### 底座 / 入口

| Skill | 能做什么 |
|-------|----------|
| **zspace-nas** | 通用文件管理(列目录 / 重命名 / 移动 / 复制 / 删除 / 搜索)——极空间零配置底座 |
| **nas-report** | 🧭 **元技能/入口**:全盘存储画像(类别体积、冷热分层、大文件榜)+ 按发现路由到专项 skill |

### 专项整理(只读扫描 → 出计划 → 确认 → 执行)

| Skill | 整理对象 | 能做什么 |
|-------|---------|----------|
| **photo-organizer** | 照片/视频 | 按拍摄日期归档、散图归位、截图/微信图识别、连拍去重、非媒体混入 |
| **music-organizer** | 音乐库 | 歌手/专辑/曲目三层结构、曲目号、封面、水印名、内置 ID3v2 解析对照路径与标签 |
| **work-organizer** | 工作文件 | 散文件归档、版本混乱(最终版/final)、同名多版本、副本、临时文件、过期归档 |
| **portfolio-organizer** | 作品集 | 项目结构、封面/说明、成品与源文件分离、多版本、大体积工程文件 |
| **download-cleaner** | 下载区 | 分诊清理:未完成/种子/安装包/压缩包/待归档媒体,给每文件一个建议动作 |

### 审计 / 去重

| Skill | 能做什么 |
|-------|----------|
| **dedup-finder** | 内容级**精确**去重(三级指纹 size→头部→全量 sha1,零误报),给保留/删除建议 |
| **backup-auditor** | 备份健康审计:版本轮转、陈旧检测、空备份、关键目录覆盖核对 |

### 外部关联

| Skill | 来源 | 能做什么 |
|-------|------|----------|
| **media-manager-skill** | [media-manager-skill](https://github.com/skyzhao1223/media-manager-skill) | 影视库命名扫描与整理(依赖 [media-naming-guide](https://github.com/skyzhao1223/media-naming-guide)) |

> **跨 NAS 通用**:上面 8 个整理/审计 skill(除 `zspace-nas` 外)的扫描脚本都是**纯 stdlib 零依赖**,跑在**本地挂载路径**(SMB/NFS)上——极空间 / 群晖 / 威联通 / 绿联等只要能挂载就能扫。写操作可由 Agent 走挂载盘 `mv -n`,极空间也可选走 `zs` CLI / MCP。全部沿袭 `media-naming` 的「**只读扫描 → LLM 出 old→new 计划 → 用户确认 → 执行**」模式,删除一律先隔离再真删。
>
> 每个 skill 都含:`SKILL.md`(触发词+场景工作流)、`*.py`(只读扫描 CLI)、`tests/smoke.sh`(离线端到端测试)、`README.md`(开发者文档)。

## 可选：MCP

想通过 MCP 协议使用底层能力（不依赖 Agent 的 skill 机制）：

```bash
pip install "zspace-cli[mcp]"
```

配置见[仓库根 README](../README.md)（或[中文版](../docs/README.zh.md)）。

## 与「填密码直连 NAS」方案的区别

| | zspace-cli | 账号密码直连方案 |
|---|-----------|------------------|
| 鉴权 | 桌面客户端已登录即可 | `.env` 里写账号密码 |
| 平台 | macOS（当前） | 更广 |
| 适合 | 本机 Agent / 日常整理 | 无桌面客户端的服务器 |

两者可以并存；zspace-cli 主打**零配置**。
