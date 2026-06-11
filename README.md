# zspace-cli

**CLI, SDK & MCP Server for ZSpace (极空间) NAS**

> Manage your 极空间 NAS files from the terminal, Python scripts, or AI agents — zero config, no SSH needed.

[English](#features) | [中文](#功能特性)

---

## Why zspace-cli?

ZSpace (极空间) is a popular NAS brand in China, but it lacks official CLI tools or developer APIs.

**zspace-cli** reverse-engineered the internal API used by the ZSpace desktop client and wraps it into:

- **`zs` CLI** — 10 commands covering all file operations, powered by [Rich](https://github.com/Textualize/rich) for beautiful output
- **Python SDK** — `ZSpaceClient` with a clean, typed interface for automation scripts
- **MCP Server** — one-line config to add ZSpace file management to Claude, Cursor, or any MCP-compatible AI agent

### Zero Configuration

Just install and run. zspace-cli reads auth tokens directly from your running ZSpace desktop client — no passwords, no SSH keys, no port forwarding.

```
pip install zspace-cli
zs check   # ✓ Connected, 11.8 TB total, 1.2 TB free
zs ls      # List your NAS files
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

## Installation

```bash
pip install zspace-cli
```

**Prerequisites:** ZSpace desktop client running on macOS (logged in).

### With MCP Server support

```bash
pip install "zspace-cli[mcp]"
```

---

## Quick Start

### CLI

```bash
# Check connection
zs check

# List files
zs ls /sata11/my/data
zs ls -l /sata11/my/data/影视    # detailed view

# File operations
zs mkdir /sata11/my/data 新建文件夹
zs rename /sata11/my/data/old_name new_name
zs mv /sata11/my/data/file.mp4 /sata11/my/data/影视
zs cp /sata11/my/data/important /sata11/my/data/backup
zs rm /sata11/my/data/temp

# Search & explore
zs find "权力的游戏"
zs tree /sata11/my/data -d 3
```

### Python SDK

```python
from zspace_cli import ZSpaceClient

with ZSpaceClient() as zs:
    # List files
    for f in zs.ls("/sata11/my/data"):
        print(f"{'📁' if f.is_dir else '📄'} {f.name}")

    # Batch rename videos
    for f in zs.ls("/sata11/my/data/影视"):
        if f.name.startswith("[raw]"):
            new_name = f.name.replace("[raw]", "").strip()
            zs.rename(f.path, new_name)
            print(f"Renamed: {new_name}")

    # Organize files
    zs.mkdir("/sata11/my/data", "已整理")
    zs.move("/sata11/my/data/散文件.pdf", "/sata11/my/data/已整理")
```

### MCP Server (for AI Agents)

Add to your Claude Desktop / Cursor MCP config:

```json
{
  "mcpServers": {
    "zspace": {
      "command": "zs-mcp",
      "args": []
    }
  }
}
```

Then ask your AI: "帮我把 NAS 上影视文件夹里的视频按年份整理一下"

---

## How It Works

```
┌─────────────┐      ┌──────────────────┐      ┌──────────┐
│  zs CLI     │      │  ZSpace Desktop  │      │  ZSpace  │
│  Python SDK ├─────►│  Client (proxy)  ├─────►│  NAS     │
│  MCP Server │ HTTP │  127.0.0.1:13579 │ P2P  │  Device  │
└─────────────┘      └──────────────────┘      └──────────┘
```

The ZSpace desktop client maintains an encrypted tunnel to your NAS and exposes a local HTTP proxy. zspace-cli communicates with this proxy using the same API the official web UI uses.

**No direct network access to the NAS is needed** — works even when your NAS is behind NAT or on a different network.

---

## API Reference

All operations go through the ZSpace internal API at `127.0.0.1:13579`.

| Endpoint | Method | Key Parameters |
|----------|--------|---------------|
| `/v2/file/list` | POST | `path`, `show_hidden` |
| `/v2/file/info` | POST | `path` |
| `/v2/file/modify` | POST | `path`, `newname` |
| `/v2/file/newdir` | POST | `parent` (not path!), `name`, `rename=0` |
| `/v2/file/move` | POST | `paths[]` (array), `to` (not dest!) |
| `/v2/file/copy` | POST | `paths[]` (array), `to` (not dest!) |
| `/v2/file/remove` | POST | `paths[]` (array) |

> **Key discovery:** The parameter names are non-standard — `newdir` uses `parent` instead of `path`, and `move`/`copy` use `to` instead of `dest`. These were found by reverse-engineering the ZSpace web UI.

---

## Roadmap

- [ ] File upload/download support
- [ ] Linux client support
- [ ] Windows client support
- [ ] Docker image for headless deployment
- [ ] Batch operations with glob patterns
- [ ] File watching / sync triggers

---

## Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change.

---

## License

MIT

---

<details>
<summary><h2>中文说明</h2></summary>

## 功能特性

**zspace-cli** 是极空间 NAS 的命令行工具、Python SDK 和 MCP Server。

通过逆向工程极空间桌面客户端的内部 API，实现了**完整的文件管理功能**——无需 SSH，无需 WebDAV，零配置即可使用。

### 安装

```bash
pip install zspace-cli
```

**前提条件：** macOS 上已安装并登录极空间桌面客户端。

### 使用方法

```bash
# 检查连接
zs check

# 文件操作
zs ls                             # 列出目录
zs ls -l /sata11/my/data/影视     # 详细模式
zs info /sata11/my/data/影视      # 查看详情
zs mkdir /sata11/my/data 新文件夹  # 创建目录
zs rename /path/old new_name       # 重命名
zs mv /path/src /path/dest         # 移动
zs cp /path/src /path/dest         # 复制
zs rm /path/to/delete              # 删除
zs find "关键词"                    # 搜索
zs tree /sata11/my/data -d 3       # 树形视图
```

### MCP Server 配置（AI 智能体）

在 Claude Desktop 或 Cursor 的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "zspace": {
      "command": "zs-mcp",
      "args": []
    }
  }
}
```

配置完成后，你可以用自然语言让 AI 管理你的 NAS 文件：
- "帮我把影视文件夹里的电影按年份分类"
- "查找所有大于 10GB 的文件"
- "把百度网盘文件夹里的文档移到文档同步文件夹"

### 工作原理

极空间桌面客户端在本机 `127.0.0.1:13579` 建立了到 NAS 的加密代理。zspace-cli 通过这个代理调用和官方 Web UI 完全相同的 API，因此：

- 不需要 NAS 在局域网内
- 不需要配置 DDNS 或端口转发
- 不需要开启 SSH
- 只要桌面客户端在运行，就能操作

</details>
