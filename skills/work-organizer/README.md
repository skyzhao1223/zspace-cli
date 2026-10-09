# Work Organizer Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描工作文件库(本地挂载路径)的结构与命名合规性。
输出问题清单 + 统计(类型分布/最大文件/最旧文件);mkdir / mv / rename / rm
由 Agent 在用户确认 `old → new` 计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):
正向合规验证 + 脚本只读 + 判断交回 LLM + 先预览后执行。

## 目录结构

```
skills/work-organizer/
├── SKILL.md           # LLM 工作流(触发词 + 场景 + 命名速查)
├── work_organizer.py  # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh       # 烟雾测试(离线,fixture 端到端)
└── README.md          # 本文件

# 用户可选自建(仓库里**不**放,理由见「配置覆盖」):
└── config.json        # 目录白名单 + 扩展名改判的覆盖层
```

## 命令

```bash
python3 skills/work-organizer/work_organizer.py scan \
  --root /Volumes/nas/工作

# 严格模式 + 归档候选
python3 skills/work-organizer/work_organizer.py scan \
  --root /Volumes/nas/工作 --strict-naming --archive-years 3 \
  --json --output /tmp/work-issues.json
```

## 检出的问题类型

| 问题 | 判定 |
|------|------|
| 根目录散文件 | 文件直接在 root 下,带 `mtime`+`suggested_dir`(年份) |
| 版本标记混乱 | 名字含 最终/终版/final/修改版/打死不改 等 |
| 同名文档多版本共存 | 同目录 base_stem 相同 ≥3 个(剥离日期/vN/最终版/副本后归一分组) |
| 副本文件 | `(1)`/`副本`/`copy`/`拷贝` 后缀 |
| 临时/锁定/垃圾 | `~$*`、`.~*`、`._*`、`.bak`、`.tmp`、`.DS_Store` |
| 安装包/镜像 | dmg/pkg/exe/msi/apk/iso 混在工作区 |
| 临时/无语义目录 | `新建文件夹`、`test`、`aaa`、纯数字 |
| 副本目录 | 目录名带副本标记 |
| 缺日期前缀(可选) | `--strict-naming`:办公文档无 `YYYYMMDD_` 前缀 |
| 过期归档候选(可选) | `--archive-years N`:mtime 超 N 年且不在归档目录 |

上面「临时/锁定/垃圾」「安装包/镜像」两条,以及 `by_type` 的全部归类,都可以被
`config.json` 的 `extension_overrides` 改写(issue #15):`{"bak": "doc"}` 把一个
文件从「垃圾可删」救回成正常文档,`{"exe": "archive"}` 让它不再算安装包;
`{"md": "code"}` 还会连带关掉 `--strict-naming` 对它的日期前缀要求。
「临时/无语义目录」「副本目录」两条则可以用 `whitelist_dirs` 豁免(**只豁免目录名,
子树里的文件照扫照报**)。详见「配置覆盖」。

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

本 skill 的 11 个类别就是 `classify_ext()` 的返回值(`doc` `sheet` `ppt` `pdf`
`archive` `installer` `design` `code` `media` `other`)加上排在它前面的 `junk`。
10 张表两两不相交(实测过),所以「先摘掉再放进」在这里不会误伤别的类别。

### `whitelist_dirs` 在本 skill 里的语义:**只豁免目录名**

内置 `WHITELIST_DIRS` 全仓库只被读一次,就在 `dir_problems()` 的第一行:

```python
def dir_problems(name, depth):
    if name.lower() in WHITELIST_DIRS or YEAR_DIR_OK.match(name):
        return []                       # ← 唯一的作用点
```

所以配置层就开在**这个判定点的调用处**,而不是发明一套「别扫这块」的新语义:

```python
child_rel = rel_parts + [name]
problems = ([] if _cfg_match(child_rel, CONFIG_WHITELIST)
            else dir_problems(name, len(child_rel)))
```

`dir_problems()` 的**签名和函数体一行没改**,TEST 3 那批直接调它的纯函数断言完全
不受影响 —— smoke TEST 7 里还专门钉了一条:配置生效前后 `dir_problems("temp", 1)`
的返回值一模一样,`WHITELIST_DIRS` 里 `合同` 还在、`temp` 没被塞进去。

**后果是子树里的文件照扫照报**:把 `temp/` 加进白名单,`temp/b.tmp` 仍然会被报
「临时/锁定/垃圾文件」,`stats.dirs` / `stats.files` 一个都不变,`stats.config`
里也**没有** `skipped_files` 这个字段(没跳过任何文件)。这与 photo/music 的
「整棵跳过」不同,差别是从各自**既有机制**继承来的,不是新发明的;三份 SKILL.md
都放了对照表。

`extension_overrides` 触达的不只是 `by_type`:`INSTALLER_EXTS` 还被 `_check_file()`
直接读了一次(`if ext in INSTALLER_EXTS: … "安装包/镜像混在工作区"`),所以
`{"exe": "archive"}` 会让那条问题消失、`stats.installers` 从 2 变 1。smoke 里
`{"md": "code"}` 更能说明阶梯顺序被尊重:`by_type.doc` 17→14、`code` 0→3,而且
`--strict-naming` 的问题数 34→33(那条检查只对 `doc`/`sheet`/`ppt`/`pdf` 生效)。

### 仓库里刻意不放 `config.json`(连 example 也不放)

`pyproject.toml` 的 `package-data` 是 `"zspace_cli.skills" = ["**/*"]`,而
`zs skill` 会把整个目录 copytree 给用户 —— 所以**任何**放进 `skills/<name>/` 的
`config.json` 都会被装到用户机器上并**立刻生效**,等于给所有用户默认开了一份覆盖。
schema 直接写在 SKILL.md 里,用户自己建。

## 设计原则

1. **正向验证** — 定义合规结构(年份/项目 + `YYYYMMDD_项目_主题_vN`),不枚举脏模式
2. **零依赖** — 纯 stdlib,Python ≥3.9,复制即用
3. **脚本只读** — 无 apply 子命令;写操作走 Agent(shell `mv -n` 或 `zs` CLI/MCP)
4. **删除必须暂存** — 工作文件优先 `mv` 到 `归档/_待删/`,不直接 rm
5. **降噪** — `node_modules`/`.git`/`@eaDir` 等系统与开发目录静默跳过
6. **可选严格度** — 日期前缀与归档检查默认关,避免首次扫描刷屏

## 测试

```bash
bash skills/work-organizer/tests/smoke.sh
```

完全离线:frontmatter 检查 + py_compile + 纯函数用例 + /tmp fixture 端到端 scan。

TEST 7(issue #15)把脚本 `cp` 进临时目录模拟「装好之后」的布局,然后:无配置基线
(23 条问题逐条对齐,并含 `--strict-naming` / `--archive-years 3` 两条基线)、
`["temp"]` 一次豁免 3 个同名目录而 `["2024/temp"]` 只豁免 1 个(23→20 vs 23→22,
互相可区分)、大小写**双向**(`TEMP` 命中 `temp/`、`tmp` 命中 `TMP/`)、`项目*`
glob、`*` 抹掉全部 6 条目录名问题但**文件级检查一条不少**、`.bak` 从 junk 救回成
doc、`md`→code 让 `--strict-naming` 的问题数 34→33、`exe`→archive 让
「安装包混在工作区」消失、`.pptx`→兜底 `other`、空对象 `{}`、被扫描目录里的
`config.json` 必须被忽略(3 种 cwd)、25 种畸形配置逐条 `exit=1` 且
`stdout == ""`、校验先于写入(11 个集合分毫未动)、`dir_problems()` 未被改动
(配置生效前后返回值一致)、`_cfg_match` 14 条纯函数断言。

## 已知 gap

- 项目归属推断靠文件名 + LLM,无语义索引
- 不做内容级去重(hash);同名多版本按文件名分组
- `WHITELIST_DIRS` 写死常见职能目录名,公司特有目录现在可以用同目录 `config.json`
  的 `whitelist_dirs` 追加(issue #15);但它**只豁免目录名**,子树里的文件照扫照报
- `config.json` 改不了:`BAD_DIR`/`VERSION_CHAOS_RE`/`COPY_MARK_RE`/`DATE_*_RE` 正则、
  `LOCK_PREFIXES`、`ARCHIVE_DIR_RE`、`base_stem()` 归一规则与同名多版本阈值
- `config.json` 是 per-安装而非 per-库;`--config PATH` 未做(要先定「与旁边那份是
  替换还是叠加」)
- 版本组「保留最新」按文件名排序,语义判断交给 LLM(JSON 带 mtime)

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
