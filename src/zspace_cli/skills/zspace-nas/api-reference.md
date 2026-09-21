# ZSpace NAS API Reference

## 连接方式

极空间桌面客户端（Electron app）在本地建立代理隧道，所有 API 通过本地端口访问：

| 服务 | 地址 | 说明 |
|------|------|------|
| NAS 管理/文件 API | `127.0.0.1:13579` | 主接口，文件操作通过此端口 |
| 青龙面板 | `127.0.0.1:10007` | Docker 应用代理 |
| Syncthing | `127.0.0.1:13581` | 同步服务 |

## 认证

从 `~/Library/Application Support/zspace/vuex.json` 读取：
- `state.user.token` — 认证 token
- `state.nas.nasId` — NAS 标识
- `state.app.deviceId` — 设备标识

Token 以 Cookie 和 form body 两种方式同时传递。

## 请求格式

所有文件 API 均为 **POST** 请求，`Content-Type: application/x-www-form-urlencoded`。

每个请求必须包含以下公共参数（form body）：
- `token`, `nasid`, `plat=web`, `version=2.3.2026042401`, `device_id`, `_l=zh_cn`

URL 末尾必须附加 `?&rnd={timestamp}_{random}&webagent=v2` 查询参数。

## 文件操作 API

### 列出目录 — POST /v2/file/list

| 参数 | 说明 |
|------|------|
| `path` | 目录路径，如 `/sata11/my/data` |
| `show_hidden` | `0` 不显示隐藏文件 |

返回 `data.list[]`，每项含 `name`, `path`, `is_dir`, `size`, `mtime` 等。

### 文件详情 — POST /v2/file/info

| 参数 | 说明 |
|------|------|
| `path` | 文件或目录的完整路径 |

### 重命名 — POST /v2/file/modify

| 参数 | 说明 |
|------|------|
| `path` | 原文件/目录的完整路径 |
| `newname` | 新名称（仅名称，不含路径） |

### 创建目录 — POST /v2/file/newdir

| 参数 | 说明 |
|------|------|
| `parent` | 父目录路径 |
| `name` | 新目录名称 |
| `rename` | `0`（不自动重命名） |

### 移动文件 — POST /v2/file/move

| 参数 | 说明 |
|------|------|
| `paths[]` | 源路径（数组格式） |
| `to` | 目标目录路径 |

### 复制文件 — POST /v2/file/copy

| 参数 | 说明 |
|------|------|
| `paths[]` | 源路径（数组格式） |
| `to` | 目标目录路径 |

### 删除文件 — POST /v2/file/remove

| 参数 | 说明 |
|------|------|
| `paths[]` | 要删除的路径（数组格式） |

### 其他已知端点

| 端点 | 说明 |
|------|------|
| `/v2/file/categories` | 文件分类统计 |
| `/disk/statics` | 磁盘使用统计 |
| `/zspool/info` | 存储池信息 |
| `/v2/recent/list` | 最近访问文件 |

## 文件上传 API

### 小文件 — POST /v2/file/create

Body 为裸文件字节，目标路径放在 **HTTP header `path`** 中。

- header 值必须可 ASCII 编码：中文路径需传 **UTF-8 原始字节**（Python: `target.encode("utf-8")`；httpx ≥ 0.28 对 str 值强制 ASCII，直接传中文 str 会抛 `UnicodeEncodeError`）
- 本地代理（openresty）对请求体大小有上限，超限返回 **HTTP 413**；大文件必须走下面的分片协议

### 大文件 — POST /v2/file/upload（桌面客户端分片协议）

```
POST /v2/file/upload?remote_port=8050&drnd={毫秒时间戳}&uuid={uuid}
Content-Type: application/octet-stream
Body: 当前分片的裸字节（远端模式 ≤ 2MB/片）
```

会话 `uuid` 由客户端本地计算（无需注册接口）：

```
uuid = md5( str(ceil(mtime_ms) + size) + target_path )
```

注意：桌面客户端为 JS 实现——`lastModified + size` 两个数字先**相加**，其和再与目标完整路径**字符串拼接**。

每片请求需携带以下 header（并按同样内容拼进 `Cookie`：`nasid` 在 Cookie 中改名 `nas_id`，所有值 percent-encode）：

| Header | 说明 |
|--------|------|
| `app` | 固定 `file` |
| `path` | 目标完整路径（percent-encode） |
| `size` | 文件总大小 |
| `uuid` | 会话 uuid（同上公式） |
| `seek` | 当前分片起始偏移 |
| `split` | 固定 `1` |
| `Content-Length` | 当前分片长度 |
| `modify_time` | `ceil(ceil(mtime_ms)/1000)`（秒） |
| `crtime` | 创建时间（秒），可传空串 |
| `rename` | `0` |
| `token` / `plat=pc` / `nasid` / `version` / `device_id` / `device` | 鉴权字段（token/device 需 percent-encode；version 用 `state.app.version`） |
| `request-purpose` | `4` |
| `remote-port` | `8050` |

分片按 `seek` 顺序逐片上传，最后一片到达后 NAS 端拼装为目标文件（实测 680MB 视频往返校验一致）。错误码 `N001302` / `N001331` / `N001603` / `N001397` 为致命错误（不要重试），其余可短暂退避后重试。

## 路径格式

根路径格式：`/sata11/my/data/...`

其中 `sata11` 是物理磁盘标识，`my` 表示个人空间。

## 已知限制

- API 单次返回最多 50 条记录，大目录需分页（`start` + `limit` 参数）
- `/v2/file/create` 单请求体积受本地代理限制（超限 413），大文件用 `/v2/file/upload` 分片协议（见上文；zspace-cli 已自动路由）
- API 来源于社区整理（非官方文档），可能随客户端版本更新而变化
- `move`/`mkdir` 的参数名不同于直觉：用 `to`（非 `dest`）、`parent`（非 `path`）+ `rename=0`
