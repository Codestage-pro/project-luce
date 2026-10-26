"""Bandeja do sistema (System Tray) para Linux via StatusNotifierItem e DBusMenu.

Compatível com Waybar, Noctalia, KDE Plasma, GNOME (AppIndicator extension),
Sway, Niri e qualquer compositor/barra compatível com a especificação SNI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

ROOT = Path(__file__).resolve().parents[2]

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
    <signal name="NewMenu"/>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
  </interface>
</node>
"""


def _load_icon_pixmap() -> tuple[GLib.Variant, str]:
    """Carrega o ícone e retorna (Variant a(iiay), icon_theme_dir)."""
    candidates = [
        Path("/app/share/icons/hicolor/scalable/apps/pro.codestage.Luce.svg"),
        Path("/usr/share/icons/hicolor/scalable/apps/pro.codestage.Luce.svg"),
        ROOT / "packaging" / "pro.codestage.Luce.svg",
        ROOT / "packaging" / "windows" / "luce.ico",
    ]
    icon_path: Path | None = None
    for cand in candidates:
        if cand.exists():
            icon_path = cand
            break

    empty_pixmap = GLib.Variant("a(iiay)", [])
    if icon_path is None:
        return empty_pixmap, ""

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), 32, 32, True)
        w, h = pixbuf.get_width(), pixbuf.get_height()
        has_alpha = pixbuf.get_has_alpha()
        pixels = pixbuf.get_pixels()
        # SNI especifica ARGB32 em network byte order (Alpha, Red, Green, Blue)
        argb = bytearray(w * h * 4)
        for i in range(w * h):
            r = pixels[i * 4]
            g = pixels[i * 4 + 1]
            b = pixels[i * 4 + 2]
            a = pixels[i * 4 + 3] if has_alpha else 255
            argb[i * 4] = a
            argb[i * 4 + 1] = r
            argb[i * 4 + 2] = g
            argb[i * 4 + 3] = b

        variant = GLib.Variant("a(iiay)", [(w, h, bytes(argb))])
        return variant, str(icon_path.parent)
    except Exception as exc:
        print(f"[tray] aviso: não foi possível carregar pixmap do ícone: {exc}", file=sys.stderr)
        return empty_pixmap, str(icon_path.parent) if icon_path else ""


class LinuxTray:
    """Implementa StatusNotifierItem nativo em D-Bus para Linux."""

    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._bus: Gio.DBusConnection | None = None
        self._sni_reg_id: int = 0
        self._menu_reg_id: int = 0
        self._watcher_id: int = 0

        self._pixmap_variant, self._icon_theme_dir = _load_icon_pixmap()
        self._menu_items = {
            1: {"label": GLib.Variant("s", "Abrir"), "enabled": GLib.Variant("b", True)},
            2: {"type": GLib.Variant("s", "separator")},
            3: {"label": GLib.Variant("s", "Sair"), "enabled": GLib.Variant("b", True)},
        }

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._register_objects()
            # Monitora presença do Watcher para registrar/re-registrar se a barra reiniciar
            self._watcher_id = Gio.bus_watch_name_on_connection(
                self._bus,
                "org.kde.StatusNotifierWatcher",
                Gio.BusNameWatcherFlags.NONE,
                self._on_watcher_appeared,
                self._on_watcher_vanished,
            )
        except Exception as exc:
            print(f"[tray] aviso: falha ao inicializar D-Bus SNI: {exc}", file=sys.stderr)

    def _register_objects(self) -> None:
        if not self._bus:
            return

        sni_node = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
        self._sni_reg_id = self._bus.register_object(
            "/StatusNotifierItem",
            sni_node.interfaces[0],
            self._on_sni_method_call,
            self._on_sni_get_property,
            None,
        )

        menu_node = Gio.DBusNodeInfo.new_for_xml(MENU_XML)
        self._menu_reg_id = self._bus.register_object(
            "/MenuBar",
            menu_node.interfaces[0],
            self._on_menu_method_call,
            self._on_menu_get_property,
            None,
        )

    def _on_watcher_appeared(self, conn: Gio.DBusConnection, name: str, owner: str) -> None:
        try:
            conn.call_sync(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", ("/StatusNotifierItem",)),
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
        except Exception as exc:
            print(f"[tray] aviso: falha ao registrar com StatusNotifierWatcher: {exc}", file=sys.stderr)

    def _on_watcher_vanished(self, conn: Gio.DBusConnection, name: str) -> None:
        pass

    # -------------------------------------------------------------------------
    # StatusNotifierItem D-Bus Callbacks
    # -------------------------------------------------------------------------

    def _on_sni_method_call(
        self,
        conn: Gio.DBusConnection,
        sender: str,
        path: str,
        interface: str,
        method: str,
        params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self._on_open)
        invocation.return_value(None)

    def _on_sni_get_property(
        self,
        conn: Gio.DBusConnection,
        sender: str,
        path: str,
        interface: str,
        prop: str,
    ) -> GLib.Variant | None:
        if prop == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if prop == "Id":
            return GLib.Variant("s", "pro.codestage.Luce")
        if prop == "Title":
            return GLib.Variant("s", "Project LUCE")
        if prop == "Status":
            return GLib.Variant("s", "Active")
        if prop == "IconName":
            return GLib.Variant("s", "pro.codestage.Luce")
        if prop == "IconThemePath":
            return GLib.Variant("s", self._icon_theme_dir)
        if prop == "IconPixmap":
            return self._pixmap_variant
        if prop == "Menu":
            return GLib.Variant("o", "/MenuBar")
        if prop == "ItemIsMenu":
            return GLib.Variant("b", False)
        return None

    # -------------------------------------------------------------------------
    # DBusMenu D-Bus Callbacks
    # -------------------------------------------------------------------------

    def _on_menu_method_call(
        self,
        conn: Gio.DBusConnection,
        sender: str,
        path: str,
        interface: str,
        method: str,
        params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method == "GetLayout":
            children = []
            for item_id, props in self._menu_items.items():
                children.append(GLib.Variant("(ia{sv}av)", (item_id, props, [])))
            root = (0, {}, children)
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (1, root)))
        elif method == "GetGroupProperties":
            ids = params[0]
            result = []
            for item_id in ids:
                if item_id in self._menu_items:
                    result.append((item_id, self._menu_items[item_id]))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif method == "GetProperty":
            item_id = params[0]
            name = params[1]
            val = self._menu_items.get(item_id, {}).get(name)
            invocation.return_value(GLib.Variant("(v)", (val,)) if val else None)
        elif method == "Event":
            item_id = params[0]
            event_id = params[1]
            if event_id == "clicked":
                if item_id == 1:
                    GLib.idle_add(self._on_open)
                elif item_id == 3:
                    GLib.idle_add(self._on_quit)
            invocation.return_value(None)
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        else:
            invocation.return_value(None)

    def _on_menu_get_property(
        self,
        conn: Gio.DBusConnection,
        sender: str,
        path: str,
        interface: str,
        prop: str,
    ) -> GLib.Variant | None:
        if prop == "Version":
            return GLib.Variant("u", 3)
        if prop == "TextDirection":
            return GLib.Variant("s", "ltr")
        if prop == "Status":
            return GLib.Variant("s", "normal")
        if prop == "IconThemePath":
            return GLib.Variant("as", [self._icon_theme_dir] if self._icon_theme_dir else [])
        return None

    def destroy(self) -> None:
        """Limpa registros D-Bus ao fechar o app."""
        if self._bus:
            if self._watcher_id:
                Gio.bus_unwatch_name(self._watcher_id)
                self._watcher_id = 0
            if self._sni_reg_id:
                self._bus.unregister_object(self._sni_reg_id)
                self._sni_reg_id = 0
            if self._menu_reg_id:
                self._bus.unregister_object(self._menu_reg_id)
                self._menu_reg_id = 0
            self._bus = None

    def close(self) -> None:
        self.destroy()
