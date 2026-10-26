"""Bandeja do sistema (System Tray) para o Project LUCE.

Despacha para a implementação nativa de cada plataforma:
- Linux: StatusNotifierItem + DBusMenu (tray_linux.py)
- Windows: Shell_NotifyIconW (tray_windows.py)
"""

from __future__ import annotations

import sys
from typing import Callable

WINDOWS = sys.platform == "win32"

if WINDOWS:
    from .tray_windows import WindowsTray as Tray
else:
    from .tray_linux import LinuxTray as Tray

__all__ = ["Tray"]
