#!/usr/bin/env python3
"""深度扫描极空间 NAS 影视目录 —— 正向验证版。

核心思路：不是检测"脏模式"，而是验证每个文件/目录是否符合规范。
任何不匹配规范格式的，全部输出。这样不会漏掉任何问题。

Usage:
    python scan.py [root_path]
    python scan.py                           # 默认 /sata11/my/data/影视
    python scan.py /sata11/my/data/影视/电影  # 只扫电影
"""

import re
import sys
import json
from zspace_cli import ZSpaceClient

DEFAULT_ROOT = '/sata11/my/data/影视'
MAX_DEPTH = 8

VIDEO_EXTS = {'mp4', 'mkv', 'avi', 'ts', 'rmvb', 'flv', 'wmv', 'mov', 'iso', 'm2ts'}
SUB_EXTS = {'srt', 'ass', 'ssa', 'sub', 'idx'}
JUNK_EXTS = {'torrent', 'nfo', 'td', 'htm', 'html', 'url', 'txt', 'jpg', 'png', 'nzb'}

# ── 合规格式定义 ──────────────────────────────────────────

# 电影文件夹名：中文名 English Name (年份) [分辨率 来源]
# 允许：中文名中混数字（毒液2）、CJK标点（：·）、数字开头（2001太空漫游）
MOVIE_DIR_OK = re.compile(
    r'^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+'  # 中文名（含标点、数字、英文如 ID/007）
    r'\s+'
    r'[\w][\w\s\':,.\-&!()0-9]+'                              # 英文名/年份/标签
    r'(\s*\(\d{4}\))?'                                         # (年份) 可选
    r'(\s*\[.*\])?'                                            # [分辨率 来源] 可选
    r'(\s*(1-\d|\d-\d|CD\d|导演剪辑版|\[副本\d?\]))?'          # 合集/CD/标注
    r'$'
)

# 电影内部视频文件名：应该与文件夹名一致（可以有 CD1/CD2/_2 后缀）
def movie_file_ok(filename, folder_name):
    stem = filename.rsplit('.', 1)[0]
    ext = filename.rsplit('.', 1)[-1].lower()
    if stem == folder_name:
        return True
    # 允许后缀：CD1, _2, [4K], [1080p], E{XX}, 语言标签, 前传1
    if stem.startswith(folder_name):
        suffix = stem[len(folder_name):]
        if re.match(r'^(\s*(CD\d|_\d|\[\w+\]|E\d{2,3}|\[粤语\]|\[国语\]|\[v\d\]|前传\d?))*$', suffix):
            return True
    # 合集文件夹（如 1-3、1-7）本身已标记为问题，内部文件不再单独校验
    if re.search(r'\d+-\d+$', folder_name):
        return True
    # 字幕文件允许语言标签
    if ext in SUB_EXTS:
        if stem.startswith(folder_name):
            return True
    return False

# 剧集文件夹名
SERIES_DIR_OK = re.compile(
    r'^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+'
    r'\s+'
    r'[\w][\w\s\':,.\-&!()0-9]+'
    r'(\s*S\d{2}(-S\d{2})?)?'
    r'(\s*\(\d{4}\))?'
    r'(\s*(特别篇|\d))?'
    r'$'
)

# 剧集内部文件名：
# 1) 纯集号: E01, S01E01, E01-E02
# 2) 带剧名前缀: 剧名 E01, 剧名 S01 E01
SERIES_FILE_OK = re.compile(
    r'^'
    r'([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+\s+[\w][\w\s\':,.\-&!()0-9]+\s+)?'  # 可选剧名前缀
    r'('
    r'E\d{2,3}'                           # E01, E02
    r'|S\d{2}\s*E\d{2,3}'                 # S01E01, S01 E01
    r'|E\d{2,3}-E\d{2,3}'                 # E01-E02
    r'|S\d{2}\s*E\d{2,3}-E\d{2,3}'        # S01E01-E02
    r')'
    r'(\s*(END|V\d))?'                    # END标记、V2等版本
    r'(\s*\[[\w.\s]+\])?'                 # [4K] [国语] [粤语]
    r'\s*\.'                              # 允许扩展名前有空格
    r'(mp4|mkv|avi|ts|rmvb|flv|wmv|mov)$',
    re.I
)

# 特殊内容：SP（彩蛋/花絮/MV等）、花絮/特辑/番外等
SERIES_SPECIAL_OK = re.compile(
    r'^'
    r'([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+\s+[\w][\w\s\':,.\-&!()0-9]+\s+)?'  # 可选剧名前缀
    r'(SP\d{2}(\s+[\u4e00-\u9fffA-Za-z]+)?'     # SP01, SP01 彩蛋, SP01 MV
    r'|花絮|特辑|彩蛋|预告|番外|幕后|特别篇|精华版|前传'
    r'|[\u4e00-\u9fff][\u4e00-\u9fff\w\s]*)'
    r'\.(mp4|mkv|avi|ts)$'
)

# ── 通用黑名单（任何位置都不应出现） ──────────────────────

BLACKLIST_CHARS = re.compile(r'[丨｜]')  # 审查规避用的特殊竖线

# 单字母替代汉字的模式（如 S探、Z义联盟、Q余Y年）
LETTER_SUB = re.compile(
    r'(?:^[A-Z]{1,2}[\u4e00-\u9fff])'     # 开头1-2个大写字母+汉字
    r'|(?:[\u4e00-\u9fff][A-Z]{1,3}[\u4e00-\u9fff])'  # 汉字+大写+汉字
    r'|(?:[\u4e00-\u9fff][A-Z]{1,3}$)'     # 汉字+大写结尾
)

WATERMARK = re.compile(
    r'【|】|\[微信|\[公众号|￡|@圣城|Mp4Ba|XZYS|XunLeiJia|'
    r'kkkanba|字幕侠|霸王龙|压制组|微信|爱影哥|瞎看菌|雷锋菌|影喵儿|'
    r'情话菌|影视步行街|RARBG|STUTTERSHIT|SmY|CHAOSPACE',
    re.I
)

PLACEHOLDER_ENGLISH = re.compile(
    r'\s+Erta\s*$|'           # "Erta" 占位符
    r'\s+TBD\s*$|'            # "TBD"
    r'\s+Unknown\s*$|'        # "Unknown"
    r'\s+XXX\s*$',            # "XXX"
    re.I
)


# ── 递归遍历（处理分页） ──────────────────────────────────

def scan_all(client, path, depth=0):
    if depth > MAX_DEPTH:
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
            name = item.get('name', '')
            item_path = item.get('path', f'{path}/{name}')
            is_dir = str(item.get('is_dir', '0')) == '1'
            yield {'path': item_path, 'name': name, 'is_dir': is_dir, 'depth': depth}
            if is_dir:
                yield from scan_all(client, item_path, depth + 1)
        if len(items) < 50:
            break
        start += 50


# ── 验证逻辑 ─────────────────────────────────────────────

def validate(item, root):
    """返回问题列表。空列表=合规。"""
    path = item['path']
    name = item['name']
    is_dir = item['is_dir']
    problems = []
    
    rel = path.replace(root + '/', '') if path.startswith(root) else path
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name and not is_dir else ''
    stem = name.rsplit('.', 1)[0] if ext else name
    
    # 确定所在区域
    in_movie = '/电影/' in path or path.endswith('/电影')
    in_series = '/剧集/' in path or path.endswith('/剧集')
    
    if not in_movie and not in_series:
        return []  # 非影视区域不检查
    
    # ── 通用黑名单检查（对所有文件/目录都做） ──
    if BLACKLIST_CHARS.search(name):
        problems.append('审查规避字符(丨｜)')
    
    if WATERMARK.search(name):
        problems.append('水印/站点标签')
    
    # 字母替代汉字检查 — 排除已知合规的模式
    clean_stem = re.sub(r'\[.*?\]|\(.*?\)', '', stem)  # 去掉方括号和圆括号内容
    if LETTER_SUB.search(clean_stem):
        # 排除：E01、S01E01 等集号格式
        if not re.match(r'^[ES]\d', name):
            # 排除：CD1、4K 等合规标签
            if not re.match(r'^(CD|4K|3D|2D|TV|HD|MP|ID)\d*', clean_stem):
                # 排除：已经是合规英文名中间的大写 (如 "The XX")
                if not re.search(r'[a-z][A-Z]', clean_stem):
                    problems.append('疑似字母替代汉字')

    # ── 占位符英文名 ──
    if is_dir and PLACEHOLDER_ENGLISH.search(name):
        problems.append('占位符英文名(需查找正确英文名)')

    # ── 垃圾文件 ──
    if not is_dir and ext in JUNK_EXTS:
        problems.append('垃圾文件')
        return problems
    
    if not is_dir and name.endswith('.bt.td'):
        problems.append('下载残留')
        return problems

    # ── 电影区域验证 ──
    if in_movie:
        # 一级子目录（电影文件夹）
        if is_dir and path == f'{root}/电影/{name}':
            if not MOVIE_DIR_OK.match(name):
                problems.append(f'电影文件夹名不合规')
            if re.search(r'\d+-\d+$', name):
                problems.append('合集文件夹(应拆分为独立文件夹)')
        
        # 花絮子目录合规（花絮, 花絮 - XXX）
        if is_dir and re.match(r'^花絮(\s*-\s*.+)?$', name):
            return []
        
        # 电影内部文件
        if not is_dir and ext in VIDEO_EXTS:
            parts = rel.split('/')
            # 跳过花絮子目录内的文件（花絮内文件名自成体系）
            if any(re.match(r'^花絮', p) for p in parts[1:]):
                return []
            if len(parts) >= 3:  # 电影/文件夹/文件
                folder = parts[1]
                if not movie_file_ok(name, folder):
                    problems.append(f'电影视频文件名不匹配文件夹')
        
        # 花絮子目录内的字幕文件也合规
        if not is_dir and ext in SUB_EXTS:
            parts = rel.split('/')
            if any(re.match(r'^花絮', p) for p in parts[1:]):
                return []
        
        # 散文件（直接在电影根目录）
        if not is_dir and path == f'{root}/电影/{name}':
            if ext in VIDEO_EXTS:
                problems.append('电影散文件(应放入独立文件夹)')

    # ── 剧集区域验证 ──
    if in_series:
        # 一级子目录（剧集文件夹）
        if is_dir and path == f'{root}/剧集/{name}':
            if not SERIES_DIR_OK.match(name):
                problems.append('剧集文件夹名不合规')
        
        # 剧集内部视频文件
        if not is_dir and ext in VIDEO_EXTS:
            parts = rel.split('/')
            if len(parts) >= 3:
                if not SERIES_FILE_OK.match(name) and not SERIES_SPECIAL_OK.match(name):
                    # 纯数字也不行
                    problems.append(f'剧集视频文件名不合规')
    
    # ── PT/Scene 原始命名 ──
    if not is_dir and ext in (VIDEO_EXTS | SUB_EXTS):
        if re.match(r'^[A-Za-z][\w.]+\.\d{4}\.', name):
            problems.append('PT/Scene原始命名')
    
    # ── 格式转换残留 ──
    if re.search(r'\.qsv\.|\.flv\.mp4$', name):
        problems.append('格式转换残留')

    return problems


# ── 主程序 ────────────────────────────────────────────────

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    output_json = '--json' in sys.argv

    with ZSpaceClient() as c:
        print(f'正在扫描 {root} ...\n', file=sys.stderr)

        stats = {'dirs': 0, 'files': 0}
        issues = []

        for item in scan_all(c, root):
            if item['is_dir']:
                stats['dirs'] += 1
            else:
                stats['files'] += 1

            problems = validate(item, root)
            if problems:
                rel = item['path'].replace(root + '/', '')
                issues.append({
                    'path': rel,
                    'name': item['name'],
                    'is_dir': item['is_dir'],
                    'problems': problems,
                })

        print(f'扫描完成: {stats["dirs"]} 目录, {stats["files"]} 文件\n', file=sys.stderr)

        if output_json:
            json.dump(issues, sys.stdout, ensure_ascii=False, indent=2)
            return

        if not issues:
            print('✅ 全部合规，零问题！')
            return

        # 重复资源检测（基于中文名去重）
        dir_names = {}
        for item in scan_all(c, root, depth=0):
            if item['is_dir'] and item['path'].count('/') == root.count('/') + 2:
                name = item['name']
                base = re.sub(r'\s*\[.*?\]', '', name)
                base = re.sub(r'\s*\(副本\d?\)', '', base)
                dir_names.setdefault(base, []).append(name)

        for base, names in dir_names.items():
            if len(names) > 1:
                for n in names:
                    issues.append({
                        'path': n,
                        'name': n,
                        'is_dir': True,
                        'problems': [f'疑似重复资源({len(names)}个)'],
                    })

        # 按问题类型分组
        by_type = {}
        for issue in issues:
            for p in issue['problems']:
                by_type.setdefault(p, []).append(issue)

        print(f'⚠  发现 {len(issues)} 个问题项:\n')

        for ptype, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
            print(f'【{ptype}】{len(items)} 项')
            for item in items[:8]:
                tag = '📁' if item['is_dir'] else '  '
                print(f'  {tag} {item["path"]}')
            if len(items) > 8:
                print(f'  ... 还有 {len(items) - 8} 项')
            print()


if __name__ == '__main__':
    main()
