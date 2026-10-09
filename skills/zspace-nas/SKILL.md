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
zs rm /sata11/my/data/某文件                # 删除（进回收站，空间未释放！）
zs find "关键词" /sata11/my/data            # 搜索
zs tree /sata11/my/data/影视 3              # 树形浏览
```

### 存储画像与健康

```bash
zs usage                                    # 全盘物理占用：按池/用户/类别
zs usage --refresh --wait 120               # 先让 NAS 重算再读
zs du /sata11/my/data/影视                  # 目录体积（服务端，秒出）
zs du /sata11/my/data/大目录 --walk         # 服务端不收敛时改用遍历（慢但准）
zs bigfiles /sata11/my/data --min-size 1024 # 服务端找大文件（全库 269 万文件约 2m41s）
zs disks                                    # 每盘余量/温度/健康/碎片/通电小时 + 空盘位
zs smart --all                              # SMART 报告
zs smart WWZ398TH                           # 按序列号查单盘
```

`zs usage` 是回答「空间被什么占了」的**首选**：它是唯一能看到 Time Machine 备份、
其他用户空间、Docker/RAID 元数据的途径——文件级遍历对这些一律 `N001411 无权限`。

`zs du` 默认走服务端 `/v2/file/statistic`，中小目录一次请求即精确；大目录服务端
**永不收敛**且无轮询接口，此时命令会明确告警「部分值，不可当真」并提示加 `--walk`。

### 回收站

```bash
zs recycle list                             # 看回收站里有什么（含原位置）
zs recycle list --public                    # 公共/家庭回收站（另一套存储）
zs recycle config                           # 保留策略（默认 -1 = 永不清理）
zs recycle config --my-cycle 30             # 设为保留 30 天
zs recycle restore 某文件.zip               # 恢复到原位置
zs recycle purge 某文件.zip                 # 只永久删这一项（安全）
zs recycle empty                            # 清空整个个人回收站（不可逆，先列清单确认）
```

**`zs rm` 只是移入回收站，池子空间一个字节都不会变**，必须 `zs recycle empty` 才真正释放。
个人与公共是**两套独立存储**：清公共回收站对个人空间删除的文件毫无作用
（接口会返回成功、`total_num: 0`）。清空前务必先 `zs recycle list`——里面可能混着
别人或早前删的东西。

> **挂载盘上它叫 `@Recycle`，不是 `/.recycle/my`。** 本 skill 的 `zs` 命令走 API，
> 看到的是 `/.recycle/my`；而 `skills/` 下那些整理脚本跑在 SMB 挂载路径上，看到的是
> 共享根下的 `@Recycle` 目录（另有 `.zspace_trash` 变体）。已核实两者都不在
> `/<pool>/my/data` 里面。自己写脚本扫挂载盘时**必须跳过 `@Recycle`**，否则会把已删除
> 的数据算成在库数据——这类错误不报错，只让数字安静地偏大（详见
> [api-reference.md](api-reference.md) 的「同一样东西的三种名字」）。

## Python SDK

```python
from zspace_cli import ZSpaceClient, ZSpaceError

with ZSpaceClient() as c:
    items = c.ls('/sata11/my/data')          # 单次最多约 50 条
    c.rename('/sata11/my/data/旧名字', '新名字')  # 第二参数纯文件名
    c.mkdir('/sata11/my/data', '新目录')
    c.move('/sata11/my/data/src', '/sata11/my/data/dest')
    c.copy('/sata11/my/data/src', '/sata11/my/data/dest')
    c.remove('/sata11/my/data/某文件')       # 进回收站，空间未释放

    # 分页（超过 50 条）
    resp = c._post('/v2/file/list', {'path': path, 'start': 50, 'limit': 50})
```

### 存储画像 / 健康 / 回收站

```python
with ZSpaceClient() as c:
    for p in c.usage_summary():              # 按池聚合的物理占用
        print(p.pool, p.total, p.by_owner()) # 含 Time Machine、其他用户、Docker
    c.disk_usage_refresh()                   # 让 NAS 重算快照
    c.disk_usage_status()                    # {is_running, updated_at}

    st = c.statistic('/sata11/my/data/影视') # 服务端目录统计，秒出
    if st.complete:                          # ⚠️ 大目录会一直是 running
        print(st.size, st.files, st.dirs, st.categories)
    else:
        st = c.walk_stat('/sata11/my/data/影视', workers=12)  # 回退：遍历

    scan = c.find_large('/sata11/my/data', min_size=1024*1024*1024)  # 服务端找大文件
    if scan.complete:                          # state: 1=扫描中, 2=完成
        print(scan.scanned, scan.matched)
        for f in scan.files[:20]:
            print(f.size, f.path)
    c.find_large_delete()                      # 只有一个全局任务槽，用完清掉

    for d in c.disks():                      # 每盘余量/温度/健康/碎片/通电小时
        print(d.pool, d.position, d.model, d.used_pct, d.power_on_hours)
    c.free_bays()                            # {'sata': 1, 'nvme': 3, ...}
    c.smart('WWZ398TH')                      # SMART 属性表（按 sn）

    entries, total = c.recycle_list()        # 个人回收站（自动等扫描完成）
    c.recycle_restore([entries[0].path])     # 恢复（注意用回收站内路径）
    c.recycle_purge([entries[0].path])       # 只永久删这一项
    c.recycle_empty()                        # 清空整个个人回收站（不可逆）
    c.recycle_config()                       # {'my_cycle': -1, ...} -1=永不清理
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
- 全盘占用：`/system/diskusage3`（按**盘**返回，需自己按 `pool_name` 求和）
- 目录体积：`/v2/file/statistic`（`paths[]`），**必须检查 `state`**
- SMART：`/zspool/smart/report2` 按 **`sn`** 查，传 `pool_id`/`/dev/*` 报 `N300403`
- 回收站：个人 `/.recycle/my` + `/v2/file/nb/list`(参数 `num`) + `/v2/file/rclean`；
  公共 `/.public_recycle` + `/v2/public/recycle/clean`。恢复用 `/v2/file/restore` 且参数是
  **单数 `path`**（`paths[]` 会报 `N001411`）

## 已知限制

- 单次 list 最多 50 条，大目录需分页；**`limit` 传多大都只回 50**
- 目录的 `size` 恒为 `0`，`/v2/file/list` 拿不到目录体积，`sortby=size` 对目录也无效
- **文件 API 只能访问 `/<pool>/my/data`**：`/`、`/<pool>/my`、`/recycle`、`/<pool>/docker`
  等一律 `N001411`，主账号也不例外 → 遍历必然少算池占用，全貌要用 `diskusage3`
- `/v2/file/statistic` 对大目录（数十万文件）**永不收敛**，只给抖动的部分值，
  而 `/v2/file/statistictask` 返回全空、无法轮询 → 大目录只能遍历
- 遍历代价由**目录数**决定：约 1 请求/目录。448k 文件/89k 目录 ≈ 93k 请求 ≈ 12 分钟(16 并发)
- `remove()` 进回收站且**不释放空间**；默认 `my_cycle=-1` 永不自动清理
- API 为社区整理的非官方接口，可能随客户端更新变化
- 目前鉴权依赖 macOS 桌面客户端

> 自己找接口：网页版由 NAS 经本地代理提供（`http://127.0.0.1:13579/home/`），
> 从 `static/js/index.<hash>.js` 里 `grep -oE 'url:"(/[a-zA-Z0-9_/.-]+)"'` 可抽出全部
> 741 个端点；懒加载页面在 `static/js/async/<chunkId>.<hash>.js`。详见
> [api-reference.md](api-reference.md) 的「如何自己找接口」。


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
