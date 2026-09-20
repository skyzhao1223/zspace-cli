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

## 已知 gap

- 项目归属推断靠文件名 + LLM,无语义索引
- 不做内容级去重(hash);同名多版本按文件名分组
- `WHITELIST_DIRS` 写死常见职能目录名,公司特有目录需自行扩充
- 版本组「保留最新」按文件名排序,语义判断交给 LLM(JSON 带 mtime)

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
