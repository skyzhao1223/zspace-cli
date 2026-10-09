# ZSpace NAS API Reference

## 连接方式

极空间桌面客户端（Electron app）在本地建立代理隧道，所有 API 通过本地端口访问：

| 服务 | 地址 | 说明 |
|------|------|------|
| NAS 管理/文件 API | `127.0.0.1:13579` | 主接口，文件操作通过此端口 |
| 青龙面板 | `127.0.0.1:<动态端口>` | Docker 应用代理，端口每次由桌面客户端分配 |
| Syncthing | `127.0.0.1:13581` | 同步服务 |

> **Docker 应用代理端口是动态的，不要硬编码。** 桌面客户端每次「打开」一个 Docker 应用都会重新分配本地端口：青龙面板实测落在 `10010`，早先文档写死的 `10007` 已失效，观测范围约 `9990–10150`（同一批端口里还可能混着 mihomo/metacubexd 等其他应用）。
>
> 正确做法是探测而非记忆：对候选端口请求 `GET /api/env.js`，响应体里出现 `__ENV__QlBaseUrl` 即为青龙面板。`zspace-qinglong-scripts` 的 `discover_qinglong()` 就是这个逻辑。
>
> `13579` / `13581` 是桌面客户端自身固定的代理端口，与上述动态端口不同，可以直接写死。

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

### 断点续传 — GET /v2/file/tmpinfo

分片上传中途失败（进程被杀、网络断）后 NAS 会保留已收到的字节，查询只要两个参数：

```
GET /v2/file/tmpinfo?path={目标完整路径}&uuid={会话 uuid}
```

`uuid` 用与上传时**同一个公式**（见上），所以不需要持久化任何状态就能重算。鉴权走与其余接口相同的 Cookie。

有未完成会话时的响应：

```json
{"code": "200", "data": {
  "size": "6291456",
  "name": ".f.bin.zd966fafcb435a7ed05e9f44ca1b898b0",
  "path": "/sata11/my/data/xxx/.f.bin.zd966fafcb435a7ed05e9f44ca1b898b0",
  "modify_time": "1791517279"
}}
```

- **`data.size`**（字符串）= NAS 已接受的字节数。桌面客户端读它作 `finishedSize` 并从该偏移续传（`app.asar` 里的 `Jo` / `getTmpInfo`：`method:"get"`、`params:{path,uuid}`，common 参数经 `nr()` 序列化进 Cookie）。
- **`data.name`** 揭示半成品是目标同目录下的**隐藏点文件** `.<名字>.z<会话id>`，普通 `ls` 看不到（需 `show_hidden`）。
- 上传**完成后会话即被消费**：同一查询改回 `N001315 文件不存在`。所以「没有会话」与「已完成」是同一个信号，都表示从头开始。

实测（真实 NAS，12MB 文件 / 2MB 分片）：

| 情形 | tmpinfo 返回 |
|------|-------------|
| 传 3 片后中断 | `code=200`，`data.size="6291456"` |
| 全部传完 | `N001315 文件不存在` |
| 从未上传过的路径 | `N001315 文件不存在` |
| 已有会话时重发 `seek=0` | **被接受**，且 `data.size` 不回退 |

最后一行很关键：它意味着**查询失败或偏移不可信时，退回从 0 重传是安全的**，不会撞上 `N001530`。`client._tmpinfo_size()` 正是这样兜底的——任何异常都返回 0；续传只是优化，不该让上传失败。它还刻意**不信任 `size >= total`**：NAS 是在最后一片到达时才拼装目标文件，若因此跳过所有分片可能什么文件都不产生。

`zs up` 默认开启续传（`--no-resume` 关闭），只对分片上传生效；真的跳过字节时会打印 `↻ 断点续传：跳过 NAS 已收到的 N MB`，返回值里也多一个 `resumed_from` 键（未续传时不加，保持既有形状）。

### 分片能否并发 —— 实测结论：不要

`vuex.json` 里的 `uploadProcess: 4` 曾被当作「客户端能并发发同一文件的分片」的线索。**静态与实机都否证了这个推断：**

- `uploadProcess` 在整个 `app.asar`（含 `app.asar.unpacked`）**只有一个读取点**，在 UploadCenter 的 `uploadNext()` 里、钳制 1..20 默认 4 —— 它是**同时上传的文件数**，不是分片并发数。
- `uploadSlice` 是**分片大小（MB）**（客户端日志原文 `"Upload by split to size " + e + "MB"`）；局域网下取该值，为 0 或非法时回退 10000MB，远端模式改用自适应的 `dynamicSplitSize`。

实机对照（12MB / 6 片，经本地代理 `127.0.0.1:13579`）：

| 发送方式 | seek 顺序 | 结果 | 往返 MD5 | 耗时 |
|---------|----------|------|---------|------|
| 严格顺序 | 0→5 | 0 错误 | ✅ 一致 | 11.27s |
| 4 线程并发 | 0,1,2,3,4,5（按序提交） | 0 错误 | ✅ 一致 | 10.41s |
| 逆序 | 5→0 | **5 个错误**（4× `N001530 无法断点续传` + 1× `502 Bad Gateway`） | ❌ **不一致（文件损坏）** | 28.0s |

结论：

1. **NAS 要求 `seek` 单调递增**，落后的分片被 `N001530 无法断点续传` 拒绝。
2. **乱序最危险的地方不是报错，而是产物仍然可下载、MD5 却不对**——静默损坏。
3. 并发那组虽然成功，但它是按序提交、到达顺序基本有序；这不代表机制安全，一次竞态就可能产出坏文件而用户看不到任何报错。补测还发现「已有 1 片时直接发 `seek=2 片`」也会被接受，即 NAS 对超前分片的态度并不一致。
4. **本地代理下加速只有 ~8%**（10.41s vs 11.27s），远低于「2–4×」的期望——瓶颈不是 RTT。高延迟中继场景未测。

所以 `zs up` 不提供 `--jobs`。将来若要重试，也必须保持 `seek` 递增。

> 另：`POST /v2/file/hash`（客户端里的 `er`）与 `POST /v2/file/upload/whyfail`（`Xo`）在 `app.asar` 中存在，但 `file/hash` **全 bundle 只出现在定义处、从未被调用**，参数与响应未知，要用只能实测；`whyfail` 顾名思义是上传失败诊断，同样未验证。

## 路径格式

根路径格式：`/sata11/my/data/...`

其中 `sata11` 是物理磁盘标识，`my` 表示个人空间。

## 已知限制

- API 单次返回最多 50 条记录，大目录需分页（`start` + `limit` 参数）
- `/v2/file/create` 单请求体积受本地代理限制（超限 413），大文件用 `/v2/file/upload` 分片协议（见上文；zspace-cli 已自动路由）
- API 来源于社区整理（非官方文档），可能随客户端版本更新而变化
- `move`/`mkdir` 的参数名不同于直觉：用 `to`（非 `dest`）、`parent`（非 `path`）+ `rename=0`
