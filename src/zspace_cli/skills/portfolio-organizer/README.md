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
| 项目 | 空项目目录 | 无任何文件 |
| 项目 | 成品多版本共存 | 成品池内同 base_stem ≥3 个 |
| 文件 | 成品目录混入源文件 | `成品/` 里有 psd/blend 等 |
| 文件 | 大体积源文件 | source 类文件 > `--large-gb`(默认 2GB) |

白名单功能目录(字体/素材库/模板/归档…)不做项目校验。

## 设计原则

1. **正向验证** — 定义合规结构(`YYYY_项目名/ + cover + README + 成品/ + 源文件/`)
2. **零依赖** — 纯 stdlib,Python ≥3.9,复制即用
3. **脚本只读** — 无 apply 子命令;写操作走 Agent(shell `mv -n` 或 `zs` CLI/MCP)
4. **项目自包含** — 结构以项目为单位,方便整体拷贝/展示/交付
5. **工程文件不可再生** — SKILL.md 约束源文件禁止自动删,压缩前必须确认
6. **降噪** — 白名单目录、系统与开发目录静默跳过

## 测试

```bash
bash skills/portfolio-organizer/tests/smoke.sh
```

完全离线:frontmatter 检查 + py_compile + 纯函数用例 + /tmp fixture 端到端 scan。

## 已知 gap

- 成品/源文件目录名靠白名单匹配(中英双语),自定义名需改 `FINAL_DIR_NAMES` / `SOURCE_DIR_NAMES`
- 「最终版」语义靠用户确认,脚本只按文件名 base_stem 分组
- 冷门设计软件格式不在 `SOURCE_EXTS`,需自行扩充
- 项目跨年改版视为两个项目,合并策略交用户

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
