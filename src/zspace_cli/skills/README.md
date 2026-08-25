# Skills（Agent 工作流）

把这些 Skill 复制到你的 Agent 项目后，用自然语言就能管理你的极空间 NAS。底层能力来自 **`zspace-cli`**（读桌面客户端登录态，无需填密码）。

## 30 秒上手

```bash
# 1. 安装底座（macOS + 极空间桌面客户端已登录）
pip install zspace-cli
zs check

# 2. 复制 skill 到你的项目（zspace-nas 和 media-manager 会一起装上）
zs skill ~/your-project/.cursor/skills/   # Cursor
# 或
zs skill ~/your-project/skills/           # Claude Code 等

# 3. 对你的 Agent 说
# 「列出我 NAS 上 /sata11/my/data 的文件」
```

> 也可以直接从仓库源码复制 `skills/zspace-nas/`、`skills/media-manager/`（media-manager 依赖 [media-naming-guide](https://github.com/skyzhao1223/media-naming-guide) 的命名规范）。

## Skill 清单

| Skill | 目录 | 能做什么 |
|-------|------|----------|
| **zspace-nas** | `skills/zspace-nas/` | 通用文件管理（列目录 / 重命名 / 移动 / …） |
| **zspace-media-manager** | `skills/media-manager/` | 影视库命名扫描与整理 |

## 可选：MCP

想通过 MCP 协议使用底层能力（不依赖 Agent 的 skill 机制）：

```bash
pip install "zspace-cli[mcp]"
```

配置见[仓库根 README](../README.md)（或[中文版](../docs/README.zh.md)）。

## 与「填密码直连 NAS」方案的区别

| | zspace-cli | 账号密码直连方案 |
|---|-----------|------------------|
| 鉴权 | 桌面客户端已登录即可 | `.env` 里写账号密码 |
| 平台 | macOS（当前） | 更广 |
| 适合 | 本机 Agent / 日常整理 | 无桌面客户端的服务器 |

两者可以并存；zspace-cli 主打**零配置**。
