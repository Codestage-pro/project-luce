"""Janela única de assistir: uma tela por pessoa numa grade (estilo Discord),
com controles sobrepostos que aparecem ao passar o mouse.

Quem cria o `WatchPipeline` é a MainWindow; aqui só se desenha o `paintable` do
`gtk4paintablesink` e se avisa (callbacks) o que a pessoa pediu: volume, mudo,
fechar uma tela, fechar a janela.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

_CSS = """
.luce-tile { background: black; }
.luce-bar { background: alpha(black, 0.7); color: white; border-radius: 10px; padding: 8px 12px; }
.luce-bar label { color: white; }
.luce-stats { font-size: 0.8em; opacity: 0.8; }
"""
_HIDE_AFTER_MS = 2500
_css_loaded = False


def _load_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(_CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


class _Tile(Gtk.Overlay):
    def __init__(self, viewer: "ViewerWindow", name: str, paintable, volume: float, muted: bool) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.name = name
        self._hide_id = 0
        self.add_css_class("luce-tile")
        self.set_size_request(320, 180)
        self.set_child(Gtk.Picture(paintable=paintable, keep_aspect_ratio=True, hexpand=True, vexpand=True))

        self.stats = Gtk.Label(label="conectando…", xalign=0)
        self.stats.add_css_class("monospace")
        self.stats.add_css_class("luce-stats")
        title = Gtk.Label(label=name, xalign=0, css_classes=["heading"])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        text.append(title)
        text.append(self.stats)

        self._mute = Gtk.ToggleButton(active=muted, css_classes=["flat"])
        self._mute.connect("toggled", lambda b: (self._sync_mute_icon(), viewer.on_mute(name, b.get_active())))
        self._sync_mute_icon()
        vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.5, 0.05)
        vol.set_value(volume)
        vol.set_draw_value(False)
        vol.set_size_request(110, -1)
        vol.connect("value-changed", lambda s: viewer.on_volume(name, s.get_value()))
        self._full = Gtk.Button(icon_name="view-fullscreen-symbolic", css_classes=["flat"], tooltip_text="Tela cheia (duplo clique)")
        self._full.connect("clicked", lambda _b: viewer.toggle_focus(self))
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"], tooltip_text="Parar de assistir")
        close.connect("clicked", lambda _b: viewer.on_close_tile(name))

        bar = Gtk.Box(spacing=8, css_classes=["luce-bar"])
        for widget in (self._mute, vol, text, self._full, close):
            bar.append(widget)
        self._revealer = Gtk.Revealer(
            child=bar, transition_type=Gtk.RevealerTransitionType.CROSSFADE, reveal_child=True,
            valign=Gtk.Align.END, halign=Gtk.Align.FILL, margin_start=12, margin_end=12, margin_bottom=12,
        )
        self.add_overlay(self._revealer)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._show_bar())
        motion.connect("leave", lambda *_: self._hide_bar())
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, n, *_: viewer.toggle_focus(self) if n == 2 else None)
        self.add_controller(click)
        self._show_bar()  # some sozinho: assim aparece no começo, pra pessoa saber que existe

    def _sync_mute_icon(self) -> None:
        self._mute.set_icon_name("audio-volume-muted-symbolic" if self._mute.get_active() else "audio-volume-high-symbolic")

    def set_focused(self, focused: bool) -> None:
        self._full.set_icon_name("view-restore-symbolic" if focused else "view-fullscreen-symbolic")

    def _show_bar(self) -> None:
        self._revealer.set_reveal_child(True)
        self.set_cursor(None)
        if self._hide_id:
            GLib.source_remove(self._hide_id)
        self._hide_id = GLib.timeout_add(_HIDE_AFTER_MS, self._hide_bar)

    def _hide_bar(self) -> bool:
        self._hide_id = 0
        self._revealer.set_reveal_child(False)
        self.set_cursor(Gdk.Cursor.new_from_name("none"))
        return False

    def dispose(self) -> None:
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = 0


class ViewerWindow(Gtk.Window):
    def __init__(self, on_volume, on_mute, on_close_tile, on_closed) -> None:
        super().__init__(title="LUCE")
        _load_css()
        self.set_default_size(960, 540)
        self.on_volume, self.on_mute, self.on_close_tile = on_volume, on_mute, on_close_tile
        self._tiles: dict[str, _Tile] = {}
        self._focused: _Tile | None = None
        self._grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True, row_spacing=2, column_spacing=2)
        self.set_child(self._grid)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", lambda _w: on_closed() or False)

    def add(self, name: str, paintable, volume: float, muted: bool) -> Gtk.Label:
        """Nova tela na grade; devolve o label onde vão as estatísticas."""
        tile = _Tile(self, name, paintable, volume, muted)
        self._tiles[name] = tile
        self._layout()
        return tile.stats

    def remove(self, name: str) -> None:
        tile = self._tiles.pop(name, None)
        if tile is None:
            return
        tile.dispose()
        if self._focused is tile:
            self._focused = None
            self.unfullscreen()
        if self._tiles:
            self._layout()
        else:
            self.close()

    def toggle_focus(self, tile: _Tile) -> None:
        """Tela cheia de uma só; chamar de novo (ou Esc) volta pra grade."""
        self._focused = None if self._focused is tile else tile
        for t in self._tiles.values():
            t.set_focused(t is self._focused)
        if self._focused:
            self.fullscreen()
        else:
            self.unfullscreen()
        self._layout()

    def _on_key(self, _ctrl, keyval, *_args) -> bool:
        if keyval == Gdk.KEY_Escape and self._focused is not None:
            self.toggle_focus(self._focused)
            return True
        return False

    def _layout(self) -> None:
        for tile in self._tiles.values():
            if tile.get_parent() is self._grid:
                self._grid.remove(tile)
        shown = [self._focused] if self._focused else list(self._tiles.values())
        cols = math.ceil(math.sqrt(len(shown))) or 1
        for i, tile in enumerate(shown):
            self._grid.attach(tile, i % cols, i // cols, 1, 1)
        self.set_title(f"LUCE — {shown[0].name}" if len(shown) == 1 else f"LUCE — {len(shown)} transmissões")
