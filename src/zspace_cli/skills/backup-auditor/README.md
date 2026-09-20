# Backup Auditor Skill(开发者文档)

## 这是什么

Agent skill:**只读**审计 NAS 备份目录的健康度。两个模式:
- `scan` — 备份集版本分析(陈旧 / 单版本 / 空备份 / 可轮转旧版本)
- `coverage` — 源数据目录 ↔ 备份目录 覆盖核对(缺失 / 陈旧 / 孤儿备份)

轮转/删除/补备份由 Agent 在用户确认计划后执行(本 skill 不做实际备份)。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能审计。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

模式沿袭 `media-naming`(zspace_skill 仓库):脚本只读 + 判断交回 LLM + 先预览后执行。

## 目录结构

```
skills/backup-auditor/
├── SKILL.md            # LLM 工作流(触发词 + 场景 + 保留策略)
├── backup_auditor.py   # CLI:scan / coverage(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh        # 烟雾测试(离线,fixture 端到端,含两模式)
└── README.md           # 本文件
```

## 命令

```bash
python3 skills/backup-auditor/backup_auditor.py scan \
  --root /Volumes/nas/备份 --stale-days 35 --keep 3

python3 skills/backup-auditor/backup_auditor.py coverage \
  --source /Volumes/nas/data --backup /Volumes/nas/备份
```

## 核心逻辑

| 函数 | 作用 |
|------|------|
| `strip_archive_ext` | 剥离 `.tar.gz`/`.zip`/`.sparsebundle` 等(含双扩展名) |
| `parse_backup_name` | 名字 → `(base_key, date, version)`;剥日期/版本后聚成备份集 |
| `dir_size` | 递归求目录大小(20000 文件上限防卡死) |
| `Auditor.scan` | 备份集分析:陈旧 / 单版本 / 空 / 可轮转 |
| `Auditor.coverage` | 源↔备份归一名双向包含匹配,查缺失/陈旧/孤儿 |

`parse_backup_name` / `strip_archive_ext` 是纯函数,smoke.sh TEST 3 直接单测。

## 检出项与 action

| mode | 问题 | action |
|------|------|--------|
| scan | 备份陈旧(最新超阈值) | `review-set` |
| scan | 单版本备份(无冗余) | `review-set` |
| scan | 空备份(0 字节/0 文件) | `investigate` |
| scan | 可轮转旧版本(超 keep) | `rotate-out` |
| coverage | 关键目录无备份 | `add-backup` |
| coverage | 有备份但陈旧 | `refresh-backup` |
| coverage | 孤儿备份(源已删/改名) | `review-orphan` |

## 设计原则

1. **审计不碰数据** — 全程只读;轮转/删除交 Agent + 用户确认
2. **零依赖** — 纯 stdlib,Python ≥3.9
3. **陈旧=修复优先** — 陈旧/空备份是「备份可能已失效」信号,优先级高于清理
4. **轮转先归档** — 旧备份 `mv` 到 `_rotated/`,删前确认最新版本完好
5. **保留策略可配** — `--keep` / `--stale-days` 按备份频率调

## 测试

```bash
bash skills/backup-auditor/tests/smoke.sh
```

完全离线;TEST 3 单测名字解析,TEST 4 fixture 覆盖 scan(多版本轮转/陈旧/空备份/单版本)
+ coverage(缺失/孤儿),用 `touch -t` 造旧 mtime 验证陈旧检测。

## 已知 gap

- 不校验可恢复性(不试解压/挂载),空备份只看 size/file_count
- 不做增量备份链式完整性检查
- 备份集聚合靠命名启发式,极不规范命名可能误聚/漏聚
- coverage 匹配是名字双向包含,不理解备份工具的实际映射
- 不识别厂商专有格式(群晖 HBB / 威联通 HBS)内部版本

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 3-2-1 备份原则、ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
