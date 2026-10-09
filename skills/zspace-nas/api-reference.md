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

## 百度网盘集成 API（/znetdisk/*）

NAS 自带一个官方百度网盘模块，桌面客户端代理把它挂在 `/znetdisk/*` 下。它复用与文件 API **完全相同**的鉴权（`token`/`nasid`/`device_id` 公共参数 + Cookie）、请求格式（POST + `application/x-www-form-urlencoded`，URL 尾附 `?&rnd=…&webagent=v2`）和响应包（`{code:"200", msg, data}`，另带一个顶层 `ts`）——所以 `client._post()`/`_check_response()` 原样可用，实测（真实 NAS，2026-10）确认 `code` 就是字符串 `"200"`。

> **协议来源与置信度**：端点与参数形状取自 **NAS 侧的 Vue web 应用**（`http://127.0.0.1:13579/home/` 的懒加载 chunk），不是 `app.asar`——`grep -a znetdisk app.asar` **零命中**，因为这个模块跑在 NAS 上而非桌面 Electron 客户端里。定义全部端点的是 chunk `64392` 的模块 `2934`；任务中心 UI 在 chunk `4548`（模块 `39137`），绑定/信息/列表页在 `23402`/`20815`/`43877`。
>
> 下面每个端点标注 **实测**（本 PR 对真实 NAS 发过只读请求）或 **源码推断**（从上述 chunk 的调用点读出，未实机验证）。凡写操作、会员门槛、绑定/登出，一律只做源码推断——不拿真实账号去改远端状态。
>
> 这是**互操作性笔记**：百度网盘集成随 NAS 固件/客户端更新随时可能变，`code 15`、`down_state` 取值、字段名都不保证稳定。

### zspace-cli 已封装的只读端点

`ZSpaceClient.baidu_*` 方法与 `zs baidu` 子命令只覆盖**能安全只读验证**的端点。会员门槛后的写操作（转存、离线下载、上传、同步、自动备份、下单、激活、绑定/登出）**刻意不封装**——见文末「未封装端点」。

| SDK 方法 | CLI | 端点 | 置信度 |
|----------|-----|------|--------|
| `baidu_check()` | `zs baidu check` | `POST /znetdisk/auth/check` | 实测 |
| `baidu_userinfo()` | `zs baidu check` | `POST /znetdisk/auth/userinfo` | 实测 |
| `baidu_ls(path)` | `zs baidu ls [path]` | `POST /znetdisk/file/list` | 实测 |
| `baidu_tasks(state)` | `zs baidu tasks [--state]` | `POST /znetdisk/task/list` | 实测 |
| `baidu_fail_list(task_id)` | `zs baidu fails [--task-id]` | `POST /znetdisk/fail/list` | 实测 |
| `baidu_task_action(method, task_id)` | `zs baidu retry` | `POST /znetdisk/task/action` | 源码推断（写操作，未实机触发） |
| `baidu_share_verify(short_url, pwd)` | — | `POST /znetdisk/share/verify` | 源码推断 |
| `baidu_share_list(short_url, spwd, path)` | — | `POST /znetdisk/share/filelist` | 源码推断 |

> `baidu_task_action` 虽在 SDK 里，但它是**写操作**且被会员门槛/限速影响，`zs baidu retry` 也只在用户显式确认后才发。它在此列出是为了完整，不代表已实机验证成功。

### 绑定状态 — POST /znetdisk/auth/check

无业务参数。**实测**响应：

```json
{"code": "200", "msg": "OK", "ts": …,
 "data": {"is_login": true, "url": "…"}}
```

- **`data.is_login`**（bool）= 该 NAS 账号是否已绑定百度网盘。
- **`data.url`** = 未绑定时 web UI 打开的百度 OAuth 授权页地址。绑定流程是浏览器驱动的，CLI/SDK **不代办**（也不该把该 URL 打进日志）。

### 账号信息与配额 — POST /znetdisk/auth/userinfo

无业务参数。**实测** `data` 字段：

| 字段 | 说明 |
|------|------|
| `user_info.uk` | 百度账号 uk（数字标识） |
| `user_info.baidu_name` / `netdisk_name` | 百度账号名 / 网盘昵称 |
| `user_info.avatar_url` | 头像 URL |
| `user_info.vip_type` | 百度网盘自身会员等级（`2` = SVIP，据任务外 UI 渲染分支） |
| `user_info.iot_vip_type` | **百度NAS会员**（`1` = 有效）——分享转存的门槛就卡在这里 |
| `user_info.iot_vip_end_time` | NAS 会员到期时间 |
| `quota.used` / `quota.total` | 已用 / 总字节数（int） |
| `iot_vip_cashier` | NAS 会员购买页 URL |

> `iot_vip_type != 1` 时，`share/transfer` 被服务端拒绝（`code 15 需要NAS会员权限`），且 web UI 客户端侧也会先拦一道（`startTaskSubmit` 里 `1 != iot_vip_type` 直接弹「需要购买百度NAS会员」）。

### 列网盘目录 — POST /znetdisk/file/list

| 参数 | 说明 |
|------|------|
| `path` | 网盘内目录，根为 `/`（空串也按 `/` 处理） |
| `page` | **1 起**的页码（不同于文件 API 的 `start` 偏移） |
| `limit` | 每页条数 |

**实测**返回 `data.list[]` + `data.current_page`；每个 entry：

| 字段 | 说明 |
|------|------|
| `fs_id` | 文件 id（**注意**：own-pan 用 `fs_id`，`share/filelist` 用 `fsid`） |
| `server_filename` | 文件名 |
| `path` | 网盘内完整路径 |
| `size` | 字节数（int） |
| `isdir` | int `0`/`1` |
| `category` | 分类码 |
| `server_ctime` / `server_mtime` / `local_ctime` / `local_mtime` | 时间戳 |

分页停止条件：**某页返回条数 < limit** 即为最后一页（web UI 用同一判据）。`baidu_ls()` 按此循环拉全。

### 任务列表 — POST /znetdisk/task/list

| 参数 | 说明 |
|------|------|
| `page` / `limit` | 同上，1 起分页 |
| `state` | 过滤：`""`（全部）/ `running` / `done` / `pause` / `fail`（对应 web UI 的四个 tab，**源码推断**取值；`state=""` 实测有效） |

**实测**返回 `data.list[]` + `data.task_count` + `data.unfinished_task` + `data.current_page`/`page`。每个任务字段（实测）：`task_id`, `name`, `mode`, `task_type`, `down_state`, `scan_state`, `download_size`, `total_size`, `rate`, `retry_times`, `download_success`, `download_fail`, `file_total`, `fail_num`, `fail_reason`, `advice`, `baidu_limit`, `illegal_content`, `space_fulle`, `extend_info`, `created_time`, `save_path`, `remote_path`。

`down_state` 语义（**源码推断**，取自任务中心 UI 模块 `39137` 的渲染分支；与实测到的取值一致）：

| `down_state` | 含义 |
|--------------|------|
| `1` | 下载中（若 `retry_times > 0` 则 UI 显示「重试中」） |
| `2` | 已暂停 |
| `4` | 已完成 |
| `6` | 排队中 |
| `9` / `10` | 备份预处理（建目录 / 算文件信息） |

**「失败」不是独立的 `down_state`**，而是 web UI 的 `taskFailed` 组合判据（**源码推断**）：`fail_reason` 非空，或 `fail_num > 0 && down_state == 4`，或 `baidu_limit == 1`，或 `illegal_content > 0`，或 `space_fulle == 1`。`zs baidu tasks` 的「失败」标签复刻了这条规则。

### 失败文件列表 — POST /znetdisk/fail/list

| 参数 | 说明 |
|------|------|
| `page` / `limit` | 1 起分页 |
| `task_id` | 可选，限定到某个任务；省略即全部任务的失败文件 |

**实测**返回 `data.list[]` + `data.total` + `current_page`/`page`。每个 entry（实测字段）：`id`, `task_id`, `task_unit_id`, `baidu_task_id`, `file_id`, `file_name`, `file_path`, `file_size`, `download_size`, `fail_reason`, `baidu_fail_code`, `advice`, `retry_times`, `save_path`, `status`, `update_at`。

### 任务操作 — POST /znetdisk/task/action（写操作）

| 参数 | 说明 |
|------|------|
| `method` | 操作名（见下） |
| `task_id` | 单任务操作时带上；批量操作省略 |

`method` 取值（**源码推断**，取自任务中心的 `operateActionMap` + `changeTasks` 调用点）：单任务 `resume` / `pause` / `clean`；批量 `pause_all` / `resume_all` / `clean_all`（全部备份中）/ `clean_all_done`（全部完成）/ `resume_fail_all`（重试全部失败）/ `clean_fail_all`（清除全部失败）。

> `clean*` 会删除任务记录，不可撤销。`baidu_task_action()` 原样透传 method/task_id，不做本地校验；`zs baidu retry` 只映射到 `resume`（带 task_id）或 `resume_fail_all`（不带），且**默认要用户确认**（`--force` 跳过）。**未实机触发**这些写操作。

### 分享链接 — share/verify + share/filelist（源码推断，未实测）

服务端代验分享链接，不需要本地百度 Cookie（这点区别于 `baidu-pan-skill` 的浏览器 Cookie 路径）：

- `POST /znetdisk/share/verify` `{short_url, pwd}` → `data.spwd`（校验后的密码，供后续步骤用；web UI 发送前只做 trim，明文传输）。
- `POST /znetdisk/share/filelist` `{short_url, spwd, path, page, limit}` → 分享内条目，含 `fsid`（**不是** `fs_id`）、`server_filename`、`size`、`md5`、`isdir`；`data.count` 为总数。`path` 在分享内导航（根 `/`）。

> `baidu_share_verify()` / `baidu_share_list()` 已在 SDK 中，但**未实机验证**——需要真实分享链接，且验证会触发对百度的服务端请求。参数形状来自 chunk `64392` 的 `getLinkContentSubmit` 调用点。

### 未封装端点（会员门槛 / 写操作 / 未验证）

以下端点在模块 `2934` 中存在（**源码推断**），但 zspace-cli **刻意不封装**——要么改远端状态、要么被会员门槛挡住、要么无法在不动真实账号的前提下验证：

| 端点 | 参数（源码推断） | 为何不封装 |
|------|------------------|-----------|
| `share/transfer` | `{short_url, file_ids, to_path, spwd?}` | 写操作；`iot_vip_type != 1` 时服务端 `code 15`（#12 + baidu-pan-skill 双重印证） |
| `share/transfer_result` | `{task_id}` → `data.status`(success/fail), `to_fsids` | 转存的异步轮询，依附于 transfer |
| `file/download` | `{file_ids, save_path}` | 建 NAS 侧下载任务；#12 实测非会员**卡在 0 B/s**（百度对非会员 openapi 硬限速） |
| `file/upload` | `{paths, save_path}` | NAS→百度网盘上传任务，写操作 |
| `file/newdir` | — | 在网盘建目录，写操作 |
| `auth/token` | `{auth_code}` | 绑定授权码换 token，认证写操作 |
| `auth/logout` | — | 解绑，写操作 |
| `sync/{open,close,delete,home,list,add}` | 各异 | 自动备份配置，改远端状态（`sync/home` 读 `{is_open, download_list, save_path}`） |
| `autobackup/{info,add,delete,start,stop,faillist,clear_fail_files}` | 各异 | NAS→百度网盘自动备份，改远端状态 |
| `membership/active` | `{code}` | 激活码兑换会员，写操作 |
| `order/{get_cashier,check_free_vip,direct_charge}` | `{}` / 各异 | 会员下单/收银台（`get_cashier` → `data.iot_vip_cashier`） |
| `zdrive/baidu/rclone/mountinfo` | — | 注意前缀是 `/zdrive` 不是 `/znetdisk`；rclone 挂载信息，未验证 |

> **与 `baidu-pan-skill` 的关系**：本模块走 **NAS 侧**集成（服务端代验分享、NAS 直接落盘），无需本地百度 Cookie，但转存/直下被**百度NAS会员**门槛卡住，非会员 `file/download` 实测 0 B/s。[baidu-pan-skill](https://github.com/skyzhao1223/baidu-pan-skill) 走**本地浏览器 Cookie**路径（自己转存 + 分块下载 + 结构化校验），绕过 NAS 会员门槛但需要本地登录态。两者互补：能上 NAS 会员就用 `/znetdisk/*`（本模块），否则用 baidu-pan-skill 下到本地再 `zs up` 传 NAS。

## 路径格式

根路径格式：`/sata11/my/data/...`

其中 `sata11` 是物理磁盘标识，`my` 表示个人空间。

## 已知限制

- API 单次返回最多 50 条记录，大目录需分页（`start` + `limit` 参数）
- `/v2/file/create` 单请求体积受本地代理限制（超限 413），大文件用 `/v2/file/upload` 分片协议（见上文；zspace-cli 已自动路由）
- API 来源于社区整理（非官方文档），可能随客户端版本更新而变化
- `move`/`mkdir` 的参数名不同于直觉：用 `to`（非 `dest`）、`parent`（非 `path`）+ `rename=0`
