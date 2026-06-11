"""Beautiful CLI for ZSpace NAS — powered by Typer + Rich."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich import box

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
        if not c.is_connected():
            console.print("[red]✗ 极空间客户端代理不可达[/red]")
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
            console.print(f"[green]✓[/green] 已删除")
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


if __name__ == "__main__":
    app()
