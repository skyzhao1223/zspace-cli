"""ZSpace CLI & SDK — manage your 极空间 NAS from the terminal or AI agents."""

import importlib.metadata as _md

from zspace_cli.client import ZSpaceClient, ZSpaceError

try:
    __version__ = _md.version("zspace-cli")
except _md.PackageNotFoundError:
    __version__ = "0.0.0"  # running from source without install

__all__ = ["ZSpaceClient", "ZSpaceError"]
