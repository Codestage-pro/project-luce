"""Windows: lista monitores e janelas (o equivalente do seletor do portal no Linux).

Janela = d3d11screencapturesrc com capture-api=wgc + window-handle (Windows Graphics Capture).
Monitor usa o HMONITOR em vez do índice: a ordem do GDK não é a do DXGI.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

Rect = tuple[int, int, int, int]  # left, top, right, bottom

_PRIMARY = 1  # MONITORINFOF_PRIMARY
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x80


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _H, _B = wintypes.HANDLE, wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    for _fn, _args, _res in (
        ("IsWindow", [_H], _B), ("IsWindowVisible", [_H], _B),
        ("GetWindowTextLengthW", [_H], ctypes.c_int),
        ("GetWindowTextW", [_H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindowLongW", [_H, ctypes.c_int], ctypes.c_long),
        ("GetWindowRect", [_H, ctypes.POINTER(wintypes.RECT)], _B),
        ("GetWindowThreadProcessId", [_H, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        ("GetMonitorInfoW", [_H, ctypes.POINTER(_MonitorInfo)], _B),
        ("LoadImageW", [_H, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT], _H),
        ("SendMessageW", [_H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.LPARAM),
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

    def apply_icon() -> None:
        """Põe o logo na barra de título/Alt-Tab das janelas do LUCE: o GTK4 no Windows não faz isso."""
        ico = Path(__file__).resolve().parents[2] / "luce.ico"
        if not ico.exists():
            ico = Path(__file__).resolve().parents[1] / "luce.ico"
        if not ico.exists():  # rodando do repositório, sem bundle
            return
        me = os.getpid()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.LPARAM)
        def visit(hwnd, _data):
            if window_pid(hwnd) == me and user32.IsWindowVisible(hwnd):
                for kind, size in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
                    icon = user32.LoadImageW(None, str(ico), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                    user32.SendMessageW(hwnd, 0x80, kind, icon)  # WM_SETICON
            return True

        user32.EnumWindows(visit, 0)

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

    def process_name(pid: int) -> str:
        """Nome do executável do processo (ex: 'Discord.exe'), ou '' se inacessível."""
        hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not hproc:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name
            return ""
        finally:
            kernel32.CloseHandle(hproc)

    def find_process_pid(name: str) -> int | None:
        """Acha o PID de um app pelo nome do executável (ex: 'Discord') ou título da janela."""
        target = name.lower().removesuffix(".exe")
        for hwnd, title in list_windows():
            try:
                pid = window_pid(hwnd)
                pname = process_name(pid).lower().removesuffix(".exe")
                if pname == target or target in title.lower():
                    return pid
            except OSError:
                pass
        return None

    def list_audio_sources() -> list[tuple[str, str]]:
        """[(valor_cfg, label_exibicao)]. Primeiro é sempre ('', 'Padrão do sistema')."""
        devices: list[tuple[str, str]] = [("", "Padrão do sistema")]
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            monitor = Gst.DeviceMonitor.new()
            monitor.add_filter("Audio/Source", None)
            monitor.start()
            for dev in monitor.get_devices():
                name = dev.get_display_name()
                props = dev.get_properties()
                dev_id = ""
                if props:
                    for k in ("device.strid", "wasapi2.device", "device.id", "device.api.id"):
                        val = props.get_value(k)
                        if val:
                            dev_id = str(val)
                            break
                if not dev_id:
                    elem = dev.create_element(None)
                    if elem and hasattr(elem.props, "device"):
                        dev_id = elem.get_property("device") or ""
                devices.append((dev_id or name, name))
            monitor.stop()
        except Exception as exc:
            print(f"[winsys] erro ao listar fontes de áudio: {exc}", file=sys.stderr)
        # shortcut: só apps com janela aberta (é o que dá pra achar o PID sem enumerar sessões WASAPI)
        devices += [(f"app:{stem}", f"App: {label}") for stem, label in list_mutable_apps()[1:]]
        return devices

    def list_mutable_apps() -> list[tuple[str, str]]:
        """[(valor_cfg, label_exibicao)]. Primeiro é sempre ('', 'Nenhum')."""
        apps: list[tuple[str, str]] = [("", "Nenhum")]
        seen: set[str] = set()
        try:
            for hwnd, title in list_windows():
                pid = window_pid(hwnd)
                pname = process_name(pid)
                stem = Path(pname).stem if pname else title.split(" - ")[-1].strip()
                if stem and stem.lower() not in seen:
                    seen.add(stem.lower())
                    label = f"{stem} ({title[:30]}…)" if len(title) > 30 else f"{stem} ({title})"
                    apps.append((stem, label))
        except Exception as exc:
            print(f"[winsys] erro ao listar apps por janela: {exc}", file=sys.stderr)

        common_apps = [
            "Discord", "Spotify", "chrome", "firefox", "msedge", "brave", "Telegram",
            "Steam", "steamwebhelper", "OBS", "vlc",
        ]
        for app_name in common_apps:
            if app_name.lower() not in seen:
                pid = find_process_pid(app_name)
                if pid:
                    seen.add(app_name.lower())
                    apps.append((app_name, app_name))

        return apps
else:
    def process_name(_pid: int) -> str:
        return ""

    def find_process_pid(_name: str) -> int | None:
        return None

    def apply_icon() -> None:
        pass

    def list_monitors() -> list[tuple[int, str]]:
        return []

    def list_windows() -> list[tuple[int, str]]:
        return []

    def window_pid(_hwnd: int) -> int:
        return 0

    def is_window(_hwnd: int) -> bool:
        return False

    def window_rect(_hwnd: int) -> Rect:
        return (0, 0, 0, 0)

    def list_audio_sources() -> list[tuple[str, str]]:
        return [("", "Padrão do sistema")]

    def list_mutable_apps() -> list[tuple[str, str]]:
        return [("", "Nenhum")]

