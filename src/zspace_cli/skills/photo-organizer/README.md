# Photo Organizer Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描照片/视频库(本地挂载路径)的结构与命名合规性。
输出问题清单 + 每文件的日期提取结果;mkdir / mv / rename 由 Agent 在用户
确认 `old → new` 计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):
正向合规验证 + 脚本只读 + 判断交回 LLM + 先预览后执行。

## 目录结构

```
skills/photo-organizer/
├── SKILL.md            # LLM 工作流(触发词 + 场景 + 命名速查)
├── photo_organizer.py  # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh        # 烟雾测试(离线,fixture 端到端)
└── README.md           # 本文件

# 用户可选自建(仓库里**不**放,理由见「配置覆盖」):
└── config.json         # 目录白名单 + 扩展名改判的覆盖层
```

## 命令

```bash
python3 skills/photo-organizer/photo_organizer.py scan \
  --root /Volumes/nas/照片

python3 skills/photo-organizer/photo_organizer.py scan \
  --root /Volumes/nas/照片 --json --output /tmp/photo-issues.json

# 可选(issue #14):用 EXIF 拍摄日期定档
python3 skills/photo-organizer/photo_organizer.py scan \
  --root /Volumes/nas/照片 --exif
```

## 检出的问题类型

| 问题 | 判定 |
|------|------|
| 散文件 | 照片/视频直接在 root 或 `YYYY/` 下,给出 `date`+`suggested_dir` |
| 目录名不符合日期规范 | 非 `YYYY`/`YYYY-MM`/`YYYY-MM-DD 事件` 且不在白名单 |
| 相机/手机原始卷目录 | `100APPLE`、`101_PANA` 等 DCIM 卷名 |
| 临时/未命名目录 | `新建文件夹`、`untitled`、`测试` 等 |
| 疑似连拍组 | 同目录同前缀连号 ≥5 张(IMG_3001-3005) |
| 疑似重复副本 | 文件名带 `(1)`/`副本`/`copy` 后缀 |
| 非媒体文件混入 | 照片库里的 pdf/zip/doc 等 |
| 垃圾/系统残留 | `._*` AppleDouble、`.part`/`.td` 下载残留 |
| sidecar 附属文件 | `.aae`/`.xmp` — 移动主图时需跟随 |

上面「非媒体文件混入」和「sidecar」两条的**扩展名归属可以被 `config.json` 改写**
(issue #15):`{"ass": "sidecar"}` 让字幕从「建议移出」变成「跟着主图走」,
`{"vob": "video"}` 让原盘里的 VOB 不再算侵入者。详见「配置覆盖」。

日期提取优先级:mmexport epoch → 微信图片_ → Screenshot/截屏 →
`YYYY-MM-DD` 前缀 → `YYYYMMDD` 紧凑 → 相机带日期名 → 全文严格日期 → mtime(弱)。

文件名提不出日期时,`--exif` 会插入两级更强的证据(默认不开):

```
filename → exiftool -DateTimeOriginal → mdls(仅 macOS) → mtime
```

每级都失败即退、不抛异常:二进制不存在、非零退出、输出不是合法 JSON、文件不在输出里、
日期是坏值(`0000:00:00`、1899 年、2 月 30 日)统统交给下一级。探测用 `shutil.which`
且**每次运行只做一次**;exiftool **批量**调用(200 个路径 + `-json`)。
实测 464 个文件:批量 exiftool 0.50s(≈0.9ms/张),逐个 mdls 11.4s(≈24ms/张)。

`mdls` 不能批量 —— 它收多个文件时只按顺序打印值、不带文件名表头,而且遇到一个未被
Spotlight 索引的文件就中止整批,按行号回填会把日期安到错误的文件上。

## 配置覆盖(`config.json`,issue #15)

可选的一层覆盖,放在**脚本自己所在目录**。**没有这个文件时输出与引入它之前
逐字节一致**。完整 schema、模式锚定规则、出错行为在 `SKILL.md` 的「配置覆盖」
一节(那份是给 LLM 读的),这里只记实现决定。

### 与 file-sorter 共用同一段代码(逐字复制)

`zs skill` 的安装方式是**逐目录 `shutil.copytree`**(`cli.py` 的 `skill()`,
`--only a,b` 也一样),所以任何放在 `skills/<name>/` 外面的共享模块都**不会被装到
用户机器上**,一 import 就 ImportError。同仓库 PR #41 遇到过同一件事,当时的选择
就是把工具函数放进 `photo_organizer.py` 而不是新建框架模块 —— 这里沿用那个先例,
**不**新建 `skills/_shared/`、**不**加包级 import。

共享部分被切成 **PART_A(`CONFIG_*` 声明,9 行)+ PART_B(`_cfg_die` /
`_cfg_match` / `load_config`,104 行)**,与 file-sorter 里的那份**逐字节相同**;
唯一 per-skill 的是夹在中间的 `CONFIG_EXT_TABLES`:

| | file-sorter | photo-organizer |
|---|---|---|
| 类别 | 15 个(`doc` `cad` `design` … `junk` `other`) | 5 个(`photo` `video` `sidecar` `junk` `non_media`) |
| 兜底(`None`) | `other` | `non_media` |

两边的类别体系本来就不同,所以**同一份配置在一边合法、在另一边必须报错** ——
smoke TEST 9 里 `{"ass": "doc"}` 和 `{"ass": "cad"}` 都要 exit 1,并在报错里列出
本 skill 的 5 个类别。静默接受一个本 skill 不认识的类别名等于静默失效。

### `whitelist_dirs` 在本 skill 里的语义:**这块别扫**

这是与 file-sorter 唯一的实质差别,来源是两边内置机制本来就不同:

| | file-sorter | photo-organizer |
|---|---|---|
| 追加到 | `--keep-dir` | `WHITELIST_DIRS` |
| 目录名判定 | (无此概念) | 命中的目录不再判「目录名不符合日期规范」 |
| 子树里的文件 | **仍计入 `stats`**,计入 `stats.protected`,只是不出计划 | **整棵跳过**:不计入 `stats.files`、不产任何问题;数量记在 `stats.config.skipped_files` |
| root 下的散文件 | 不吃白名单 | 不吃白名单 |

选「跳过」而不是「扫了但不报」,是因为 issue #15 的原话就是 "paths/patterns the
scanner **skips**",而且本 skill 的白名单本来就是**目录名合规**豁免 —— 一个
`原盘/VIDEO_TS` 树里几十个 `.VOB` 每个都报一条「非媒体文件混入照片库」,只豁免
目录名而不豁免文件,等于没解决用户的抱怨。

代价是配了白名单之后 `stats.files` 会**变小**,汇报时容易讲错。所以:
① `skipped_files` 明确给出被跳过的数量;② `stats.dirs` 仍然把白名单目录本身算进去;
③ SKILL.md 里专门有一节讲这个差别,并要求两个数字一起汇报。

### 接入点只有 4 处,且默认值保证零变化

```python
def _walk(self, dir_path, rel_parts, wl: bool = False) -> None:   # +1 个默认参数
    ...
    child_wl = wl or _cfg_match(child_rel, CONFIG_WHITELIST)      # 新增
    problems = [] if child_wl else dir_problems(name, depth, parent_is_year)
    self._walk(entry.path, child_rel, child_wl)                   # 多传一个参数
    ...
    if wl:                                                        # 新增
        self._wl_files += 1
        continue
```

`dir_problems()` 的签名和函数体**一行没改**(它仍然只看内置 `WHITELIST_DIRS`),
所以 TEST 3 那批直接调它的纯函数断言不受影响。没有 `config.json` 时
`CONFIG_WHITELIST == []` → `_cfg_match` 的第一行 `if not patterns` 直接返回 False
→ `child_wl` 恒为 False → 上面两处新分支一行都不会走到。`stats["config"]` 与
`--exif` 的 `date_sources` 是同一个手法(#41 的先例):只在真启用时才多这个键。

### 仓库里刻意不放 `config.json`(连 example 也不放)

`pyproject.toml` 的 `package-data` 是 `"zspace_cli.skills" = ["**/*"]`,而
`zs skill` 会把整个目录 copytree 给用户 —— 所以**任何**放进 `skills/<name>/` 的
`config.json` 都会被装到用户机器上并**立刻生效**,等于给所有用户默认开了一份覆盖。
schema 直接写在 SKILL.md 里,用户自己建。

## 设计原则

1. **正向验证** — 定义合规结构(年/月/事件),不枚举脏模式
2. **零依赖** — 纯 stdlib,Python ≥3.9,复制即用。`--exif` 也守这条:不 import
   Pillow/exifread,而是 shell out 到系统里已有的 exiftool / mdls
3. **脚本只读** — 无 apply 子命令;写操作走 Agent(shell `mv -n` 或 `zs` CLI/MCP)
4. **弱证据标注** — `date_source` 区分 filename / exif / mdls / mtime;mtime 必须让用户
   抽查,mdls 也不是 EXIF(是 Spotlight 内容创建时间),同样要抽查
5. **降噪** — 系统元数据目录(@eaDir/#recycle/.Trashes)、开发目录(node_modules/.git)与点文件静默跳过
6. **opt-in 且可证伪** — 不加 `--exif` 时输出与旧版**逐字节一致**,连 JSON 字段都不多一个;
   smoke test 里有一条负控制专门盯这件事。`config.json` 沿用同一条:没有这个文件时
   输出与引入覆盖层之前**逐字节一致**,`stats` 里连 `config` 这个键都不出现
7. **配置有问题就报错退出,绝不静默忽略** — 键名拼错一个字母会让整份配置悄悄失效,
   而现象是「我明明把 `原盘/` 加进白名单了它还在报」。校验**全部跑完**才动手改
   内置集合,半途退出不会留下半张改过的表

## 测试

```bash
bash skills/photo-organizer/tests/smoke.sh
```

完全离线:frontmatter 检查 + py_compile + 纯函数用例 + /tmp fixture 端到端 scan。

**TEST 9(config.json 覆盖层)** 把脚本复制进临时目录来模拟「装好之后」的样子
(配置按 `__file__` 解析),覆盖:无配置时 `stats` 里没有 `config` 键、issue 里的
原例 `{"ass": "sidecar"}`、键归一化(`.VOB`)、把内置 sidecar `.aae` 改判成
`photo`(这条专门验证「先从别的表里摘掉」那一步:不摘就会同时命中两张表)、
兜底类别 `non_media`、`原盘` / `原盘/*` / `深层/原盘/*` 三种锚定的**互相可区分**的
结果、`*` 也管不到 root 下的散文件、空对象 `{}`、键缺省、**被扫描目录里的
`config.json` 被忽略**(三种 cwd)、17 种畸形配置逐条 exit=1 + 只写 stderr + 指名键、
校验先于写入(内置三张表分毫未动)、`_cfg_match` 的 14 条纯函数断言、
`whitelist_dirs` 是追加(内置的 `截图/` 加了配置后仍然合规)、大小写不敏感**双向**成立。

与 file-sorter 的那批断言一起做过变异测试(`/tmp/mutate.py`,PR 里贴了输出):
33 处故意破坏全部被至少一条断言检出。过程中查出 3 条自己的假断言,其中两条在
本 skill:① 畸形配置清单当时是 file-sorter 的子集,漏了「空字符串」那条,对应的
破坏在这边活了下来;② 大小写不敏感只测了「目录大写 / 模式小写」一个方向 ——
实现里相对路径总是先 `.lower()`,所以那个方向即使忘了给模式做 `lower` 也照样过。
两条都已修正(补齐用例 + 补反方向断言,并加了端到端的双向用例)。

`--exif` 的两组测试(TEST 7 / TEST 8)**不依赖机器上装了 exiftool**:

- TEST 7 把 `subprocess.run` 换成假的,直接考 `DateResolver` 的契约 —— 批量调用数、
  同名不同目录不串日期、`exit=1` 但 JSON 有效时照用、坏输出/OSError 失败即退、
  mdls 逐个调、`(null)` 不认账、非 macOS 不探测 mdls
- TEST 8 端到端:把 PATH 换成空目录(exiftool/mdls 都探测不到),断言**无 EXIF 的
  fixture 退回 mtime**;再断言此时产物与不加 `--exif` 时逐条相同(含顺序);
  机器上真有 exiftool 时额外跑一遍 happy path,否则显式打印跳过

## 已知 gap

- `--exif` 依赖外部二进制,脚本自己不解析 EXIF(纯 stdlib 硬规则);两者都不在时
  等于没开,只有 mtime
- 只读 `DateTimeOriginal`,不回退 `CreateDate`/`ModifyDate`,也不看 XMP sidecar
- `mdls` 对 NAS 挂载盘基本无效(Spotlight 没索引),挂载盘场景实际只有
  「exiftool 或 mtime」两档
- 无内容级去重(感知哈希);连拍仅按文件名连号识别
- ~~白名单功能区目录名写死中文/英文常见集合,自定义库名需改 `WHITELIST_DIRS`~~ →
  **已解决**(issue #15):同目录 `config.json` 的 `whitelist_dirs` 追加自定义库名,
  `extension_overrides` 追加自定义扩展名归属,都不用改脚本
- **`config.json` 只能改扩展名归类与目录白名单**:`BAD_DIR` / `DATE_DIR_OK` /
  `CAMERA_ROLL_DIR` 这些目录名正则、连拍阈值(≥5 张连号)、`MIN_PLAUSIBLE_YEAR`
  都还是硬编码的
- **`config.json` 是 per-安装、不是 per-库**:多个照片库想用不同白名单,目前只能
  装两份 skill。加一个可选的 `--config PATH` 是自然的后续

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 影视命名规范:https://github.com/skyzhao1223/media-naming-guide
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
