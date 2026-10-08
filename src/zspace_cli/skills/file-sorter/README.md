# File Sorter Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描任意目录(本地挂载路径),把「什么都往里丢」的乱目录
按扩展名归类,并为每个待归档文件算出 `old → new` 目标路径。
`mkdir -p` / `mv -n` 由 Agent 在用户确认整理计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

定位:family 里唯一的**类型驱动通用分类器**。其他 organizer 都是「某个领域的
合规校验」(照片按日期、音乐按歌手专辑、作品集按项目),本 skill 是「不管什么
领域,先按类型把散文件归位」,常作为进入专项 skill 之前的第一道工序。

## 目录结构

```
skills/file-sorter/
├── SKILL.md          # LLM 工作流(触发词 + 4 个场景 + 整理顺序)
├── file_sorter.py    # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh      # 烟雾测试(离线,fixture 端到端 + 合规库零误报)
└── README.md         # 本文件
```

## 命令

```bash
python3 skills/file-sorter/file_sorter.py scan --root /Volumes/nas/data

# 类别 + 年份,英文目录名,全部归到「整理后/」下试水
python3 skills/file-sorter/file_sorter.py scan \
  --root /Volumes/nas/data --layout type-year --naming en --dest 整理后 \
  --json --output /tmp/sort.json

# 保住项目目录,只归位散文件
python3 skills/file-sorter/file_sorter.py scan \
  --root /Volumes/nas/data --keep-dir '2024_*' --keep-dir '成品*'

# 大库分批:这轮只出图纸的计划(统计仍全量)
python3 skills/file-sorter/file_sorter.py scan \
  --root /Volumes/nas/data --only-cat cad --output /tmp/sort-cad.json
```

## 每个文件的判定阶梯(顺序即优先级)

```
1. protected    命中 --keep-dir 白名单            → 跳过,计入 stats.protected
2. compliant    祖先目录名能映射到本文件的类别      → 跳过,计入 stats.compliant
3. nested       祖先目录是「别的」类别目录          → 默认跳过(nested_other_cat)
                                                      --strict 才提议搬家
4. project      落在疑似项目目录里                 → 默认跳过(project_files),
                                                      改为一条目录级 issue
                                                      --split-project-dirs 才拆
5. filtered     类别不在 --only-cat 里             → 跳过,计入 stats.filtered_out
6. 其余                                              → 出 issue:
     junk(系统垃圾)                                action=delete
     junk(临时/残留 .log/.tmp/.bak)                action=delete-confirm
     other(扩展名不认识/无扩展名)                  action=review,target=待分类/
     重名冲突(目标已被占)                          action=review,target 加 __2
     正常                                           action=move,target=<类别>/…
```

第 3、4 步是**防误报的核心**:`图纸/渲染.jpg` 和 `2024_官网改版/` 里的混合文件
都是「有意的结构」,默认一律不动,只在 `stats` 里计数 + 报一条目录级 issue
让人决定。这是与「无脑按扩展名平铺」脚本的关键区别。

## 疑似项目目录判定

```
子目录 d(非类别目录、非白名单)的子树里:文件数 ≥ --project-min-files(默认 3)
                              且 跨 ≥ 2 个类别
→ d 记为疑似项目目录;只报最外层(嵌套的不重复报)
```

`stats.project_dirs` / `project_files` 给总量;每个目录一条 `is_dir: true` 的
issue(带 `files` / `categories` / `sample`),`action=review`。

## 目标路径与重名

- `target_parts(cat, layout, mtime, naming)` 决定目录层级:
  `type` → `图纸/`;`type-year` → `图纸/2024/`;`year-type` → `2024/图纸/`
- `--dest` 再加一层前缀:`整理后/图纸/`
- `occupied` 集合 = 所有现存相对路径 + 已分配的目标;撞名时依次试
  `x__2.ext` / `x__3.ext`,并把原候选写进 `conflict_with`、降级为 `review`
- **撞名封顶**(`MAX_RENAME_TRIES = 10`):同名文件多于 10 个时不再自动编号,
  返回 `target=None` + 建议保留来源目录名(`图片/d9_x.jpg`),`confidence=low`。
  两个理由:① 避免几千个同名文件时的 O(n²) 试探;② `x__4999.jpg` 这种计划
  对用户毫无价值,命名规则该由人定

## 疑似副本(不做内容判定)

只对**文件名带副本标记**(`(1)` / `[2]` / `副本` / `copy`)的文件,查是否存在
「同大小 + 去标记后同名」的另一个文件;有则加一条 problem 并计入
`stats.dup_suspects`,引用对象优先选**不带标记的那个**(本尊)。

这是命名启发式,**不是**去重:内容级判定一律交给 `dedup-finder`
(三级指纹零误报)。SKILL.md 场景 3 明确要求「先去重、再分类」。

## JSON 输出

```jsonc
{
  "skill": "file-sorter", "root": "...", "generated_at": "...",
  "stats": {
    "files", "dirs", "total_size_bytes",
    "by_category": {"doc": {"count", "size"}, ...},   // 15 类,含 junk/other
    "root_files",                                      // 散在根目录的
    "compliant", "protected", "nested_other_cat",      // 三种「不动」
    "project_dirs", "project_files",
    "to_move", "to_review", "to_delete",
    "move_bytes", "junk_bytes", "kept_bytes",
    "dup_suspects", "conflicts", "stale_files",
    "filtered_out",                                      // --only-cat 挡掉的
    "omitted_issues", "issues_truncated",                // 计划截断(--max-issues)
    "target_dirs": {"图纸": {"count", "size"}},        // 归档后结构预览
    "unknown_exts": {".xyz": 3}, "largest": [...],
    "layout", "naming", "dest", "strict", "split_project_dirs",
    "only_cats", "max_issues",
    "sampled_out", "elapsed_sec"
  },
  "count": 17,
  "issues": [{
    "path", "name", "is_dir", "problems": [...], "category", "category_zh",
    "action",            // move | review | delete-confirm | delete
    "target",            // old → new 的 new;junk / 撞名封顶 时为 null
    "size", "age_days", "confidence",   // high | medium | low
    "conflict_with"?, "files"?, "categories"?, "sample"?   // 目录级 issue
  }],
  "errors": [{"path", "error"}]
}
```

**`stats` 永远全量,`issues` 会被 `--max-issues`(默认 500)截断。**
理由:2 万文件的库全量计划是 2.5 MB JSON,任何 Agent 都读不进去;截断后
203 KB,而汇报用的数字(待归档多少、能回收多少)一个不少。目录级 issue
(疑似项目目录)不参与截断 —— 那是「别动」的信号,不能被淹掉。
分批取计划的正确姿势是 `--only-cat`,不是调大 `--max-issues`。

## 实测规模(本地 SSD,macOS,Python 3.9/3.11)

| 场景 | 文件数 | 扫描耗时 | JSON 大小 |
|------|-------|---------|-----------|
| 混合乱目录(4 层深、57 目录) | 20 000 | **0.28 s** | 203 KB(截断后) |
| 单层平铺照片库 | 100 000 | **1.73 s** | 187 KB(截断后) |
| 5 000 个同名文件(撞名压测) | 5 000 | **0.22 s** | — |

只 `stat`、不读文件内容,所以比 `dedup-finder`(要算 sha1)轻得多。
**注意**:以上是本地 SSD 数字;**SMB 挂载上的 stat 延迟是真正的瓶颈**,
几万文件的小库建议先 `--sample 2000` 摸底再全量。

## 类别表(15 类)

`cad`(图纸/CAD/3D)、`doc`、`design`(设计源文件)、`image`、`video`、`audio`、
`ebook`、`archive`、`installer`、`font`、`code`、`backup`(镜像)、`torrent`、
`junk`、`other`。

与 `nas-report` 的 `CATEGORIES` 有意**不完全一致**:本 skill 从 `design` 里分出
`cad`(dwg/dxf/step/sldprt/rvt/skp…),从 `doc` 里分出 `ebook`,把 `iso`/`img`
从 video 挪到 `backup`,并新增 `font`。原因:分类归档要落到**目录名**上,
「图纸」对用户是有意义的独立目录,而存储画像只需要粗粒度占比。

目录名识别(`_DIR_TO_CAT`)同时吃中英文别名:`图纸`/`CAD`/`Drawings`/`施工图`、
`文档`/`Documents`/`资料`、`图片`/`照片`/`Images`/`img`…,所以已手工分好类的库
不会被再搬一次。别名表刻意保守 —— 认错目录(把项目目录当类别目录)比不认更糟。

## 设计原则

1. **只读** — 无 apply/mv/rm 子命令;搬家走 Agent + 用户确认
2. **零依赖** — 纯 stdlib,Python ≥3.9
3. **正向合规** — 定义「文件该在与类别匹配的目录里」,报偏离,不枚举脏模式
4. **默认保守** — 有意的结构(类别目录内、项目目录内)不动,要动得显式加 flag
5. **目标路径算好** — LLM 不用自己拼路径,直接读 `target`;重名已去重
6. **不越界** — 不做内容去重(dedup-finder)、不做版本梳理(work-organizer)、
   不做项目规范(portfolio-organizer)

## 测试

```bash
bash skills/file-sorter/tests/smoke.sh
PY=/usr/bin/python3 bash skills/file-sorter/tests/smoke.sh   # 3.9 兼容验证
```

完全离线;fixture 覆盖:14 类扩展名归类 + junk 优先、合规库**零误报**、
跨类别嵌套默认不动 / `--strict` 才动、疑似项目目录整体保留 /
`--split-project-dirs` 才拆、`--keep-dir` 白名单、重名冲突改名 `__2`、
**12 个同名文件撞名封顶**(溢出项 `target=null` + 建议来源目录名;
这条是回归测试,曾经因为把 tuple 传给 `os.path.basename` 而崩)、
疑似副本指向本尊、junk 两种 action、`--layout` 三种 × `--naming` 两种 ×
`--dest` 的目标路径、`--max-issues` 截断(stats 仍全量、目录级 issue 不被截断)、
`--only-cat` 分批。

**未覆盖**:真实 SMB 挂载上的性能、真实 CAD 库的扩展名分布
(taxonomy 是按公开格式清单写的,没跑过真实图纸库)、Windows 路径语义。

## 已知 gap

- 只按扩展名分类:不嗅探 magic bytes,不按文件名语义归项目
- 不建目录、不清空目录(搬完的空壳交给 nas-report / 手工 `rmdir`)
- 年份取 mtime,拷贝/下载会刷新 → `*-year` 布局可能把老文件归到今年
- 项目目录判定是启发式(≥3 文件 + ≥2 类),2 个文件的真项目会漏判
- 类别表是硬编码集合;专业软件私有格式要改脚本(见 SKILL.md 故障排查)
  —— 与 roadmap #15(per-skill config overrides)是同一个缺口
- **taxonomy 未在真实图纸库上验证过**:`.prt`/`.asm`/`.obj`/`.ts`/`.m` 这类
  跨领域扩展名的归属是按主流用法猜的,真实反馈前别说"图纸全认得"

## 参考

- 模式来源:`work-organizer` / `download-cleaner`(同仓库,只读扫描 + LLM 出计划)
- 类别表参照:`nas-report` 的 `CATEGORIES`
- 搭配:`dedup-finder`(先去重再分类)、`nas-report`(先画像再决定跑哪个)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
