"""Windows: lista monitores e janelas (o equivalente do seletor do portal no Linux).

O d3d11screencapturesrc do GStreamer 1.28 só captura monitor inteiro (não tem
window-handle). Janela = monitor onde ela está + crop-* no retângulo dela
(ver crop_for). Usa o HMONITOR em vez do índice: a ordem do GDK não é a do DXGI.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

Rect = tuple[int, int, int, int]  # left, top, right, bottom

_PRIMARY = 1  # MONITORINFOF_PRIMARY
_NEAREST = 2  # MONITOR_DEFAULTTONEAREST
_SW_RESTORE = 9
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x80
_MIN_CROP = 64


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def compute_crop(win: Rect, mon: Rect) -> tuple[int, int, int, int]:
    """Retângulo da janela em coordenadas do monitor: (x, y, w, h), sempre dentro dele.

    Nunca negativo (as propriedades crop-* são guint), largura/altura pares (NV12) e pelo
    menos 64×64. Janela parcialmente fora da tela é cortada na borda do monitor.
    """
    mon_w, mon_h = mon[2] - mon[0], mon[3] - mon[1]
    left, top = max(0, win[0] - mon[0]), max(0, win[1] - mon[1])
    right, bottom = min(mon_w, win[2] - mon[0]), min(mon_h, win[3] - mon[1])
    w = max(_MIN_CROP, (right - left) & ~1)
    h = max(_MIN_CROP, (bottom - top) & ~1)
    w, h = min(w, mon_w & ~1), min(h, mon_h & ~1)  # monitor menor que o mínimo
    return max(0, min(left, mon_w - w)), max(0, min(top, mon_h - h)), w, h


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    _H, _B = wintypes.HANDLE, wintypes.BOOL
    for _fn, _args, _res in (
        ("IsWindow", [_H], _B), ("IsWindowVisible", [_H], _B), ("IsIconic", [_H], _B),
        ("ShowWindow", [_H, ctypes.c_int], _B), ("SetForegroundWindow", [_H], _B),
        ("GetWindowTextLengthW", [_H], ctypes.c_int),
        ("GetWindowTextW", [_H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindowLongW", [_H, ctypes.c_int], ctypes.c_long),
        ("GetWindowRect", [_H, ctypes.POINTER(wintypes.RECT)], _B),
        ("GetWindowThreadProcessId", [_H, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        ("MonitorFromWindow", [_H, wintypes.DWORD], _H),
        ("GetMonitorInfoW", [_H, ctypes.POINTER(_MonitorInfo)], _B),
    ):
        getattr(user32, _fn).argtypes, getattr(user32, _fn).restype = _args, _res
    dwmapi.DwmGetWindowAttribute.argtypes = [_H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    # Sem isso o GetWindowRect devolve pixels "virtualizados" num monitor com escala, e o crop erra.
    # Falha sem problema se o GTK já definiu (é o mesmo modo).
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass

    def _monitor_info(hmon) -> tuple[Rect, bool]:
        info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            raise OSError(f"GetMonitorInfoW falhou ({ctypes.get_last_error()})")
        r = info.rcMonitor
        return (r.left, r.top, r.right, r.bottom), bool(info.dwFlags & _PRIMARY)

    def list_monitors() -> list[tuple[int, str]]:
        """[(HMONITOR, "Monitor 1 (principal) · 2560×1440")], principal primeiro."""
        found: list[tuple[int, str, bool]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def visit(handle, _hdc, _rect, _data):
            try:
                r, primary = _monitor_info(handle)
                found.append((int(handle), f"{r[2] - r[0]}×{r[3] - r[1]}", primary))
            except OSError:
                pass
            return True

        user32.EnumDisplayMonitors(None, None, visit, 0)
        found.sort(key=lambda m: not m[2])
        return [
            (handle, f"Monitor {i}{' (principal)' if primary else ''} · {size}")
            for i, (handle, size, primary) in enumerate(found, 1)
        ]

    def window_pid(hwnd: int) -> int:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def window_rect(hwnd: int) -> Rect:
        """Retângulo visível (sem a borda invisível de sombra do Win10/11); GetWindowRect de reserva."""
        r = wintypes.RECT()
        if dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                raise OSError(f"janela {hwnd} sem retângulo")
        return r.left, r.top, r.right, r.bottom

    def is_window(hwnd: int) -> bool:
        return bool(user32.IsWindow(hwnd))

    def is_iconic(hwnd: int) -> bool:
        return bool(user32.IsIconic(hwnd))

    def crop_for(hwnd: int) -> tuple[int, int, int, int, int]:
        """(HMONITOR, crop_x, crop_y, crop_w, crop_h) da janela."""
        hmon = user32.MonitorFromWindow(hwnd, _NEAREST)
        mon, _primary = _monitor_info(hmon)
        return (int(hmon), *compute_crop(window_rect(hwnd), mon))

    def bring_to_front(hwnd: int) -> None:
        """Restaura se minimizada e traz pra frente, pra nada ficar por cima dela na captura."""
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)
            for _ in range(10):  # a animação de restaurar demora um pouco pra o retângulo ficar certo
                if not user32.IsIconic(hwnd):
                    break
                time.sleep(0.05)
            time.sleep(0.2)
        user32.SetForegroundWindow(hwnd)  # pode ser recusado pelo Windows; nesse caso só segue

    def list_windows() -> list[tuple[int, str]]:
        """[(HWND, título)] das janelas de aplicativo visíveis, menos as do próprio LUCE."""
        found: list[tuple[int, str]] = []
        me = os.getpid()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.LPARAM)
        def visit(hwnd, _data):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW or window_pid(hwnd) == me:
                return True
            cloaked = wintypes.DWORD()  # apps UWP suspensos continuam "visíveis", mas escondidos
            dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                return True
            buf = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
            user32.GetWindowTextW(hwnd, buf, len(buf))
            if buf.value != "Program Manager":  # a área de trabalho
                found.append((int(hwnd), buf.value))
            return True

        user32.EnumWindows(visit, 0)
        return found
