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
```

## 命令

```bash
python3 skills/photo-organizer/photo_organizer.py scan \
  --root /Volumes/nas/照片

python3 skills/photo-organizer/photo_organizer.py scan \
  --root /Volumes/nas/照片 --json --output /tmp/photo-issues.json
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

日期提取优先级:mmexport epoch → 微信图片_ → Screenshot/截屏 →
`YYYY-MM-DD` 前缀 → `YYYYMMDD` 紧凑 → 相机带日期名 → 全文严格日期 → mtime(弱)。

## 设计原则

1. **正向验证** — 定义合规结构(年/月/事件),不枚举脏模式
2. **零依赖** — 纯 stdlib,Python ≥3.9,复制即用
3. **脚本只读** — 无 apply 子命令;写操作走 Agent(shell `mv -n` 或 `zs` CLI/MCP)
4. **弱证据标注** — `date_source` 区分 filename / mtime,mtime 必须让用户抽查
5. **降噪** — 系统元数据目录(@eaDir/#recycle/.Trashes)与点文件静默跳过

## 测试

```bash
bash skills/photo-organizer/tests/smoke.sh
```

完全离线:frontmatter 检查 + py_compile + 纯函数用例 + /tmp fixture 端到端 scan。

## 已知 gap

- 不读 EXIF(保持零依赖);精确拍摄时间需 Agent 外挂 `exiftool`/`mdls`
- 无内容级去重(感知哈希);连拍仅按文件名连号识别
- 白名单功能区目录名写死中文/英文常见集合,自定义库名需改 `WHITELIST_DIRS`

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 影视命名规范:https://github.com/skyzhao1223/media-naming-guide
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
