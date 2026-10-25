"""Windows: lista os monitores (o equivalente do seletor do portal no Linux).

O d3d11screencapturesrc do GStreamer 1.28 só captura monitor inteiro (não tem
window-handle), então o seletor devolve o HMONITOR de cada tela. Usar o handle
em vez do índice evita errar a tela: a ordem do GDK não é a do DXGI.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_PRIMARY = 1  # MONITORINFOF_PRIMARY


def list_monitors() -> list[tuple[int, str]]:
    """[(HMONITOR, "Monitor 1 (principal) · 2560×1440")], principal primeiro."""
    user32 = ctypes.windll.user32
    found: list[tuple[int, str, bool]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def visit(handle, _hdc, _rect, _data):
        info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.rcMonitor
            found.append((int(handle), f"{r.right - r.left}×{r.bottom - r.top}", bool(info.dwFlags & _PRIMARY)))
        return True

    user32.EnumDisplayMonitors(None, None, visit, 0)
    found.sort(key=lambda m: not m[2])
    return [
        (handle, f"Monitor {i}{' (principal)' if primary else ''} · {size}")
        for i, (handle, size, primary) in enumerate(found, 1)
    ]
