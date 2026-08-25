# zspace-cli

Manage your 极空间 (ZSpace) NAS from the terminal or AI agents — **no password, no SSH, no DDNS**.

> Just keep the ZSpace desktop client logged in on macOS.

[中文文档](docs/README.zh.md) · [Skills](skills/README.md)

---

## Install

```bash
pip install zspace-cli        # base
pip install "zspace-cli[mcp]" # optional MCP support
zs check                      # ✓ reads the desktop client login state
```

**Prerequisite:** the ZSpace desktop client is running and logged in on macOS.

---

## Quick start

```bash
zs ls /sata11/my/data/影视
zs find "权力的游戏"                  # full-text search
zs tree /sata11/my/data -d 3
zs up ./本地文件.mp4 /sata11/my/data/影视   # upload
zs down /sata11/my/data/影视/某文件.mkv ./下载 # download
```

```python
from zspace_cli import ZSpaceClient

with ZSpaceClient() as zs:
    for f in zs.ls("/sata11/my/data"):
        print(f"{'📁' if f.is_dir else '📄'} {f.name}")
```

### Use with AI agents (Skills)

```bash
zs skill ~/your-project/.cursor/skills/   # Cursor
# zs skill ~/your-project/skills/         # Claude Code, etc.
```

Then tell your agent things like "list the files in `/sata11/my/data`". See [skills/README.md](skills/README.md) for the full skill list.

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
| Upload | `zs up <local> <dir>` | `client.upload(local, dir)` | `zspace_upload` |
| Download | `zs down <path> [dir]` | `client.download(path, dir)` | `zspace_download` |
| Health check | `zs check` | `client.is_connected()` | `zspace_check` |

> `ls` pages through large directories automatically (the NAS API returns at most 50 entries per call). `find` uses the NAS full-text index, so it searches across directories.

---

## How it works

ZSpace has no official CLI or public API. **zspace-cli** talks to the desktop client's local proxy, so it works behind NAT as long as the client is online:

```
Skill / zs / SDK / MCP  →  127.0.0.1:13579 (desktop client proxy)  →  NAS
```

### MCP configuration (optional)

```json
{
  "mcpServers": {
    "zspace": { "command": "zs-mcp", "args": [] }
  }
}
```

---

## API reference

| Endpoint | Key Parameters |
|----------|----------------|
| `/v2/file/list` | `path`, `show_hidden`, `start`, `limit` |
| `/v2/file/info` | `path` |
| `/v2/file/modify` | `path`, `newname` |
| `/v2/file/newdir` | `parent`, `name`, `rename=0` |
| `/v2/file/move` / `copy` | `paths[]`, `to` |
| `/v2/file/remove` | `paths[]` |
| `/v2/file/create` | binary body, header `path` (upload) |
| `/v2/file/download` | GET `path`, `remote_port=8050` |
| `/file_search/file_search` | `keyword` |

> Parameter names are non-standard (`parent` / `to` instead of `path` / `dest`) — mapped after reverse-engineering the web UI.

---

## Roadmap

- [x] File upload/download
- [ ] Linux / Windows client auth
- [ ] Docker headless option
- [ ] Batch glob helpers

---

## Contributing

Open an issue first to discuss changes. PRs welcome.

## License

MIT
