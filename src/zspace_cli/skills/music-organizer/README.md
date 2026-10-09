# Music Organizer Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描音乐库(本地挂载路径)的「歌手/专辑/曲目」三层结构合规性。
内置**最小 ID3v2 解析器**(纯 stdlib),可选对照路径与标签一致性。
mkdir / mv / rename 由 Agent 在用户确认 `old → new` 计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):
正向合规验证 + 脚本只读 + 判断交回 LLM + 先预览后执行。

## 目录结构

```
skills/music-organizer/
├── SKILL.md            # LLM 工作流(触发词 + 场景 + 命名速查)
├── music_organizer.py  # CLI:scan(纯 stdlib,零依赖,含 ID3v2 解析)
├── tests/
│   └── smoke.sh        # 烟雾测试(离线,fixture + 手工构造 ID3 帧)
└── README.md           # 本文件

# 用户可选自建(仓库里**不**放,理由见「配置覆盖」):
└── config.json         # 目录白名单 + 扩展名改判的覆盖层
```

## 命令

```bash
python3 skills/music-organizer/music_organizer.py scan \
  --root /Volumes/nas/音乐

python3 skills/music-organizer/music_organizer.py scan \
  --root /Volumes/nas/音乐 --read-tags --tag-limit 200 \
  --json --output /tmp/music-issues.json
```

## 检出的问题类型

| 层级 | 问题 | 判定 |
|------|------|------|
| 结构 | 散曲(根/歌手层) | 音频不在专辑目录里 |
| 结构 | 缺歌手层 | 根级目录直接装音频(其实是张专辑) |
| 专辑 | 缺专辑封面 | 无图片文件且无内嵌 APIC |
| 专辑 | 空专辑目录 | 无音频文件 |
| 专辑 | 整轨镜像+CUE | 合法结构,仅提示(跳过逐轨校验) |
| 曲目 | 缺曲目号 | 无 `NN` / `NN - ` / `D-TT` 前缀 |
| 曲目 | 曲目号在结尾 | `简单爱 02` → 建议前缀式 |
| 曲目 | 水印/音质标签 | `【】`、`[FLAC]`、`320K`、站点名 |
| 曲目 | 同曲多格式共存 | 同 stem 有 mp3+flac 等 |
| 附属 | 歌词文件不配对 | `.lrc` 无同名音频 |
| 附属 | 非音频文件混入 | 专辑里的 mp4 等(封面/歌词/CUE/log 除外) |
| 标签 | 缺 ID3 标签 | `--read-tags`:无 TIT2 |
| 标签 | 路径与标签不符 | `--read-tags`:TPE1/TALB 与目录名对不上 |

上面「非音频文件混入」「缺专辑封面」「垃圾/临时文件」三条的**扩展名归属可以被
`config.json` 改写**(issue #15):`{"shn": "audio"}` 让内置表没有的无损格式从
「建议移出」变成正常曲目,`{"jpg": "allowed"}` 反过来会让那张专辑失去封面。
「散曲」「缺歌手层」这类**结构**判定则可以用 `whitelist_dirs` 整棵跳过。
详见「配置覆盖」。

## ID3v2 解析器

`parse_id3(data: bytes)` 支持 v2.2/2.3/2.4(帧头长度与 synchsafe 按版本分支),
提取 TIT2/TPE1/TALB/TRCK/year(TYER/TDRC)/APIC 存在性;
文本帧按 encoding 字节解码(latin-1/UTF-16/UTF-16BE/UTF-8)。
纯函数、可单测(smoke.sh 里手工构造 ID3 帧验证)。

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

本 skill 的 5 个类别来自 `_check_file()` 的真实判定阶梯:

```python
if name in JUNK_NAMES or ext in JUNK_EXTS or name.startswith("._"): …   # junk 最先
if ext in AUDIO_EXTS: …                                                 # audio
if ext in IMAGE_EXTS: …  elif ext == "lrc": …  elif ext == "cue": …      # image
elif ext in ALLOWED_EXTS: pass                                          # allowed
else: 非音频文件混入专辑                                                 # non_audio
```

### `CUE_IMAGE_EXTS` 为什么刻意不在 `CONFIG_EXT_TABLES` 里

它是 `AUDIO_EXTS` 的一个**派生子集**(实测 `{ape, flac, tta, wav, wv}` ⊆
`AUDIO_EXTS`),用来判 `is_cue_sheet`。而改判语义是「先从**所有**别的表里摘掉,
再放进指定那张」—— 把 `CUE_IMAGE_EXTS` 放进表里,一条 `{"flac": "image"}` 就会
把 `flac` 从整轨镜像的判定里静默摘走,那些专辑突然开始被要求逐轨编号。这正是
issue #15 最忌讳的静默失效,所以宁可少开放一个集合。smoke TEST 7 里两层都钉住了:
进程内 import 断言任何合法配置加载后 `CUE_IMAGE_EXTS` 仍是那 5 个元素。

同理,专辑分支里的 `lrc` / `cue` 是**硬编码字面量**(排在 `ALLOWED_EXTS` 之前),
所以 `{"lrc": "allowed"}` 是显式无变化;要把 `.lrc` 当曲目得写 `{"lrc": "audio"}`
(`is_audio` 在专辑分支之前就 return 了)。

### `whitelist_dirs` 在本 skill 里的语义:**这块别扫**

内置 `WHITELIST_DIRS` 在 `_walk()` 的 `depth == 1` 分支里就是一个 `continue`
(`合辑/` `歌单/` 里的东西从来不进 `stats.audio_files`),配置层沿用**同一个**语义,
只是不限层数、支持 glob、大小写不敏感。所以配了白名单之后 `stats.artists` /
`albums` / `audio_files` 都会变小,`skipped_files` 给出被跳过的文件数。

内置白名单命中的目录仍然走原来那个 `continue`(整棵**不走进去**),所以它里面的
文件**不计入** `skipped_files` —— 那个数字只统计**配置**白名单跳过的。smoke 里
这条是真断言的:`{"whitelist_dirs": ["原盘"]}` 之后 `skipped_files == 4`
(原盘 3 + 深层/原盘 1),而 `合辑/群星/z.flac` 既没被扫到也没被计进去。

### 接入点只有 11 处,且默认值保证零变化

`_walk()` 里 8 处,加上 `__init__` / `scan()` / `main()` 各 1 处:

```python
self._wl_files = 0                                                             # __init__
def _walk(self, dir_path, rel_parts, artist, album, wl: bool = False) -> None:  # +1 参数
    child_wl = wl or _cfg_match(child_rel, CONFIG_WHITELIST)                    # 新增
    if not child_wl and BAD_DIR.match(name):                                    # 加卫语句
    if not child_wl and name.lower() in WHITELIST_DIRS:                         # 加卫语句
    art = None if child_wl else Artist(name)                                    # 不注册歌手
    alb = None if child_wl else Album(name, "/".join(child_rel))                # 不注册专辑
    self._walk(entry.path, child_rel, art, None, child_wl)                      # ×3 多传一个参数
    if wl: self._wl_files += 1; continue                                        # 新增
if CONFIG_INFO: self.stats["config"] = {**CONFIG_INFO, "skipped_files": …}      # scan()
load_config()                                                                  # main()
```

`art` / `alb` 那两处原来是「构造 + 直接塞进 dict」两行,现在改成「构造或 None」+
「非 None 才塞」,是为了让白名单子树里的目录**既不被注册、也不产 issue**,而递归
照常往下走(带着 `wl=True`)好把文件数记进 `skipped_files`。

`Album` / `Artist` 的构造与 `_check_file()` / `_eval_album()` 的**签名和函数体一行
没改**,所以 TEST 3 那批纯函数断言不受影响。没有 `config.json` 时
`CONFIG_WHITELIST == []` → `_cfg_match` 的第一行 `if not patterns` 直接返回 False
→ `child_wl` 恒为 False → 上面每一处新分支一行都不会走到(`art` 仍是 `Artist(name)`、
`alb` 仍是 `Album(...)`)。`stats["config"]` 用的是 #41 / #47 的同一个手法:只在
真启用时才多这个键。

`art = None` 之后递归照常往下走(只是带着 `wl=True`),所以白名单子树里
`depth == 2 and artist is not None` 会自然落到 `else` 分支,里面的目录既不会被注册
成专辑、也不会产 issue —— 只贡献 `skipped_files`。

### 仓库里刻意不放 `config.json`(连 example 也不放)

`pyproject.toml` 的 `package-data` 是 `"zspace_cli.skills" = ["**/*"]`,而
`zs skill` 会把整个目录 copytree 给用户 —— 所以**任何**放进 `skills/<name>/` 的
`config.json` 都会被装到用户机器上并**立刻生效**,等于给所有用户默认开了一份覆盖。
schema 直接写在 SKILL.md 里,用户自己建。

## 设计原则

1. **正向验证** — 定义合规结构(歌手/专辑/曲目),不枚举脏模式
2. **零依赖** — 标签解析也纯 stdlib,不引入 mutagen
3. **脚本只读** — 无 apply 子命令,也**不写标签**
4. **合法结构不误报** — 整轨镜像+CUE、合辑白名单、多碟 CD1/CD2
5. **冲突不裁决** — 标签 vs 路径不一致只报告,以哪个为准由用户定

## 测试

```bash
bash skills/music-organizer/tests/smoke.sh
```

完全离线;TEST 3 手工构造 ID3v2.3 帧验证解析器,TEST 4 fixture 覆盖
合规专辑零误报 + 11 类问题检出。

TEST 7(issue #15)把脚本 `cp` 进临时目录模拟「装好之后」的布局,然后:无配置基线
(18 条问题逐条对齐)、`.shn` 改判为音频、`.BAK` 从 junk 救回、`jpg` 从
`IMAGE_EXTS` 摘掉后专辑立刻失去封面、兜底类别 `non_audio`、`原盘` / `原盘/*` /
`深层/原盘/*` 三种锚定(跳过数 4 / 2 / 1,互相可区分)、`*` 管不到 root 下散文件、
空对象 `{}`、被扫描目录里的 `config.json` 必须被忽略(3 种 cwd)、18 种畸形配置
逐条 `exit=1` 且 `stdout == ""`、校验先于写入(5 个集合分毫未动)、
`CUE_IMAGE_EXTS` 不受覆盖影响、`_cfg_match` 14 条纯函数断言(大小写**双向**)。

## 已知 gap

- 标签只支持 mp3(ID3v2);FLAC Vorbis Comment / M4A MP4 atom 未解析
- 不写标签(补标签用 eyeD3/mutagen/Picard)
- 不联网查曲目表;`NN` 前缀顺序靠 TRCK 标签或 LLM 推断
- `TRACK_NO_OK` 对「年份开头的曲名」(如 `2001 太空漫游`)会误报缺曲目号
- `config.json` 改不了:`CUE_IMAGE_EXTS`、专辑分支里 `lrc`/`cue` 的硬编码字面量、
  `BAD_DIR`/`TRACK_NO_*`/`WATERMARK_RE` 正则、同曲多格式阈值
- `config.json` 是 per-安装而非 per-库;`--config PATH` 未做(要先定「与旁边那份是
  替换还是叠加」)

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
