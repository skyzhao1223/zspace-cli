# Skills（Agent 工作流）

复制到你的 Agent 项目后，说自然语言即可触发。底层能力来自 **`zspace-cli`（零配置鉴权）**。

## 30 秒上手

```bash
# 1. 安装底座（macOS + 极空间桌面客户端已登录）
pip install zspace-cli
zs check

# 2. 复制 skill 到你的项目
cp -r skills/zspace-nas ~/your-project/.cursor/skills/   # Cursor
# 或
cp -r skills/zspace-nas ~/your-project/skills/           # Claude Code 等

# 3. 对 Agent 说
# 「列出我 NAS 上 /sata11/my/data 的文件」
```

影视整理再复制 `skills/media-manager/`（依赖 [media-naming-guide](https://github.com/skyzhao1223/media-naming-guide) 的命名规范）。

## 清单

| Skill | 目录 | 做什么 |
|-------|------|--------|
| **zspace-nas** | `skills/zspace-nas/` | 通用文件管理（ls / rename / mv / …） |
| **zspace-media-manager** | `skills/media-manager/` | 影视库命名扫描与整理编排 |

## 分层

```
Skill（获客 / 工作流）  →  告诉 Agent「怎么做」
        ↓
MCP / CLI / SDK（能力） →  单步操作
        ↓
zspace-cli 零配置鉴权   →  读桌面客户端 token，不填密码
```

可选 MCP：`pip install "zspace-cli[mcp]"`，配置见仓库根 README。

## 和「填密码直连 NAS」方案的区别

| | 本仓库 | 账号密码直连方案 |
|---|--------|------------------|
| 鉴权 | 桌面客户端已登录即可 | `.env` 写账号密码 |
| 平台 | macOS（当前） | 更广 |
| 适合 | 本机 Agent / 日常整理 | 无桌面客户端的服务器 |

两者可并存；本仓库主打 **零配置**。
