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

## ID3v2 解析器

`parse_id3(data: bytes)` 支持 v2.2/2.3/2.4(帧头长度与 synchsafe 按版本分支),
提取 TIT2/TPE1/TALB/TRCK/year(TYER/TDRC)/APIC 存在性;
文本帧按 encoding 字节解码(latin-1/UTF-16/UTF-16BE/UTF-8)。
纯函数、可单测(smoke.sh 里手工构造 ID3 帧验证)。

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

## 已知 gap

- 标签只支持 mp3(ID3v2);FLAC Vorbis Comment / M4A MP4 atom 未解析
- 不写标签(补标签用 eyeD3/mutagen/Picard)
- 不联网查曲目表;`NN` 前缀顺序靠 TRCK 标签或 LLM 推断
- `TRACK_NO_OK` 对「年份开头的曲名」(如 `2001 太空漫游`)会误报缺曲目号

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
