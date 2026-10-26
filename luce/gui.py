"""Janela GTK4: botão de transmitir, configurações, preview embutida e lista
de quem está no ar.

A preview de quem transmite usa `gtk4paintablesink` (`Gtk.Picture`), vinda de
um ramo DMA-BUF à parte (ver capture_pipeline.py) pra não forçar o GTK (que
roda em Vulkan aqui) a fazer bridge com textura GL. Quem é assistido aparece na
janela única de viewer.py (uma tela por pessoa, em grade).
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstGL", "1.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib, Gdk, Gst, GstGL, Gtk, Pango

# O GSK Vulkan do GTK no mesmo processo faz o glupload do NVENC importar DMA-BUF
# preto (niri + NVIDIA: transmissão toda preta, ~60 kbps). Com GL fica normal.
WINDOWS = sys.platform == "win32"
if not WINDOWS:
    os.environ.setdefault("GSK_RENDERER", "gl")

from . import config, roster, stats, theme, updater
from .capture_pipeline import CapturePipeline
from .tray import Tray
if WINDOWS:
    from . import winsys
    from .winsys import apply_icon, is_window, list_monitors, list_windows
else:  # o portal usa dbus, que não existe no Windows
    from .portal import PortalError, ScreenCast, screencast_available, x11_session
from .viewer import ViewerWindow
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
    if WINDOWS:  # sem pactl/pw-dump: os microfones vêm do WASAPI, device.id é o que o wasapi2src aceita
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Audio/Source", None)
        for dev in monitor.get_devices() or []:
            props = dev.get_properties()
            if props is None or not props.has_field("device.id"):
                continue
            if props.has_field("wasapi2.device.loopback") and props.get_value("wasapi2.device.loopback"):
                continue  # o áudio do sistema já é o "Padrão do sistema"
            devices.append((props.get_value("device.id"), dev.get_display_name()))
        return devices
    try:
        out = subprocess.run(
            ["pactl", "list", "sources"],
            env={**os.environ, "LC_ALL": "C"},  # em pt_BR o pactl imprime "Nome:"/"Descrição:" e nada casava
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


def _list_mutable_apps() -> list[tuple[str, str]]:
    """[(valor_cfg, label_exibicao)]. Primeiro é sempre ('', 'Nenhum')."""
    apps: list[tuple[str, str]] = [("", "Nenhum")]
    seen: set[str] = set()
    if WINDOWS:
        try:
            for hwnd, title in list_windows():
                pname = winsys.process_name(winsys.window_pid(hwnd))
                stem = Path(pname).stem if pname else title.split(" - ")[-1].strip()
                if stem and stem.lower() not in seen:
                    seen.add(stem.lower())
                    apps.append((stem, f"{stem} ({title[:30]}…)" if len(title) > 30 else f"{stem} ({title})"))
        except Exception as exc:  # engolido sem log, um NameError deixou essa lista vazia
            print(f"lista de apps: {exc}", flush=True)
    else:  # Linux: pega apps produzindo áudio via pw-dump
        try:
            import json

            out = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True, timeout=2).stdout
            for node in json.loads(out):
                props = (node.get("info") or {}).get("props") or {}
                if props.get("media.class") != "Stream/Output/Audio":
                    continue
                app = props.get("application.name") or props.get("node.name")
                if app and app.lower() not in seen:
                    seen.add(app.lower())
                    apps.append((app, app))
        except Exception:
            pass
    return apps


def _choice_value(pairs: list[tuple[Gtk.ToggleButton, object]], fallback):
    return next((option for btn, option in pairs if btn.get_active()), fallback)


_ENCODER_LABELS = {
    "nvenc": "NVENC, GPU", "nvd3d11": "NVENC, GPU", "va": "VA-API, GPU", "amf": "AMF, GPU",
    "qsv": "Quick Sync, GPU", "mf": "Media Foundation, GPU", "x264": "x264, CPU",
}


class SettingsWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, on_saved=None) -> None:
        super().__init__(title="Configurações", transient_for=parent, modal=True)
        self._on_saved = on_saved
        theme.load()
        self.set_default_size(360, 0)
        cfg = config.load()
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(grid)

        self._fields: dict[str, Gtk.Entry] = {}
        text_rows = [
            ("base_url", "Servidor (WHIP/WHEP)"),
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

        grid.attach(Gtk.Label(label="Silenciar app", tooltip_text="Silencia o aplicativo na transmissão (evita eco do Discord)", xalign=0), 0, next_row + 5, 1, 1)
        self._mute_apps = _list_mutable_apps()
        current_mute = cfg.get("mute_app", "")
        if current_mute and not any(val.lower() == current_mute.lower() for val, _ in self._mute_apps):
            self._mute_apps.append((current_mute, f"{current_mute} (não aberto)"))
        mute_labels = [desc for _val, desc in self._mute_apps]
        self._mute_dropdown = Gtk.DropDown.new_from_strings(mute_labels)
        mute_idx = 0
        for idx, (val, _desc) in enumerate(self._mute_apps):
            if val.lower() == current_mute.lower():
                mute_idx = idx
                break
        self._mute_dropdown.set_selected(mute_idx)
        grid.attach(self._mute_dropdown, 1, next_row + 5, 1, 1)

        self._audio_check = Gtk.CheckButton(label="Transmitir áudio", active=cfg["audio"])
        grid.attach(self._audio_check, 0, next_row + 6, 2, 1)

        save = Gtk.Button(label="Salvar", css_classes=["go"])
        save.connect("clicked", self._on_save)
        grid.attach(save, 0, next_row + 7, 2, 1)

    def _on_save(self, _button: Gtk.Button) -> None:
        cfg = config.load()
        old = dict(cfg)
        for key, entry in self._fields.items():
            cfg[key] = entry.get_text()
        cfg["width"], cfg["height"] = _choice_value(self._resolution, (cfg["width"], cfg["height"]))
        cfg["fps"] = _choice_value(self._fps, cfg["fps"])
        cfg["bitrate"] = _choice_value(self._bitrate, cfg["bitrate"])
        cfg["audio_bitrate"] = _choice_value(self._audio_bitrate, cfg["audio_bitrate"])
        sel_idx = self._audio_dropdown.get_selected()
        if 0 <= sel_idx < len(self._audio_sources):
            cfg["audio_device"] = self._audio_sources[sel_idx][0]
        mute_idx = self._mute_dropdown.get_selected()
        if 0 <= mute_idx < len(self._mute_apps):
            cfg["mute_app"] = self._mute_apps[mute_idx][0]
        cfg["audio"] = self._audio_check.get_active()
        config.save(cfg)
        self.close()
        if self._on_saved is not None:
            self._on_saved(old, cfg)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Project LUCE")
        theme.load()
        self.set_default_size(380, 580)

        self._capture: CapturePipeline | None = None
        self._portal = None  # ScreenCast (Linux)
        self._monitor_handle: int | None = None  # HMONITOR escolhido (Windows)
        self._window_handle: int | None = None  # HWND escolhido (Windows); vale no lugar do monitor
        self._watches: dict[str, WatchPipeline] = {}
        self._viewer: ViewerWindow | None = None
        # Sobrevive ao rebuild do roster (a cada 3s) pra não perder o volume/mute
        # que a pessoa escolheu enquanto assiste.
        self._volumes: dict[str, float] = {}
        self._muted: dict[str, bool] = {}
        self._restart_count: int = 0
        # Última leitura de stats por chave ("share" ou nome de quem se assiste), pra calcular o bitrate.
        self._last_stats: dict[str, dict] = {}
        self._last_enc: tuple[object, int, float] | None = None  # (captura, quadros codificados, instante) da leitura anterior
        self._ping_tick = 0
        self._fps_shown = 0.0
        self._missed: dict[str, int] = {}
        self._watch_stats: dict[str, Gtk.Label] = {}
        # Captura sem portal (ximagesrc): decidido a cada "Transmitir", porque
        # o backend do portal pode ser instalado com o app já aberto.
        self._x11: bool = False
        self._x11_region: tuple[int, int, int, int] | None = None
        self._last_restart_time: float = 0.0
        self._restart_window_start: float = 0.0
        # Um só GLDisplay pra vida da janela: recriar um novo a cada restart
        # (troca de tela/resolução) é o que parece deixar a NVIDIA devolver
        # EGL_BAD_CONTEXT — ver comentário em CapturePipeline._on_need_context.
        self._gl_display = GstGL.GLDisplay.new()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        self.set_child(root)

        # cabeçalho: logo, nome e o feixe de luz (vira magenta e respira no ar)
        head = Gtk.Box(spacing=10)
        if Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon("pro.codestage.Luce"):
            head.append(Gtk.Image(icon_name="pro.codestage.Luce", pixel_size=40))
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        title.append(Gtk.Label(label="LUCE", xalign=0, css_classes=["wordmark"]))
        title.append(Gtk.Label(label="sua tela, ao vivo, com a galera", xalign=0, css_classes=["tagline"]))
        head.append(title)
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Configurações", valign=Gtk.Align.CENTER)
        settings_btn.add_css_class("flat")
        settings_btn.connect("clicked", lambda _b: SettingsWindow(self, self._on_settings_saved).present())
        head.append(settings_btn)
        root.append(head)
        root.append(Gtk.Box(css_classes=["beam"]))

        self.connect("close-request", self._on_close_request)
        self._tray = Tray(
            on_open=lambda: GLib.idle_add(self.present),
            on_quit=lambda: GLib.idle_add(self.quit_app),
        )

        if WINDOWS or updater.is_flatpak():
            self._update_btn = Gtk.Button(visible=False, css_classes=["suggested-action"])
            self._update_btn.connect("clicked", self._on_update_clicked)
            root.append(self._update_btn)
            self._update: tuple[str, str, str | None] | None = None
            self._run_in_thread(updater.find_update, self._on_update_found)

        cfg = config.load()
        self._name_entry = Gtk.Entry(text=cfg["name"], placeholder_text="seu nome")
        root.append(self._name_entry)
        if not cfg["auth_password"]:  # primeira abertura: sem senha do grupo nada conecta
            GLib.idle_add(SettingsWindow(self).present)  # present() devolve None: roda uma vez

        share_row = Gtk.Box(spacing=8)
        root.append(share_row)
        self._share_btn = Gtk.Button(label="Transmitir", hexpand=True, css_classes=["go"])
        self._share_btn.connect("clicked", self._on_share_clicked)
        share_row.append(self._share_btn)
        self._switch_btn = Gtk.Button(label="Trocar tela", visible=False)
        self._switch_btn.connect("clicked", self._on_switch_source_clicked)
        share_row.append(self._switch_btn)

        status_pill = Gtk.Box(spacing=10, css_classes=["status"])
        status_pill.append(Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["status-dot"]))
        self._status = Gtk.Label(label="pronto para transmitir", xalign=0, wrap=True, hexpand=True)
        status_pill.append(self._status)
        root.append(status_pill)
        self._stats_label = Gtk.Label(label="", xalign=0, css_classes=["monospace", "stats"])
        root.append(self._stats_label)
        self._viewers_label = Gtk.Label(label="", xalign=0, wrap=True, visible=False, css_classes=["dim"])
        root.append(self._viewers_label)

        self._preview = Gtk.Picture(hexpand=True, vexpand=True)
        self._preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        hint = Gtk.Label(label="A prévia da sua tela aparece aqui", css_classes=["stage-hint"])
        self._preview.connect("notify::paintable", lambda p, _: hint.set_visible(p.get_paintable() is None))
        stage = Gtk.Overlay(child=self._preview, css_classes=["stage"], overflow=Gtk.Overflow.HIDDEN)
        stage.add_overlay(hint)
        root.append(stage)

        root.append(Gtk.Label(label="No ar agora", xalign=0, css_classes=["section"]))
        self._roster_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        root.append(self._roster_box)

        self._refresh_roster()
        GLib.timeout_add_seconds(3, self._refresh_roster)
        GLib.timeout_add_seconds(1, self._poll_stats)

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
        # Senha errada/vazia só aparecia como "erro geral nos recursos" do whipsink;
        # o roster responde 401 na hora e dá pra dizer o motivo de verdade.
        try:
            roster.list_paths(cfg["base_url"], config.auth_token())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                self._status.set_label("senha do grupo errada ou vazia: confira em Configurações")
                return
        except Exception as exc:
            self._status.set_label(f"servidor não respondeu: {exc}")
            return

        self._restart_count = 0
        fd = node = None
        if WINDOWS:
            if self._monitor_handle is None and self._window_handle is None:
                def picked(monitor, window):
                    self._monitor_handle, self._window_handle = monitor, window
                    self._on_share_clicked(self._share_btn)
                self._pick_windows_source(picked)
                return
            self._status.set_label("capturando a janela…" if self._window_handle else "capturando o monitor…")
        # Nem todo Linux tem portal de captura: ele depende de um backend do
        # ambiente (gnome/kde/wlr/hyprland). Quem só tem o backend gtk
        # (XFCE, Cinnamon, i3…) ficava esperando um seletor de tela que nunca
        # abria; no X11 dá pra capturar direto, sem portal e sem seletor.
        self._x11 = not WINDOWS and not screencast_available()
        if self._x11 and not x11_session():
            self._status.set_label(
                "sem como capturar a tela: falta o backend do portal "
                "(xdg-desktop-portal-gnome, -kde, -wlr ou -hyprland) — "
                "instale o do seu ambiente e saia e entre na sessão"
            )
            self._x11 = False
            return

        if self._x11:
            if self._x11_region is None and not self._pick_x11_monitor():
                self._x11 = False  # o diálogo chama _on_share_clicked de novo depois da escolha
                return
            self._status.set_label("sem portal: capturando o monitor escolhido (X11)…")
        elif not WINDOWS:
            self._status.set_label("escolha o monitor na janela do sistema…")
            self._portal = ScreenCast()
            try:
                fd, node = self._portal.open()
            except PortalError as exc:
                self._status.set_label(str(exc))
                self._portal = None
                return

        try:
            self._capture = CapturePipeline(
                whip_endpoint=config.whip_endpoint(name),
                width=cfg["width"],
                height=cfg["height"],
                fps=cfg["fps"],
                bitrate_kbps=cfg["bitrate"],
                x11=self._x11,
                x11_region=self._x11_region,
                portal_fd=fd,
                portal_node=node,
                monitor_handle=self._monitor_handle,
                window_handle=self._window_handle,
                preview_sink=self._make_preview_sink,
                audio_enabled=cfg["audio"],
                audio_bitrate_kbps=cfg["audio_bitrate"],
                mute_app=cfg.get("mute_app", ""),
                audio_device=cfg["audio_device"],
                auth_token=config.auth_token(),
                on_pipeline_error=self._on_share_error,
                gl_display=self._gl_display,
            )
            self._capture.start()
        except Exception as exc:
            # Montar o pipeline falha de verdade em máquina sem encoder de GPU
            # (sem NVIDIA e sem driver VA-API): sem isto a exceção só ia pro
            # stderr do handler do GTK e a janela ficava parada em "escolha o
            # monitor…", sem dizer o que faltava.
            self._capture = None
            if self._portal is not None:
                self._portal.close()
                self._portal = None
            self._x11 = False
            self._monitor_handle = self._window_handle = None
            self._preview.set_paintable(None)
            self._status.set_label(f"não deu pra transmitir: {exc}")
            return
        self._share_btn.set_label("Parar transmissão")
        self._share_btn.remove_css_class("go")
        self._share_btn.add_css_class("stop")
        self.add_css_class("live")
        # Trocar de tela pede uma sessão nova do portal; no X11 cru a fonte é
        # sempre a tela inteira, então o botão não teria o que fazer.
        self._switch_btn.set_visible(self._portal is not None or WINDOWS)
        enc_label = _ENCODER_LABELS[self._capture.encoder_kind]
        self._status.set_label(f"transmitindo como {name} ({enc_label})")

    def _on_settings_saved(self, old: dict, new: dict) -> None:
        """Aplica na hora o que mudou nas Configurações enquanto transmite.

        Qualidade e áudio o encoder aceita em voo (sem cortar a imagem de
        ninguém); resolução, fps, fonte de áudio e servidor mudam a cadeia
        inteira, então a captura é refeita — o mesmo reinício da troca de tela,
        sem pedir o seletor de novo.
        """
        cap = self._capture
        if cap is None:
            return
        rebuild = ("width", "height", "fps", "audio_device", "base_url", "auth_password")
        if any(old[k] != new[k] for k in rebuild):
            self._restart_count = 0
            self._status.set_label("aplicando as configurações…")
            self._preview.set_paintable(None)
            cap.stop()
            self._capture = None
            gc.collect()
            GLib.timeout_add(800, self._do_restart_sharing)  # mesma folga pro driver da NVIDIA que o _on_share_error
            return
        if old["bitrate"] != new["bitrate"]:
            cap.set_bitrate(new["bitrate"])
        if old["audio_bitrate"] != new["audio_bitrate"]:
            cap.set_audio_bitrate(new["audio_bitrate"])
        if old["audio"] != new["audio"]:
            cap.set_audio_enabled(new["audio"])

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
        if WINDOWS:
            if self._capture is not None:
                self._pick_windows_source(self._switch_windows_source)
            return
        if self._capture is None or self._portal is None:
            return
        name = self._name_entry.get_text().strip()
        enc_label = _ENCODER_LABELS[self._capture.encoder_kind]

        self._status.set_label("escolha a nova tela na janela do sistema…")
        new_portal = ScreenCast()
        try:
            fd, node = new_portal.open()
        except PortalError as exc:
            self._status.set_label(str(exc))
            new_portal.close()
            self._status.set_label(f"transmitindo como {name} ({enc_label})")
            return

        try:
            self._capture.switch_source(fd, node)
        except Exception as exc:
            new_portal.close()
            self._status.set_label(f"erro ao trocar de tela: {exc}")
            return

        self._portal.close()
        self._portal = new_portal
        self._status.set_label(f"transmitindo como {name} ({enc_label})")

    def _on_share_error(self, message: str) -> None:
        if self._portal is None and not self._x11 and self._window_handle is None:
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

    def _make_preview_sink(self) -> Gst.Element | None:
        sink = Gst.ElementFactory.make("gtk4paintablesink", "preview-sink")
        if sink is not None:
            self._preview.set_paintable(sink.get_property("paintable"))
        return sink

    def _do_restart_sharing(self) -> bool:
        if self._portal is None and not self._x11 and self._monitor_handle is None and self._window_handle is None:
            return False
        name = self._name_entry.get_text().strip()
        if not name:
            self._stop_sharing()
            return False
        if self._window_handle is not None and not is_window(self._window_handle):
            self._status.set_label("a janela transmitida foi fechada")
            self._stop_sharing()
            return False
        fd = node = None
        if self._portal is not None:
            try:
                fd = self._portal.reopen_fd()
                node = self._portal.node_id
            except Exception as exc:
                self._status.set_label(f"sessão de captura fechou: {exc}")
                self._stop_sharing()
                return False

        cfg = config.load()
        try:
            self._capture = CapturePipeline(
                whip_endpoint=config.whip_endpoint(name),
                width=cfg["width"],
                height=cfg["height"],
                fps=cfg["fps"],
                bitrate_kbps=cfg["bitrate"],
                x11=self._x11,
                x11_region=self._x11_region,
                portal_fd=fd,
                portal_node=node,
                monitor_handle=self._monitor_handle,
                window_handle=self._window_handle,
                preview_sink=self._make_preview_sink,
                audio_enabled=cfg["audio"],
                audio_bitrate_kbps=cfg["audio_bitrate"],
                mute_app=cfg.get("mute_app", ""),
                audio_device=cfg["audio_device"],
                auth_token=config.auth_token(),
                on_pipeline_error=self._on_share_error,
                gl_display=self._gl_display,
            )
            self._capture.start()
            enc_label = _ENCODER_LABELS[self._capture.encoder_kind]
            self._status.set_label(f"transmitindo como {name} ({enc_label})")
        except Exception as exc:
            self._status.set_label(f"erro ao reiniciar transmissão: {exc}")
            self._stop_sharing()

        return False

    def _pick_x11_monitor(self) -> bool:
        """Define _x11_region pelo monitor. True se já dá pra seguir; False se abriu o
        diálogo de escolha (a resposta volta por _on_share_clicked)."""
        monitors = Gdk.Display.get_default().get_monitors()
        found = [monitors.get_item(i) for i in range(monitors.get_n_items())]

        def region(mon):  # o ximagesrc usa pixels reais, o GDK devolve pixels lógicos
            g, s = mon.get_geometry(), mon.get_scale_factor()
            return (g.x * s, g.y * s, g.width * s, g.height * s)

        if len(found) <= 1:
            self._x11_region = region(found[0]) if found else None
            return True
        labels = [f"{m.get_connector() or m.get_model()} ({m.get_geometry().width}x{m.get_geometry().height})" for m in found]
        dialog = Gtk.AlertDialog(message="Qual monitor transmitir?", buttons=[*labels, "Cancelar"], cancel_button=len(labels))

        def chosen(dlg, result):
            try:
                idx = dlg.choose_finish(result)
            except GLib.Error:
                return
            if idx < len(found):
                self._x11_region = region(found[idx])
                self._on_share_clicked(self._share_btn)

        dialog.choose(self, None, chosen)
        return False

    def _pick_windows_source(self, on_pick) -> None:
        """Lista monitores e janelas abertas; on_pick(hmonitor, hwnd) recebe um dos dois (o outro é None)."""
        targets: list[tuple[int | None, int | None] | None] = []  # alinhado às linhas; None = cabeçalho
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)

        def add(text: str, target) -> None:
            label = Gtk.Label(label=text, xalign=0, ellipsize=Pango.EllipsizeMode.END, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
            row = Gtk.ListBoxRow(child=label, activatable=target is not None)
            if target is None:
                label.add_css_class("heading")
            listbox.append(row)
            targets.append(target)

        add("Monitores", None)
        for handle, text in list_monitors():
            add(text, (handle, None))
        windows = list_windows()
        if windows:
            add("Janelas", None)
        for hwnd, title in windows:
            add(title, (None, hwnd))

        dialog = Gtk.Window(title="O que transmitir?", transient_for=self, modal=True, default_width=440, default_height=480)
        scroll = Gtk.ScrolledWindow(child=listbox, vexpand=True)
        dialog.set_child(scroll)

        def activated(_box, row):
            target = targets[row.get_index()]
            dialog.close()
            if target is not None:
                on_pick(*target)

        listbox.connect("row-activated", activated)
        dialog.present()

    def _switch_windows_source(self, monitor: int | None, window: int | None) -> None:
        """Troca monitor/janela no Windows refazendo a captura (o d3d11screencapturesrc não troca de fonte em voo)."""
        self._monitor_handle, self._window_handle = monitor, window
        self._restart_count = 0
        self._status.set_label("trocando de tela…")
        self._preview.set_paintable(None)
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        gc.collect()
        GLib.timeout_add(800, self._do_restart_sharing)

    def _stop_sharing(self) -> None:
        self._restart_count = 0
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        if self._portal is not None:
            self._portal.close()
            self._portal = None
        self._x11 = False
        self._x11_region = None
        self._monitor_handle = self._window_handle = None
        self._preview.set_paintable(None)
        self._stats_label.set_label("")
        self._viewers_label.set_visible(False)
        self._last_stats.pop("share", None)
        self._last_enc = None
        # Mesmo gc do auto-restart em _on_share_error: libera o contexto
        # GL/EGL do pipeline antigo agora, e não quando o GC cíclico quiser —
        # clicar "Transmitir" logo depois de "Parar" criava o contexto novo
        # com o antigo ainda vivo (EGL_BAD_CONTEXT na NVIDIA).
        gc.collect()
        self._share_btn.set_label("Transmitir")
        self._share_btn.remove_css_class("stop")
        self._share_btn.add_css_class("go")
        self.remove_css_class("live")
        self._switch_btn.set_visible(False)

    # --- assistir ---

    def _toggle_watch(self, name: str, button: Gtk.Button) -> None:
        if name in self._watches:
            self._stop_watch(name)
            return

        sink = Gst.ElementFactory.make("gtk4paintablesink", None)
        if sink is None:
            self._status.set_label("falta o pacote gst-plugin-gtk4 para exibir o vídeo")
            return

        watch = WatchPipeline(
            whep_endpoint=config.whep_endpoint(name),
            auth_token=config.auth_token(),
            on_pipeline_error=lambda msg, n=name: self._on_watch_error(n, msg),
            video_sink=sink,
        )
        watch.set_volume(self._volumes.get(name, 1.0))
        watch.set_muted(self._muted.get(name, False))
        # Uma janela só pra todas as transmissões (grade), em vez de uma por pessoa.
        if self._viewer is None:
            self._viewer = ViewerWindow(self._on_volume_changed, self._on_mute_toggled, self._stop_watch, self._on_viewer_closed)
        self._watch_stats[name] = self._viewer.add(
            name, sink.get_property("paintable"), self._volumes.get(name, 1.0), self._muted.get(name, False)
        )
        self._watches[name] = watch
        watch.start()
        self._tell_watching(name, True)
        self._viewer.present()
        self._refresh_roster()

    def _stop_watch(self, name: str) -> None:
        watch = self._watches.pop(name, None)
        self._forget_stats(name)
        if watch is not None:
            watch.stop()
            self._tell_watching(name, False)
        if self._viewer is not None:
            self._viewer.remove(name)  # fecha a janela quando era a última tela
        self._refresh_roster()

    def _on_viewer_closed(self) -> None:
        """O 'X' da janela de vídeo para todas as transmissões que ainda estavam nela."""
        self._viewer = None
        for name in list(self._watches):
            self._stop_watch(name)

    def _forget_stats(self, key: str) -> None:
        self._watch_stats.pop(key, None)
        self._last_stats.pop(key, None)

    def _poll_stats(self) -> bool:
        """A cada 1s mostra pacotes/bitrate/perda de quem transmite e de cada transmissão assistida."""
        def ask(key: str, element, arrow: str, show) -> None:
            def done(now):
                if now is None:
                    return
                line = stats.format_line(arrow, now, self._last_stats.get(key))
                self._last_stats[key] = now
                GLib.idle_add(show, line)
            stats.request(element, done)

        if self._capture is not None:
            ask("share", self._capture.pipeline.get_by_name("whip"), "↑", self._show_share_stats)
        self._ping_tick += 1
        if self._ping_tick % 10 == 0:  # servidor tira quem some por 30 s da lista de espectadores
            for name in self._watches:
                self._tell_watching(name, True)
        for name, watch in self._watches.items():
            label = self._watch_stats.get(name)
            if label is not None:
                ask(name, watch.pipeline.get_by_name("src"), "↓", label.set_label)
        return True

    def _show_share_stats(self, line: str) -> None:
        """Soma resolução e fps medidos na saída do encoder: é o que prova que a config vale."""
        cap = self._capture
        if cap is not None and cap.encoded_size is not None:
            now = (cap, cap.encoded_frames, time.monotonic())
            before = self._last_enc
            # janela de ~3 s: a de 1 s oscila ±5 fps só pelo ritmo de entrega dos quadros
            if before is None or before[0] is not cap or now[2] - before[2] > 3:
                self._last_enc, self._fps_shown = now, (
                    (now[1] - before[1]) / (now[2] - before[2]) if before and before[0] is cap else 0.0
                )
            fps = self._fps_shown
            w, h = cap.encoded_size
            line += f" · {w}×{h} · {fps:.0f} fps"
        self._stats_label.set_label(line)

    @staticmethod
    def _run_in_thread(work, done) -> None:
        """work() fora da thread da GUI; done(resultado, erro) de volta nela."""
        def run():
            try:
                result, error = work(), None
            except Exception as exc:  # sem rede / GitHub fora do ar / limite da API
                result, error = None, exc
            GLib.idle_add(done, result, error)
        threading.Thread(target=run, daemon=True).start()

    def _on_update_found(self, found, error) -> None:
        if error is not None or found is None:  # na checagem, falhar em silêncio: não atrapalha quem só quer transmitir
            return
        self._update = found
        self._update_btn.set_label(f"Nova versão {found[0]} disponível · Atualizar agora")
        self._update_btn.set_visible(True)

    def _on_update_clicked(self, _btn) -> None:
        self._update_btn.set_sensitive(False)
        if updater.is_flatpak():
            self._update_btn.set_label("Atualizando… 0%")
            updater.flatpak_update(
                lambda p: self._update_btn.set_label(f"Atualizando… {p}%"),
                self._on_flatpak_updated,
            )
            return
        _version, url, sha256 = self._update
        self._update_btn.set_label("Baixando… 0%")
        self._run_in_thread(
            lambda: updater.download(url, sha256, lambda f: GLib.idle_add(self._update_btn.set_label, f"Baixando… {f:.0%}")),
            self._on_update_downloaded,
        )

    def _on_flatpak_updated(self, error) -> None:
        if error is not None:
            self._update_btn.set_label(f"Falha ao atualizar ({error}) · Tentar de novo")
            self._update_btn.set_sensitive(True)
            return
        self._update_btn.set_label("Atualizado! Reiniciando…")
        GLib.timeout_add_seconds(1, updater.restart_flatpak)

    def _on_update_downloaded(self, installer, error) -> None:
        if error is not None:
            self._update_btn.set_label(f"Não consegui baixar ({error}) · Tentar de novo")
            self._update_btn.set_sensitive(True)
            return
        updater.run_installer(installer)  # fecha o app e abre de novo quando terminar
        self.get_application().quit()

    def _on_close_request(self, _win) -> bool:
        self.set_visible(False)
        return True

    def quit_app(self) -> None:
        self._on_stop_clicked()
        for w in list(self._watches.values()):
            w.stop()
        if self._viewer:
            self._viewer.close()
        if self._tray:
            self._tray.close()
        self.get_application().quit()

    def _tell_watching(self, name: str, on: bool) -> None:
        """Avisa o servidor (fora da thread da GUI) que estou assistindo `name`."""
        cfg = config.load()

        def run():
            try:
                roster.watching(cfg["base_url"], name, cfg["name"], config.auth_token(), on)
            except Exception:  # servidor sem a rota ou rede caída: só some da lista de espectadores
                pass
        threading.Thread(target=run, daemon=True).start()

    def _on_watch_error(self, name: str, message: str) -> None:
        self._status.set_label(f"assistir {name} caiu: {message}")
        self._stop_watch(name)

    def _show_viewers(self, who: list[str]) -> None:
        self._viewers_label.set_visible(self._capture is not None)
        self._viewers_label.set_label(
            f"👁 {len(who)} assistindo: {', '.join(who)}" if who else "👁 ninguém assistindo ainda"
        )

    def _refresh_roster(self) -> bool:
        cfg = config.load()
        for child in list(self._roster_box):
            self._roster_box.remove(child)
        try:
            live = roster.list_paths(cfg["base_url"], config.auth_token())
            names = [n for n in live if n != cfg["name"]]
        except Exception as exc:
            # Falha de rede vira mensagem na própria lista: antes virava lista
            # vazia indistinguível de "ninguém no ar", que foi o que escondeu
            # um 403 do Cloudflare.
            self._roster_box.append(
                Gtk.Label(label=f"servidor não respondeu: {exc}", xalign=0, wrap=True)
            )
            return True
        # Duas leituras seguidas sem ele: quem reinicia a transmissão some do roster por instantes.
        self._missed = {n: self._missed.get(n, 0) + 1 for n in self._watches if n not in live}
        gone = [n for n, k in self._missed.items() if k >= 2]
        if gone:  # quem eu assistia parou: o servidor tira do roster, o vídeo só congelaria
            for n in gone:
                self._stop_watch(n)  # fecha só a caixa dele (a janela fecha se era a última)
            self._status.set_label(f"{', '.join(gone)} parou de transmitir")
            return True  # o _stop_watch já redesenhou a lista
        self._show_viewers(live.get(cfg["name"], []))
        if not names:
            self._roster_box.append(Gtk.Label(label="Ninguém no ar. Que tal abrir a sala?", xalign=0, margin_top=6, css_classes=["dim"]))
        for name in names:
            row = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            row.append(Gtk.Label(label=name[:1].upper(), css_classes=["avatar"], width_chars=2))
            row.append(Gtk.Label(label=name, xalign=0, hexpand=True))
            row.append(Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["on-air"]))
            watch = self._watches.get(name)
            button = Gtk.Button(label="Parar" if watch is not None else "Assistir")
            button.connect("clicked", lambda _b, n=name, btn=button: self._toggle_watch(n, btn))
            row.append(button)
            self._roster_box.append(row)
        return True

    def _on_mute_toggled(self, name: str, muted: bool) -> None:
        self._muted[name] = muted
        watch = self._watches.get(name)
        if watch is not None:
            watch.set_muted(muted)

    def _on_volume_changed(self, name: str, value: float) -> None:
        self._volumes[name] = value
        watch = self._watches.get(name)
        if watch is not None:
            watch.set_volume(value)


class LuceApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="pro.codestage.Luce")
        self._held = False
        self._main_window: MainWindow | None = None
        if WINDOWS:
            self.connect("window-added", lambda _a, win: win.connect("map", lambda _w: GLib.idle_add(apply_icon)))

    def do_activate(self) -> None:
        if not self._held:
            self.hold()
            self._held = True
        if self._main_window is None:
            self._main_window = MainWindow(self)
        self._main_window.present()


def main() -> int:
    return LuceApp().run(None)
