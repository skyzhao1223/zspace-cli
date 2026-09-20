#!/usr/bin/env python3
"""music-organizer skill 命令行入口

设计原则(沿袭 media-naming / photo-organizer 模式):
- 只读扫描走脚本(正向合规验证:歌手/专辑/曲目 三层结构)
- 写操作(mkdir / mv / rename)不在脚本里 — LLM 生成 old→new 计划,
  用户确认后由 Agent 执行(挂载盘 shell,或极空间 zs CLI / MCP tool)
- 纯 stdlib 零依赖,跑在本地挂载路径上(SMB/NFS),各品牌 NAS 通用
- 内置最小 ID3v2 解析器(--read-tags 可选),对照路径与标签是否一致

用法:
  python music_organizer.py scan --root /Volumes/nas/音乐
  python music_organizer.py scan --root ... --read-tags --json --output /tmp/issues.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 6

AUDIO_EXTS = {
    "mp3", "flac", "m4a", "m4b", "aac", "ogg", "opus", "wav", "aif", "aiff",
    "wma", "ape", "wv", "dsf", "dff", "tta", "mpc", "alac", "mka",
}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
# 专辑目录内允许的非音频附属文件
ALLOWED_EXTS = {
    "lrc", "cue", "log", "txt", "pdf", "m3u", "m3u8", "pls", "nfo",
    "md5", "sfv", "json",
}
CUE_IMAGE_EXTS = {"ape", "flac", "wav", "wv", "tta"}  # 整轨镜像常见容器
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
JUNK_EXTS = {"tmp", "temp", "bak", "part", "crdownload", "td"}
SKIP_DIRS = {
    "@eaDir", "#recycle", "#@__recycle_bin", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", "System Volume Information", "$RECYCLE.BIN",
    ".snapshots", ".zspace_trash", ".trash", ".cache", "node_modules", ".git",
    "lost+found",
}
WHITELIST_DIRS = {
    "合辑", "合集", "群星", "原声", "原声带", "单曲", "精选", "歌单", "待整理",
    "有声书", "播客", "收藏", "最近添加", "音乐",
    "various artists", "va", "ost", "soundtrack", "soundtracks", "singles",
    "compilations", "playlists", "podcasts", "audiobooks", "favorites",
}
BAD_DIR = re.compile(
    r"^(新建文件夹.*|未命名.*|无标题.*|untitled.*|new folder.*|temp|tmp|test|测试|aaa+)$",
    re.I,
)
CD_DIR = re.compile(r"^(CD\s?\d+|Disc\s?\d+| disc\d+|CD\d+)$", re.I)
# 曲目号前缀:01 / 01. / 01 - / 1-01(碟-轨)
TRACK_NO_OK = re.compile(r"^(?:\d{1,2}[-._]\d{1,3}|\d{1,3})(?:[-._ ]|$)")
TRACK_NO_TAIL = re.compile(r"(?<!\d)\d{1,3}$")
WATERMARK_RE = re.compile(
    r"【|】|\[(?:FLAC|MP3|APE|WAV|320K?|192K?|128K?|HQ|SQ|VBR|无损|高品质|"
    r"Hi-?Res|抖音|快手|公众号|首发|独家|Promo|Web)\]|www\.|\.(?:com|net|org)\b|公众号",
    re.I,
)

# ── 最小 ID3v2 解析(纯 stdlib,只认文本帧 + APIC 存在性)──────────────

_V2_MAP = {"TT2": "TIT2", "TP1": "TPE1", "TAL": "TALB", "TRK": "TRCK",
           "TYE": "TYER", "PIC": "APIC"}


def _synchsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _decode_text(body: bytes) -> str:
    if not body:
        return ""
    enc, t = body[0], body[1:]
    try:
        if enc == 0:
            return t.split(b"\x00", 1)[0].decode("latin-1").strip()
        if enc == 1:
            return t.decode("utf-16").strip("\x00").strip()
        if enc == 2:
            return t.decode("utf-16-be").strip("\x00").strip()
        if enc == 3:
            return t.split(b"\x00", 1)[0].decode("utf-8").strip()
    except (UnicodeDecodeError, ValueError):
        return ""
    return ""


def parse_id3(data: bytes) -> dict:
    """解析 ID3v2.2/2.3/2.4 头部,返回 {TIT2,TPE1,TALB,TRCK,year,APIC}。"""
    out: dict = {}
    if len(data) < 10 or data[:3] != b"ID3":
        return out
    ver = data[3]
    if ver not in (2, 3, 4):
        return out
    tag_size = _synchsafe(data[6:10])
    end = min(len(data), 10 + tag_size)
    fid_len = 3 if ver == 2 else 4
    hdr_len = 6 if ver == 2 else 10
    pos = 10
    while pos + hdr_len <= end:
        fid_raw = data[pos:pos + fid_len]
        if not fid_raw or fid_raw[0:1] == b"\x00":
            break
        if ver == 2:
            fsz = int.from_bytes(data[pos + 3:pos + 6], "big")
        else:
            raw = data[pos + 4:pos + 8]
            fsz = _synchsafe(raw) if ver == 4 else int.from_bytes(raw, "big")
        if fsz <= 0 or pos + hdr_len + fsz > end:
            break
        fid = _V2_MAP.get(fid_raw.decode("latin-1"), fid_raw.decode("latin-1"))
        body = data[pos + hdr_len:pos + hdr_len + fsz]
        if fid == "APIC":
            out["APIC"] = "1"
        elif fid.startswith("T"):
            val = _decode_text(body)
            if val:
                if fid in ("TYER", "TDRC"):
                    m = re.search(r"(?:19|20)\d{2}", val)
                    out.setdefault("year", m.group(0) if m else val)
                else:
                    out.setdefault(fid, val)
        pos += hdr_len + fsz
    return out


def read_id3_file(path: str, max_bytes: int = 512 * 1024) -> dict:
    try:
        with open(path, "rb") as f:
            return parse_id3(f.read(max_bytes))
    except OSError:
        return {}


def norm_name(s: str) -> str:
    return re.sub(r"[\s_\-.·&'\"()\[\]【】!！?？,，、]+", "", s).lower()


def tag_matches(path_part: str, tag_val: str) -> bool:
    a, b = norm_name(path_part), norm_name(tag_val)
    return bool(a) and bool(b) and (a in b or b in a)


class Album:
    def __init__(self, name: str, rel: str) -> None:
        self.name = name
        self.rel = rel          # 相对 root 的目录路径
        self.audio: list[dict] = []
        self.images: list[str] = []
        self.lrcs: list[str] = []
        self.has_cue = False
        self.others: list[str] = []
        self.has_apic = False

    @property
    def is_cue_sheet(self) -> bool:
        return (self.has_cue and len(self.audio) <= 2
                and any(a["ext"] in CUE_IMAGE_EXTS for a in self.audio))


class Artist:
    def __init__(self, name: str) -> None:
        self.name = name
        self.albums: dict[str, Album] = {}
        self.loose_audio: list[dict] = []
        self.loose_images: list[str] = []
        self.loose_lrcs: list[str] = []
        self.has_loose_cue = False


class Scanner:
    def __init__(self, root: str, max_depth: int, sample: int,
                 read_tags: bool, tag_limit: int) -> None:
        self.root = Path(root).resolve()
        self.max_depth = max_depth
        self.sample = sample
        self.read_tags = read_tags
        self.tag_limit = tag_limit
        self.stats: dict = {
            "artists": 0, "albums": 0, "audio_files": 0, "audio_size_bytes": 0,
            "by_ext": {}, "junk": 0, "loose_root": 0, "loose_artist": 0,
            "misplaced_albums": 0, "albums_no_cover": 0, "tracks_no_number": 0,
            "tracks_watermark": 0, "multi_format": 0, "lrc_mismatch": 0,
            "non_audio_mixed": 0, "cue_sheets": 0,
            "tag_checked": 0, "tag_missing": 0, "tag_mismatch": 0,
            "sampled_out": False,
        }
        self.issues: list[dict] = []
        self.artists: dict[str, Artist] = {}
        self._n_files = 0
        self._tag_budget = tag_limit

    # -- 遍历 ----------------------------------------------------------

    def _walk(self, dir_path: Path, rel_parts: list[str],
              artist: Artist | None, album: Album | None) -> None:
        if len(rel_parts) > self.max_depth:
            return
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as e:
            print(f"⚠️ 无法读取 {dir_path}: {e}", file=sys.stderr)
            return
        for entry in entries:
            if self.sample and self._n_files >= self.sample:
                self.stats["sampled_out"] = True
                return
            name = entry.name
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                depth = len(rel_parts) + 1
                child_rel = rel_parts + [name]
                problems: list[str] = []
                if BAD_DIR.match(name):
                    problems = ["临时/未命名目录(建议重命名或清理)"]
                if depth == 1:
                    if name.lower() in WHITELIST_DIRS:
                        continue  # 合辑/歌单等功能区不校验
                    art = Artist(name)
                    self.artists[name] = art
                    if problems:
                        self._add("/".join(child_rel), name, True, problems)
                    self._walk(entry.path, child_rel, art, None)
                elif depth == 2 and artist is not None:
                    alb = Album(name, "/".join(child_rel))
                    artist.albums[name] = alb
                    if problems:
                        self._add("/".join(child_rel), name, True, problems)
                    self._walk(entry.path, child_rel, artist, alb)
                else:
                    # 专辑内子目录(CD1/Disc2/自由子目录)仍归属该专辑
                    if problems:
                        self._add("/".join(child_rel), name, True, problems)
                    self._walk(entry.path, child_rel, artist, album)
            elif entry.is_file():
                self._n_files += 1
                self._check_file(entry, rel_parts, name, artist, album)

    def _add(self, path: str, name: str, is_dir: bool,
             problems: list[str], **extra) -> None:
        issue = {"path": path, "name": name, "is_dir": is_dir, "problems": problems}
        issue.update({k: v for k, v in extra.items() if v is not None})
        self.issues.append(issue)

    def _check_file(self, entry: os.DirEntry, rel_parts: list[str], name: str,
                    artist: Artist | None, album: Album | None) -> None:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        stem = os.path.splitext(name)[0]
        full = "/".join(rel_parts + [name])

        try:
            size = entry.stat().st_size
        except OSError:
            size = 0

        if (name in JUNK_NAMES or ext in JUNK_EXTS or name.startswith("._")
                or name.startswith("~$")):
            self.stats["junk"] += 1
            self._add(full, name, False, ["垃圾/临时文件(可删)"], size=size)
            return
        if name.startswith("."):
            return

        is_audio = ext in AUDIO_EXTS
        if is_audio:
            self.stats["audio_files"] += 1
            self.stats["audio_size_bytes"] += size
            self.stats["by_ext"][ext] = self.stats["by_ext"].get(ext, 0) + 1
        rec = {
            "rel": ("/".join(rel_parts[2:] + [name])
                    if album is not None and len(rel_parts) >= 2 else name),
            "name": name, "stem": stem, "ext": ext, "size": size,
            "abs": entry.path, "full": full,
        }

        if is_audio:
            if album is not None:
                album.audio.append(rec)
            elif artist is not None:
                self.stats["loose_artist"] += 1
                artist.loose_audio.append(rec)
                self._add(full, name, False,
                          ["歌手层散曲(未归入专辑目录)"], size=size)
            else:
                self.stats["loose_root"] += 1
                self._add(full, name, False,
                          ["散曲(未归入 歌手/专辑 结构)"], size=size)
            return

        if album is not None:
            if ext in IMAGE_EXTS:
                album.images.append(name)
            elif ext == "lrc":
                album.lrcs.append(stem)
            elif ext == "cue":
                album.has_cue = True
            elif ext in ALLOWED_EXTS:
                pass
            else:
                self.stats["non_audio_mixed"] += 1
                self._add(full, name, False,
                          ["非音频文件混入专辑(建议移出)"], size=size)
        elif artist is not None:
            # 歌手层直下的图片/歌词/CUE — 该目录可能其实是张专辑(缺歌手层)
            if ext in IMAGE_EXTS:
                artist.loose_images.append(name)
            elif ext == "lrc":
                artist.loose_lrcs.append(stem)
            elif ext == "cue":
                artist.has_loose_cue = True

    # -- 项目级校验 ----------------------------------------------------

    def _eval_album(self, artist_name: str, album: Album) -> None:
        self.stats["albums"] += 1
        if album.is_cue_sheet:
            self.stats["cue_sheets"] += 1

        if not album.audio:
            self._add(album.rel, album.name, True, ["空专辑目录(无音频文件)"])
            return

        # 封面:目录内有图片,或(--read-tags 时)任一曲目内嵌 APIC
        has_cover = bool(album.images)
        if not has_cover and album.has_apic:
            has_cover = True
        if not has_cover:
            self.stats["albums_no_cover"] += 1
            self._add(album.rel, album.name, True,
                      ["缺专辑封面(建议 cover.jpg 或内嵌 APIC)"])

        # 整轨镜像+CUE 是合法结构,跳过逐轨编号/多格式检查
        if album.is_cue_sheet:
            self._add(album.rel, album.name, True,
                      ["整轨镜像+CUE 结构(合法;如需逐轨建议分轨)"])

        audio_stems: dict[str, set[str]] = {}
        for a in album.audio:
            audio_stems.setdefault(a["stem"], set()).add(a["ext"])
            if album.is_cue_sheet:
                continue
            if not TRACK_NO_OK.match(a["stem"]):
                if TRACK_NO_TAIL.search(a["stem"]):
                    self.stats["tracks_no_number"] += 1
                    self._add(f"{album.rel}/{a['rel']}", a["name"], False,
                              ["曲目号在结尾(建议 NN - 标题 前缀式)"])
                else:
                    self.stats["tracks_no_number"] += 1
                    self._add(f"{album.rel}/{a['rel']}", a["name"], False,
                              ["缺曲目号(建议 NN - 标题)"])
            if WATERMARK_RE.search(a["stem"]):
                self.stats["tracks_watermark"] += 1
                self._add(f"{album.rel}/{a['rel']}", a["name"], False,
                          ["水印/音质标签(建议清理文件名)"])

        # 同曲多格式
        if not album.is_cue_sheet:
            for stem, exts in audio_stems.items():
                if len(exts) >= 2:
                    self.stats["multi_format"] += 1
                    self._add(f"{album.rel}/{stem}", stem, False,
                              [f"同曲多格式共存({'/'.join(sorted(exts))},建议保留最高音质)"])

        # 歌词不配对
        for lrc_stem in album.lrcs:
            if lrc_stem not in audio_stems:
                self.stats["lrc_mismatch"] += 1
                self._add(f"{album.rel}/{lrc_stem}.lrc", f"{lrc_stem}.lrc", False,
                          ["歌词文件不配对(无同名音频)"])

        # 标签对照(可选)
        if self.read_tags:
            self._check_tags(artist_name, album)

    def _check_tags(self, artist_name: str, album: Album) -> None:
        is_va = artist_name.lower() in WHITELIST_DIRS or artist_name in ("合辑", "群星")
        for a in album.audio:
            if self._tag_budget <= 0:
                return
            if a["ext"] not in ("mp3",):
                continue
            self._tag_budget -= 1
            tags = read_id3_file(a["abs"])
            self.stats["tag_checked"] += 1
            if tags.get("APIC"):
                album.has_apic = True
            if not tags or not tags.get("TIT2"):
                self.stats["tag_missing"] += 1
                self._add(f"{album.rel}/{a['rel']}", a["name"], False,
                          ["缺 ID3 标签(建议补全 标题/歌手/专辑)"])
                continue
            problems = []
            if not is_va and tags.get("TPE1") \
                    and not tag_matches(artist_name, tags["TPE1"]):
                problems.append(f"路径与标签不符(TPE1={tags['TPE1']})")
            if tags.get("TALB") and not tag_matches(album.name, tags["TALB"]):
                problems.append(f"路径与标签不符(TALB={tags['TALB']})")
            if problems:
                self.stats["tag_mismatch"] += 1
                self._add(f"{album.rel}/{a['rel']}", a["name"], False, problems)

    def _eval(self) -> None:
        for name in sorted(self.artists):
            artist = self.artists[name]
            self.stats["artists"] += 1
            if artist.loose_audio and not artist.albums:
                # 根级目录直接装音频 → 其实是张专辑,缺歌手层
                self.stats["misplaced_albums"] += 1
                album = Album(name, name)
                album.audio = artist.loose_audio
                album.images = artist.loose_images
                album.lrcs = artist.loose_lrcs
                album.has_cue = artist.has_loose_cue
                self._add(name, name, True,
                          ["缺歌手层(建议 歌手/专辑/ 结构;或归入 合辑/)"])
                self._eval_album(name, album)
                continue
            for alb_name in sorted(artist.albums):
                self._eval_album(name, artist.albums[alb_name])

    # -- 入口 ----------------------------------------------------------

    def scan(self) -> dict:
        print(f"正在扫描 {self.root} ...\n", file=sys.stderr)
        if not self.root.is_dir():
            raise SystemExit(f"❌ --root 不是有效目录: {self.root}")
        self._walk(self.root, [], None, None)
        self._eval()
        print(
            f'扫描完成: {self.stats["artists"]} 歌手, {self.stats["albums"]} 专辑, '
            f'{self.stats["audio_files"]} 曲目\n', file=sys.stderr,
        )
        return {
            "skill": "music-organizer",
            "root": str(self.root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stats": self.stats,
            "count": len(self.issues),
            "issues": self.issues,
        }


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def _print_human(result: dict, top: int) -> None:
    s = result["stats"]
    print("=" * 70)
    print("music-organizer — 音乐库合规扫描报告")
    print("=" * 70)
    print(f"根目录: {result['root']}")
    print(f"歌手 {s['artists']} | 专辑 {s['albums']} | 曲目 {s['audio_files']} "
          f"| 音频总大小 {_human_size(s['audio_size_bytes'])}")
    if s["by_ext"]:
        exts = " / ".join(f"{k}:{v}" for k, v in
                          sorted(s["by_ext"].items(), key=lambda x: -x[1])[:6])
        print(f"格式分布: {exts}")
    print(f"缺封面专辑 {s['albums_no_cover']} | 缺曲目号 {s['tracks_no_number']} "
          f"| 水印名 {s['tracks_watermark']} | 同曲多格式 {s['multi_format']}")
    print(f"散曲(根 {s['loose_root']} / 歌手层 {s['loose_artist']}) "
          f"| 缺歌手层 {s['misplaced_albums']} | 整轨镜像 {s['cue_sheets']} "
          f"| 歌词不配对 {s['lrc_mismatch']} | 垃圾 {s['junk']}")
    if s["tag_checked"]:
        print(f"标签对照: 检查 {s['tag_checked']} | 缺标签 {s['tag_missing']} "
              f"| 路径不符 {s['tag_mismatch']}")
    if s.get("sampled_out"):
        print("(已达 --sample 上限,结果不完整)")

    issues = result["issues"]
    if not issues:
        print("\n✅ 全部合规,零问题!")
        return

    by_type: dict[str, list] = {}
    for issue in issues:
        for p in issue["problems"]:
            key = re.sub(r"\(.*", "", p)  # 归并同类(去掉括号内明细)
            by_type.setdefault(key, []).append(issue)

    print(f"\n⚠ 发现 {len(issues)} 个问题项:\n")
    for ptype, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"【{ptype}】{len(items)} 项")
        for item in items[:top]:
            tag = "📁" if item["is_dir"] else "  "
            print(f"  {tag} {item['path']}")
        if len(items) > top:
            print(f"  ... 还有 {len(items) - top} 项")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="music-organizer: 音乐库只读合规扫描(各品牌 NAS 通用)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="正向合规扫描(只读)")
    scan_p.add_argument("--root", required=True,
                        help="音乐库根目录(本地挂载路径),如 /Volumes/nas/音乐")
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")
    scan_p.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                        help=f"最大递归深度(默认 {MAX_DEPTH})")
    scan_p.add_argument("--sample", type=int, default=0,
                        help="最多扫描文件数(0=不限)")
    scan_p.add_argument("--top", type=int, default=8,
                        help="人类可读输出每类问题最多显示条数")
    scan_p.add_argument("--read-tags", action="store_true",
                        help="读取 mp3 ID3v2 标签,对照路径与标签一致性(较慢)")
    scan_p.add_argument("--tag-limit", type=int, default=100,
                        help="--read-tags 时最多读取的文件数(默认 100)")

    args = parser.parse_args()
    if args.cmd != "scan":
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        raise SystemExit(1)

    scanner = Scanner(args.root, args.max_depth, args.sample,
                      args.read_tags, args.tag_limit)
    result = scanner.scan()
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {args.output}", file=sys.stderr)
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        _print_human(result, args.top)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
