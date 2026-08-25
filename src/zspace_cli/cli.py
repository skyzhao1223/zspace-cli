"""Beautiful CLI for ZSpace NAS — powered by Typer + Rich."""

from __future__ import annotations

from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from zspace_cli.client import ZSpaceClient, ZSpaceError

app = typer.Typer(
    name="zs",
    help="ZSpace NAS CLI — 在终端管理你的极空间 NAS 文件",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

DEFAULT_PATH = "/sata11/my/data"


def _client() -> ZSpaceClient:
    try:
        return ZSpaceClient()
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ 连接失败:[/red] {e}")
        raise typer.Exit(1)


def _size_str(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.1f} KB"
    return f"{size} B"


@app.command()
def check():
    """检查极空间客户端连接状态"""
    with _client() as c:
        status = c.client_status()
        if not status.ok:
            console.print(f"[red]✗[/red] {status.reason}")
            raise typer.Exit(1)

        console.print("[green]✓ 极空间客户端已连接[/green]")
        try:
            pool = c.pool_info()
            for p in pool["data"]["pool_list"]:
                total = p["total_size"] / (1024**4)
                free = p["free_size"] / (1024**4)
                used_pct = (1 - free / total) * 100 if total else 0
                console.print(
                    f"  [bold]{p['name']}[/bold]: "
                    f"{total:.1f} TB 总容量, {free:.1f} TB 可用 "
                    f"([{'red' if used_pct > 80 else 'yellow' if used_pct > 60 else 'green'}]"
                    f"{used_pct:.0f}% 已用[/])"
                )
        except ZSpaceError:
            pass


@app.command()
def ls(
    path: str = typer.Argument(DEFAULT_PATH, help="目录路径"),
    hidden: bool = typer.Option(False, "--hidden", "-a", help="显示隐藏文件"),
    long: bool = typer.Option(False, "--long", "-l", help="详细信息"),
):
    """列出目录内容"""
    with _client() as c:
        try:
            entries = c.ls(path, show_hidden=hidden)
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)

        if long:
            table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
            table.add_column("类型", width=5)
            table.add_column("大小", justify="right", width=10)
            table.add_column("名称")
            table.add_column("路径", style="dim")
            for e in entries:
                icon = "📁" if e.is_dir else "📄"
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
def info(path: str = typer.Argument(..., help="文件或目录路径")):
    """查看文件/目录详细信息"""
    with _client() as c:
        try:
            data = c.info(path)
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)

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
            console.print(f"[green]✓[/green] 已重命名为 [bold]{result.name}[/bold]")
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)


@app.command()
def mv(
    src: str = typer.Argument(..., help="源路径"),
    dest: str = typer.Argument(..., help="目标目录"),
):
    """移动文件或目录"""
    with _client() as c:
        try:
            c.move(src, dest)
            console.print(f"[green]✓[/green] 已移动到 [bold]{dest}[/bold]")
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)


@app.command()
def cp(
    src: str = typer.Argument(..., help="源路径"),
    dest: str = typer.Argument(..., help="目标目录"),
):
    """复制文件或目录"""
    with _client() as c:
        try:
            c.copy(src, dest)
            console.print(f"[green]✓[/green] 复制到 [bold]{dest}[/bold]")
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
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
            console.print(f"[green]✓[/green] 已创建 [bold]{result.path}[/bold]")
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)


@app.command()
def rm(
    path: str = typer.Argument(..., help="要删除的路径"),
    force: bool = typer.Option(False, "--force", "-f", help="跳过确认"),
):
    """删除文件或目录"""
    if not force:
        confirm = typer.confirm(f"确定要删除 {path}？")
        if not confirm:
            raise typer.Abort()

    with _client() as c:
        try:
            c.remove(path)
            console.print("[green]✓[/green] 已删除")
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)


@app.command()
def find(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    path: str = typer.Argument(DEFAULT_PATH, help="搜索目录"),
):
    """搜索文件名"""
    with _client() as c:
        try:
            results = c.search(keyword, path)
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)

        if not results:
            console.print(f"[yellow]未找到匹配 '{keyword}' 的文件[/yellow]")
            return

        for e in results:
            icon = "📁" if e.is_dir else "📄"
            console.print(f"  {icon} {e.path}")
        console.print(f"\n[dim]找到 {len(results)} 项[/dim]")


@app.command()
def tree(
    path: str = typer.Argument(DEFAULT_PATH, help="根目录"),
    depth: int = typer.Option(2, "--depth", "-d", help="递归深度"),
):
    """树形显示目录结构"""
    with _client() as c:
        try:
            nodes = c.tree(path, max_depth=depth)
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)

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


@app.command()
def up(
    local: Path = typer.Argument(..., help="本地文件路径"),
    remote_dir: str = typer.Argument(..., help="NAS 目标目录"),
    name: str = typer.Option(None, "--name", "-n", help="上传后的文件名（默认用本地文件名）"),
):
    """上传本地文件到 NAS"""
    with _client() as c:
        try:
            result = c.upload(local, remote_dir, new_name=name)
            target = result.get("path", f"{remote_dir.rstrip('/')}/{local.name}")
            console.print(f"[green]✓[/green] 已上传到 [bold]{target}[/bold]")
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)
        except FileNotFoundError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)


@app.command()
def down(
    remote_path: str = typer.Argument(..., help="NAS 文件路径"),
    local_dir: Path = typer.Argument(".", help="本地保存目录"),
):
    """从 NAS 下载文件到本地"""
    with _client() as c:
        try:
            out = c.download(remote_path, local_dir)
            console.print(f"[green]✓[/green] 已下载到 [bold]{out}[/bold]")
        except ZSpaceError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1)


@app.command()
def skill(
    target_dir: Path = typer.Argument(
        ...,
        help="目标目录，如 ~/your-project/.cursor/skills 或 ~/your-project/skills",
    ),
):
    """复制 Agent skills 到你的项目目录"""
    import shutil

    # 定位打包进 wheel 的 skills 数据目录
    data_root = Path(__file__).resolve().parent / "skills"
    if not data_root.is_dir():
        console.print(
            "[red]✗[/red] 未找到 skills 数据目录（请确认已通过 pip 安装 zspace-cli）"
        )
        raise typer.Exit(1)

    target = target_dir.expanduser()
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in data_root.iterdir():
        if item.name in ("__init__.py", "__pycache__"):
            continue
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                dest,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            shutil.copy2(item, dest)
        copied += 1
    console.print(f"[green]✓[/green] 已复制 {copied} 个 skill 到 [bold]{target}[/bold]")
    console.print("  对 Agent 说「列出 NAS 文件」即可使用。")


if __name__ == "__main__":
    app()
