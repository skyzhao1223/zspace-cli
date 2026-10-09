"""Beautiful CLI for ZSpace NAS — powered by Typer + Rich."""

# ruff: noqa: UP007, UP045 - typer resolves annotations at runtime; py3.9 needs Optional[]

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.tree import Tree

from zspace_cli.auth import CONFIG_DIR_ENV
from zspace_cli.client import ZSpaceClient, ZSpaceError

app = typer.Typer(
    name="zs",
    help="ZSpace NAS CLI — 在终端管理你的极空间 NAS 文件",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

DEFAULT_PATH = "/sata11/my/data"

_config_dir: str | None = None


@app.callback()
def _main(
    config_dir: Optional[str] = typer.Option(
        None,
        "--config-dir",
        envvar=CONFIG_DIR_ENV,
        help="极空间客户端配置目录（默认自动探测）",
    ),
) -> None:
    """Override the ZSpace desktop client config directory."""
    global _config_dir
    _config_dir = config_dir


def _client() -> ZSpaceClient:
    try:
        if _config_dir:
            return ZSpaceClient(config_dir=_config_dir)
        return ZSpaceClient()
    except FileNotFoundError as e:
        _print_error(e)
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]! 连接失败:[/red] {e}")
        raise typer.Exit(1)


def _size_str(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.1f} KB"
    return f"{size} B"


def _expand_glob(c: ZSpaceClient, arg: str) -> list[str]:
    """Expand a glob pattern into concrete NAS paths; literal args pass through."""
    if any(ch in arg for ch in "*?["):
        return [e.path for e in c.glob(arg)]
    return [arg]


def _emit_json(data: Any) -> None:
    import json

    console.print(json.dumps(data, ensure_ascii=False, indent=2))


def _print_error(e: Exception) -> None:
    """Print an error, appending an actionable hint when the code is known."""
    console.print(f"[red]![/red] {e}")
    if isinstance(e, ZSpaceError):
        hint = ZSpaceError.diagnose(e.code, e.msg)
        if hint:
            console.print(f"  [dim]提示: {hint}[/dim]")


@app.command()
def check(json_output: bool = typer.Option(False, "--json", help="JSON 输出")):
    """检查极空间客户端连接状态"""
    with _client() as c:
        status = c.client_status()
        if not status.ok:
            if json_output:
                _emit_json({"ok": False, "reason": status.reason})
            console.print(f"[red]![/red] {status.reason}")
            raise typer.Exit(1)

        pools: list[dict] = []
        if not json_output:
            console.print("[green]OK 极空间客户端已连接[/green]")
        try:
            pool = c.pool_info()
            for p in pool["data"]["pool_list"]:
                total = p["total_size"] / (1024**4)
                free = p["free_size"] / (1024**4)
                used_pct = (1 - free / total) * 100 if total else 0
                pools.append({
                    "name": p["name"],
                    "total_tb": round(total, 1),
                    "free_tb": round(free, 1),
                    "used_pct": round(used_pct),
                })
                if not json_output:
                    console.print(
                        f"  [bold]{p['name']}[/bold]: "
                        f"{total:.1f} TB 总容量, {free:.1f} TB 可用 "
                        f"([{'red' if used_pct > 80 else 'yellow' if used_pct > 60 else 'green'}]"
                        f"{used_pct:.0f}% 已用[/])"
                    )
        except ZSpaceError:
            pass
        except (KeyError, TypeError, ValueError):
            # pool API responded but in an unexpected shape — degrade gracefully
            if not json_output:
                console.print("  [dim](存储池信息解析失败)[/dim]")
        if json_output:
            _emit_json({"ok": True, "pools": pools})


@app.command()
def ls(
    path: str = typer.Argument(DEFAULT_PATH, help="目录路径"),
    hidden: bool = typer.Option(False, "--hidden", "-a", help="显示隐藏文件"),
    long: bool = typer.Option(False, "--long", "-l", help="详细信息"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出目录内容"""
    with _client() as c:
        try:
            entries = c.ls(path, show_hidden=hidden)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json([{
                "name": e.name, "path": e.path, "is_dir": e.is_dir, "size": e.size,
            } for e in entries])
            return

        if long:
            table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
            table.add_column("类型", width=5)
            table.add_column("大小", justify="right", width=10)
            table.add_column("名称")
            table.add_column("路径", style="dim")
            for e in entries:
                icon = "DIR" if e.is_dir else "FILE"
                size = "" if e.is_dir else _size_str(e.size)
                table.add_row(icon, size, e.name, e.path)
            console.print(table)
        else:
            for e in entries:
                if e.is_dir:
                    console.print(f"  [bold blue]{e.name}/[/bold blue]")
                else:
                    console.print(f"  {e.name}  [dim]{_size_str(e.size)}[/dim]")

        console.print(f"\n[dim]共 {len(entries)} 项[/dim]")


@app.command()
def info(
    path: str = typer.Argument(..., help="文件或目录路径"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看文件/目录详细信息"""
    with _client() as c:
        try:
            data = c.info(path)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json(data)
            return

        table = Table(box=box.ROUNDED, show_header=False, title=data.get("name", path))
        table.add_column("属性", style="bold")
        table.add_column("值")
        table.add_row("路径", data.get("path", ""))
        table.add_row("类型", "目录" if data.get("is_dir") == "1" else "文件")
        if data.get("size"):
            table.add_row("大小", _size_str(int(data["size"])))
        if data.get("modify_time"):
            table.add_row("修改时间", data["modify_time"])
        if data.get("create_time"):
            table.add_row("创建时间", data["create_time"])
        console.print(table)


@app.command()
def rename(
    path: str = typer.Argument(..., help="文件或目录路径"),
    new_name: str = typer.Argument(..., help="新名称"),
):
    """重命名文件或目录"""
    with _client() as c:
        try:
            result = c.rename(path, new_name)
            console.print(f"[green]OK[/green] 已重命名为 [bold]{result.name}[/bold]")
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)


@app.command()
def mv(
    src: str = typer.Argument(..., help="源路径（支持 * ? glob）"),
    dest: str = typer.Argument(..., help="目标目录"),
):
    """移动文件或目录"""
    with _client() as c:
        try:
            paths = _expand_glob(c, src)
            if not paths:
                console.print("[yellow]没有匹配的文件[/yellow]")
                return
            c.move(paths, dest)
            console.print(f"[green]OK[/green] 已移动 {len(paths)} 项到 [bold]{dest}[/bold]")
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)


@app.command()
def cp(
    src: str = typer.Argument(..., help="源路径（支持 * ? glob）"),
    dest: str = typer.Argument(..., help="目标目录"),
):
    """复制文件或目录"""
    with _client() as c:
        try:
            paths = _expand_glob(c, src)
            if not paths:
                console.print("[yellow]没有匹配的文件[/yellow]")
                return
            c.copy(paths, dest)
            console.print(f"[green]OK[/green] 已复制 {len(paths)} 项到 [bold]{dest}[/bold]")
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)


@app.command()
def mkdir(
    parent: str = typer.Argument(..., help="父目录路径"),
    name: str = typer.Argument(..., help="新目录名"),
):
    """创建新目录"""
    with _client() as c:
        try:
            result = c.mkdir(parent, name)
            console.print(f"[green]OK[/green] 已创建 [bold]{result.path}[/bold]")
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)


@app.command()
def rm(
    path: str = typer.Argument(..., help="要删除的路径（支持 * ? glob）"),
    force: bool = typer.Option(False, "--force", "-f", help="跳过确认"),
):
    """删除文件或目录（移入个人回收站，可用 zs recycle restore 恢复）"""
    with _client() as c:
        try:
            paths = _expand_glob(c, path)
            if not paths:
                console.print("[yellow]没有匹配的文件[/yellow]")
                return
            if not force:
                for p in paths:
                    if not typer.confirm(f"确定要删除 {p}？"):
                        raise typer.Abort()
            c.remove(paths)
            console.print(f"[green]OK[/green] 已将 {len(paths)} 项移入回收站")
            console.print(
                "  [dim]空间尚未释放 — 用 [bold]zs recycle empty[/bold]"
                " 清空后才真正腾出[/dim]"
            )
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)


@app.command()
def find(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    path: str = typer.Argument(DEFAULT_PATH, help="搜索目录"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """搜索文件名"""
    with _client() as c:
        try:
            results = c.search(keyword, path)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json([{
                "name": e.name, "path": e.path, "is_dir": e.is_dir, "size": e.size,
            } for e in results])
            return

        if not results:
            console.print(f"[yellow]未找到匹配 '{keyword}' 的文件[/yellow]")
            return

        for e in results:
            icon = "DIR" if e.is_dir else "FILE"
            console.print(f"  {icon} {e.path}")
        console.print(f"\n[dim]找到 {len(results)} 项[/dim]")


@app.command()
def tree(
    path: str = typer.Argument(DEFAULT_PATH, help="根目录"),
    depth: int = typer.Option(2, "--depth", "-d", help="递归深度"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """树形显示目录结构"""
    with _client() as c:
        try:
            nodes = c.tree(path, max_depth=depth)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json(nodes)
            return

        root_name = path.rsplit("/", 1)[-1] or path
        rich_tree = Tree(f"[bold]{root_name}/[/bold]")
        _build_rich_tree(rich_tree, nodes, 0)
        console.print(rich_tree)


def _build_rich_tree(parent: Tree, nodes: list[dict], depth: int) -> None:
    """Convert flat node list (with depth) into a Rich Tree."""
    stack: list[tuple[Tree, int]] = [(parent, -1)]
    for node in nodes:
        d = node["depth"]
        while stack and stack[-1][1] >= d:
            stack.pop()
        current_parent = stack[-1][0] if stack else parent
        if node["is_dir"]:
            label = f"[bold blue]{node['name']}/[/bold blue]"
        else:
            label = f"{node['name']}  [dim]{_size_str(node.get('size', 0))}[/dim]"
        branch = current_parent.add(label)
        stack.append((branch, d))


def _transfer_columns() -> list:
    """Progress columns for file transfer (hidden when not a real terminal)."""
    return [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ]


@app.command()
def up(
    local: Path = typer.Argument(..., help="本地文件路径"),
    remote_dir: str = typer.Argument(..., help="NAS 目标目录"),
    name: str = typer.Option(None, "--name", "-n", help="上传后的文件名（默认用本地文件名）"),
    verify: bool = typer.Option(False, "--verify", help="上传后下载并比较 MD5 完整性"),
):
    """上传本地文件到 NAS"""
    with _client() as c:
        with Progress(
            *_transfer_columns(), console=console, disable=not console.is_terminal
        ) as prog:
            try:
                size = local.stat().st_size
            except OSError:
                size = 0
            task = prog.add_task(f"[cyan]上传 {local.name}[/cyan]", total=size or None)
            try:
                result = c.upload(
                    local,
                    remote_dir,
                    new_name=name,
                    progress=lambda done, total: prog.update(
                        task, completed=done, total=total or None
                    ),
                    verify=verify,
                )
                target = result.get("path", f"{remote_dir.rstrip('/')}/{local.name}")
                console.print(f"[green]OK[/green] 已上传到 [bold]{target}[/bold]")
                if verify:
                    console.print("[green]OK[/green] MD5 完整性校验通过")
            except ZSpaceError as e:
                _print_error(e)
                raise typer.Exit(1)
            except FileNotFoundError as e:
                _print_error(e)
                raise typer.Exit(1)


@app.command()
def down(
    remote_path: str = typer.Argument(..., help="NAS 文件路径"),
    local_dir: Path = typer.Argument(".", help="本地保存目录"),
):
    """从 NAS 下载文件到本地"""
    with _client() as c:
        with Progress(
            *_transfer_columns(), console=console, disable=not console.is_terminal
        ) as prog:
            task = prog.add_task(f"[cyan]下载 {Path(remote_path).name}[/cyan]", total=None)
            try:
                paths = _expand_glob(c, remote_path)
                if not paths:
                    console.print("[yellow]没有匹配的文件[/yellow]")
                    return
                for p in paths:
                    out = c.download(
                        p,
                        local_dir,
                        progress=lambda done, total: prog.update(
                            task, completed=done, total=total or None
                        ),
                    )
                    console.print(f"[green]OK[/green] 已下载到 [bold]{out}[/bold]")
            except ZSpaceError as e:
                _print_error(e)
                raise typer.Exit(1)


@app.command()
def skill(
    target_dir: Optional[Path] = typer.Argument(
        None,
        help="目标目录，如 ~/your-project/.cursor/skills 或 ~/your-project/skills",
    ),
    list_: bool = typer.Option(
        False, "--list", help="只列出可用 skills，不复制"
    ),
    only: Optional[str] = typer.Option(
        None,
        "--only",
        help="只复制指定 skills（逗号分隔），如 photo-organizer,dedup-finder",
    ),
):
    """复制 Agent skills 到你的项目目录"""
    import shutil

    # 定位打包进 wheel 的 skills 数据目录
    data_root = Path(__file__).resolve().parent / "skills"
    if not data_root.is_dir():
        console.print(
            "[red]![/red] 未找到 skills 数据目录（请确认已通过 pip 安装 zspace-cli）"
        )
        raise typer.Exit(1)

    # 可用 skills = data_root 下的子目录
    available = sorted(
        d.name for d in data_root.iterdir()
        if d.is_dir() and d.name != "__pycache__"
    )

    if list_:
        console.print("可用 skills：")
        for name in available:
            console.print(f"  - {name}")
        raise typer.Exit(0)

    if target_dir is None:
        console.print(
            "[red]![/red] 请提供目标目录（或用 --list 查看可用 skills）"
        )
        raise typer.Exit(1)

    # 解析 --only（缺省=全部）
    if only:
        wanted = [s.strip() for s in only.split(",") if s.strip()]
        unknown = [s for s in wanted if s not in available]
        if unknown:
            console.print(f"[red]![/red] 未知 skill：{', '.join(unknown)}")
            console.print(f"  可用：{', '.join(available)}")
            raise typer.Exit(1)
    else:
        wanted = available

    target = target_dir.expanduser()
    target.mkdir(parents=True, exist_ok=True)

    # 一并带上 skills/README.md 总览(不计入 skill 数)
    readme = data_root / "README.md"
    if readme.is_file():
        shutil.copy2(readme, target / "README.md")

    copied = 0
    for name in wanted:
        src = data_root / name
        if not src.is_dir():
            continue
        shutil.copytree(
            src,
            target / name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        copied += 1
    console.print(f"[green]OK[/green] 已复制 {copied} 个 skill 到 [bold]{target}[/bold]")
    console.print("  对 Agent 说「列出 NAS 文件」即可使用。")


# ── 存储画像与健康 ──


def _tib_str(size: int) -> str:
    """Byte count in TiB when large enough to warrant it, else _size_str."""
    tib = 1 << 40
    if size >= tib:
        return f"{size / tib:.3f} TiB"
    return _size_str(size)


@app.command()
def usage(
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
    refresh: bool = typer.Option(False, "--refresh", help="先让 NAS 重新统计"),
    wait: int = typer.Option(120, "--wait", help="配合 --refresh 的最长等待秒数"),
):
    """存储画像 — 按池/用户/类别看物理占用

    这是唯一能看到 Time Machine 备份、其他用户空间、Docker/RAID 系统数据的
    途径：普通 token 对 /<pool>/my 之外的路径一律 N001411，任何文件级遍历
    都会系统性少算池占用。
    """
    with _client() as c:
        try:
            if refresh:
                c.disk_usage_refresh()
                deadline = time.time() + wait
                while time.time() < deadline:
                    st = c.disk_usage_status()
                    if not st.get("is_running"):
                        break
                    time.sleep(3)
            status = c.disk_usage_status()
            pools = c.usage_summary()
            info = {p["name"]: p for p in (c.pool_info().get("data") or {}).get("pool_list") or []}
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json({
                "snapshot_updated_at": status.get("updated_at"),
                "is_running": status.get("is_running"),
                "pools": [
                    {
                        "pool": p.pool,
                        "total_bytes": p.total,
                        "user_bytes": p.user_total,
                        "system_bytes": p.system_total,
                        "pool_usage_size": (info.get(p.pool) or {}).get("usage_size"),
                        "pool_total_size": (info.get(p.pool) or {}).get("total_size"),
                        "pool_free_size": (info.get(p.pool) or {}).get("free_size"),
                        "entries": [
                            {"kind": e.kind, "owner": e.owner, "label": e.label,
                             "label_cn": e.label_cn, "bytes": e.size}
                            for e in sorted(p.entries, key=lambda x: -x.size)
                        ],
                    }
                    for p in pools
                ],
            })
            return

        if status.get("is_running"):
            console.print("[yellow]![/yellow] NAS 正在重新统计，以下为上次快照")
        for p in pools:
            meta = info.get(p.pool) or {}
            total = _as_int_cli(meta.get("total_size"))
            used = _as_int_cli(meta.get("usage_size"))
            free = _as_int_cli(meta.get("free_size"))
            pct = (used / total * 100) if total else 0
            color = "red" if pct > 80 else "yellow" if pct > 60 else "green"
            console.print(
                f"\n[bold]{p.pool}[/bold]  [{color}]{pct:.1f}% 已用[/{color}]  "
                f"{_tib_str(used)} / {_tib_str(total)}  可用 {_tib_str(free)}"
            )
            console.print(
                f"  [dim]diskusage3 合计 {_tib_str(p.total)}"
                f"（用户 {_tib_str(p.user_total)}"
                f" + 系统 {_tib_str(p.system_total)}）[/dim]"
            )
            table = Table(box=box.SIMPLE, show_header=True, pad_edge=False)
            table.add_column("体积", justify="right", no_wrap=True)
            table.add_column("归属")
            table.add_column("类别")
            for e in sorted(p.entries, key=lambda x: -x.size):
                table.add_row(_tib_str(e.size), e.owner or "[dim]系统[/dim]",
                              f"{e.label_cn} [dim]({e.label})[/dim]")
            console.print(table)


def _as_int_cli(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@app.command()
def du(
    paths: list[str] = typer.Argument(..., help="一个或多个目录"),
    walk: bool = typer.Option(False, "--walk", help="强制客户端递归遍历（大目录唯一可靠方式，慢）"),
    workers: int = typer.Option(8, "--workers", help="--walk 的并发数"),
    max_requests: int = typer.Option(0, "--max-requests", help="--walk 请求上限，0=不限"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """目录体积统计

    默认走服务端 /v2/file/statistic：小中型目录一次请求即精确。
    但大目录（数十万文件）服务端永不收敛，只返回抖动的部分值 ——
    此时本命令会明确标注不完整，不会把部分值当结果报给你。
    """
    results = []
    with _client() as c:
        for path in paths:
            try:
                if walk:
                    console.print(f"[dim]遍历 {path} …（每目录约 1 请求，可能很久）[/dim]")
                    st = c.walk_stat(path, workers=workers, max_requests=max_requests)
                else:
                    st = c.statistic(path)
                    if not st.complete and not walk:
                        console.print(
                            f"[yellow]![/yellow] {path} 服务端统计未收敛"
                            f"（state={st.state}），下面是[bold]部分值[/bold]，不可当真"
                        )
                        console.print("  [dim]加 --walk 可得到准确结果（较慢）[/dim]")
            except ZSpaceError as e:
                _print_error(e)
                raise typer.Exit(1)
            results.append(st)

        if json_output:
            _emit_json([{
                "path": s.path, "bytes": s.size, "files": s.files, "dirs": s.dirs,
                "state": s.state, "complete": s.complete, "source": s.source,
                "requests": s.requests, "hidden_bytes": s.hidden_size,
                "categories": s.categories,
            } for s in results])
            return

        for s in results:
            if s.complete:
                mark = "[green]OK[/green]"
            else:
                mark = f"[red]不完整 state={s.state}[/red]"
            console.print(f"\n{mark} [bold]{s.path}[/bold]")
            console.print(f"  体积 {_tib_str(s.size)}   文件 {s.files:,}   目录 {s.dirs:,}")
            if s.hidden_size:
                console.print(
                    f"  [dim]其中隐藏文件 {_tib_str(s.hidden_size)}"
                    f" / {s.hidden_files:,} 个[/dim]"
                )
            if s.categories:
                cats = "  ".join(f"{k} {_tib_str(v)}" for k, v in
                                sorted(s.categories.items(), key=lambda x: -x[1]) if v)
                if cats:
                    console.print(f"  [dim]{cats}[/dim]")
            if s.source == "walk":
                console.print(f"  [dim]客户端遍历，用了 {s.requests:,} 个请求[/dim]")


@app.command()
def disks(
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """每块盘的余量/温度/健康/碎片率/通电小时，以及空闲盘位"""
    with _client() as c:
        try:
            ds = c.disks()
            bays = c.free_bays()
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json({
                "free_bays": bays,
                "disks": [{
                    "pool": d.pool, "position": d.position, "model": d.model,
                    "sn": d.sn, "dev_type": d.dev_type, "mount": d.mount,
                    "total_bytes": d.total_size, "free_bytes": d.free_size,
                    "used_bytes": d.usage_size, "used_pct": round(d.used_pct, 1),
                    "health": d.health, "status": d.status, "temp_c": d.temp,
                    "power_on_hours": d.power_on_hours,
                    "reallocated_sectors": d.reallocated_sectors,
                    "fragment_pct": d.fragment_pct, "suspected_smr": d.suspected_smr,
                } for d in ds],
            })
            return

        table = Table(box=box.SIMPLE_HEAD, show_header=True, title="磁盘")
        table.add_column("池/位", no_wrap=True)
        table.add_column("型号", no_wrap=True)
        table.add_column("余量", justify="right")
        table.add_column("已用", justify="right")
        table.add_column("温度", justify="right")
        table.add_column("通电h", justify="right")
        table.add_column("碎片", justify="right")
        table.add_column("健康")
        for d in ds:
            pct = d.used_pct
            color = "red" if pct > 90 else "yellow" if pct > 75 else "green"
            table.add_row(
                f"{d.pool} #{d.position}", d.model[:22],
                _size_str(d.free_size), f"[{color}]{pct:.1f}%[/{color}]",
                f"{d.temp}°C", f"{d.power_on_hours:,}", f"{d.fragment_pct*100:.1f}%",
                "[green]ok[/green]" if d.health == "ok" else f"[red]{d.health}[/red]",
            )
        console.print(table)
        for d in ds:
            if d.reallocated_sectors or d.suspected_smr:
                console.print(
                    f"[yellow]![/yellow] {d.model} 重映射扇区={d.reallocated_sectors} "
                    f"疑似SMR={d.suspected_smr}"
                )
        free_txt = "  ".join(f"{k} 空 {v} 位" for k, v in bays.items() if v)
        console.print(f"\n[dim]空闲盘位: {free_txt or '无'}[/dim]")


@app.command()
def smart(
    sn: str = typer.Argument(None, help="磁盘序列号（zs disks 可查）；不填则查所有盘"),
    all_disks: bool = typer.Option(False, "--all", help="所有盘（与不填 sn 等效）"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """SMART 报告（/zspool/smart/report2 按 sn 查询，不是 pool_id）"""
    with _client() as c:
        try:
            targets = [d.sn for d in c.disks()] if all_disks or not sn else [sn]
            if not targets:
                console.print("[yellow]请给出序列号或 --all[/yellow]")
                raise typer.Exit(1)
            reports = {s: c.smart(s) for s in targets}
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json(reports)
            return

        for s, rep in reports.items():
            attrs = rep.get("attributes") or []
            console.print(f"\n[bold]{s}[/bold]  health={rep.get('health')}  ({len(attrs)} 项属性)")
            if not attrs:
                console.print("  [dim]无经典 SMART 属性（NVMe 走另一套字段）[/dim]")
                continue
            bad = [a for a in attrs if a.get("health") not in ("ok", None, "")]
            watch = (5, 187, 188, 196, 197, 198, 199)
            table = Table(box=box.SIMPLE, show_header=True)
            table.add_column("ID")
            table.add_column("属性")
            table.add_column("当前", justify="right")
            table.add_column("最差", justify="right")
            table.add_column("阈值", justify="right")
            table.add_column("原始值", justify="right")
            for a in attrs:
                if a.get("id") not in watch and a.get("health") in ("ok", None, ""):
                    continue
                table.add_row(str(a.get("id")), str(a.get("attr_name"))[:22],
                              str(a.get("now_value")), str(a.get("worst")),
                              str(a.get("thresh")), str(a.get("str_value"))[:22])
            console.print(table)
            if bad:
                for a in bad:
                    console.print(f"[red]![/red] {a.get('attr_name')} 非 ok: "
                                  f"当前={a.get('now_value')} 原始={a.get('str_value')}")
            else:
                console.print("  [green]关键故障计数器均正常[/green]")


recycle_app = typer.Typer(help="回收站（个人 / 公共两套独立存储）", no_args_is_help=True)
app.add_typer(recycle_app, name="recycle")


@recycle_app.command("list")
def recycle_list_cmd(
    public: bool = typer.Option(False, "--public", help="公共/家庭回收站而非个人"),
    limit: int = typer.Option(200, "--limit", help="取回条数"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出回收站内容（含原位置）"""
    with _client() as c:
        try:
            target = c.RECYCLE_PUBLIC if public else c.RECYCLE_MY
            entries, total = c.recycle_list(target, num=limit)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json({"bin": target, "total": total, "items": [{
                "name": e.name, "path": e.path, "original_path": e.original_path,
                "is_dir": e.is_dir, "bytes": e.size, "modify_time": e.modify_time,
            } for e in entries]})
            return

        which = "公共回收站" if public else "个人回收站"
        if not entries:
            console.print(f"[green]OK[/green] {which}是空的")
            return
        size = sum(e.size for e in entries if not e.is_dir)
        console.print(f"[bold]{which}[/bold]  {total} 项，文件合计 {_tib_str(size)}")
        table = Table(box=box.SIMPLE, show_header=True)
        table.add_column("体积", justify="right", no_wrap=True)
        table.add_column("名称")
        table.add_column("原位置")
        for e in sorted(entries, key=lambda x: -x.size)[:limit]:
            table.add_row(
                "DIR" if e.is_dir else _size_str(e.size),
                e.name[:44], f"[dim]{e.original_path[:52]}[/dim]",
            )
        console.print(table)


@recycle_app.command("empty")
def recycle_empty_cmd(
    public: bool = typer.Option(False, "--public", help="清公共回收站而非个人"),
    force: bool = typer.Option(False, "--force", "-f", help="跳过确认"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """清空回收站 — 不可逆，且此后空间才真正释放"""
    with _client() as c:
        try:
            target = c.RECYCLE_PUBLIC if public else c.RECYCLE_MY
            entries, total = c.recycle_list(target, num=500)
            size = sum(e.size for e in entries if not e.is_dir)
            which = "公共回收站" if public else "个人回收站"
            if not force:
                console.print(f"将永久清除[bold]{which}[/bold]的 {total} 项"
                              f"（文件合计 {_tib_str(size)}）")
                for e in sorted(entries, key=lambda x: -x.size)[:10]:
                    console.print(f"  [dim]{'DIR ' if e.is_dir else _size_str(e.size):>10}"
                                  f"  {e.name[:50]}[/dim]")
                if total > 10:
                    console.print(f"  [dim]…另有 {total - 10} 项[/dim]")
                if not typer.confirm("确认永久删除？无法恢复"):
                    raise typer.Abort()
            task = c.recycle_empty(public=public)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json(task)
            return
        num = task.get("total_num") or "0"
        fail = task.get("fail_num") or "0"
        if str(num) == "0":
            console.print(f"[yellow]![/yellow] {which}本来就是空的（清除 0 项）。"
                          f"若你刚删的是个人空间文件，注意个人与公共是两个独立回收站。")
        else:
            console.print(f"[green]OK[/green] 已清除 {num} 项，失败 {fail} 项")


@recycle_app.command("purge")
def recycle_purge_cmd(
    names: list[str] = typer.Argument(..., help="回收站内路径或文件名（先 zs recycle list 查）"),
    force: bool = typer.Option(False, "--force", "-f", help="跳过确认"),
):
    """只永久删除指定项 — 比 empty 安全，其余内容原样保留"""
    with _client() as c:
        try:
            entries, _ = c.recycle_list(c.RECYCLE_MY, num=500)
            wanted = []
            for n in names:
                hit = [e for e in entries if e.path == n or e.name == n]
                if not hit:
                    console.print(f"[yellow]![/yellow] 回收站里没有 {n}")
                    continue
                wanted.extend(e.path for e in hit)
            if not wanted:
                raise typer.Exit(1)
            if not force:
                for p in wanted:
                    console.print(f"  [dim]{p}[/dim]")
                if not typer.confirm(f"确认永久删除这 {len(wanted)} 项？无法恢复"):
                    raise typer.Abort()
            ok, failed = c.recycle_purge(wanted)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)
        console.print(f"[green]OK[/green] 已永久删除 {ok} 项，其余内容未动")
        for f in failed:
            console.print(f"[red]![/red] 删除失败: {f}")


@recycle_app.command("restore")
def recycle_restore_cmd(
    names: list[str] = typer.Argument(
        ..., help="回收站内路径或文件名（先 zs recycle list 查）"
    ),
):
    """从个人回收站恢复文件到原位置"""
    with _client() as c:
        try:
            entries, _ = c.recycle_list(c.RECYCLE_MY, num=500)
            wanted = []
            for n in names:
                hit = [e for e in entries if e.path == n or e.name == n]
                if not hit:
                    console.print(f"[yellow]![/yellow] 回收站里没有 {n}")
                    continue
                wanted.extend(e.path for e in hit)
            if not wanted:
                raise typer.Exit(1)
            ok, failed = c.recycle_restore(wanted)
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)
        console.print(f"[green]OK[/green] 已恢复 {ok} 项")
        for f in failed:
            console.print(f"[red]![/red] 恢复失败: {f}")


@recycle_app.command("config")
def recycle_config_cmd(
    my_cycle: Optional[int] = typer.Option(None, "--my-cycle", help="个人回收站保留天数，-1=永不"),
    public_cycle: Optional[int] = typer.Option(
        None, "--public-cycle", help="公共回收站保留天数，-1=永不"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看/设置回收站保留策略（默认 -1 = 永不自动清理）"""
    with _client() as c:
        try:
            if my_cycle is None and public_cycle is None:
                cfg = c.recycle_config()
            else:
                cfg = c.recycle_set_config(my_cycle=my_cycle, public_cycle=public_cycle)
                console.print("[green]OK[/green] 已更新保留策略")
        except ZSpaceError as e:
            _print_error(e)
            raise typer.Exit(1)

        if json_output:
            _emit_json(cfg)
            return
        for key, label in (("my_cycle", "个人回收站"), ("public_cycle", "公共回收站")):
            v = cfg.get(key)
            txt = "永不自动清理" if _as_int_cli(v) == -1 else f"{v} 天"
            warn = " [yellow]← 删掉的空间会一直占着[/yellow]" if _as_int_cli(v) == -1 else ""
            console.print(f"  {label}: {txt}{warn}")


if __name__ == "__main__":
    app()
