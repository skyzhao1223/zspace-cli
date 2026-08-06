# zspace-cli

**用自然语言管极空间 NAS** — Agent Skill + 零配置底座（CLI / SDK / MCP）

> macOS 上极空间桌面客户端已登录即可，不填密码、不开 SSH、不配 DDNS。

[English](#why-zspace-cli) · [中文](#中文说明) · [Skills](skills/README.md)

---

## 30 秒开始（推荐：Skill）

```bash
pip install zspace-cli
zs check                          # ✓ 读到桌面客户端登录态

# 复制 skill 到你的 Agent 项目
cp -r skills/zspace-nas ~/your-project/.cursor/skills/    # 或 .../skills/
```

然后对 Agent 说：

- 「列出 NAS `/sata11/my/data` 里的文件」
- 「帮我扫一下影视库命名有没有问题」→ 再装 [`media-manager`](skills/media-manager/)

| Skill | 用途 |
|-------|------|
| [`zspace-nas`](skills/zspace-nas/) | 通用文件管理 |
| [`media-manager`](skills/media-manager/) | 影视命名扫描 / 整理编排 |
| [media-naming-guide](https://github.com/skyzhao1223/media-naming-guide) | 通用命名规范（不绑极空间） |

详情：[skills/README.md](skills/README.md)

---

## Why zspace-cli?

ZSpace (极空间) has no official CLI or public developer API.

**zspace-cli** reverse-engineers the desktop client API and ships three layers:

| Layer | What | Who |
|-------|------|-----|
| **Skill** | Copy-paste Agent workflows | Fastest to try |
| **MCP / CLI / SDK** | Atomic file ops | Automation & Agents |
| **Zero-config auth** | Reads running macOS client token | No password in `.env` |

```
pip install zspace-cli
zs check   # ✓ Connected — token from ~/Library/.../zspace/vuex.json
zs ls
```

Optional MCP:

```bash
pip install "zspace-cli[mcp]"
```

```json
{
  "mcpServers": {
    "zspace": { "command": "zs-mcp", "args": [] }
  }
}
```

---

## Features

| Operation | CLI | SDK | MCP |
|-----------|-----|-----|-----|
| List directory | `zs ls [path]` | `client.ls(path)` | `zspace_ls` |
| File info | `zs info <path>` | `client.info(path)` | `zspace_info` |
| Rename | `zs rename <path> <name>` | `client.rename(path, name)` | `zspace_rename` |
| Create dir | `zs mkdir <parent> <name>` | `client.mkdir(parent, name)` | `zspace_mkdir` |
| Move | `zs mv <src> <dest>` | `client.move(src, dest)` | `zspace_move` |
| Copy | `zs cp <src> <dest>` | `client.copy(src, dest)` | `zspace_copy` |
| Delete | `zs rm <path>` | `client.remove(path)` | `zspace_remove` |
| Search | `zs find <keyword>` | `client.search(kw)` | `zspace_search` |
| Tree view | `zs tree [path]` | `client.tree(path)` | `zspace_tree` |
| Health check | `zs check` | `client.is_connected()` | `zspace_check` |

---

## CLI & SDK

```bash
zs ls -l /sata11/my/data/影视
zs mkdir /sata11/my/data 新建文件夹
zs rename /sata11/my/data/old_name new_name   # new_name = basename only
zs mv /sata11/my/data/file.mp4 /sata11/my/data/影视
zs find "权力的游戏"
zs tree /sata11/my/data -d 3
```

```python
from zspace_cli import ZSpaceClient

with ZSpaceClient() as zs:
    for f in zs.ls("/sata11/my/data"):
        print(f"{'📁' if f.is_dir else '📄'} {f.name}")
```

**Prerequisite:** ZSpace desktop client running on macOS (logged in).

---

## How It Works

```
Skill / zs / SDK / MCP  →  127.0.0.1:13579 (desktop client proxy)  →  NAS
```

No direct NAS reachability required — works behind NAT as long as the desktop client is online.

---

## API Reference

| Endpoint | Key Parameters |
|----------|----------------|
| `/v2/file/list` | `path`, `show_hidden` |
| `/v2/file/info` | `path` |
| `/v2/file/modify` | `path`, `newname` |
| `/v2/file/newdir` | `parent`, `name`, `rename=0` |
| `/v2/file/move` / `copy` | `paths[]`, `to` |
| `/v2/file/remove` | `paths[]` |

Parameter names are non-standard (`parent` / `to` not `path` / `dest`) — documented after reverse-engineering the web UI.

---

## Roadmap

- [ ] File upload/download
- [ ] Linux / Windows client auth
- [ ] Docker headless option
- [ ] Batch glob helpers

---

## Contributing

Open an issue first to discuss changes. PRs welcome.

## License

MIT

---

<a id="中文说明"></a>

## 中文说明

**定位**：Skill 负责让人「愿意试」；`zspace-cli` 零配置鉴权是护城河；CLI / SDK / MCP 负责留下重度用户。

### 安装

```bash
pip install zspace-cli          # 底座
pip install "zspace-cli[mcp]"   # 可选 MCP
zs check
```

前提：macOS 极空间桌面客户端已登录。

### 复制 Skill

```bash
cp -r skills/zspace-nas ~/your-project/.cursor/skills/
cp -r skills/media-manager ~/your-project/.cursor/skills/   # 影视整理
```

对 Agent 说「列出 NAS 文件」「扫一下影视命名问题」即可。说明见 [skills/README.md](skills/README.md)。

### CLI 速查

```bash
zs ls /sata11/my/data/影视
zs rename /path/old new_name    # 第二参数纯文件名
zs mv /path/src /path/dest
zs find "关键词"
zs tree /sata11/my/data -d 3
```

### 和账号密码方案

本仓库主打**读桌面客户端 token**。若你需要服务器上填账号密码直连，可看社区其他 MCP 项目；两者场景不同，可并存。
