<div align="center">

# zspace-cli（中文）

**简体中文** · [English](../README.md)

[![PyPI - Version](https://img.shields.io/pypi/v/zspace-cli?cacheSeconds=3600)](https://pypi.org/project/zspace-cli/)
[![PyPI - Python](https://img.shields.io/pypi/pyversions/zspace-cli?cacheSeconds=3600)](https://pypi.org/project/zspace-cli/)
[![CI](https://github.com/skyzhao1223/zspace-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/skyzhao1223/zspace-cli/actions/workflows/ci.yml)
[![skyzhao1223/zspace-cli MCP server](https://glama.ai/mcp/servers/skyzhao1223/zspace-cli/badges/score.svg)](https://glama.ai/mcp/servers/skyzhao1223/zspace-cli)

</div>

用命令行或 AI Agent 管理你的**极空间 NAS** —— 不填密码、不开 SSH、不配 DDNS。

> 只要 macOS 上极空间桌面客户端已登录即可。

[Skills 说明](../skills/README.md) · [English README](../README.md)

---

## 安装

```bash
pip install zspace-cli          # 底座
pip install "zspace-cli[mcp]"   # 可选：MCP 支持
zs check                        # ✓ 读到桌面客户端登录态
```

**前提**：macOS 极空间桌面客户端已登录并正在运行。

---

## 快速上手

```bash
zs ls /sata11/my/data/影视                 # 列目录
zs find "权力的游戏"                        # 全文搜索（跨目录）
zs tree /sata11/my/data -d 3               # 树形浏览
zs up ./本地文件.mp4 /sata11/my/data/影视    # 上传
zs down /sata11/my/data/影视/某文件.mkv ./下载 # 下载
```

用 Python SDK：

```python
from zspace_cli import ZSpaceClient

with ZSpaceClient() as zs:
    for f in zs.ls("/sata11/my/data"):
        print(f"{'📁' if f.is_dir else '📄'} {f.name}")
```

### 配合 AI Agent 使用（Skills）

```bash
zs skill ~/your-project/.cursor/skills/   # Cursor
zs skill ~/your-project/skills/           # Claude Code 等（可复制到多个项目）
```

复制后，直接对你的 Agent 说「列出 NAS `/sata11/my/data` 里的文件」即可。Skills 清单见 [skills/README.md](../skills/README.md)。

---

## CLI 命令一览

| 命令 | 说明 |
|------|------|
| `zs check` | 检查桌面客户端代理是否可达 |
| `zs ls [path]` | 列目录（`-a/--hidden` 显示隐藏，`-l/--long` 详细） |
| `zs info <path>` | 查看文件/目录详情 |
| `zs rename <path> <new>` | 重命名 |
| `zs mv <src> <dest>` | 移动 |
| `zs cp <src> <dest>` | 复制 |
| `zs mkdir <parent> <name>` | 新建目录 |
| `zs rm <path>` | 删除（`-f/--force` 跳过确认） |
| `zs find <keyword> [path]` | NAS 全文搜索 |
| `zs tree [path]` | 树形浏览（`-d/--depth N`，默认 2） |
| `zs up <local> <remote_dir>` | 上传（`-n/--name` 指定远端文件名） |
| `zs down <path> [dir]` | 下载 |
| `zs skill <dir>` | 把 Agent skills 复制到项目目录 |
| `zs --config-dir <dir>` | 指定非默认 `vuex.json` 位置（或环境变量 `ZS_CONFIG_DIR`） |

> `ls` 自动分页（NAS 单次最多 50 条，会循环拉全）；`find` 走 NAS 全文索引，跨目录搜索。上传/下载在真实终端显示进度条，且为流式传输（不整文件读入内存）。

---

## 功能一览

| 操作 | CLI | SDK | MCP |
|------|-----|-----|-----|
| 列目录 | `zs ls [path]` | `client.ls(path)` | `zspace_ls` |
| 查看详情 | `zs info <path>` | `client.info(path)` | `zspace_info` |
| 重命名 | `zs rename <path> <name>` | `client.rename(path, name)` | `zspace_rename` |
| 新建目录 | `zs mkdir <parent> <name>` | `client.mkdir(parent, name)` | `zspace_mkdir` |
| 移动 | `zs mv <src> <dest>` | `client.move(src, dest)` | `zspace_move` |
| 复制 | `zs cp <src> <dest>` | `client.copy(src, dest)` | `zspace_copy` |
| 删除 | `zs rm <path>` | `client.remove(path)` | `zspace_remove` |
| 搜索 | `zs find <keyword>` | `client.search(kw)` | `zspace_search` |
| 树形浏览 | `zs tree [path]` | `client.tree(path)` | `zspace_tree` |
| 上传 | `zs up <local> <dir>` | `client.upload(local, dir)` | `zspace_upload` |
| 下载 | `zs down <path> [dir]` | `client.download(path, dir)` | `zspace_download` |
| 连接检查 | `zs check` | `client.is_connected()` | `zspace_check` |

> `ls` 会自动分页（NAS 单次最多返回 50 条，会循环拉全）；`find` 走 NAS 全文索引，跨目录搜索。

---

## 工作原理

极空间没有官方 CLI 或公开 API。**zspace-cli** 通过桌面客户端的本地代理访问 NAS，所以只要客户端在线，NAT 后也能用：

```
Skill / zs / SDK / MCP  →  127.0.0.1:13579（桌面客户端代理）  →  NAS
```

> **免责声明**：本项目是**非官方的社区项目**，与极空间官方无关，也未获其认可。它依赖桌面客户端的本地代理接口，该接口**并非官方公开文档**。本项目**只读取你本机、你自己账号的登录态**——不绕过认证、不破解加密、不触碰他人数据。请自行承担使用风险，并确保你的使用符合极空间的用户协议及当地法律。

### 平台支持

任何系统只要桌面客户端在 `127.0.0.1:13579` 暴露本地代理即可使用。登录态（`vuex.json`）自动探测：

| 平台 | 默认位置 |
|------|----------|
| macOS | `~/Library/Application Support/zspace/vuex.json` |
| Windows | `%APPDATA%\zspace\vuex.json`（也尝试 `%LOCALAPPDATA%`、`%USERPROFILE%`） |
| Linux | `~/.zspace/vuex.json`、`~/.config/zspace/vuex.json`（尽力而为） |

若客户端把配置放在别处，可显式指定：

```bash
zs --config-dir ~/path/to/zspace-config check
ZS_CONFIG_DIR=~/path/to/zspace-config zs check   # 或环境变量
```

> Windows/Linux 配置路径是尽力猜测（未经真实客户端验证）。若自动探测不到你的路径，请开 issue 附上实际路径，便于补充。

> **Windows ARM64**：`[mcp]` 的部分依赖（如 `cryptography`）并非每个版本都提供 ARM64 wheel，直接 `pip install "zspace-cli[mcp]"` 可能从源码编译（很慢，无 Rust 会失败）。建议强制使用预编译 wheel：
> `pip install --only-binary=:all: "zspace-cli[mcp]"`。

### 可选：MCP 配置

```json
{
  "mcpServers": {
    "zspace": { "command": "zs-mcp", "args": [] }
  }
}
```

---

## API 参考

| 端点 | 关键参数 |
|------|----------|
| `/v2/file/list` | `path`, `show_hidden`, `start`, `limit` |
| `/v2/file/info` | `path` |
| `/v2/file/modify` | `path`, `newname` |
| `/v2/file/newdir` | `parent`, `name`, `rename=0` |
| `/v2/file/move` / `copy` | `paths[]`, `to` |
| `/v2/file/remove` | `paths[]` |
| `/v2/file/create` | 二进制 body，header `path`（上传） |
| `/v2/file/download` | GET `path`, `remote_port=8050` |
| `/file_search/file_search` | `keyword` |

> 注意：接口参数名不标准（如用 `parent`/`to` 而非 `path`/`dest`），这是极空间接口本身的命名习惯，由社区根据桌面客户端行为整理。

---

## 仓库结构

```
zspace-cli/
├── src/zspace_cli/
│   ├── cli.py         # Typer CLI（zs ...）
│   ├── client.py      # ZSpaceClient SDK（重试 / 流式 / 进度）
│   ├── auth.py        # vuex.json 自动探测 + 凭据缓存
│   ├── mcp_server.py  # MCP tools（zs-mcp）
│   └── skills/        # 打包进 wheel 的 Agent skills
├── skills/            # Skill 文档与源
├── scripts/mcp_smoke.py
├── tests/             # pytest（CLI + SDK + MCP + auth）
└── promo/             # 发布/推广材料（submodule）
```

---

## Roadmap

- [x] 文件上传 / 下载
- [x] Linux / Windows 客户端鉴权（尽力路径探测 + `ZS_CONFIG_DIR`）
- [ ] Docker 无头模式
- [ ] 批量 glob 辅助

---

## 贡献

先开 issue 讨论再改。欢迎 PR。

## 许可证

MIT
