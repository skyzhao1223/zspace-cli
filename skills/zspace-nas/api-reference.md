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

**删除是移入个人回收站，不是抹除**：数据被搬到 `/<pool>/.recycle/my/`，仍计入
`usage_size`，池子的 used/free **一个字节都不会变**（实测删 43.6 GiB 后前后读数完全一致）。
要真正释放空间必须再调 `/v2/file/rclean`（见下文「回收站 API」）。

保留策略由 `/v2/file/recycle/config/get` 给出，出厂默认 `my_cycle = -1` 即**永不自动清理**，
所以删掉的空间会一直占着，直到有东西清空回收站。

### 其他已知端点

| 端点 | 说明 |
|------|------|
| `/v2/file/categories` | 文件分类统计 |
| `/disk/statics` | 磁盘使用统计（按用户，实测 `used` 恒为 0，不可用） |
| `/zspool/info` | 存储池信息 |
| `/v2/recent/list` | 最近访问文件 |

## 存储画像 API

回答「空间被什么占了」的正确工具。**文件级遍历会系统性少算**：普通 token 对
`/recycle`、`/<pool>/my`、`/<pool>/docker`、`/<pool>/backup` 以及**其他用户的空间**
一律返回 `N001411 无权限`，只有 `/<pool>/my/data` 可读。Time Machine 备份、保险箱、
Docker、RAID 元数据、别的账号的文件全都在遍历视野之外。

### 全盘物理占用 — POST /system/diskusage3

无参数。**仅 127.0.0.1 可访问**（走桌面客户端本地代理即可）。

```jsonc
{"data": {"ctime": 1791530738, "disk_usage": [
  {"pool_name": "sata11", "mount_point": "/data_s001", "dev_name": "sdc", "position": "1",
   "usage_v2": {
     "user": [{"id": 1, "is_master": 1, "username": "138…", "remark": "138…",
               "list": [{"label": "my",         "phy_size": 4587160039424},
                        {"label": "my_tm",      "phy_size": 149629927424},
                        {"label": "my_recycle", "phy_size": 1371807744},
                        {"label": "my_safe",    "phy_size": 9098919936}]}],
     "public_recycle": {"label": "pub_recycle", "phy_size": 0},
     "public": [],
     "sys": [{"label": "sys_raid",   "phy_size": 20940668928},
             {"label": "sys_docker", "phy_size": 125865766912},
             {"label": "sys_vm",     "phy_size": 0},
             {"label": "sys_iscsi",  "phy_size": 0},
             {"label": "sys_other",  "phy_size": 115899088896}]}}]}}
```

关键点：

- **`disk_usage` 是按「盘」而非按「池」**：3 盘池会有 3 条，必须自己按 `pool_name` 求和，
  才能对上 `/zspool/info` 的池级 `usage_size`（实测误差 < 0.1%）
- 同一 `(用户, label)` 会在每块盘各出现一次，聚合时要合并，否则表格会重复 N 行
- `label` 含义：`my` 个人文件 / `my_tm` Time Machine 备份 / `my_recycle` 个人回收站 /
  `my_safe` 保险箱；`sys_raid` 池元数据 / `sys_docker` / `sys_vm` / `sys_iscsi` / `sys_other`
- 数据是**周期性重算的快照**，配套 `/system/diskusage/status`（`{is_running, updated_at}`）
  与 `/system/diskusage/runanyway`（触发立即重算，异步；调用后 `is_running` 变 1）

### 目录体积 — POST /v2/file/statistic

| 参数 | 说明 |
|------|------|
| `paths[]` | 目录路径（数组格式，可多个） |
| `show_hidden` | `1` / `0` |

返回 `data.task`，字段：`state`、`size`、`tfnum`（递归文件数）、`tdirnum`（递归目录数）、
`hidden_size` / `hidden_fnum` / `hidden_dirnum`、以及分类计数
（`tvnum` 视频 / `tanum` 音频 / `tinum` 图片 / `tdocnum` 文档 / `tappnum` 应用 / `tcomnum` 压缩包；
带 `t` 前缀是递归总数，不带的是直接子项）。

**⚠️ 大目录陷阱（务必检查 `state`）**：中小目录一次请求即精确
（实测 `影视` 返回 2.9198 TiB / 7,378 文件 / 435 目录，与完整遍历逐位吻合）。
但大目录会返回 `state: "running"` 加一份**部分且抖动**的快照——对 1.175 TiB / 448,687 文件的目录
连续调用 14 次，全部 `running`，`size` 在 0.35 TiB 附近、文件数在 27,457~30,366 之间跳，
**永不收敛**，而真值是它的 3.4 倍。

看起来像配套轮询接口的 `/v2/file/statistictask`（参数 `show_hidden`，无需 task_id）
**实测返回全空 task，毫无用处**。所以没有轮询机制可用：`state != "done"` 时只能
放弃服务端结果、改走 `/v2/file/list` 递归遍历。

### 硬件与健康

| 端点 | 参数 | 说明 |
|------|------|------|
| `/zspool/hardware/info` | — | 槽位数 `{slot:{sata,nvme,esata}}` |
| `/zspool/smart/report2` | **`sn`** | 完整 SMART 属性表；传 `pool_id` 或 `/dev/*` 会报 `N300403 参数错误` |
| `/zspool/info` | — | 除 `pool_list` 外还有 `cache_list` / `free_list` / `ext_mnt_list` |
| `/zspool/polling` | — | 轻量实时状态 |
| `/zstatus` | GET | HTML 状态页（开机时长、序列号、负载、各盘位使用率） |

`/zspool/info` 的 `disk_list[]` 里可直接取到：`temp`、`health`、`suspected_smr`、
`fs_fragment.percentage`（碎片率）、`simple_smart.power_on_hours`、
`simple_smart.reallocated_sector_count`。

> 小技巧：把 `power_on_hours` 和「装机时间 `itime` 至今的小时数」对比，若通电小时**更多**，
> 说明这块盘装机前就被用过（二手/拆机盘）。

### 服务端大文件扫描 — POST /v2/file/find/large/{create,info,delete}

比客户端遍历快得多的找大文件方式：实测全库 269 万文件 **2 分 41 秒**扫完，
而 7k 文件的小目录只要 **约 2 秒**（客户端遍历要 765 请求 / 10 秒）。

| 端点 | 参数 | 说明 |
|------|------|------|
| `create` | **`paths[]`** + `min_size` + `max_size` + `topk` | 启动扫描，立即返回 `state: 1` |
| `info` | 无 | 查当前任务；无任务时报 `N001307 任务不存在` |
| `delete` | 无 | 停止/清除任务（`N001307` 视为已清，网页版也这么处理） |

**`create` 的四个参数缺一不可**——只传 `paths[]` 会报 `N001212 参数有误`，
不会用默认值。网页版用的是 `min_size=0x3200000`（50 MiB）、
`max_size=0x4000000000000`（1 PiB）、`topk=1000`。

`info` 返回：

```jsonc
{"id": 859634, "paths": ["/sata11/my/data/软件"], "min_size": 52428800,
 "max_size": 1125899906842624, "topk": 1000,
 "state": 2,            // 1=扫描中, 2=完成
 "scan_count": 6979,    // 扫过的文件数
 "match_count": 311,    // 命中数（⚠️ 网页版对它做了 Array.isArray 判断，可能是数组）
 "list": [ /* 完整 file 条目，含 size/path/modify_time/ftype */ ]}
```

**只有一个全局任务槽**（`info`/`delete` 都不接受 task id），所以新建前要先清掉旧任务，
且两个并发扫描会互相干扰。网页版的轮询节奏是 2.5 秒一次、判据 `Number(state) === 1`。

## 回收站 API

**个人与公共是两套完全独立的存储，接口也不同**——这是最容易踩的坑：
删自己个人空间的文件进的是个人回收站，此时去清公共回收站会返回
`code: 200` 且 `total_num: "0"`，**成功但什么都没清、空间一点没释放**。

| 用途 | 个人回收站 | 公共/家庭回收站 |
|------|-----------|----------------|
| 虚拟根路径 | `/.recycle/my` | `/.public_recycle` |
| 列举 | `/v2/file/nb/list` | 同左（换 path） |
| 清空 | **`/v2/file/rclean`** | `/v2/public/recycle/clean` |
| 恢复 | `/v2/file/restore` | `/v2/public/recycle/restore` |

实际存储位置是 `/<pool>/.recycle/my/<名字>`，该路径本身对文件 API 返回 `N001411`，
只能通过上面的虚拟根 `/.recycle/my` 访问。

### 列举 — POST /v2/file/nb/list

| 参数 | 说明 |
|------|------|
| `path` | `/.recycle/my` 或 `/.public_recycle` |
| `start` / **`num`** | 分页；注意是 `num`，不是 `limit` |

返回 `{scan_info, req_param, info, list, total}`。首次调用会触发服务端扫描，
`scan_info.state != "done"` 时 `list` 不完整，**必须轮询到 done**，否则会把「扫描中」误读成「回收站是空的」。
条目带 `original_path`（删除前的位置），恢复时要用。

### 清空 — POST /v2/file/rclean

**无参数**。一次清掉**所有池**的个人回收站（返回的 task 里
`src: ["/nvme12/.recycle/my", "/sata11/.recycle/my"]`）。
响应 task 含 `total_num` / `succeed_num` / `fail_num` / `total_size`，据此确认实际清了多少。
**不可逆**，且会连带清掉别人/别的时候删进来的东西——清之前务必先 `nb/list` 看一眼。

### 恢复 — POST /v2/file/restore

| 参数 | 说明 |
|------|------|
| **`path`** | 回收站内路径，**单数**。传 `paths[]` 会报 `N001411 无权限` |

批量恢复只能逐个调用。（`/v2/file/mrestore`、`/v2/file/restoreall` 存在，但参数契约未验证。）

### 选择性永久删除单项

对**回收站内路径**调用普通的 `/v2/file/remove` 即可只删该项、其余原样保留
（网页版勾选删除走的就是这条路）。当回收站里混着不想动的东西时，这比 `rclean` 安全得多。

### 保留策略 — POST /v2/file/recycle/config/get · save

`{my_cycle, public_cycle}`，单位天，`-1` = 永不自动清理。`save` 需同时传两个字段
（只想改一个时先 `get` 再把另一个原值带上）。

### 下载支持 Range

`/v2/file/download`（GET，参数 `path` + `remote_port=8050`）**接受 `Range` 请求头**，
但响应状态码是 **200 而非 206**，body 只有请求的区间。
可用于只取文件尾部——例如读 ZIP 中央目录来核对压缩包与已解压目录是否一致，
无需下载整个 27 GiB。注意先检查 `Content-Length` 并给读取量设上限，
万一服务端忽略 Range 会把整个文件灌进内存。

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

- API 单次返回最多 50 条记录，大目录需分页（`start` + `limit` 参数）。
  **`limit` 上限就是 50**：传 100/200/500 实测仍只返回 50 条，别指望调大能提速
- `/v2/file/create` 单请求体积受本地代理限制（超限 413），大文件用 `/v2/file/upload` 分片协议（见上文；zspace-cli 已自动路由）
- API 来源于社区整理（非官方文档），可能随客户端版本更新而变化
- `move`/`mkdir` 的参数名不同于直觉：用 `to`（非 `dest`）、`parent`（非 `path`）+ `rename=0`
- **文件 API 的路径作用域只有 `/<pool>/my/data`**：`/`、`/<pool>`、`/<pool>/my`、
  `/<pool>/docker`、`/<pool>/backup`、`/recycle` 全部 `N001411`，即使当前账号是主账号
  （`is_master=1`）也一样。要看全池占用只能用 `/system/diskusage3`
- **目录的 `size` 字段恒为 `0`**，`fnum`/`tfnum` 等计数字段在 `/v2/file/list` 里也全是 `0`，
  拿不到目录体积；`/v2/file/list` 传 `sortby=size` 对目录无效（都是 0，排不出东西）
- `/v2/file/statistic` 对大目录不收敛且无可用轮询接口（见上文「⚠️ 大目录陷阱」）
- 递归遍历的代价由**目录数**决定而非字节数：约 1 请求/目录。实测 448k 文件 / 89k 目录
  需要 ~93k 请求、16 并发下 12 分钟；一个 216 万文件的同步目录需要 ~285k 请求、57 分钟

## 如何自己找接口

桌面客户端是 Electron 壳，**真正的网页版由 NAS 通过本地代理提供**：

```bash
curl -sL http://127.0.0.1:13579/home/          # 网页版入口
curl -s   http://127.0.0.1:13579/home/static/js/index.<hash>.js -o index.js
```

从 `index.js` 抽全部接口路径（本机实测 741 个，远多于本文档收录的）：

```bash
grep -oE 'url:"(/[a-zA-Z0-9_/.-]+)"' index.js | sed 's/url:"//;s/"//' | sort -u
```

调用点形如 `function xx(t){return(0,n.Z)({url:"/v2/file/rclean",method:"post",data:t})}`，
导出表把语义名映射到压缩后的函数名（如 `rdelall:t_`、`cleanPubRecycle:tv`、
`getRecycleFileList:K`），据此能反推每个接口的用途和参数形状。
懒加载页面的代码在 `static/js/async/<chunkId>.<hash>.js`，chunkId→hash 的映射表也在 `index.js` 里。

`~/Library/Application Support/zspace/vuex.json` 可读到 `deviceMode`（机型，如 `z423`）、
`diskNum`（盘位数）、`isMaster`（是否主账号）、`nasId` 等，排查兼容性问题时比猜有用。

