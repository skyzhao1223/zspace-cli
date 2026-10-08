---
name: file-sorter
description: Use when 用户想给 NAS 上一个「什么都往里丢」的乱目录做分类归档 — "帮我把文件分个类"、"图纸和文档混在一起"、"根目录一堆散文件"、"按类型建目录归档"、"dwg/pdf/图片全堆在一层"、"外置盘拷进来一堆东西没整理"。只读扫描按扩展名归类(文档/图纸CAD/设计源文件/图片/视频/音频/电子书/压缩包/安装包/字体/代码/备份镜像/待分类),算出每个文件的 old → new 目标路径;默认不动已在类别目录里的文件、不动疑似项目目录;LLM 出整理计划,用户确认后由 Agent 执行 mv。
  触发词:文件分类、按类型整理、分类归档、归类、自动分类、乱目录整理、根目录散文件、图纸整理、图纸归档、CAD 归档、dwg 整理、文档图纸混放、混合目录、外置盘整理、拷贝进来没整理、建目录分类、file sorter、sort files、organize by type、categorize files、classify files。
  不适用:内容级去重(走 dedup-finder — 本 skill 只按文件名副本标记给「疑似」提示);照片按拍摄日期归档(走 photo-organizer);音乐库歌手/专辑结构(走 music-organizer);办公文档按年份/项目 + 版本混乱(走 work-organizer);作品集项目规范(走 portfolio-organizer);下载区分诊清理(走 download-cleaner);全盘存储画像与路由(走 nas-report)。
---

# File Sorter — 通用文件分类归档

## 概述

**跨 NAS 通用**:扫描脚本跑在**本地挂载路径**上(SMB/NFS 挂载的 NAS 共享、
外置硬盘、本地同步目录都行),纯 stdlib 零依赖,不绑定任何品牌 API。

专治「什么都往里丢」的目录:图纸、文档、图片、压缩包、安装包平铺在一层,
或散落在几个无意义的子目录里。本 skill 做**正向合规扫描**:定义合规结构
(每个文件待在与它类别匹配的目录里)→ 不匹配即给出 `old → new` 目标路径。

**写操作不在脚本里** — 脚本只算目标路径,LLM 汇总成整理计划,
经用户确认后由 Agent 执行(挂载盘 `mkdir -p` + `mv -n`,极空间也可走
`zs mv` / MCP `move`)。

**默认保守**:两类文件不会被提议搬家 —
1. 已在某个类别目录里的(哪怕类别不符,如 `图纸/` 里的效果图)
2. 疑似项目目录里的(命名子目录内多类混合,如 `2024_官网改版/`)

要动它们必须显式加 `--strict` / `--split-project-dirs`。

## Prerequisites

```bash
open smb://<NAS_IP>/data        # 极空间/群晖/威联通/绿联等均支持 SMB
python3 --version               # ≥3.9,无第三方依赖
```

## 快速验证

```bash
python3 file_sorter.py scan --root /Volumes/nas/data --sample 500
```

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 分类归档扫描(只读),给出每个文件的 `target` |
| `scan --root PATH --json --output F` | JSON 输出 |
| `--layout {type,type-year,year-type}` | 目标结构:`图纸/` · `图纸/2024/` · `2024/图纸/`(默认 `type`) |
| `--naming {zh,en}` | 目标目录中文名(`图纸`)或英文名(`Drawings`),默认 zh |
| `--dest DIR` | 归档到子目录前缀下(如 `--dest 整理后` → `整理后/图纸/`),默认就地归档 |
| `--keep-dir GLOB` | 白名单目录(可重复),命中整体不动,如 `--keep-dir '2024_*'` |
| `--strict` | 加查「已在别的类别目录里但类别不符」的文件(默认不动) |
| `--split-project-dirs` | 拆开疑似项目目录,里面文件也按类型归档(默认整体保留) |
| `--project-min-files N` | 判定疑似项目目录的最少文件数(默认 3) |
| `--only-cat LIST` | 只给这些类别出搬家计划(逗号分隔,如 `cad,doc`);**统计仍是全量的** |
| `--max-issues N` | 计划条数上限(默认 500,0=不限);超出只截断 `issues`,`stats` 不受影响 |
| `--max-depth N` / `--sample N` / `--top N` | 递归深度 / 文件数上限 / 每组显示条数 |
| `--stale-days N` | 超过 N 天未动计入「久未动」(默认 1095) |

```bash
# 按「类别 + 年份」归档,先摸底 2000 个文件
python3 file_sorter.py scan --root /Volumes/nas/data --layout type-year --sample 2000

# 不敢就地动?全部归档到 整理后/ 下,原结构不变
python3 file_sorter.py scan --root /Volumes/nas/data --dest 整理后

# 项目目录要保留,但里面的散图散文档要归位
python3 file_sorter.py scan --root /Volumes/nas/data --keep-dir '2024_官网改版'

# 大库分批:这轮只处理图纸,下一轮再 --only-cat doc
python3 file_sorter.py scan --root /Volumes/nas/data --only-cat cad --output /tmp/sort-cad.json
```

## 期望结构

```
data/
  图纸/                 ← dwg dxf step ipt rvt skp stl…(CAD/3D)
    平面布置图.dwg
  文档/                 ← docx xlsx pptx pdf txt md csv…
    合同.pdf
  设计源文件/            ← psd ai fig sketch blend prproj…(工程文件)
    logo.psd
  图片/                 ← jpg png heic tif svg raw…
  视频/  音频/  电子书/  压缩包/  安装包/  字体/  代码/  备份镜像/
  待分类/                ← 扩展名不认识 / 没有扩展名 → 人工判断
  2024_官网改版/         ← 疑似项目目录:默认整体保留,不拆
```

`--layout type-year` → `图纸/2024/平面布置图.dwg`;`--layout year-type` →
`2024/图纸/平面布置图.dwg`(年份取 mtime)。

## 类别与判定

| 类别 | 目标目录(zh/en) | 典型扩展名 |
|------|------------------|-----------|
| cad | 图纸 / Drawings | dwg dxf dwf dgn ifc step stp iges sldprt sldasm ipt iam rvt rfa skp stl obj fbx 3dm gcode |
| doc | 文档 / Documents | doc(x) xls(x) ppt(x) pdf txt md csv rtf odt key numbers pages wps xmind vsdx srt |
| design | 设计源文件 / Design-Sources | psd psb ai eps sketch fig xd cdr indd aep blend c4d max ma mb prproj fla kra xcf |
| image | 图片 / Images | jpg png gif webp heic tif svg ico avif dng cr2 nef arw raf raw |
| video / audio | 视频 / 音频 | mp4 mkv mov ts m2ts webm · mp3 flac wav m4a aac ape dsf cue |
| ebook | 电子书 / Ebooks | epub mobi azw3 djvu fb2 chm |
| archive | 压缩包 / Archives | zip rar 7z tar gz xz zst tgz |
| installer | 安装包 / Installers | dmg pkg exe msi apk deb rpm appimage |
| font | 字体 / Fonts | ttf otf ttc woff woff2 dfont |
| code | 代码 / Code | py js ts? go rs java c cpp sh sql html css json yaml yml toml ipynb |
| backup | 备份镜像 / Backups | iso img dsk vhd vhdx vmdk qcow2 ova sparsebundle |
| junk | (不归档,建议清理) | `.DS_Store` `Thumbs.db` `._*` tmp temp log bak old part crdownload |
| other | 待分类 / Unsorted | 其余(含无扩展名) |

**判定只看扩展名**(不读文件内容、不猜语义)。类别目录的识别同时接受中英文别名
(`图纸`/`CAD`/`Drawings`/`施工图`、`文档`/`Documents`/`资料`、`图片`/`照片`/`Images`…),
所以已经手工分好类的库不会被再搬一次。

## 工作流

### 场景 1:用户说"帮我把这个目录分个类"

**步骤**:
1. **exec** `python3 file_sorter.py scan --root <挂载路径> --output /tmp/sort.json`
   (大库先 `--sample 2000` 摸底,看耗时与类别分布再决定要不要全量)
2. 读 `stats`:文件数/总体积、类别分布、`root_files`(散在根目录的)、
   `to_move` / `to_review` / `to_delete`、`project_dirs`(疑似项目目录)、
   `dup_suspects`(疑似副本)、`conflicts`(重名冲突)、`unknown_exts`
   - **注意 `issues_truncated`**:大库的 `issues` 默认只给前 500 条
     (`--max-issues`),但 `stats` 永远是全量的。被截断时**不要**以为自己
     看到了全部计划 —— 按 `--only-cat` 分类别分批重扫
3. 汇报:「共 N 个文件,其中 M 个待归档;会新建这些目录:图纸/ 文档/ 图片/…;
   另有 K 个疑似项目目录默认不动」
4. **不做写操作** — 问用户:目录名用中文还是英文(`--naming en`)、
   要不要带年份(`--layout type-year`)、要不要先归到 `--dest 整理后/` 试水、
   从哪一类开始(建议先图纸/文档这类用户最在意的)

### 场景 2:用户说"就按你说的整理"(执行计划)

**步骤**:
1. 复用 `/tmp/sort.json`,过滤 `action == "move"`,按 `target` 的目录分组
   - `issues_truncated == true` 时先重扫本轮要处理的那类:
     `scan --root … --only-cat cad --output /tmp/sort-cad.json`
2. **先预览再执行**:列出前 20 条 `old → new` + 每个目标目录的文件数/体积
3. 用户确认后执行(挂载盘):
   ```bash
   cd /Volumes/nas/data
   mkdir -p "图纸" "文档" "图片"
   mv -n "平面布置图.dwg" "图纸/"
   mv -n "合同.pdf" "文档/"
   ```
   - 一律 `mv -n`(不覆盖);目标路径已由脚本算好去重(重名的会带 `__2`)
   - 极空间用户也可走 zspace-nas skill 的 `zs mv`,或 MCP `zspace_move`(带 UI 确认)
4. 分批执行(每次 ≤200 个文件),每批后重扫验证 `to_move` 下降
5. `action == "review"` 的**逐条问用户**:未识别扩展名(`.xyz`)、
   重名冲突(`x__2.jpg`)、同名文件过多(`target` 为 null,脚本拒绝瞎编号)、
   疑似项目目录

### 场景 3:用户说"清理重复文件 + 分类"(最常见组合)

**顺序不能反** — 先删重复,再分类;否则会把副本一起搬进新目录:

1. **exec** `python3 ../dedup-finder/dedup_finder.py scan --root <路径> --output /tmp/dups.json`
   内容级精确去重(三级指纹,零误报),出「保留哪个/隔离哪个」计划
2. 用户确认后 `mv` 到 `_dup_quarantine/`(隔离期 ≥2 周再真删)
3. **exec** `python3 file_sorter.py scan --root <路径> --output /tmp/sort.json`
   此时 `dup_suspects` 应显著下降;剩下的按场景 2 归档
4. 若 file-sorter 仍报「疑似副本」但 dedup-finder 说内容不同 →
   那是**同名不同版本**(如 `图纸_v1.dwg` / `图纸_v2.dwg`),
   不要删,走 work-organizer 的版本梳理

### 场景 4:用户说"图纸太多了,单独整一下"

**步骤**:
1. `--root` 指到图纸所在目录,只关心 `category == "cad"` 的条目
2. 图纸库常见诉求是「按项目/按年份」而不是「按类型」——
   类型已经单一了,这时改用 `--layout type-year`(图纸/2024/)或
   `--keep-dir` 保住项目目录、只把散落的图归位
3. 提醒:`.pdf` 出图与 `.dwg` 源图会被分到「文档」和「图纸」两类;
   若用户的习惯是「一个项目的 pdf 出图和 dwg 放一起」,
   用 `--keep-dir '<项目名>'` 或 `--split-project-dirs` 之外的方式保住结构
4. 版本混乱(`最终版`/`final`/`v3`)不是本 skill 的活 → work-organizer

## 整理顺序(严格)

1. **先去重**:dedup-finder(内容级)→ 隔离副本
2. **再清垃圾**:`action == delete`(`.DS_Store`/`._*`)可直接删;
   `delete-confirm`(`.log`/`.tmp`/`.bak`)先 `mv` 到隔离目录
3. **再分类**:`action == move` 按目标目录分批 `mkdir -p` + `mv -n`
4. **最后人工项**:`action == review`(未识别扩展名 / 重名冲突 / 疑似项目目录)
5. **重扫验证**:`to_move` 应降到 0,`compliant` 占比接近 100%

## 写操作通道

| 通道 | 适用 | 命令 |
|------|------|------|
| 挂载盘 shell(macOS/Linux) | 任何 NAS | `mkdir -p` + `mv -n`(推荐);删除先隔离 |
| PowerShell(Windows) | 任何 NAS | `New-Item -ItemType Directory -Force` + `Move-Item`(**不要加 `-Force`**) |
| `zs` CLI | 极空间(任意平台) | `zs mkdir` / `zs mv` / `zs rm`(见 zspace-nas skill)——**Windows 首选**,绕开 shell 转义 |
| MCP tool | 极空间 + MCP | `zspace_mkdir` / `zspace_move` / `zspace_remove`,弹 UI 二次确认 |

### Windows 执行对照(Agent 照这个执行,别用 `mv`)

`mv -n` / `mkdir -p` 在 PowerShell 里不存在。等价写法(假设映射盘 `Z:\data`):

```powershell
# 中文路径先切 UTF-8,否则目录名会乱码
chcp 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8

# mkdir -p  →  建目录(-Force 在这里是「已存在也不报错」,安全)
New-Item -ItemType Directory -Force -Path "Z:\data\图纸" | Out-Null

# mv -n     →  移动。Move-Item 默认就是「目标已存在则报错」= 不覆盖,
#              所以**绝对不要加 -Force**(加了就变成覆盖)
Move-Item -Path "Z:\data\平面布置图.dwg" -Destination "Z:\data\图纸\"

# 删除 = 先隔离(和 POSIX 一样,不直接 Remove-Item)
New-Item -ItemType Directory -Force -Path "Z:\data\_quarantine" | Out-Null
Move-Item -Path "Z:\data\build.log" -Destination "Z:\data\_quarantine\"
```

批量搬同一类(可选,比逐个 `Move-Item` 快);`/XC /XN /XO` = 跳过所有已存在文件,
合起来才等于 `mv -n` 的不覆盖语义:

```powershell
robocopy "Z:\data" "Z:\data\图纸" *.dwg *.dxf *.step /MOV /XC /XN /XO /NJH /NJS
```

> ⚠️ `robocopy /MOV` 会**移动**源文件;先用 `/L`(只列不做)跑一遍看清单再真跑。
> 极空间用户在 Windows 上更省事的路子是 `zs mv`——走 API,不依赖映射盘和 shell 转义。

## 关键约束

1. **脚本只读**:无 apply / mv / rm 子命令;搬家由 Agent 在用户确认后执行
2. **不覆盖**:POSIX 用 `mv -n`;Windows 用 `Move-Item` 且**不加 `-Force`**
   (脚本已算好去重名,但别赌)
3. **不打散有意的结构**:类别目录内的文件、疑似项目目录默认不动;
   要动必须用户明确要求 + 显式加 `--strict` / `--split-project-dirs`
4. **只认扩展名,不读内容**:改名过的文件会被误判类别 → 批量执行前抽样核对
5. **`待分类/` 不是垃圾桶**:未识别扩展名要逐条问用户,别一股脑挪进去
6. **去重在前,分类在后**:见场景 3
7. **分批执行 + 重扫**:一次搬几万个文件出错难回滚

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 有歧义的扩展名 | `.ts` 判为视频(也可能是 TypeScript)、`.obj` 判为 3D 模型(也可能是编译产物)、`.m` 判为代码(也可能是 MATLAB)、`.img`/`.iso` 判为备份镜像 | 看 `by_category` 里该类计数是否离谱;必要时 `--keep-dir` 保住原目录 |
| 图纸目录里的效果图 | `图纸/渲染.jpg` 被 `--strict` 判为「该去图片/」 | 默认不加 `--strict` 就不会报;这种混放通常是有意的 |
| pdf 出图与 dwg 分家 | 同一项目的 `.pdf` 进「文档」、`.dwg` 进「图纸」 | 按项目组织的库改用 `--keep-dir '<项目>'`,或整体交给 portfolio-organizer |
| 项目目录被拆散 | `2024_官网改版/` 里的文件被逐个搬走 | 默认已按「疑似项目目录」保留;误判时调大 `--project-min-files` 或加 `--keep-dir` |
| 重名冲突 | 两个目录里的 `x.jpg` 都要进 `图片/` | 脚本自动改 `x__2.jpg` 并标 `review`;更推荐保留来源目录名,让 LLM 改成 `图片/来自a_x.jpg` |
| 无扩展名文件 | `README`、`LICENSE`、导出脚本 → 待分类 | 逐条确认;常见的可手工指定去向 |
| SMB 上扫描慢 | 大库 stat 耗时 | `--sample` 摸底;`--max-depth` 限制层数;分类只 stat 不读内容,比去重快得多 |
| 中文目录名 + shell | `mv 图纸/x.dwg` 引号/转义问题 | 路径一律加双引号;极空间走 `zs mv` 可避开 shell 转义 |

## 已知 gap

- **只按扩展名分类**,不做内容嗅探(magic bytes)、不按文件名语义归类
  (如「XX项目_报价单」自动进该项目目录)——语义判断留给 LLM
- **不建新目录**:脚本只算路径,`mkdir -p` 由 Agent 执行
- **不处理空目录清理**:搬完留下的空壳目录交给 nas-report / 手工 `rmdir`
- **年份取 mtime**:拷贝/下载会刷新 mtime,`--layout *-year` 可能把老文件归到今年
- **疑似项目目录判定是启发式**(≥3 文件 + ≥2 类别):真的项目目录只有 2 个文件时会漏判
- **副本提示是命名启发式**,不是内容判定;内容级去重一律走 dedup-finder
- **计划列表有上限**(`--max-issues`,默认 500):几万文件的库不会一次吐完,
  要按 `--only-cat` 分批;`stats` 始终全量,所以汇报数字不会错
- **同名文件超过 10 个就不再自动编号**:脚本给 `target: null` + 建议命名
  (保留来源目录名),把决定权交回给人,不生成几千条 `x__4999.jpg`

## 故障排查

| 现象 | 处理 |
|------|------|
| `--root 不是有效目录` | 确认已挂载:`ls /Volumes/`;Windows 用 `net use` 映射盘符 |
| `to_move` 为 0 但目录很乱 | 乱在深层子目录:调大 `--max-depth`;或子目录名恰好命中类别别名 → 换 `--strict` 看跨类别项 |
| 待归档数量吓人 | 先 `--dest 整理后` 归到独立子目录试水,确认无误再就地整理;或 `--only-cat` 一类一类来 |
| 报告说 `计划条数已达上限` | 正常保护(避免几 MB JSON 塞爆上下文);用 `--only-cat cad` 这样分批取,或 `--max-issues 0` 全量导出到文件再自己切 |
| `unknown_exts` 一大堆 | 专业软件私有格式(如 `.rfa`/`.pln` 之外的);把该扩展名加进脚本对应 `*_EXTS` 集合再跑 |
| 搬完发现搬错了 | 所以要求分批 + `mv -n`;JSON 里存着完整 `path`/`target`,可反向生成回滚计划 |
| 读取失败进 `errors` | 权限或挂载断连;重连后重扫,脚本不会崩 |
