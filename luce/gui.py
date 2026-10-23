"""Janela GTK4: botão de transmitir, configurações, preview embutida e lista
de quem está no ar.

A preview de quem transmite usa `gtk4paintablesink` (`Gtk.Picture`), vinda de
um ramo DMA-BUF à parte (ver capture_pipeline.py) pra não forçar o GTK (que
roda em Vulkan aqui) a fazer bridge com textura GL. A janela de cada pessoa
assistida continua sendo a janela GL nativa que o `glimagesink` abre sozinho
— embutir isso também fica pra v2.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk

from . import config, roster
from .capture_pipeline import CapturePipeline
from .portal import PortalError, ScreenCast
from .watch_pipeline import WatchPipeline

# Valores fixos pra resolução/fps/qualidade — botão em vez de caixa de texto,
# pra ninguém digitar um valor fora do que o encoder/a rede aguenta.
RESOLUTIONS = [(1280, 720), (1920, 1080), (2560, 1440)]
FPS_OPTIONS = [30, 60]
VIDEO_BITRATES_KBPS = [2500, 4000, 8000]
AUDIO_BITRATES_KBPS = [64, 128, 256]


def _choice_row(grid: Gtk.Grid, row: int, label: str, options: list, selected, fmt=str) -> list[tuple[Gtk.ToggleButton, object]]:
    """Linha de botões mutuamente exclusivos (GTK4 não tem mais RadioButton)."""
    grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
    box = Gtk.Box(spacing=4)
    grid.attach(box, 1, row, 1, 1)
    pairs: list[tuple[Gtk.ToggleButton, object]] = []
    first: Gtk.ToggleButton | None = None
    for option in options:
        btn = Gtk.ToggleButton(label=fmt(option))
        if first is None:
            first = btn
        else:
            btn.set_group(first)
        btn.set_active(option == selected)
        box.append(btn)
        pairs.append((btn, option))
    return pairs


def _choice_value(pairs: list[tuple[Gtk.ToggleButton, object]], fallback):
    return next((option for btn, option in pairs if btn.get_active()), fallback)


class SettingsWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Configurações", transient_for=parent, modal=True)
        self.set_default_size(360, 0)
        cfg = config.load()
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(grid)

        self._fields: dict[str, Gtk.Entry] = {}
        text_rows = [
            ("base_url", "Servidor (WHIP/WHEP)"),
            ("api_url", "API do servidor"),
            ("auth_password", "Senha do grupo (BDK)"),
        ]
        for i, (key, label) in enumerate(text_rows):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, i, 1, 1)
            entry = Gtk.Entry(text=str(cfg[key]))
            grid.attach(entry, 1, i, 1, 1)
            self._fields[key] = entry

        next_row = len(text_rows)
        self._resolution = _choice_row(
            grid, next_row, "Resolução", RESOLUTIONS, (cfg["width"], cfg["height"]), fmt=lambda r: f"{r[1]}p"
        )
        self._fps = _choice_row(grid, next_row + 1, "FPS", FPS_OPTIONS, cfg["fps"])
        self._bitrate = _choice_row(
            grid, next_row + 2, "Qualidade de vídeo", VIDEO_BITRATES_KBPS, cfg["bitrate"], fmt=lambda k: f"{k} kbps"
        )
        self._audio_bitrate = _choice_row(
            grid, next_row + 3, "Qualidade de áudio", AUDIO_BITRATES_KBPS, cfg["audio_bitrate"], fmt=lambda k: f"{k} kbps"
        )

        self._audio_check = Gtk.CheckButton(label="Transmitir áudio", active=cfg["audio"])
        grid.attach(self._audio_check, 0, next_row + 4, 2, 1)

        save = Gtk.Button(label="Salvar")
        save.connect("clicked", self._on_save)
        grid.attach(save, 0, next_row + 5, 2, 1)

    def _on_save(self, _button: Gtk.Button) -> None:
        cfg = config.load()
        for key, entry in self._fields.items():
            cfg[key] = entry.get_text()
        cfg["width"], cfg["height"] = _choice_value(self._resolution, (cfg["width"], cfg["height"]))
        cfg["fps"] = _choice_value(self._fps, cfg["fps"])
        cfg["bitrate"] = _choice_value(self._bitrate, cfg["bitrate"])
        cfg["audio_bitrate"] = _choice_value(self._audio_bitrate, cfg["audio_bitrate"])
        cfg["audio"] = self._audio_check.get_active()
        config.save(cfg)
        self.close()


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Project LUCE")
        self.set_default_size(360, 420)

        self._capture: CapturePipeline | None = None
        self._portal: ScreenCast | None = None
        self._watches: dict[str, WatchPipeline] = {}
        # Sobrevive ao rebuild do roster (a cada 3s) pra não perder o volume/mute
        # que a pessoa escolheu enquanto assiste.
        self._volumes: dict[str, float] = {}
        self._muted: dict[str, bool] = {}

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(root)

        top = Gtk.Box(spacing=8)
        root.append(top)
        cfg = config.load()
        self._name_entry = Gtk.Entry(text=cfg["name"], placeholder_text="seu nome")
        top.append(self._name_entry)
        settings_btn = Gtk.Button(label="Configurações")
        settings_btn.connect("clicked", lambda _b: SettingsWindow(self).present())
        top.append(settings_btn)

        self._share_btn = Gtk.Button(label="Transmitir")
        self._share_btn.connect("clicked", self._on_share_clicked)
        root.append(self._share_btn)

        self._status = Gtk.Label(label="", xalign=0)
        root.append(self._status)

        self._preview = Gtk.Picture(hexpand=True, vexpand=True)
        self._preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        root.append(self._preview)

        root.append(Gtk.Label(label="Quem está no ar:", xalign=0))
        self._roster_box = Gtk.ListBox()
        root.append(self._roster_box)

        self._refresh_roster()
        GLib.timeout_add_seconds(3, self._refresh_roster)

    # --- transmitir ---

    def _on_share_clicked(self, _button: Gtk.Button) -> None:
        if self._capture is not None:
            self._stop_sharing()
            return
        name = self._name_entry.get_text().strip()
        if not name:
            self._status.set_label("digite um nome antes de transmitir")
            return
        cfg = config.load()
        cfg["name"] = name
        config.save(cfg)

        self._status.set_label("escolha o monitor na janela do sistema…")
        self._portal = ScreenCast()
        try:
            fd, node = self._portal.open()
        except PortalError as exc:
            self._status.set_label(str(exc))
            self._portal = None
            return

        preview_sink = Gst.ElementFactory.make("gtk4paintablesink", "preview-sink")
        if preview_sink is not None:
            self._preview.set_paintable(preview_sink.get_property("paintable"))

        self._capture = CapturePipeline(
            whip_endpoint=config.whip_endpoint(name),
            width=cfg["width"],
            height=cfg["height"],
            fps=cfg["fps"],
            bitrate_kbps=cfg["bitrate"],
            portal_fd=fd,
            portal_node=node,
            preview_sink=preview_sink,
            audio_enabled=cfg["audio"],
            audio_bitrate_kbps=cfg["audio_bitrate"],
            auth_token=config.auth_token(),
            on_pipeline_error=self._on_share_error,
        )
        self._capture.start()
        self._share_btn.set_label("Parar transmissão")
        enc_label = "NVENC" if self._capture.encoder_kind == "nvenc" else "VAAPI"
        self._status.set_label(f"transmitindo como {name} ({enc_label}, GPU)")

    def _on_share_error(self, message: str) -> None:
        self._status.set_label(f"transmissão caiu: {message}")
        self._stop_sharing()

    def _stop_sharing(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        if self._portal is not None:
            self._portal.close()
            self._portal = None
        self._preview.set_paintable(None)
        self._share_btn.set_label("Transmitir")

    # --- assistir ---

    def _toggle_watch(self, name: str, button: Gtk.Button) -> None:
        existing = self._watches.pop(name, None)
        if existing is not None:
            existing.stop()
            button.set_label("Assistir")
            return
        watch = WatchPipeline(
            whep_endpoint=config.whep_endpoint(name),
            auth_token=config.auth_token(),
            on_pipeline_error=lambda msg, n=name: self._on_watch_error(n, msg),
        )
        watch.set_volume(self._volumes.get(name, 1.0))
        watch.set_muted(self._muted.get(name, False))
        watch.start()
        self._watches[name] = watch
        button.set_label("Parar")

    def _on_watch_error(self, name: str, message: str) -> None:
        self._status.set_label(f"assistir {name} caiu: {message}")
        self._watches.pop(name, None)

    def _refresh_roster(self) -> bool:
        cfg = config.load()
        names = [n for n in roster.list_paths(cfg["api_url"]) if n != cfg["name"]]
        for child in list(self._roster_box):
            self._roster_box.remove(child)
        for name in names:
            row = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
            row.append(Gtk.Label(label=name, xalign=0, hexpand=True))
            watch = self._watches.get(name)
            if watch is not None:
                mute_btn = Gtk.ToggleButton(label="🔇", active=self._muted.get(name, False))
                mute_btn.connect("toggled", lambda b, n=name: self._on_mute_toggled(n, b))
                row.append(mute_btn)
                vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.5, 0.05)
                vol.set_value(self._volumes.get(name, 1.0))
                vol.set_size_request(100, -1)
                vol.set_draw_value(False)
                vol.connect("value-changed", lambda s, n=name: self._on_volume_changed(n, s))
                row.append(vol)
            button = Gtk.Button(label="Parar" if watch is not None else "Assistir")
            button.connect("clicked", lambda _b, n=name, btn=button: self._toggle_watch(n, btn))
            row.append(button)
            self._roster_box.append(row)
        return True

    def _on_mute_toggled(self, name: str, button: Gtk.ToggleButton) -> None:
        muted = button.get_active()
        self._muted[name] = muted
        watch = self._watches.get(name)
        if watch is not None:
            watch.set_muted(muted)

    def _on_volume_changed(self, name: str, scale: Gtk.Scale) -> None:
        value = scale.get_value()
        self._volumes[name] = value
        watch = self._watches.get(name)
        if watch is not None:
            watch.set_volume(value)


class LuceApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="me.nemtudo.projectluce")

    def do_activate(self) -> None:
        MainWindow(self).present()


def main() -> int:
    return LuceApp().run(None)
