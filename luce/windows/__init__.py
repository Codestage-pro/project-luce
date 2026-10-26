"""Módulos específicos para Windows do Project LUCE."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from . import tray, winenv, winsys
    __all__ = ["tray", "winenv", "winsys"]
else:
    from . import winenv, winsys
    __all__ = ["winenv", "winsys"]
