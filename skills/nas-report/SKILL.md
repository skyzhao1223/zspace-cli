---
name: nas-report
description: Use when 用户想给 NAS 做整体存储体检 — "我 NAS 上都存了些啥"、"空间被什么占了"、"帮我做个存储报告"、"哪些数据该归档/清理"、"该用哪个整理 skill"。只读扫描出存储画像(按类别体积/文件数、冷热分层、顶层目录榜、最大文件/目录榜、垃圾与空目录),并按发现路由到专门的整理 skill(file-sorter/photo/music/work/portfolio/download/dedup/backup/media-naming)。这是「元技能」/入口:先跑它看清全局,再决定跑哪个专项 skill。
  触发词:存储报告、存储画像、NAS 体检、空间占用、磁盘占用、都存了什么、大文件榜、冷热数据、数据分层、归档建议、该整理什么、从哪开始整理、storage report、disk usage、what's on my nas、nas profile。
  不适用:具体某类文件的规范整理(那是被路由到的专项 skill 的活);本 skill 只出「画像 + 建议去哪」,不做任何整理写操作。
---

# NAS Report — 存储画像 + 整理路由(元技能)

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS),纯 stdlib 零依赖。

这是整个整理 skill 家族的**入口/元技能**:面对一个不知从何下手的 NAS,先跑 `nas-report`
看清全局——空间被什么占了、哪些是冷数据、哪里最乱——然后它会**按发现的问题路由到专门的
整理 skill**。本 skill **只读、只出画像和建议,不做任何整理写操作**。

```
        nas-report(画像 + 路由)
                 │
                 ├─ file-sorter         通用分类归档(乱目录第一道工序)
                 ├─ photo-organizer     照片按拍摄日期
                 ├─ music-organizer     音乐歌手/专辑结构
                 ├─ work-organizer      办公文档按年份/项目
                 ├─ portfolio-organizer 作品集项目规范
                 ├─ download-cleaner    下载区分诊清理
                 ├─ dedup-finder        内容级精确去重
                 ├─ backup-auditor      备份健康审计
                 └─ media-naming        影视命名(外部 skill)
```

## Prerequisites

```bash
open smb://<NAS_IP>/data        # 挂载要体检的共享
python3 --version               # ≥3.9,无第三方依赖
```

## 快速验证

```bash
python3 nas_report.py report --root /Volumes/nas/data --max-files 5000
```

## 命令

| 命令 | 用途 |
|------|------|
| `report --root PATH` | 生成存储画像 + 路由建议(只读) |
| `report --root PATH --json --output F` | JSON 输出 |
| `diff OLD.json NEW.json [--json]` | **两份快照的差分**(离线,不碰 NAS):按类别/顶层目录的增减、新出现与消失的大文件、增长速率与 ETA |
| `--max-depth N` / `--max-files N` / `--top N` | 深度 / 文件数上限 / 各榜单条数 |

```bash
# 全盘画像(大库先 --max-files 摸底,看耗时再决定要不要全量)
python3 nas_report.py report --root /Volumes/nas/data --output /tmp/report.json

# 攒了两份画像之后:看这段时间到底长在哪、长了多少、离满盘还有多久
python3 nas_report.py diff /tmp/report-0901.json /tmp/report-0911.json --capacity-gb 4000
```

## 画像维度

| 维度 | 内容 |
|------|------|
| 总览 | 文件数 / 目录数 / 总体积 / 扫描耗时 |
| 按类别 | 影视/音频/照片/文档/压缩包/安装包/设计源文件/代码/备份镜像/其他 的体积与占比 |
| 冷热分层 | 按 mtime 分:活跃(≤30天)/ 温(30天~1年)/ 凉(1~3年)/ 冷(3年+) |
| 顶层目录榜 | root 下各一级目录的体积与文件数 |
| 最大目录榜 | 全库最大的 N 个目录(含子树体积) |
| 最大文件榜 | 全库最大的 N 个文件(带类别标签) |
| 垃圾/空目录 | 垃圾/临时/种子文件计数与体积、空目录数、根目录散文件数 |

## 路由建议(核心价值)

脚本按画像自动生成「下一步该跑哪个 skill」:

| 触发条件 | 路由到 |
|----------|--------|
| 根目录散文件 ≥ 20 个 | file-sorter |
| 影视体积占比 > 15% | media-naming / media-manager-skill |
| 照片 > 500 张 | photo-organizer |
| 音频 > 100 首 | music-organizer |
| 文档 > 100 个 | work-organizer |
| 设计源文件 > 20 个 | portfolio-organizer |
| 冷数据(3年+)> 20% | (冷热分层归档建议) |
| 垃圾/临时/种子 > 50 个 | download-cleaner |
| 文件总数 > 1000 | dedup-finder |
| 空目录 > 20 个 | (空目录清理) |
| 顶层目录名含 备份/backup/快照 | backup-auditor |

阈值是启发式,`recommendations` 里带 `why` 说明触发原因。

### 下游 skill 可以带一份 `config.json` 覆盖层(issue #15)

路由过去之后要告诉用户的一件事:**file-sorter 和 photo-organizer 现在支持可选的
`config.json`**,放在**脚本自己所在目录**(`zs skill` 装好后就是用户项目里的那份
旁边),用来描述他库里的**故意例外** —— 一个 `原盘/VIDEO_TS` 树、随片留着的
`.ass` 字幕、某个专业软件的私有扩展名。

```jsonc
// skills/file-sorter/config.json
{
  "whitelist_dirs": ["原盘", "samples"],          // 命中的目录整个子树不出搬家计划
  "extension_overrides": {".rfa": "cad", "ass": "video"}
}
```

| 谁支持 | 覆盖什么 |
|--------|---------|
| **file-sorter** | `whitelist_dirs`(追加到 `--keep-dir`)、`extension_overrides`(15 个类别名) |
| **photo-organizer** | `whitelist_dirs`(追加到 `WHITELIST_DIRS`,整棵子树跳过)、`extension_overrides`(`photo`/`video`/`sidecar`/`junk`/`non_media`) |
| 其余 skill | 暂不支持(见 PR 说明);机制是逐字复制的,可直接照搬 |
| **nas-report(本 skill)** | **不支持**,见下 |

**没有这个文件时,两个 skill 的行为与引入该机制之前逐字节一致**(不多一个 JSON
字段),所以路由过去之前不需要先问用户有没有配。

路由时可以这样说:「你的 `原盘/VIDEO_TS` 是有意的结构,file-sorter 默认会把它
报成待整理;在 `skills/file-sorter/config.json` 里加一条 `whitelist_dirs` 就不会了
—— 具体写法见那份 SKILL.md 的『配置覆盖』。」

两个键的完整语义、模式锚定规则(`原盘/*` 到底命中什么)、出错行为都写在**各自
skill 的 SKILL.md** 里,别在这里复述:两个 skill 的 `whitelist_dirs` 效果**并不
相同**(file-sorter 仍计入 `stats`、photo-organizer 整棵跳过),照抄会讲错。

#### 为什么 nas-report 自己不接受 `config.json`

不是漏了,是刻意的:

1. **本 skill 不判合规**。它出的是存储画像(各类别体积、冷热分层、大文件榜)和
   路由建议,没有「这个文件该不该在这儿」的判断可以被豁免 —— 而 `whitelist_dirs`
   的语义正是「别报它」。
2. **改类别表会静默改变路由结论**。`CATEGORIES`(扩展名 → 类别)直接决定
   「照片 > 500 张 → photo-organizer」这类阈值触发。让用户覆盖它,等于让画像和
   路由建议都建立在一套私有 taxonomy 上,而**下游那个专项 skill 用的是它自己的
   表** —— 两边一旦不一致,nas-report 说「去跑 photo-organizer」而 photo-organizer
   看到的类别分布完全不同,这比没有覆盖更难查。
3. `CATEGORIES` 与 file-sorter 的 15 类**有意不一致**(file-sorter 从 `design` 里
   分出 `cad`、从 `doc` 里分出 `ebook`、把 `iso`/`img` 归 `backup`),所以一份
   「通用」的 `extension_overrides` 在两边本来就不是同一件事。

要让画像的类别口径也跟着用户走,得先解决「nas-report 与专项 skill 共用一张类别表」
这个更大的问题 —— 那是另一个 issue,不该夹在这里做。

## 工作流

### 场景 1:用户说"帮我看看 NAS 上都存了些啥 / 空间被什么占了"

**步骤**:
1. **exec** `python3 nas_report.py report --root <挂载路径> --output /tmp/report.json`
   (大库先 `--max-files 20000` 摸底,看 `elapsed_sec` 再决定要不要全量)
2. 读 JSON / 人类可读输出,汇报:总体积、Top 类别占比、冷热分布、最大目录/文件
3. 重点讲**路由建议**:告诉用户「你的库主要问题是 X,建议先跑 Y skill」
4. **不做写操作** — 问用户要不要接着跑某个专项 skill

### 场景 2:用户说"我该从哪开始整理"

**步骤**:
1. 复用上一轮 `/tmp/report.json` 的 `recommendations`
2. 按「收益/风险」给用户排序建议:
   - 先易后难:download-cleaner(清垃圾)、dedup-finder(回收空间)见效快、风险低
   - 目录很乱、多类混放:file-sorter(按类型分类归档)——推荐排在去重之后
     (省掉白搬即将删掉的字节),但反过来也不会漏检,两者互不依赖
   - 再按需:photo/work/portfolio/music-organizer(结构化整理)
   - 备份类:backup-auditor(先保命,别在没备份时大改)
3. 用户选定后,**切到对应 skill** 继续(本 skill 到此为止)

### 场景 3:用户说"哪些数据该归档到冷存储"

**步骤**:
1. report 的「冷热分层」+「最大目录榜」定位冷数据集中区
2. 冷(3年+)占比高 → 建议整体迁到冷存储/外置盘;列出具体的大目录 + 体积
3. 迁移是写操作 → 出 `old → new` 计划交用户确认,走挂载盘 `mv` 或 `zs mv`

## 关键约束

1. **纯只读元技能**:不出任何整理映射,只出画像 + 路由建议;实际整理交给专项 skill
2. **建议是启发式**:阈值触发,LLM 要结合用户实际情况解读,别机械照搬
3. **大库先摸底**:`--max-files` 控制规模,SMB 上全盘 stat 慢,先看耗时
4. **冷热靠 mtime**:mtime 是弱证据(拷贝会刷新),归档决策前让用户抽查
5. **路由不越权**:本 skill 不替专项 skill 做决定,只指路

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 全盘 stat 慢 | 大库 report 很久 | `--max-files` 分批;`--max-depth` 限深;先看顶层画像 |
| 稀疏文件 | size 与实际占用不符 | 画像按逻辑 size;冷热/回收估算够用了 |
| 顶层目录名千奇百怪 | 备份目录探测漏判 | 探测靠名字关键词;漏判时用户可手动指定跑 backup-auditor |
| mtime 失真 | 迁移过的数据全显示"新" | 冷热分层仅供参考;重要决策结合文件名日期 |
| 挂载断连 | `⚠️ 无法读取` | 画像不完整;重连后重扫 |

## 已知 gap

- 不算重复占用(那是 dedup-finder 的活);画像里的体积含重复
- 冷热分层靠 mtime,不读 atime(多数 NAS 挂 noatime,atime 不可靠)
- 路由阈值写死,不按库总量自适应(小库可能一个建议都不触发)
- 增长趋势要看**两份**快照:单次 report 只是当前切片,`diff OLD NEW` 才给差分与速率(快照留的是 top-N 榜单,文件级增减不是全库 diff;容量要 `--capacity-gb` 喂进去,快照里没有容量字段)
- 类别靠扩展名,不改名的错扩展名会误分类
- **本 skill 不接受 `config.json`**(见上「为什么 nas-report 自己不接受」):画像与路由的类别口径无法按用户覆盖。下游的 file-sorter / photo-organizer 支持,所以「画像说照片多、专项 skill 却按另一套类别判」这种口径不一致目前无法从配置层面消除
- 路由阈值(20 个散文件 / 500 张照片 / 15% 影视占比…)是写死的常量,既不自适应库总量,也不接受覆盖

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认已挂载:`ls /Volumes/` |
| report 很慢 | `--max-files 20000` 摸底,或 `--max-depth 4` 先看浅层 |
| 没有任何路由建议 | 库较小或较规整;可手动按需跑专项 skill |
| 类别占比全是"其他" | 库里多是无扩展名/冷门扩展名文件;看最大文件榜人工判断 |
