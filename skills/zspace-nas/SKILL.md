---
name: zspace-nas
description: >-
  Manage files on ZSpace (极空间) NAS through zspace-cli Python SDK and CLI.
  List, rename, move, copy, remove, search, and organize files on the NAS.
  Use when the user mentions 极空间, ZSpace, NAS file management,
  NAS storage operations, or zspace-cli.
---

# ZSpace NAS File Management

通过 `zspace-cli` 操作极空间 NAS。支持 CLI、Python SDK、MCP Server。

**差异化**：零配置——自动读取 macOS 极空间桌面客户端登录态，无需账号密码、SSH、DDNS。

## Prerequisites

- `pip install zspace-cli`
- 极空间 macOS 桌面客户端**已登录且正在运行**
- 认证自动从 `~/Library/Application Support/zspace/vuex.json` 读取

## 快速验证

```bash
zs check
zs ls /sata11/my/data
```

## CLI (`zs`)

```bash
zs check                                    # 检查连接
zs ls /sata11/my/data/影视                  # 列出目录
zs info /sata11/my/data/影视/某文件.mkv     # 查看详情
zs rename /sata11/my/data/旧名字 新名字     # 重命名（第二参数=纯文件名）
zs mv /sata11/my/data/src /sata11/my/data/dest/  # 移动
zs cp /sata11/my/data/src /sata11/my/data/dest/  # 复制
zs mkdir /sata11/my/data 新目录名           # 创建目录
zs rm /sata11/my/data/某文件                # 删除
zs find "关键词" /sata11/my/data            # 搜索
zs tree /sata11/my/data/影视 3              # 树形浏览
```

## Python SDK

```python
from zspace_cli import ZSpaceClient, ZSpaceError

with ZSpaceClient() as c:
    items = c.ls('/sata11/my/data')          # 单次最多约 50 条
    c.rename('/sata11/my/data/旧名字', '新名字')  # 第二参数纯文件名
    c.mkdir('/sata11/my/data', '新目录')
    c.move('/sata11/my/data/src', '/sata11/my/data/dest')
    c.copy('/sata11/my/data/src', '/sata11/my/data/dest')
    c.remove('/sata11/my/data/某文件')

    # 分页（超过 50 条）
    resp = c._post('/v2/file/list', {'path': path, 'start': 50, 'limit': 50})
```

## MCP（可选）

```bash
pip install "zspace-cli[mcp]"
```

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

## API 要点

- 基地址：`http://127.0.0.1:13579`
- POST `application/x-www-form-urlencoded`
- 公共参数：`token`, `nasid`, `plat=web`, `version`, `device_id`, `_l=zh_cn`
- `move`/`copy`：源=`paths[]`，目标=`to`
- `newdir`：父目录=`parent`，名=`name`，`rename=0`

## 已知限制

- 单次 list 最多约 50 条，大目录需分页
- API 为社区整理的非官方接口，可能随客户端更新变化
- 目前鉴权依赖 macOS 桌面客户端

## 相关 skills(整理工作流)

本 skill 是极空间文件操作的**底座**。专项整理任务优先路由到对应 skill
(它们的扫描跑在挂载路径上、跨 NAS 通用;极空间的写操作可回到本 skill 的 `zs` 命令):

| 任务 | 用哪个 |
|------|--------|
| 不知道从哪开始 / 全盘存储画像 | **nas-report**(入口,会给路由建议) |
| 照片视频按日期归档 | photo-organizer |
| 音乐库(歌手/专辑/曲目) | music-organizer |
| 工作文档(年份/项目/版本) | work-organizer |
| 作品集(项目/封面/成品源文件) | portfolio-organizer |
| 下载区清理分诊 | download-cleaner |
| 重复文件精确去重 | dedup-finder |
| 备份健康审计 | backup-auditor |

## 影视整理

专用工作流见独立 skill：**media-manager-skill**（https://github.com/skyzhao1223/media-manager-skill）  
通用命名规范：[media-naming-guide](https://github.com/skyzhao1223/media-naming-guide)

## 参考

- API 细节：[api-reference.md](api-reference.md)
- 源码：https://github.com/skyzhao1223/zspace-cli
- PyPI：https://pypi.org/project/zspace-cli/
