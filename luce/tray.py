"""Bandeja do sistema (System Tray) para Linux e Windows.

No Linux: StatusNotifierItem + dbusmenu (Waybar, KDE, painéis GNOME/Sway/Niri).
No Windows: Shell_NotifyIconW via ctypes.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

WINDOWS = sys.platform == "win32"

if WINDOWS:
    import ctypes
    from ctypes import wintypes
    from pathlib import Path
    from gi.repository import GLib

    ROOT = Path(__file__).resolve().parents[1]
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    WM_USER = 0x0400
    WM_TRAY = WM_USER + 20
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004

    WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROCTYPE),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HICON),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class WindowsTray:
        def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None]) -> None:
            self.on_open = on_open
            self.on_quit = on_quit
            self._wndproc = WNDPROCTYPE(self._window_proc)
            self._class_name = f"ProjectLuceTray_{os.getpid()}"

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.lpszClassName = self._class_name
            user32.RegisterClassExW(ctypes.byref(wc))

            HWND_MESSAGE = -3
            self.hwnd = user32.CreateWindowExW(
                0, self._class_name, "Project LUCE Tray",
                0, 0, 0, 0, 0,
                HWND_MESSAGE, 0, 0, 0
            )

            ico_path = ROOT / "luce.ico"
            h_icon = 0
            if ico_path.exists():
                IMAGE_ICON = 1
                LR_LOADFROMFILE = 0x00000010
                LR_DEFAULTSIZE = 0x00000040
                h_icon = user32.LoadImageW(0, str(ico_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)

            self.nid = NOTIFYICONDATAW()
            self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            self.nid.hWnd = self.hwnd
            self.nid.uID = 1
            self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self.nid.uCallbackMessage = WM_TRAY
            self.nid.hIcon = h_icon
            self.nid.szTip = "Project LUCE"
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))

        def _window_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_TRAY:
                if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    GLib.idle_add(self.on_open)
                    return 0
                elif lparam == WM_RBUTTONUP:
                    menu = user32.CreatePopupMenu()
                    user32.AppendMenuW(menu, 0, 1, "Abrir Project LUCE")
                    user32.AppendMenuW(menu, 0x0800, 0, None)  # MF_SEPARATOR
                    user32.AppendMenuW(menu, 0, 2, "Sair")
                    pos = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pos))
                    user32.SetForegroundWindow(hwnd)
                    cmd = user32.TrackPopupMenuEx(menu, 0x0100 | 0x0002, pos.x, pos.y, hwnd, None)
                    user32.DestroyMenu(menu)
                    if cmd == 1:
                        GLib.idle_add(self.on_open)
                    elif cmd == 2:
                        GLib.idle_add(self.on_quit)
                    return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def close(self) -> None:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
                user32.DestroyWindow(self.hwnd)
                user32.UnregisterClassW(self._class_name, 0)
            except Exception:
                pass

else:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    class DbusMenu(dbus.service.Object):
        def __init__(self, bus_name: dbus.service.BusName, on_open: Callable[[], None], on_quit: Callable[[], None]) -> None:
            super().__init__(bus_name, "/MenuBar")
            self.on_open = on_open
            self.on_quit = on_quit

        @dbus.service.method("com.canonical.dbusmenu", in_signature="iias", out_signature="u(ia{sv}av)")
        def GetLayout(self, _parent_id, _recursion_depth, _property_names):
            item_open = (1, {"label": "Abrir Project LUCE"}, [])
            item_quit = (2, {"label": "Sair"}, [])
            root = (0, {"children-display": "submenu"}, [item_open, item_quit])
            return dbus.UInt32(1), root

        @dbus.service.method("com.canonical.dbusmenu", in_signature="isvu", out_signature="")
        def Event(self, item_id, event_id, _data, _timestamp):
            if event_id == "clicked":
                if item_id == 1:
                    GLib.idle_add(self.on_open)
                elif item_id == 2:
                    GLib.idle_add(self.on_quit)

        @dbus.service.method("com.canonical.dbusmenu", in_signature="i", out_signature="b")
        def AboutToShow(self, _item_id):
            return True

        @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
        def Get(self, _iface, prop):
            return {"Version": dbus.UInt32(3), "Status": "normal"}.get(prop, "")

        @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
        def GetAll(self, _iface):
            return {"Version": dbus.UInt32(3), "Status": "normal"}

    class StatusNotifierItem(dbus.service.Object):
        def __init__(self, bus_name: dbus.service.BusName, on_open: Callable[[], None]) -> None:
            super().__init__(bus_name, "/StatusNotifierItem")
            self.on_open = on_open

        @dbus.service.method("org.kde.StatusNotifierItem", in_signature="ii", out_signature="")
        def Activate(self, _x, _y):
            GLib.idle_add(self.on_open)

        @dbus.service.method("org.kde.StatusNotifierItem", in_signature="ii", out_signature="")
        def ContextMenu(self, _x, _y):
            pass

        @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
        def Get(self, _iface, prop):
            props = {
                "Category": "ApplicationStatus",
                "Id": "pro.codestage.Luce",
                "Title": "Project LUCE",
                "Status": "Active",
                "IconName": "pro.codestage.Luce",
                "Menu": dbus.ObjectPath("/MenuBar"),
            }
            return props.get(prop, "")

        @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
        def GetAll(self, _iface):
            return {
                "Category": "ApplicationStatus",
                "Id": "pro.codestage.Luce",
                "Title": "Project LUCE",
                "Status": "Active",
                "IconName": "pro.codestage.Luce",
                "Menu": dbus.ObjectPath("/MenuBar"),
            }

    class LinuxTray:
        def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None]) -> None:
            DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SessionBus()
            self._service_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
            self.bus_name = dbus.service.BusName(self._service_name, bus=self.bus)
            self.item = StatusNotifierItem(self.bus_name, on_open)
            self.menu = DbusMenu(self.bus_name, on_open, on_quit)

            try:
                watcher = self.bus.get_object("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher")
                iface = dbus.Interface(watcher, "org.kde.StatusNotifierWatcher")
                iface.RegisterStatusNotifierItem(self._service_name)
            except Exception as exc:
                print(f"StatusNotifierWatcher não disponível: {exc}", flush=True)

        def close(self) -> None:
            pass


class Tray:
    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None]) -> None:
        try:
            if WINDOWS:
                self._impl = WindowsTray(on_open, on_quit)
            else:
                self._impl = LinuxTray(on_open, on_quit)
        except Exception as exc:
            print(f"Bandeja do sistema não pôde ser iniciada: {exc}", flush=True)
            self._impl = None

    def close(self) -> None:
        if self._impl:
            self._impl.close()
