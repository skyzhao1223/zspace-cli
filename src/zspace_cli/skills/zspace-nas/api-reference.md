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

### 服务端大文件扫描

`/v2/file/find/large/create` / `info` / `delete` — 服务端大文件扫描任务（本次未实测参数契约）。

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

