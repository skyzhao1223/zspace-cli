# Dedup Finder Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描任意目录(本地挂载路径)做**内容级精确去重**。
输出重复组 + 每组保留建议;rm / mv 由 Agent 在用户确认删除计划后执行。

**跨 NAS 通用**:不依赖任何品牌 API — 只要能挂载成本地路径(SMB/NFS)就能扫。
极空间用户写操作也可选走 `zs` CLI / MCP(见 zspace-nas skill)。

与 `file-organizer`(zspace_skill 仓库,size+ext 弱指纹、误报率高)的核心区别:
**三级指纹(size → 头部 64KB → 全量 sha1)零误报** — 只有字节完全相同才判重复。

## 目录结构

```
skills/dedup-finder/
├── SKILL.md          # LLM 工作流(触发词 + 场景 + 保留优先级)
├── dedup_finder.py   # CLI:scan(纯 stdlib,零依赖)
├── tests/
│   └── smoke.sh      # 烟雾测试(离线,fixture 端到端)
└── README.md         # 本文件
```

## 命令

```bash
python3 skills/dedup-finder/dedup_finder.py scan \
  --root /Volumes/nas/data

# 跨目录找重复 + 只看大文件
python3 skills/dedup-finder/dedup_finder.py scan \
  --root /Volumes/nas/照片 --root /Volumes/nas/下载 \
  --min-size 1024 --json --output /tmp/dups.json
```

## 三级指纹

| 阶段 | 成本 | 作用 |
|------|------|------|
| 1. size 分组 | 免费(stat) | 砍掉绝大多数不可能重复的 |
| 2. 头部 64KB sha1 | 低(每文件读 64KB) | 砍掉同 size 但内容不同的 |
| 3. 全量 sha1 | 高(读完整文件) | 只对头哈希相同的极少数做,零误报 |

`stats` 里 `hashed_head` / `hashed_full` / `bytes_read` 可以看到漏斗效果。

## 保留优先级(keep_rank)

组内排序,越小越该保留:
1. 路径含 `成品/源文件/原始/master/original/import/相册`
2. 路径更浅
3. mtime 更旧
4. 名字更短(避开 `xxx 副本`、`xxx (1)`)

启发式仅供 LLM 初排,最终由用户拍板;SKILL.md 约束「先 mv 隔离,再真删」。

## 设计原则

1. **精确优先** — 三级指纹保证零误报,宁可多读 IO 不误删
2. **零依赖** — 纯 stdlib(hashlib),Python ≥3.9
3. **脚本只读** — 无 apply/rm 子命令;删除走 Agent + 隔离目录
4. **漏斗降 IO** — size→head→full,SMB 上尽量少读
5. **多 root** — 支持跨目录找重复(照片库 vs 下载区)

## 测试

```bash
bash skills/dedup-finder/tests/smoke.sh
```

完全离线;fixture 覆盖:真重复检出、同 size 不同内容不误报、
head 相同 full 不同(三级指纹价值)、小文件跳过、keep 优先级。

## 已知 gap

- 不做相似去重(感知哈希)— 连拍/转码/改尺寸抓不到
- 硬链接按普通文件比对(SMB 上少见);软链接跳过
- 全量 hash 是 IO 密集,百万文件级建议分区多次扫

## 参考

- 模式来源:`media-naming`(https://github.com/coracoo/zspace_skill)
- 弱指纹对照组:`file-organizer`(同仓库)
- ZSpace 底座:https://github.com/skyzhao1223/zspace-cli
