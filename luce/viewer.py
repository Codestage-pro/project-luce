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
from gi.repository import Gdk, GLib, Gtk, Pango

_CSS = """
.luce-tile { background: black; }
.luce-bar { background: alpha(#0a0c1f, 0.82); color: white; border: 1px solid alpha(white, 0.14); border-radius: 12px; padding: 6px 10px; }
.luce-bar label { color: white; }
.luce-bar button { min-height: 28px; min-width: 28px; padding: 0 6px; border-radius: 8px; }
.luce-stats { font-size: 11px; opacity: 0.85; }
"""
_HIDE_AFTER_MS = 2500
_ZOOM_MIN, _ZOOM_MAX, _ZOOM_STEP = 1.0, 4.0, 0.25
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
        self._last_xy: tuple[float, float] | None = None
        self.add_css_class("luce-tile")
        self.set_size_request(240, 135)
        self._zoom = 1.0
        self._pan_start_x = 0.0
        self._pan_start_y = 0.0
        self._picture = Gtk.Picture(paintable=paintable, keep_aspect_ratio=True, hexpand=True, vexpand=True)
        self._scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True, child=self._picture)
        self._scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.EXTERNAL)
        self._scroller.set_kinetic_scrolling(False)
        self.set_child(self._scroller)

        # a roda vira zoom (captura, antes do ScrolledWindow rolar sozinho); arrastar com zoom > 1 move a imagem
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._on_wheel)
        self._scroller.add_controller(wheel)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        self._scroller.add_controller(drag)

        self.stats = Gtk.Label(label="conectando…", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.stats.add_css_class("monospace")
        self.stats.add_css_class("luce-stats")
        title = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"])

        zoom_out = Gtk.Button(icon_name="zoom-out-symbolic", css_classes=["flat"], tooltip_text="Diminuir zoom")
        zoom_out.connect("clicked", lambda _b: self.set_zoom(self._zoom - _ZOOM_STEP))
        self._zoom_label = Gtk.Button(label="100%", css_classes=["flat"], tooltip_text="Voltar para 100%")
        self._zoom_label.connect("clicked", lambda _b: self.set_zoom(1.0))
        zoom_in = Gtk.Button(icon_name="zoom-in-symbolic", css_classes=["flat"], tooltip_text="Aumentar zoom")
        zoom_in.connect("clicked", lambda _b: self.set_zoom(self._zoom + _ZOOM_STEP))

        self._mute = Gtk.ToggleButton(active=muted, css_classes=["flat"])
        self._mute.connect("toggled", lambda b: (self._sync_mute_icon(), viewer.on_mute(name, b.get_active())))
        self._sync_mute_icon()
        vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.5, 0.05)
        vol.set_value(volume)
        vol.set_draw_value(False)
        vol.set_size_request(72, -1)
        vol.set_hexpand(False)
        vol.connect("value-changed", lambda s: viewer.on_volume(name, s.get_value()))
        self._popout = Gtk.Button(icon_name="window-new-symbolic", css_classes=["flat"], tooltip_text="Abrir em janela separada")
        self._popout.connect("clicked", lambda _b: viewer.toggle_popout(self))
        self._full = Gtk.Button(icon_name="view-fullscreen-symbolic", css_classes=["flat"], tooltip_text="Tela cheia (duplo clique)")
        self._full.connect("clicked", lambda _b: viewer.toggle_focus(self))
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"], tooltip_text="Parar de assistir")
        close.connect("clicked", lambda _b: viewer.on_close_tile(name))

        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["luce-bar"])
        row1 = Gtk.Box(spacing=6)
        for widget in (title, zoom_out, self._zoom_label, zoom_in, self._popout, self._full, close):
            row1.append(widget)
        row2 = Gtk.Box(spacing=6)
        for widget in (self._mute, vol, self.stats):
            row2.append(widget)
        bar.append(row1)
        bar.append(row2)
        self._revealer = Gtk.Revealer(
            child=bar, transition_type=Gtk.RevealerTransitionType.CROSSFADE, reveal_child=True,
            valign=Gtk.Align.END, halign=Gtk.Align.FILL, margin_start=12, margin_end=12, margin_bottom=12,
        )
        self.add_overlay(self._revealer)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_: self._hide_bar())
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, n, *_: viewer.toggle_focus(self) if n == 2 else None)
        self.add_controller(click)
        self._show_bar()  # some sozinho: assim aparece no começo, pra pessoa saber que existe

    def _sync_mute_icon(self) -> None:
        self._mute.set_icon_name("audio-volume-muted-symbolic" if self._mute.get_active() else "audio-volume-high-symbolic")

    def set_popped(self, popped: bool) -> None:
        if popped:
            self._popout.set_icon_name("view-restore-symbolic")
            self._popout.set_tooltip_text("Voltar para a grade principal")
        else:
            self._popout.set_icon_name("window-new-symbolic")
            self._popout.set_tooltip_text("Abrir em janela separada")

    def set_focused(self, focused: bool) -> None:
        self._full.set_icon_name("view-restore-symbolic" if focused else "view-fullscreen-symbolic")
        self._full.set_tooltip_text("Voltar para a grade" if focused else "Tela cheia (duplo clique)")

    def _on_wheel(self, _ctrl, _dx: float, dy: float) -> bool:
        xy = self._last_xy or (None, None)
        self.set_zoom(self._zoom - dy * _ZOOM_STEP, xy[0], xy[1])
        return True

    def _on_drag_begin(self, _g, _x: float, _y: float) -> None:
        self._pan_start_x = self._scroller.get_hadjustment().get_value()
        self._pan_start_y = self._scroller.get_vadjustment().get_value()

    def _on_drag_update(self, _g, offset_x: float, offset_y: float) -> None:
        if self._zoom <= 1.0:
            return
        self._scroller.get_hadjustment().set_value(self._pan_start_x - offset_x)
        self._scroller.get_vadjustment().set_value(self._pan_start_y - offset_y)

    def set_zoom(self, level: float, center_x: float | None = None, center_y: float | None = None) -> None:
        """Zoom de 100% a 400%; o ponto sob o cursor (ou o centro) fica parado na tela."""
        level = max(_ZOOM_MIN, min(_ZOOM_MAX, round(level, 2)))
        if level <= 1.03:
            level = 1.0
        old = self._zoom
        if level == old:
            return
        view_w = max(self._scroller.get_width(), 320)
        view_h = max(self._scroller.get_height(), 180)
        cx = view_w / 2 if center_x is None else center_x
        cy = view_h / 2 if center_y is None else center_y
        hadj = self._scroller.get_hadjustment()
        vadj = self._scroller.get_vadjustment()
        # posição do ponto dentro do conteúdo, em fração, antes de redimensionar
        frac_x = (hadj.get_value() + cx) / (view_w * old)
        frac_y = (vadj.get_value() + cy) / (view_h * old)
        self._zoom = level
        if level == 1.0:
            self._picture.set_size_request(-1, -1)
            self._zoom_label.set_label("100%")
            return
        self._picture.set_size_request(int(view_w * level), int(view_h * level))
        self._zoom_label.set_label(f"{int(level * 100)}%")

        def _apply() -> bool:
            hadj.set_value(frac_x * view_w * level - cx)
            vadj.set_value(frac_y * view_h * level - cy)
            return False

        GLib.idle_add(_apply)

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        # O compositor reenvia "motion" com o mesmo ponto (a imagem muda, a
        # grade se reorganiza): sem este filtro a barra e o cursor reaparecem
        # sozinhos e nunca somem com o mouse parado em cima.
        if (x, y) != self._last_xy:
            self._last_xy = (x, y)
            self._show_bar()

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


    def update_paintable(self, paintable) -> None:
        self._picture.set_paintable(paintable)
        self.stats.set_label("reconectando…")


class ViewerWindow(Gtk.Window):
    def __init__(self, on_volume, on_mute, on_close_tile, on_closed) -> None:
        super().__init__(title="LUCE")
        _load_css()
        self.set_default_size(960, 540)
        self.on_volume, self.on_mute, self.on_close_tile = on_volume, on_mute, on_close_tile
        self.on_closed = on_closed
        self._tiles: dict[str, _Tile] = {}
        self._popouts: dict[str, Gtk.Window] = {}
        self._focused: _Tile | None = None
        self._grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True, row_spacing=2, column_spacing=2)
        self.set_child(self._grid)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", lambda _w: self._on_window_close())

    def _on_window_close(self) -> bool:
        for win in list(self._popouts.values()):
            win.set_child(None)
            win.destroy()
        self._popouts.clear()
        self.on_closed()
        return False

    def update_paintable(self, name: str, paintable) -> None:
        tile = self._tiles.get(name)
        if tile is not None:
            tile.update_paintable(paintable)

    def add(self, name: str, paintable, volume: float, muted: bool) -> Gtk.Label:
        """Nova tela na grade; devolve o label onde vão as estatísticas."""
        tile = _Tile(self, name, paintable, volume, muted)
        self._tiles[name] = tile
        self._layout()
        return tile.stats

    def remove(self, name: str) -> None:
        win = self._popouts.pop(name, None)
        if win is not None:
            win.set_child(None)
            win.destroy()

        tile = self._tiles.pop(name, None)
        if tile is None:
            return
        tile.dispose()
        if self._focused is tile:
            self._focused = None
            self.unfullscreen()
        if self._tiles or self._popouts:
            self._layout()
        else:
            self.close()

    def toggle_popout(self, tile: _Tile) -> None:
        if tile.name not in self._popouts:
            if self._focused is tile:
                self.toggle_focus(tile)
            if tile.get_parent() is self._grid:
                self._grid.remove(tile)
            win = Gtk.Window(title=f"LUCE — {tile.name}", default_width=854, default_height=480)
            tile.set_popped(True)
            win.set_child(tile)

            keys = Gtk.EventControllerKey()
            def _on_popout_key(_ctrl, keyval, *_args) -> bool:
                if keyval == Gdk.KEY_F11:
                    if win.is_fullscreen():
                        win.unfullscreen()
                        tile.set_focused(False)
                    else:
                        win.fullscreen()
                        tile.set_focused(True)
                    return True
                if keyval == Gdk.KEY_Escape and win.is_fullscreen():
                    win.unfullscreen()
                    tile.set_focused(False)
                    return True
                return False
            keys.connect("key-pressed", _on_popout_key)
            win.add_controller(keys)

            win.connect("close-request", lambda _w: (self.on_close_tile(tile.name), False)[1])
            self._popouts[tile.name] = win
            win.present()
            self._layout()
        else:
            win = self._popouts.pop(tile.name)
            win.set_child(None)
            win.destroy()
            tile.set_popped(False)
            self._layout()
            self.present()

    def dock_tile(self, name: str) -> None:
        tile = self._tiles.get(name)
        if tile and name in self._popouts:
            self.toggle_popout(tile)

    def toggle_focus(self, tile: _Tile) -> None:
        """Tela cheia de uma só; chamar de novo (ou Esc) volta pra grade."""
        if tile.name in self._popouts:
            win = self._popouts[tile.name]
            if win.is_fullscreen():
                win.unfullscreen()
                tile.set_focused(False)
            else:
                win.fullscreen()
                tile.set_focused(True)
            return

        self._focused = None if self._focused is tile else tile
        for t in self._tiles.values():
            if t.name not in self._popouts:
                t.set_focused(t is self._focused)
        if self._focused:
            self.fullscreen()
        else:
            self.unfullscreen()
        self._layout()

    def _on_key(self, _ctrl, keyval, *_args) -> bool:
        if keyval in (Gdk.KEY_F11,) and self._focused is not None:
            self.toggle_focus(self._focused)
            return True
        if keyval == Gdk.KEY_Escape and self._focused is not None:
            self.toggle_focus(self._focused)
            return True
        return False

    def _layout(self) -> None:
        for tile in self._tiles.values():
            if tile.get_parent() is self._grid:
                self._grid.remove(tile)

        grid_tiles = [t for name, t in self._tiles.items() if name not in self._popouts]
        if not grid_tiles and self._popouts:
            self.set_visible(False)
            return

        self.set_visible(True)
        shown = [self._focused] if (self._focused and self._focused.name not in self._popouts) else grid_tiles
        if not shown:
            return
        cols = math.ceil(math.sqrt(len(shown))) or 1
        for i, tile in enumerate(shown):
            self._grid.attach(tile, i % cols, i // cols, 1, 1)
        self.set_title(f"LUCE — {shown[0].name}" if len(shown) == 1 else f"LUCE — {len(shown)} transmissões")
