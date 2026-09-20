# NAS Report Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描任意目录(本地挂载路径)生成**存储画像**,并按发现的问题
**路由到专门的整理 skill**。这是整个 skill 家族的**入口/元技能** — 先跑它看清全局,
再决定跑哪个专项 skill。本 skill 不做任何整理写操作。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。

模式沿袭 `media-naming`(zspace_skill 仓库):脚本只读 + 判断交回 LLM。

## 目录结构

```
skills/nas-report/
├── SKILL.md        # LLM 工作流(触发词 + 场景 + 路由表)
├── nas_report.py   # CLI:report(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh    # 烟雾测试(离线,fixture 端到端)
└── README.md       # 本文件
```

## 命令

```bash
python3 skills/nas-report/nas_report.py report \
  --root /Volumes/nas/data

python3 skills/nas-report/nas_report.py report \
  --root /Volumes/nas/data --max-files 20000 --json --output /tmp/report.json
```

## 画像维度

- **总览**:文件数 / 目录数 / 总体积 / 扫描耗时
- **按类别**:影视/音频/照片/文档/压缩包/安装包/设计/代码/备份/其他 的体积与占比
- **冷热分层**:按 mtime 分 hot_30d / warm_1y / cool_3y / cold_3y+
- **顶层目录榜 / 最大目录榜 / 最大文件榜**
- **垃圾与空目录**:垃圾/临时/种子计数与体积、空目录数

## 路由逻辑(_recommend)

按画像阈值生成「下一步跑哪个 skill」,每条带 `why`:

| 触发 | 路由 |
|------|------|
| 影视体积 > 15% | media-naming / media-manager-skill |
| 照片 > 500 | photo-organizer |
| 音频 > 100 | music-organizer |
| 文档 > 100 | work-organizer |
| 设计源文件 > 20 | portfolio-organizer |
| 冷数据 > 20% | 冷热分层归档建议 |
| 垃圾 > 50 | download-cleaner |
| 文件数 > 1000 | dedup-finder |
| 空目录 > 20 | 空目录清理 |
| 顶层目录名含 备份/backup/快照 | backup-auditor |

`categorize(ext)` / `growth_bucket(days)` 是纯函数,smoke.sh TEST 3 单测。

## 设计原则

1. **元技能** — 只出画像 + 路由,不做整理;实际整理交专项 skill
2. **零依赖** — 纯 stdlib,Python ≥3.9
3. **单次遍历** — 一趟 walk 同时汇总类别/冷热/顶层/榜单/垃圾,不重复扫
4. **大库可摸底** — `--max-files` / `--max-depth` 控规模
5. **建议是启发式** — 阈值触发,LLM 结合实况解读

## 测试

```bash
bash skills/nas-report/tests/smoke.sh
```

完全离线;TEST 3 单测 categorize/growth_bucket,TEST 4 fixture 覆盖
多类别画像 + 冷热分层 + 路由建议(含备份目录探测)。

## 已知 gap

- 不算重复占用(交给 dedup-finder),画像体积含重复
- 冷热分层靠 mtime(多数 NAS 挂 noatime,不读 atime)
- 路由阈值写死,不按库总量自适应
- 不做增长趋势(需多次快照对比)
- 类别靠扩展名,错扩展名会误分类

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 家族成员:nas-report(入口)→ photo/work/portfolio/music/download/dedup/backup + media-naming
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
