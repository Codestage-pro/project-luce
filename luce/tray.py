"""Bandeja do sistema (System Tray) para o Project LUCE.

Despacha para a implementação nativa de cada plataforma:
- Linux: StatusNotifierItem + DBusMenu (linux/tray.py)
- Windows: Shell_NotifyIconW (windows/tray.py)
"""

from __future__ import annotations

import sys
from typing import Callable

WINDOWS = sys.platform == "win32"

if WINDOWS:
    from .windows.tray import WindowsTray as Tray
else:
    from .linux.tray import LinuxTray as Tray

__all__ = ["Tray"]
