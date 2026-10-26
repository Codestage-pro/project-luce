"""Bandeja do sistema (System Tray) para Windows via Shell_NotifyIconW.

Usa ctypes puro para interagir com user32/shell32 sem dependência externa,
integrado ao loop de eventos do GLib (GTK).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from gi.repository import GLib

ROOT = Path(__file__).resolve().parents[2]

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Sem argtypes/restype o ctypes trata HWND/HMENU/HICON como int de 32 bits e trunca o
    # handle em Python 64 bits: CreatePopupMenu "funcionava" mas o AppendMenuW falhava (menu vazio).
    _V, _U, _I, _W, _L = ctypes.c_void_p, wintypes.UINT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    for _lib, _fn, _args, _res in (
        (user32, "CreatePopupMenu", [], _V),
        (user32, "AppendMenuW", [_V, _U, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL),
        (user32, "TrackPopupMenu", [_V, _U, _I, _I, _I, _V, _V], wintypes.BOOL),
        (user32, "DestroyMenu", [_V], wintypes.BOOL),
        (user32, "GetCursorPos", [ctypes.c_void_p], wintypes.BOOL),
        (user32, "SetForegroundWindow", [_V], wintypes.BOOL),
        (user32, "PostMessageW", [_V, _U, _W, _L], wintypes.BOOL),
        (user32, "DefWindowProcW", [_V, _U, _W, _L], _L),
        (user32, "RegisterClassExW", [ctypes.c_void_p], wintypes.ATOM),
        (user32, "CreateWindowExW", [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, _I, _I, _I, _I, _V, _V, _V, _V], _V),
        (user32, "DestroyWindow", [_V], wintypes.BOOL),
        (user32, "UnregisterClassW", [wintypes.LPCWSTR, _V], wintypes.BOOL),
        (user32, "LoadImageW", [_V, wintypes.LPCWSTR, _U, _I, _I, _U], _V),
        (user32, "LoadIconW", [_V, _V], _V),
        (user32, "DestroyIcon", [_V], wintypes.BOOL),
        (shell32, "Shell_NotifyIconW", [wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        (shell32, "ExtractIconW", [_V, wintypes.LPCWSTR, _U], _V),
        (kernel32, "GetModuleHandleW", [wintypes.LPCWSTR], _V),
    ):
        getattr(_lib, _fn).argtypes, getattr(_lib, _fn).restype = _args, _res
else:
    user32 = None  # type: ignore
    shell32 = None  # type: ignore
    kernel32 = None  # type: ignore

WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

WS_POPUP = 0x80000000

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002

ID_OPEN = 1001
ID_QUIT = 1002

WNDPROCTYPE = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_void_p, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


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


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WindowsTray:
    """Implementa a bandeja do sistema no Windows com janela top-level oculta."""

    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._hwnd: wintypes.HWND | None = None
        self._h_icon: wintypes.HICON | None = None
        self._wndproc: WNDPROCTYPE | None = None
        self._nid: NOTIFYICONDATAW | None = None
        self._class_atom: int = 0
        self._hinstance: wintypes.HINSTANCE = kernel32.GetModuleHandleW(None)

        self._create_window()
        if self._hwnd:
            self._setup_tray_icon()

    def _create_window(self) -> None:
        self._wndproc = WNDPROCTYPE(self._wnd_proc)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinstance
        wc.lpszClassName = "LuceTrayWindow"

        self._class_atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not self._class_atom:
            err = ctypes.get_last_error()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                print(f"[tray] erro ao registrar classe de janela: {err}", file=sys.stderr)
                return

        # Janela top-level oculta (WS_POPUP, 0x0).
        # Não pode ser HWND_MESSAGE (-3), pois o Windows não entrega mensagens do
        # Shell_NotifyIcon para janelas message-only.
        self._hwnd = user32.CreateWindowExW(
            0,
            "LuceTrayWindow",
            "LuceTray",
            WS_POPUP,
            0,
            0,
            0,
            0,
            None,
            None,
            self._hinstance,
            None,
        )
        if not self._hwnd:
            print(f"[tray] erro ao criar janela de suporte: {ctypes.get_last_error()}", file=sys.stderr)

    def _load_icon(self) -> wintypes.HICON:
        candidates = [
            Path(sys.executable).parent / "luce.ico",
            Path(sys.executable).parent.parent / "luce.ico",
            ROOT / "packaging" / "windows" / "luce.ico",
            ROOT / "luce.ico",
        ]
        for path in candidates:
            if path.exists():
                h_icon = user32.LoadImageW(
                    None,
                    str(path),
                    IMAGE_ICON,
                    0,
                    0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE,
                )
                if h_icon:
                    return h_icon

        # Tenta extrair do próprio executável (onde embutimos o ícone)
        try:
            h_icon = shell32.ExtractIconW(self._hinstance, sys.executable, 0)
            if h_icon and h_icon > 1:
                return h_icon
        except Exception:
            pass

        # Fallback padrão do Windows
        return user32.LoadIconW(None, IDI_APPLICATION)

    def _setup_tray_icon(self) -> None:
        self._h_icon = self._load_icon()

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_TIP | (NIF_ICON if self._h_icon else 0)
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._h_icon or 0
        nid.szTip = "Project LUCE"

        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            print(f"[tray] aviso: Shell_NotifyIconW falhou: {ctypes.get_last_error()}", file=sys.stderr)
            return
        self._nid = nid

    def _wnd_proc(self, hwnd: wintypes.HWND, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                GLib.idle_add(lambda: self._on_open() and False)  # uma vez só, qualquer que seja o retorno
                return 0
            if lparam == WM_RBUTTONUP:
                self._show_menu()
                return 0
        elif msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == ID_OPEN:
                GLib.idle_add(lambda: self._on_open() and False)  # uma vez só, qualquer que seja o retorno
                return 0
            elif cmd == ID_QUIT:
                GLib.idle_add(lambda: self._on_quit() and False)  # uma vez só, qualquer que seja o retorno
                return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        if not self._hwnd:
            return
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "Abrir")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Sair")

        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        # Necessário para que o menu suma caso o usuário clique fora dele
        user32.SetForegroundWindow(self._hwnd)
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)

    def destroy(self) -> None:
        """Remove o ícone da bandeja e destrói a janela de mensagens."""
        if self._nid:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None
        if self._h_icon:
            user32.DestroyIcon(self._h_icon)
            self._h_icon = None
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._class_atom:
            user32.UnregisterClassW("LuceTrayWindow", self._hinstance)
            self._class_atom = 0

    def close(self) -> None:
        self.destroy()
