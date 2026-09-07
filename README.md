<div align="center">

# zspace-cli

**English** · [简体中文](docs/README.zh.md)

[![PyPI - Version](https://img.shields.io/pypi/v/zspace-cli?cacheSeconds=3600)](https://pypi.org/project/zspace-cli/)
[![PyPI - Python](https://img.shields.io/pypi/pyversions/zspace-cli?cacheSeconds=3600)](https://pypi.org/project/zspace-cli/)
[![CI](https://github.com/skyzhao1223/zspace-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/skyzhao1223/zspace-cli/actions/workflows/ci.yml)
[![skyzhao1223/zspace-cli MCP server](https://glama.ai/mcp/servers/skyzhao1223/zspace-cli/badges/score.svg)](https://glama.ai/mcp/servers/skyzhao1223/zspace-cli)

</div>

Manage your 极空间 (ZSpace) NAS from the terminal or AI agents — **no password, no SSH, no DDNS**.

> Just keep the ZSpace desktop client logged in on macOS.

[Skills](skills/README.md) · [中文文档](docs/README.zh.md)

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

---

## CLI options

| Command | Meaning |
|---------|---------|
| `zs check` | Verify the desktop client proxy is reachable |
| `zs ls [path]` | List directory (`-a/--hidden`, `-l/--long`) |
| `zs info <path>` | Detailed file/dir info |
| `zs rename <path> <new>` | Rename a file or directory |
| `zs mv <src> <dest>` | Move a file/directory |
| `zs cp <src> <dest>` | Copy a file/directory |
| `zs mkdir <parent> <name>` | Create a directory |
| `zs rm <path>` | Delete (`-f/--force` skips confirmation) |
| `zs find <keyword> [path]` | Full-text search across the NAS |
| `zs tree [path]` | Tree view (`-d/--depth N`, default 2) |
| `zs up <local> <remote_dir>` | Upload (`-n/--name` to rename remotely) |
| `zs down <path> [dir]` | Download |
| `zs skill <dir>` | Copy Agent skills into a project |
| `zs --config-dir <dir>` | Point at a non-default `vuex.json` location (or `ZS_CONFIG_DIR`) |

> `ls` pages through large directories automatically (the NAS API returns at most 50 entries per call). `find` uses the NAS full-text index, so it searches across directories. Upload/download show a progress bar on a real terminal and stream the file (no full-file buffering).

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

---

## Use with AI agents (Skills)

```bash
zs skill ~/your-project/.cursor/skills/   # Cursor
# zs skill ~/your-project/skills/         # Claude Code, etc.
```

Then tell your agent things like "list the files in `/sata11/my/data`". See [skills/README.md](skills/README.md) for the full skill list.

The skills ship inside the wheel, so `zs skill` works on any machine that has `zspace-cli` installed.

---

## How it works

ZSpace has no official CLI or public API. **zspace-cli** talks to the desktop client's local proxy, so it works behind NAT as long as the client is online:

```
Skill / zs / SDK / MCP  →  127.0.0.1:13579 (desktop client proxy)  →  NAS
```

> **Disclaimer** — This is an **unofficial, community-maintained** project, not affiliated with or endorsed by ZSpace (极空间). It relies on the desktop client's local proxy interface, which is **not officially documented**. It only reads the login state of **your own** account on **your own** machine — it does not bypass authentication, crack encryption, or touch anyone else's data. Use at your own risk; make sure your use complies with the ZSpace user agreement and your local laws.

### Platform support

Works on any OS where the ZSpace desktop client exposes its local proxy on
`127.0.0.1:13579`. The login state (`vuex.json`) is auto-detected:

| Platform | Default location |
|----------|------------------|
| macOS | `~/Library/Application Support/zspace/vuex.json` |
| Windows | `%APPDATA%\zspace\vuex.json` (also tries `%LOCALAPPDATA%`, `%USERPROFILE%`) |
| Linux | `~/.zspace/vuex.json`, `~/.config/zspace/vuex.json` (best-effort) |

If the client stores it elsewhere, point the CLI/SDK at it explicitly:

```bash
zs --config-dir ~/path/to/zspace-config check
ZS_CONFIG_DIR=~/path/to/zspace-config zs check   # or as an env var
```

> Windows/Linux config locations are best-effort guesses (not verified against
> a real client). If auto-detection misses yours, please open an issue with the
> actual path so it can be added.

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

> Note: the interface parameter names are non-standard (`parent` / `to` instead of `path` / `dest`) — documented by the community from the desktop client's behavior.

---

## Repository layout

```
zspace-cli/
├── src/zspace_cli/
│   ├── cli.py         # Typer CLI (zs ...)
│   ├── client.py      # ZSpaceClient SDK (retry / stream / progress)
│   ├── auth.py        # vuex.json auto-detection + credential cache
│   ├── mcp_server.py  # MCP tools (zs-mcp)
│   └── skills/        # Agent skills shipped inside the wheel
├── skills/            # Skill docs + sources
├── scripts/mcp_smoke.py
├── tests/             # pytest (CLI + SDK + MCP + auth)
└── promo/             # launch/promo material (submodule)
```

---

## Roadmap

- [x] File upload/download
- [x] Linux / Windows client auth (best-effort path detection + `ZS_CONFIG_DIR`)
- [ ] Docker headless option
- [ ] Batch glob helpers

---

## Contributing

Open an issue first to discuss changes. PRs welcome.

## License

MIT
