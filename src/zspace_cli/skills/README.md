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

### 执行阶段的两个坑（所有 skill 通用，Agent 务必注意）

扫描是只读的、不会出错；**风险全在"照着计划拼命令执行"这一步**。这两条对 9 个 skill 都适用：

**1. 文件名对 shell 不友好。** 以 `-` 开头的名字会被当成命令选项：

```console
$ mv -n "-f.pdf" 文档/
mv: illegal option -- .
```

`-i` 更隐蔽（让 `mv` 变交互式，非交互的 Agent 里会挂住）；含 `$` 或反引号的名字**即使加了双引号也会被 shell 展开**。正确写法：

| 平台 | 写法 |
|------|------|
| POSIX | `mv -n -- "-f.pdf" 文档/` 或 `mv -n "./-f.pdf" 文档/` |
| PowerShell | `Move-Item -LiteralPath "Z:\data\-f.pdf" -Destination "Z:\data\文档\"` |

`file-sorter` 会主动检出这类名字（`stats.shell_unsafe_names`，并在 `problems` 里给出上面这两种写法）；**其余 skill 目前不检**，所以 Agent 拿到任何 `old → new` 计划都该先扫一眼有没有 `-` 开头或含 `$` `` ` `` `"` `\` 的名字。（空格和中文**不算**——双引号就够了。）

**2. 只有大小写不同的同名文件。** macOS（APFS）和 Windows（NTFS）默认**大小写不敏感**，`图片/X.jpg` 与 `图片/x.jpg` 是**同一个路径**：

```console
$ mv -n a/X.jpg 图片/ && mv -n b/x.jpg 图片/     # 第二条静默什么都不做
```

`mv -n` 不覆盖，于是第二个文件**留在原地**，而计划看起来执行成功了——不报错，所以最容易漏。`file-sorter` 会探测文件系统并把这种情况判为撞名（自动改名 `x__2.jpg` + 降级人工确认，见 `stats.case_insensitive_fs`）；只给"建议目录"不给完整目标路径的 skill（如 photo-organizer 给 `2024/2024-05/`）检测不到，需要 Agent 自己在搬之前比一下同目录内是否已有只差大小写的名字。

### 可选：`config.json` 覆盖层（故意的例外）

真实库里总有**故意的例外**：一个 `原盘/VIDEO_TS` 树、随片留着的 `.ass` 字幕、某个专业软件的私有扩展名、一批留着的样片。整理 skill 的合规形状是写死在脚本里的，遇到这些例外只能改代码——所以有了这个可选的覆盖层（[issue #15](https://github.com/skyzhao1223/zspace-cli/issues/15)）。

在**脚本自己所在的目录**（`zs skill` 装好后就是你项目里的那一份旁边，不是被扫描的目录）放一个 `config.json`：

```jsonc
// skills/file-sorter/config.json
{
  "whitelist_dirs": ["原盘", "samples"],              // 命中的目录整个子树不再报
  "extension_overrides": {".rfa": "cad", "ass": "video"}   // 逐扩展名改判
}
```

四个键，**没有一个 skill 支持全部四个**——每个 skill 只暴露它自己真正有的机制：

| 键 | 作用 | 合并语义 | 哪些 skill 有 |
|----|------|---------|--------------|
| `whitelist_dirs` | 目录白名单，`fnmatch` glob | **追加**到内置白名单，内置条目一条都不删 | file-sorter、photo-organizer、music-organizer、work-organizer、portfolio-organizer |
| `skip_dirs` | 目录跳过，`fnmatch` glob | **追加**到内置 `SKIP_DIRS`；命中的目录**整棵不进入** | download-cleaner、backup-auditor、dedup-finder |
| `extension_overrides` | 扩展名 → 类别 | **逐扩展名改判**：该扩展名先从**所有**内置表里摘掉，再放进指定的那一个；没写到的扩展名完全不受影响 | 除 dedup-finder 外的 7 个（它没有任何扩展名表） |
| `prefer_keep_hints` | 重复组里优先保留哪一份 | **追加**到内置 `PREFER_KEEP_HINTS`；仍是子串匹配，**没有升级成 glob** | dedup-finder 独有 |

> **为什么 `whitelist_dirs` 和 `skip_dirs` 不合并成一个键**：`whitelist_dirs` 的语义是「这块是**有意的结构**，别当成不合规」，`skip_dirs` 的语义是「这块**别扫**」。前 5 个 skill 判的是目录名合规性，后 3 个根本没有合规判定这个概念——给它们同一个键却是另一种含义，会让「在 skill 之间复制一份 config」产生静默的意外行为。

四条全家族一致的规则：

1. **没有这个文件 = 行为与引入该机制之前逐字节一致**（不多一个 JSON 字段）。所以它不是升级风险，只是多了一个可选项。
2. **配置有问题就报错退出（exit 1），绝不静默忽略**——键名拼错一个字母（`whitelist_dir`）会让整份配置悄悄失效，而用户看到的现象是「我明明加进白名单了它还在报」。报错会指名文件路径和出错的键。
3. **模式锚定在 `--root`**，对「整段相对路径」或「任意一级目录名」做大小写不敏感的 `fnmatch`。所以 `原盘` 命中任意深度的同名目录，而 `原盘/*` 只命中 `--root` 第一层的 `原盘/` 底下的**子目录**（连 `原盘/` 自己一起保住要写 `原盘`，不带 `/*`）。绝对路径匹配不上任何东西，所以直接报错。
4. **内置条目只增不删**——白名单、跳过表、扩展名表、保留提示词都是**追加**，配置永远撤不掉内置行为。唯一会「删」的是 `extension_overrides`：它把该扩展名从所有内置表里摘掉再放进你指定的那一张，这是改判所必需的（分类是有序阶梯，只加不删等于静默无效）。

生效与否有据可查：加载成功时 stderr 打一行 `ℹ️ 已加载覆盖配置 …`，`--json` 里多一个 `stats.config`。**结果里没有 `stats.config` 这个键 = 没找到配置文件 = 覆盖没生效。**

**支持范围：8 个专项 scanner 全部接入**（机制是逐字复制进每个脚本的——`zs skill` 按目录 `copytree` 安装，`skills/<name>/` 之外的模块到不了用户机器，所以无法共享）：

| Skill | 键集 | `whitelist_dirs` / `skip_dirs` 命中后**具体发生什么** | 可观测的计数键 |
|-------|------|------------------------------------------------------|---------------|
| **file-sorter** | `whitelist_dirs` `extension_overrides` | 等价于自动加上这几条 `--keep-dir`；命中的文件**仍计入 `stats`**，只是归入 `protected` | `stats.protected` |
| **photo-organizer** | `whitelist_dirs` `extension_overrides` | **整棵子树跳过**，里面的文件不计入 `stats.files` | `stats.config.skipped_files` |
| **music-organizer** | `whitelist_dirs` `extension_overrides` | **整棵子树跳过**（同 photo-organizer） | `stats.config.skipped_files` |
| **work-organizer** | `whitelist_dirs` `extension_overrides` | **只豁免目录名**，子树里的文件照扫照报（它豁免的是「目录名不符合规范」这一条） | 无 |
| **portfolio-organizer** | `whitelist_dirs` `extension_overrides` | 语义是「**这个目录不算项目**」，文件仍被计入统计 | 无 |
| **download-cleaner** | `skip_dirs` `extension_overrides` | **整棵不进入**，里面的文件既不计入 `stats` 也不出任何问题 | `stats.config.skipped_dirs` |
| **backup-auditor** | `skip_dirs` `extension_overrides` | **整棵不进入**，且作用在**三层**：备份项收集、coverage 的源目录收集、`dir_size` 递归求体积 | `stats.config.skipped_dirs` |
| **dedup-finder** | `skip_dirs` `prefer_keep_hints` | **整棵不进入**，不参与任何重复组 | `stats.config.skipped_dirs` |
| nas-report | ❌ 刻意不支持 | 它只出画像与路由、不判合规，而改它的类别表会静默改变路由结论并与下游专项 skill 的口径脱节。完整理由与**路由时该怎么把这件事告诉用户**都在它的 SKILL.md 里 | — |
| zspace-nas | ❌ 不适用 | 它是 API 参考与脚本底座，没有扫描器 | — |

> ⚠️ **`whitelist_dirs` 在 5 个 skill 里有 4 种不同效果**（仍计入并归 protected / 整棵跳过 / 只豁免目录名 / 不算项目），因为它们的内置机制本来就不同。`skip_dirs` 则在 3 个 skill 里**完全一致**。想在 skill 之间复制一份 config 之前，先看这一列，或看各自 SKILL.md 的「配置覆盖」一节。

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
| **file-sorter** | 任意混合目录 | 🧹 **通用第一道工序**：认 **255 种扩展名、分 15 类**（图片 / 视频 / 音频 / 文档 / 电子书 / 压缩包 / 安装包 / 字体 / 代码 / 设计源文件 / 图纸CAD / 备份镜像 / 种子 / 垃圾 / 待分类），算好每个文件的 `old → new`；认 **135 个目录名别名**（简繁中英），默认不打散项目目录、不重搬已归类的文件 |
| **photo-organizer** | 照片/视频 | 按拍摄日期归档（`--exif` 外挂 exiftool / mdls 读真实拍摄日期）、散图归位、截图/微信图识别、连拍去重、非媒体混入 |
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
