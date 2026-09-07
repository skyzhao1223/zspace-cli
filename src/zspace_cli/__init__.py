"""ZSpace CLI & SDK — manage your 极空间 NAS from the terminal or AI agents."""

import importlib.metadata as _md

from zspace_cli.client import ZSpaceClient, ZSpaceError

__all__ = ["ZSpaceClient", "ZSpaceError"]
__version__ = _md.version("zspace-cli")
