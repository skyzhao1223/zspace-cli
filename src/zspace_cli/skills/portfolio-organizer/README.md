# Portfolio Organizer Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描作品集(本地挂载路径),以「项目」为单位校验结构完整性。
输出问题清单 + 覆盖率统计(封面/说明/成品目录);mkdir / mv / rename 由 Agent
在用户确认 `old → new` 计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):
正向合规验证 + 脚本只读 + 判断交回 LLM + 先预览后执行。

## 目录结构

```
skills/portfolio-organizer/
├── SKILL.md                # LLM 工作流(触发词 + 场景 + 命名速查)
├── portfolio_organizer.py  # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh            # 烟雾测试(离线,fixture 端到端)
└── README.md               # 本文件

# 用户可选自建(仓库里**不**放,理由见「配置覆盖」):
└── config.json             # 目录白名单 + 扩展名改判的覆盖层
```

## 命令

```bash
python3 skills/portfolio-organizer/portfolio_organizer.py scan \
  --root /Volumes/nas/作品集

python3 skills/portfolio-organizer/portfolio_organizer.py scan \
  --root /Volumes/nas/作品集 --large-gb 1 --json --output /tmp/pf-issues.json
```

## 检出的问题类型

| 层级 | 问题 | 判定 |
|------|------|------|
| root | 根目录散文件 | 文件不属于任何项目目录 |
| root | 垃圾/临时文件 | `.DS_Store`、`._*`、`.tmp`、`~$*` |
| 项目 | 项目名缺年份 | 名字无 `YYYY` 前缀/后缀 |
| 项目 | 缺封面图 | 顶层与成品目录都无 `cover/封面/preview/thumb` 图 |
| 项目 | 缺项目说明 | 顶层无 `README/说明/简介` |
| 项目 | 成品与源文件混放 | 顶层同时有 psd 类与 jpg/pdf 类,且无 `成品/` 目录 |
| 项目 | 空项目目录 | 无任何文件,**且读得到**(读不了的走下一行) |
| 项目 | 无法读取 | `scandir` 抛 `OSError`(权限不足/挂载断连)—— 内容未知,不等于空项目;`errors`/`unreadable_projects` 单列,别据此删除 |
| 项目 | 成品多版本共存 | 成品池内同 base_stem ≥3 个 |
| 文件 | 成品目录混入源文件 | `成品/` 里有 psd/blend 等 |
| 文件 | 大体积源文件 | source 类文件 > `--large-gb`(默认 2GB) |

白名单功能目录(字体/素材库/模板/归档…)不做项目校验;这个集合现在可以用同目录
`config.json` 的 `whitelist_dirs` **追加**(issue #15),自定义目录名不必再改脚本。

`source` / `export` 的归属也可以被 `extension_overrides` 改写,而它触达的是**真判定**
不只是计数:`{"psd": "export"}` 让「成品目录混入源文件」消失,`{"pdf": "source"}`
让它在新位置出现,`{"c4d": "export"}` 让「大体积源文件」与「成品与源文件混放」一起翻。
**`IMAGE_EXTS`(封面判定)刻意不可覆盖** —— 它与 `EXPORT_EXTS` 有 8 个扩展名故意
重叠,可覆盖会让 `cover.png` 静默失去封面资格。详见「配置覆盖」。

## 配置覆盖(`config.json`,issue #15)

可选的一层覆盖,放在**脚本自己所在目录**。**没有这个文件时输出与引入它之前
逐字节一致**。完整 schema、模式锚定规则、出错行为在 `SKILL.md` 的「配置覆盖」
一节(那份是给 LLM 读的),这里只记实现决定。

### 与另外四个 scanner 共用同一段代码(逐字复制)

`zs skill` 的安装方式是**逐目录 `shutil.copytree`**(`cli.py` 的 `skill()`,
`--only a,b` 也一样),所以任何放在 `skills/<name>/` 外面的共享模块都**不会被装到
用户机器上**,一 import 就 ImportError。同仓库 PR #41 遇到过同一件事,当时的选择
就是把工具函数放进 `photo_organizer.py` 而不是新建框架模块;#47 沿用那个先例把
覆盖层复制进了 file-sorter 与 photo-organizer —— 本 PR 继续复制到这三个,
**不**新建 `skills/_shared/`、**不**加包级 import。

共享部分被切成 **PART_A(`CONFIG_*` 声明,9 行 / 813 字节)+ PART_B(`_cfg_die` /
`_cfg_match` / `load_config`,104 行 / 5305 字节)**,五个 scanner 里的这两段
**逐字节相同**(有一个比对脚本逐字节 diff,漂移立刻可见而不是靠人记)。唯一
per-skill 的是夹在中间的 `CONFIG_EXT_TABLES`:

| | file-sorter | photo-organizer | music-organizer | work-organizer | portfolio-organizer |
|---|---|---|---|---|---|
| 类别数 | 15 | 5 | 5 | 11 | 4 |
| 兜底(`None`) | `other` | `non_media` | `non_audio` | `other` | `other` |

五个 skill 的类别体系本来就不同,所以**同一份配置在一边合法、在另一边必须报错**
—— smoke TEST 7 里另外四个 skill 的类别名逐个都要 exit 1,并在报错里列出本 skill
的可用类别。静默接受一个本 skill 不认识的类别名等于静默失效。

本 skill 只有 4 个类别 —— `classify_ext()` 的返回值(`source` `export` `other`)
加上排在它前面的 `junk`。

### `IMAGE_EXTS` 为什么刻意不在 `CONFIG_EXT_TABLES` 里

它与 `EXPORT_EXTS` 有 **8 个扩展名是故意重叠的**(实测交集
`{bmp, gif, jpeg, jpg, png, tif, tiff, webp}`:一张 png 既算成品、又算封面候选)。
而改判语义是「先从**所有**别的表里摘掉,再放进指定那张」,于是两个最自然的写法
都会静默拆掉另一套判定:

| 写法 | 如果 `image` 可覆盖 | 后果 |
|---|---|---|
| `{"png": "export"}` | png 从 `IMAGE_EXTS` 被摘掉 | `cover.png` **静默失去封面资格** → 项目突然多报「缺封面图」 |
| `{"png": "image"}` | png 从 `EXPORT_EXTS` 被摘掉 | `classify_ext("png")` 变 `other` → 成品多版本分组、成品/源文件混放全部失效 |

所以本 skill 只开放 `classify_ext()` 真正返回的那几个类别 + `junk`,`is_cover()`
用的 `IMAGE_EXTS` 保持硬编码。smoke TEST 7 两层都钉住了:端到端 `{"png": "export"}`
与 `{"jpg": "source"}` 之后 `stats.with_cover` 仍是 3、`空格 项目 2020` 不进
「缺封面图」;进程内 import 断言 `IMAGE_EXTS` 九个元素分毫未动、
`is_cover("cover.png")` 仍为 True。代价是**封面格式不能通过配置扩展**(`.avif`
封面这类需求仍要改脚本),已记进两份 gap 清单。

### `whitelist_dirs` 在本 skill 里的语义:**这个目录不是项目**,而且只认 root 一级

内置 `WHITELIST_DIRS` 被读两次,两次都是同一个意思:

```python
# _walk():root 一级目录 → 注册成项目
if len(child_rel) == 1 and name.lower() not in WHITELIST_DIRS:
    self.projects.setdefault(name, Project(name))
# _check_file():它的文件不做项目校验(但仍然计入 stats.files)
if proj_name.lower() in WHITELIST_DIRS:
    return
```

配置层在**这两处**各加一个 `_cfg_match`,语义与内置完全一致:

```python
if (len(child_rel) == 1 and name.lower() not in WHITELIST_DIRS
        and not _cfg_match(child_rel, CONFIG_WHITELIST)):
if (proj_name.lower() in WHITELIST_DIRS
        or _cfg_match([proj_name], CONFIG_WHITELIST)):
    return
```

两处都要改,否则只改 `_walk` 的话,有文件的项目会被 `_check_file()` 里那句
`self.projects.setdefault(proj_name, …)` 重新创建出来。

**判定拿到的相对路径只有 root 下这一级**,所以含 `/` 的模式永远不命中(`_cfg_match`
的「整段相对路径」规则在单元素列表上配不上带 `/` 的模式,「任一级目录名」规则
拿到的也就是那一个名字)。这不是 bug,是内置机制的既有边界;与其让它看起来能用,
不如钉成断言 —— smoke TEST 7 用 4 个含 `/` 的模式断言 `stats.projects` 仍是 8、
问题数与基线逐条相同,同时 stderr 提示与 `stats.config` **照常出现**(配置确实
加载了,只是没有可命中的对象)。SKILL.md 里明写了这一点并要求别写这种模式。

后果是**子树里的文件照扫、照计入 `stats.files` / `total_size_bytes` / `largest`**
(与内置对 `素材库/` 的处理完全一致),`stats.config` 里也**没有** `skipped_files`;
但 `source_files` / `export_files` 会变小,因为白名单目录里的文件不再进
`proj.files`。`Project` / `_eval_project()` / `is_cover()` / `classify_ext()` 的
签名与函数体一行没改。

### 仓库里刻意不放 `config.json`(连 example 也不放)

`pyproject.toml` 的 `package-data` 是 `"zspace_cli.skills" = ["**/*"]`,而
`zs skill` 会把整个目录 copytree 给用户 —— 所以**任何**放进 `skills/<name>/` 的
`config.json` 都会被装到用户机器上并**立刻生效**,等于给所有用户默认开了一份覆盖。
schema 直接写在 SKILL.md 里,用户自己建。

## 设计原则

1. **正向验证** — 定义合规结构(`YYYY_项目名/ + cover + README + 成品/ + 源文件/`)
2. **零依赖** — 纯 stdlib,Python ≥3.9,复制即用
3. **脚本只读** — 无 apply 子命令;写操作走 Agent(shell `mv -n` 或 `zs` CLI/MCP)
4. **项目自包含** — 结构以项目为单位,方便整体拷贝/展示/交付
5. **工程文件不可再生** — SKILL.md 约束源文件禁止自动删,压缩前必须确认
6. **降噪** — 白名单目录、系统与开发目录静默跳过
7. **读失败不静默** — `scandir`/`stat` 失败进顶层 `errors` 键(家族约定,干净时 `[]`);读不了的项目不再伪装成「空项目目录」

## 测试

```bash
bash skills/portfolio-organizer/tests/smoke.sh
```

完全离线:frontmatter 检查 + py_compile + 纯函数用例 + /tmp fixture 端到端 scan。

TEST 7(issue #15)把脚本 `cp` 进临时目录模拟「装好之后」的布局,然后:无配置基线
(16 条问题逐条对齐,并含 `--large-gb 0.000001` 基线)、`练习*` 让 `练习稿` 不再算
项目而 `stats.files` 一条不少、内置 `素材库/` `templates/` 加配置后仍然不算项目、
大小写**双向**(`drafts` 命中 `DRAFTS/`、`WIP` 命中 `wip/`)、`*` 让 8 个项目全部
不算项目但 `files`/`junk` 不变、**4 个含 `/` 的模式加载成功却永不命中**(本 skill
只认 root 一级,钉成断言而不是让它静默失效)、`.bak` 从 junk 救回成 source、
`psd`→export 与 `pdf`→source 让「成品目录混入源文件」双向翻转、`c4d`→export 让
「大体积源文件」与「成品与源文件混放」一起翻(17→15)、`png`/`jpg` 改判**不影响**
封面判定(`IMAGE_EXTS` 刻意不可覆盖)、`.sketch`→兜底 `other`、空对象 `{}`、被扫描
目录里的 `config.json` 必须被忽略(3 种 cwd)、26 种畸形配置逐条 `exit=1` 且
`stdout == ""`、校验先于写入(5 个集合分毫未动)、`is_cover()` 不受覆盖影响、
`_cfg_match` 14 条纯函数断言。

TEST 8(家族审计 F5)钉死「读不了 ≠ 空」:5 MB 的 `chmod 000` 项目产出非空
`errors` 点名它、`unreadable_projects == 1`、收到「无法读取 … 别据此删除」而
**不是**「空项目目录」,真空的兄弟项目**仍然**被判空(负控制,防修复退化成
「不再报空项目」),`total_size_bytes` 不含那 5 MB;干净树 `errors == []`。
权限段在 Windows 上跳过并明说(chmod 000 在那里不产生 scandir 失败)。

## 读失败:根因与修复(F5)

跨 scanner 一致性审计发现,与 backup-auditor 的 F1 同族:**把「不知道」当成「没有」**。

`_walk()` 的 `scandir` 失败原本只打一行 `⚠️ 无法读取` 到 stderr,然后整棵子树静默
跳过。后果(修复前实测):一个 `chmod 000` 但内含真实 5 MB `big.psd` 的项目,产出的
issue 与真正的空项目**逐字节相同** —— 「空项目目录(建议删除或补充内容)」,
`total_size_bytes` 里那 5 MB 完全不可见,JSON 顶层没有 `errors` 键,唯一线索是
Agent 捕获输出时经常被吞掉的 stderr 行。而「空项目目录」是本 skill 唯一的删除引导。

修复(与 backup-auditor F1 语义逐条对齐):

- `Scanner.errors` 通道:`scandir` 与 `stat` 失败都记 `{path, error}`;JSON 顶层
  **总是**输出 `errors`(干净时 `[]`,最多 50 条),与 file-sorter / dedup-finder /
  backup-auditor 同名同义
- root 直下读不了的项目 → 专门 issue「无法读取(权限不足或挂载断连),内容未知 ——
  不等于空项目,别据此删除」,`stats.unreadable_projects` 单列,空项目判定排除它们
- 文件 `stat` 失败时大小未知:不计入 `total_size_bytes`(算 0 会低报,估算又是假精确),
  `stats.unreadable_files` 单列
- 人类可读报告加 `⚠️ 读不了的项目/文件` 行与「读取失败」明细段(措辞与家族一致)

## 已知 gap

- 成品/源文件目录名靠白名单匹配(中英双语),自定义名需改 `FINAL_DIR_NAMES` / `SOURCE_DIR_NAMES`
- 「最终版」语义靠用户确认,脚本只按文件名 base_stem 分组
- 冷门设计软件格式不在 `SOURCE_EXTS`,现在可以用同目录 `config.json` 的
  `extension_overrides` 追加(issue #15);**但 `IMAGE_EXTS`(封面判定)刻意不可覆盖**
  —— 它与 `EXPORT_EXTS` 有 8 个扩展名故意重叠,可覆盖会让 `cover.png` 静默失去
  封面资格,所以 `.avif` 封面这类需求仍要改脚本
- `whitelist_dirs` 只作用于 root 一级(继承内置语义),含 `/` 的模式永远不命中;
  「项目内某个子目录别做校验」做不到
- `config.json` 是 per-安装而非 per-库;`--config PATH` 未做(要先定「与旁边那份是
  替换还是叠加」)
- 项目跨年改版视为两个项目,合并策略交用户

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
