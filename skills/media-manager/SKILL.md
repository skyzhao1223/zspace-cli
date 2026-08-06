---
name: zspace-media-manager
description: >-
  Organize movies and TV series on ZSpace (极空间) NAS using zspace-cli.
  Naming conventions live in media-naming-guide; this skill handles ZSpace API ops.
  Use when 极空间影视整理, 影视命名扫描, ZSpace media, NAS 影视管理, or zspace rename.
---

# ZSpace 影视文件管理

基于 `zspace-cli`（零配置）整理极空间影视库。先扫描、出 `old→new` 预览，再执行 rename/move。

> **命名规范**见 [media-naming-guide](https://github.com/skyzhao1223/media-naming-guide)  
> 本 skill 只含极空间 API 落地（分页、rename/move、踩坑）。

**前置 skill**：通用文件操作用同仓库 [`zspace-nas`](../zspace-nas/)。

## Prerequisites

- `pip install zspace-cli` + `zs check` 通过
- 极空间 macOS 桌面客户端已登录
- Python 3.9+

## 工作流程

### Step 1: 扫描

```bash
# 在本 skill 目录下，或写绝对路径
python skills/media-manager/scripts/scan.py /sata11/my/data/影视
python skills/media-manager/scripts/scan.py --json /sata11/my/data/影视 > /tmp/issues.json
```

### Step 2: 修复

修复顺序见 [media-naming-guide SKILL.md 整理操作清单](https://github.com/skyzhao1223/media-naming-guide/blob/main/SKILL.md#整理操作清单)。

### Step 3: 验证

重新扫描，问题数 = 0 即完成。

## 递归遍历核心逻辑

极空间 API `/v2/file/list` 每次最多返回 50 条，必须用 `start` + `limit` 分页遍历。

```python
def scan_all(client, path, depth=0, max_depth=8):
    """递归遍历目录树，处理 50 条分页限制"""
    if depth > max_depth:
        return
    start = 0
    while True:
        try:
            resp = client._post('/v2/file/list', {
                'path': path, 'start': start, 'limit': 50, 'show_hidden': 0
            })
        except Exception:
            break
        data = resp.get('data', resp) if isinstance(resp, dict) else {}
        items = data.get('list', []) if isinstance(data, dict) else []
        if not items:
            break
        for item in items:
            yield item  # name, path, is_dir
            if item.get('is_dir') == '1':
                yield from scan_all(client, item['path'], depth + 1, max_depth)
        if len(items) < 50:
            break
        start += 50
```

## 文件操作 API

### 重命名

```python
# rename(原路径, 新文件名)  — 第二参数是纯文件名，不含路径
c.rename('/sata11/my/data/影视/电影/旧名', '新名')
```

### 移动

```python
# move(源路径, 目标目录路径)
c.move('/sata11/my/data/影视/电影/某剧 S01', '/sata11/my/data/影视/剧集')
```

### 创建目录

```python
# mkdir(父目录路径, 新目录名)
c.mkdir('/sata11/my/data/影视/电影', '新电影 Movie Name (2024)')
```

### 删除

```python
c.remove('/sata11/my/data/影视/电影/空目录')
```

## 视频内容验证

文件名不可信时，可通过 API 获取视频元数据辅助判断。

### 元数据验证

通过 `/v2/file/info` 获取：

```python
info = c._post('/v2/file/info', {'path': video_path})
data = info.get('data', info)
duration_min = int(data.get('duration', 0)) // 60
resolution = f"{data.get('width')}x{data.get('height')}"
size_mb = int(data.get('size', 0)) // (1024 * 1024)
```

**用途**：排除明显不匹配的情况（如 22 分钟的文件不可能是 131 分钟的电影）。

### 视频片段下载（实验性）

```python
resp = c._http.get('/v2/file/download',
    params={**c._common_params(), 'path': path},
    headers={'Range': 'bytes=0-5242879'})  # 前 5MB
```

可以下载前 5MB 后用 ffmpeg 提取帧截图确认内容。

**当前限制**：token 有效期短、需要本地 ffmpeg、不支持缩略图 API。

**替代方案**：请用户在极空间 App/Web 界面上打开视频确认。

## 踩坑记录

| 坑 | 表现 | 解决 |
|----|------|------|
| API 分页 | `c.ls()` 最多返回 50 条 | 用 `_post` + `start`/`limit` 循环遍历 |
| 只扫一层 | Season/4K 子目录遗漏 | 递归遍历 `max_depth=8` |
| rename 参数 | 第二参数含路径导致失败 | 第二参数是**纯文件名**，不含路径 |
| move/mkdir 参数 | `to` 不是 `dest` | 参数名见 zspace-cli SDK 文档 |
| token 过期 | 下载 API 中途失败 | 重新初始化 `ZSpaceClient` |

## 注意事项

- 操作前**先预览**（生成 old → new 映射表），确认后再批量执行
- 每次 rename/move 约 100ms，大批量注意总耗时
- `rename` 第二参数是纯文件名不含路径
- 新增资源后需重新跑扫描确认
- 扫描结果为 0 问题才算完成
