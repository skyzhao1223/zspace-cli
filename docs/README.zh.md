# zspace-cli（中文）

用命令行或 AI Agent 管理你的**极空间 NAS** —— 不填密码、不开 SSH、不配 DDNS。

> 只要 macOS 上极空间桌面客户端已登录即可。

[English README](../README.md) · [Skills 说明](../skills/README.md)

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

> 注意：接口参数名不标准（如用 `parent`/`to` 而非 `path`/`dest`），这是极空间接口本身的命名习惯。

---

## Roadmap

- [x] 文件上传 / 下载
- [ ] Linux / Windows 客户端鉴权
- [ ] Docker 无头模式
- [ ] 批量 glob 辅助

---

## 贡献

先开 issue 讨论再改。欢迎 PR。

## 许可证

MIT
