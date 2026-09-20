# Download Cleaner Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描 NAS 下载目录,按类别**分诊**并给每文件一个建议动作。
rm / mv / 解压由 Agent 在用户确认清理计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):
脚本只读 + 判断交回 LLM + 先预览后执行 + 删除先隔离。

## 目录结构

```
skills/download-cleaner/
├── SKILL.md              # LLM 工作流(触发词 + 场景 + 分诊表)
├── download_cleaner.py   # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh          # 烟雾测试(离线,fixture 端到端)
└── README.md             # 本文件
```

## 命令

```bash
python3 skills/download-cleaner/download_cleaner.py scan \
  --root /Volumes/nas/下载

python3 skills/download-cleaner/download_cleaner.py scan \
  --root /Volumes/nas/下载 --stale-days 180 --json --output /tmp/dl.json
```

## 分诊模型

`categorize(name, ext)` → 类别(纯函数),`advise(category, age, extracted, stale)` →
(问题, 动作)(纯函数)。两函数无副作用,smoke.sh 直接单测。

| action | 含义 |
|--------|------|
| `delete` | 垃圾,可直接删 |
| `delete-confirm` | 种子/未完成/已解压包/老旧安装包,逐项确认后删 |
| `extract-or-review` | 未解压压缩包,确认内容 |
| `move-to-library` | 媒体/文档,分流到对应库 |
| `review` | 杂项或新安装包,人工看 |
| `keep` | 无需处理 |

「可回收空间」= junk + partial + torrent + 已解压 archive + 老旧 installer 的体积。

## 设计原则

1. **分诊不裁决** — 每文件给类别 + 建议动作,删/留由用户拍板
2. **零依赖** — 纯 stdlib,Python ≥3.9
3. **脚本只读** — 无 apply/rm;删除先 `mv` 隔离再真删
4. **归档不越权** — 只指出媒体该去哪个库,规范化交给对应 skill
5. **可回收估算保守** — 只统计高置信度可删类别

## 测试

```bash
bash skills/download-cleaner/tests/smoke.sh
```

完全离线;TEST 3 单测 categorize/advise 纯函数,TEST 4 fixture 覆盖
各类别 + 已解压/未解压 + 重复下载 + 老旧文件(`touch -t` 造旧 mtime)。

## 已知 gap

- 「已解压」靠同名目录启发式,不比对压缩包内容
- 不解析压缩包内文件列表
- 重复下载只按名字识别,内容级重复走 dedup-finder
- 归档目标是类别建议,实际库路径需用户指定

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 下游归档:media-naming / music-organizer / photo-organizer / work-organizer
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
