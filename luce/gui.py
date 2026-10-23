"""Janela GTK4: botão de transmitir, configurações, preview embutida e lista
de quem está no ar.

A preview de quem transmite usa `gtk4paintablesink` (`Gtk.Picture`), vinda de
um ramo DMA-BUF à parte (ver capture_pipeline.py) pra não forçar o GTK (que
roda em Vulkan aqui) a fazer bridge com textura GL. A janela de cada pessoa
assistida continua sendo a janela GL nativa que o `glimagesink` abre sozinho
— embutir isso também fica pra v2.
"""

from __future__ import annotations

import gc
import subprocess
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstGL", "1.0")
from gi.repository import GLib, Gst, GstGL, Gtk

from . import config, roster
from .capture_pipeline import CapturePipeline
from .portal import PortalError, ScreenCast
from .watch_pipeline import WatchPipeline

# Valores fixos pra resolução/fps/qualidade — botão em vez de caixa de texto,
# pra ninguém digitar um valor fora do que o encoder/a rede aguenta.
# (0, 0) é "Fonte" (igual ao Discord): transmite no tamanho nativo de quem
# foi capturado, sem forçar nenhuma resolução — ver capture_pipeline.py.
RESOLUTIONS = [(0, 0), (1280, 720), (1920, 1080), (2560, 1440)]
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


def _list_audio_sources() -> list[tuple[str, str]]:
    """Enumera microfones (pactl) + áudio de apps abertos agora (pw-dump).

    Retorna lista de pares (device_value, display_label). device_value é o
    nome pulsesrc pro microfone, ou "app:<serial>" pra pipewiresrc pegar o
    playback de um app específico (confirmado com nível de áudio real, não
    só contagem de buffer — ver capture_pipeline.py).
    shortcut: a lista de apps só é lida quando a janela de Configurações
    abre, não atualiza sozinha enquanto ela está aberta.
    """
    devices: list[tuple[str, str]] = [("", "Padrão do sistema")]
    try:
        out = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout
        curr_name: str | None = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Name: "):
                curr_name = line.split("Name: ", 1)[1]
            elif line.startswith("Description: "):
                desc = line.split("Description: ", 1)[1]
                if curr_name and not curr_name.endswith(".monitor"):
                    devices.append((curr_name, desc))
                curr_name = None
    except Exception:
        pass
    try:
        import json

        out = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True, timeout=2).stdout
        for node in json.loads(out):
            props = (node.get("info") or {}).get("props") or {}
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            serial = props.get("object.serial")
            app = props.get("application.name") or props.get("node.name") or "app"
            media_name = props.get("media.name") or app
            if serial is None:
                continue
            label = f"{app}: {media_name}" if media_name != app else app
            devices.append((f"app:{serial}", label))
    except Exception:
        pass
    return devices


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
            grid,
            next_row,
            "Resolução",
            RESOLUTIONS,
            (cfg["width"], cfg["height"]),
            fmt=lambda r: "Fonte" if r == (0, 0) else f"{r[1]}p",
        )
        self._fps = _choice_row(grid, next_row + 1, "FPS", FPS_OPTIONS, cfg["fps"])
        self._bitrate = _choice_row(
            grid, next_row + 2, "Qualidade de vídeo", VIDEO_BITRATES_KBPS, cfg["bitrate"], fmt=lambda k: f"{k} kbps"
        )
        self._audio_bitrate = _choice_row(
            grid, next_row + 3, "Qualidade de áudio", AUDIO_BITRATES_KBPS, cfg["audio_bitrate"], fmt=lambda k: f"{k} kbps"
        )

        grid.attach(Gtk.Label(label="Fonte de áudio", xalign=0), 0, next_row + 4, 1, 1)
        self._audio_sources = _list_audio_sources()
        audio_labels = [desc for _dev, desc in self._audio_sources]
        self._audio_dropdown = Gtk.DropDown.new_from_strings(audio_labels)
        current_dev = cfg.get("audio_device", "")
        sel_idx = 0
        for idx, (dev, _desc) in enumerate(self._audio_sources):
            if dev == current_dev:
                sel_idx = idx
                break
        self._audio_dropdown.set_selected(sel_idx)
        grid.attach(self._audio_dropdown, 1, next_row + 4, 1, 1)

        self._audio_check = Gtk.CheckButton(label="Transmitir áudio", active=cfg["audio"])
        grid.attach(self._audio_check, 0, next_row + 5, 2, 1)

        save = Gtk.Button(label="Salvar")
        save.connect("clicked", self._on_save)
        grid.attach(save, 0, next_row + 6, 2, 1)

    def _on_save(self, _button: Gtk.Button) -> None:
        cfg = config.load()
        for key, entry in self._fields.items():
            cfg[key] = entry.get_text()
        cfg["width"], cfg["height"] = _choice_value(self._resolution, (cfg["width"], cfg["height"]))
        cfg["fps"] = _choice_value(self._fps, cfg["fps"])
        cfg["bitrate"] = _choice_value(self._bitrate, cfg["bitrate"])
        cfg["audio_bitrate"] = _choice_value(self._audio_bitrate, cfg["audio_bitrate"])
        sel_idx = self._audio_dropdown.get_selected()
        if 0 <= sel_idx < len(self._audio_sources):
            cfg["audio_device"] = self._audio_sources[sel_idx][0]
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
        self._restart_count: int = 0
        self._last_restart_time: float = 0.0
        self._restart_window_start: float = 0.0
        # Um só GLDisplay pra vida da janela: recriar um novo a cada restart
        # (troca de tela/resolução) é o que parece deixar a NVIDIA devolver
        # EGL_BAD_CONTEXT — ver comentário em CapturePipeline._on_need_context.
        self._gl_display = GstGL.GLDisplay.new()

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

        share_row = Gtk.Box(spacing=8)
        root.append(share_row)
        self._share_btn = Gtk.Button(label="Transmitir")
        self._share_btn.connect("clicked", self._on_share_clicked)
        share_row.append(self._share_btn)
        self._switch_btn = Gtk.Button(label="Trocar tela", visible=False)
        self._switch_btn.connect("clicked", self._on_switch_source_clicked)
        share_row.append(self._switch_btn)

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
        self._restart_count = 0
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
            audio_device=cfg["audio_device"],
            auth_token=config.auth_token(),
            on_pipeline_error=self._on_share_error,
            gl_display=self._gl_display,
        )
        self._capture.start()
        self._share_btn.set_label("Parar transmissão")
        self._switch_btn.set_visible(True)
        enc_label = "NVENC" if self._capture.encoder_kind == "nvenc" else "VAAPI"
        self._status.set_label(f"transmitindo como {name} ({enc_label}, GPU)")

    def _on_switch_source_clicked(self, _button: Gtk.Button) -> None:
        """Troca a janela/monitor transmitido sem parar a transmissão.

        Abre uma sessão NOVA do portal (único jeito dele mostrar o seletor
        de tela de novo) e só troca a fonte do pipeline já rodando (ver
        CapturePipeline.switch_source) DEPOIS que essa sessão devolver um
        fd — se a pessoa cancelar o seletor, a transmissão velha nem pisca.
        Antes isso recriava o CapturePipeline inteiro (igual o auto-restart
        de _on_share_error); trocar só a fonte evita o EGL_BAD_CONTEXT que
        reimportar DMA-BUF num glupload/NVENC novo disparava.
        """
        if self._capture is None or self._portal is None:
            return
        name = self._name_entry.get_text().strip()
        enc_label = "NVENC" if self._capture.encoder_kind == "nvenc" else "VAAPI"

        self._status.set_label("escolha a nova tela na janela do sistema…")
        new_portal = ScreenCast()
        try:
            fd, node = new_portal.open()
        except PortalError as exc:
            self._status.set_label(str(exc))
            new_portal.close()
            self._status.set_label(f"transmitindo como {name} ({enc_label}, GPU)")
            return

        try:
            self._capture.switch_source(fd, node)
        except Exception as exc:
            new_portal.close()
            self._status.set_label(f"erro ao trocar de tela: {exc}")
            return

        self._portal.close()
        self._portal = new_portal
        self._status.set_label(f"transmitindo como {name} ({enc_label}, GPU)")

    def _on_share_error(self, message: str) -> None:
        if self._portal is None:
            self._status.set_label(f"transmissão caiu: {message}")
            self._stop_sharing()
            return

        # Libera o preview do GTK e para o pipeline imediatamente pra desarmar
        # os timers e o contexto GL/EGL da NVIDIA antes de recriar.
        self._preview.set_paintable(None)
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        gc.collect()

        now = time.monotonic()
        # Compara com o INÍCIO da sequência de tentativas, não com a última:
        # o backoff abaixo cresce até 3.2s, então medir contra a última
        # tentativa deixava a janela de "ainda é a mesma sequência" curta
        # demais e o contador voltava pra 1 a cada tentativa — nunca batia
        # o teto de 3 e ficava tentando pra sempre.
        if now - self._restart_window_start > 15.0:
            self._restart_count = 0
            self._restart_window_start = now
        self._restart_count += 1
        self._last_restart_time = now

        if self._restart_count > 3:
            self._status.set_label(f"transmissão caiu: {message}")
            self._stop_sharing()
            return

        self._status.set_label("troca de tela detectada, reconectando…")
        # shortcut: não tem como perguntar ao driver da NVIDIA se o contexto
        # EGL/CUDA da sessão NVENC anterior já terminou de ser liberado — só
        # dá pra esperar. 400ms fixo não bastava mesmo na primeira tentativa
        # (visto em teste real); backoff crescente dá mais fôlego a cada
        # retry em vez de martelar o driver sempre no mesmo intervalo curto.
        delay = min(800 * (2 ** (self._restart_count - 1)), 3200)
        GLib.timeout_add(delay, self._do_restart_sharing)

    def _do_restart_sharing(self) -> bool:
        if self._portal is None:
            return False
        name = self._name_entry.get_text().strip()
        if not name:
            self._stop_sharing()
            return False
        try:
            fd = self._portal.reopen_fd()
            node = self._portal.node_id
        except Exception as exc:
            self._status.set_label(f"sessão de captura fechou: {exc}")
            self._stop_sharing()
            return False

        cfg = config.load()
        preview_sink = Gst.ElementFactory.make("gtk4paintablesink", "preview-sink")
        if preview_sink is not None:
            self._preview.set_paintable(preview_sink.get_property("paintable"))

        try:
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
                audio_device=cfg["audio_device"],
                auth_token=config.auth_token(),
                on_pipeline_error=self._on_share_error,
                gl_display=self._gl_display,
            )
            self._capture.start()
            enc_label = "NVENC" if self._capture.encoder_kind == "nvenc" else "VAAPI"
            self._status.set_label(f"transmitindo como {name} ({enc_label}, GPU)")
        except Exception as exc:
            self._status.set_label(f"erro ao reiniciar transmissão: {exc}")
            self._stop_sharing()

        return False

    def _stop_sharing(self) -> None:
        self._restart_count = 0
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        if self._portal is not None:
            self._portal.close()
            self._portal = None
        self._preview.set_paintable(None)
        self._share_btn.set_label("Transmitir")
        self._switch_btn.set_visible(False)

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
