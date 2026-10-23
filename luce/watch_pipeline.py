"""Recebe a tela (e o áudio) via WHEP e mostra/toca. Simétrico ao capture_pipeline.py.

Não existia no protótipo golive-linux (que só publicava). O `whepsrc` só cria
os pads depois de negociar com o servidor (availability "Sometimes"), então o
resto da cadeia é montado no sinal `pad-added`. Os dois pads (áudio e vídeo)
se chamam genericamente `src_%u` — não dá pra saber qual é qual pelo nome, só
pelas caps depois de negociadas. Por isso cada pad novo passa por um
`parsebin` (separa/parseia sem decodificar) e só então olhamos o nome da
caps resultante pra montar a cadeia certa.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

log = logging.getLogger("luce.watch")

Gst.init(None)


def _try_set(element: Gst.Element, name: str, value) -> None:
    if element.find_property(name) is None:
        return
    try:
        element.set_property(name, value)
    except Exception as exc:
        log.debug("%s=%r ignorado: %s", name, value, exc)


def _make_decoder() -> Gst.Element:
    """NVDEC primeiro; sem NVIDIA, cai pro VAAPI (AMD/Intel via gst-plugin-va).

    shortcut: igual ao _make_encoder de capture_pipeline.py, nunca testado
    decodificando de verdade num driver VAAPI (esta máquina só tem NVIDIA).
    """
    dec = Gst.ElementFactory.make("nvh264dec", "dec")
    if dec is not None:
        print("decodificando com NVDEC (GPU NVIDIA)", flush=True)
        return dec
    dec = Gst.ElementFactory.make("vah264dec", "dec")
    if dec is not None:
        print("decodificando com VAAPI (GPU AMD/Intel)", flush=True)
        return dec
    raise RuntimeError(
        "faltam nvh264dec (NVIDIA, gst-plugins-bad com nvcodec) e "
        "vah264dec (AMD/Intel, gst-plugin-va)"
    )


class WatchPipeline:
    """Assiste a tela de um publisher WHIP através do relay (WHEP)."""

    def __init__(self, whep_endpoint: str, auth_token: str | None = None, on_pipeline_error=None) -> None:
        self._on_pipeline_error = on_pipeline_error
        self._volume: Gst.Element | None = None
        # O pad de áudio só aparece depois de negociar com o WHEP; guarda o
        # valor pedido e aplica quando _wire_audio criar o elemento de verdade.
        self._wanted_volume = 1.0
        self._wanted_muted = False
        self.pipeline = Gst.Pipeline.new("luce-watch")
        src = Gst.ElementFactory.make("whepsrc", "src")
        if src is None:
            raise RuntimeError("whepsrc não está instalado (gst-plugin-webrtchttp)")
        src.set_property("whep-endpoint", whep_endpoint)
        if auth_token:
            _try_set(src, "auth-token", auth_token)
        self.pipeline.add(src)
        src.connect("pad-added", self._on_pad_added)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

    def start(self) -> None:
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        bus = self.pipeline.get_bus()
        # Mesmo motivo do CapturePipeline.stop(): quebra o ciclo self <-> bus
        # <-> pipeline antes de liberar o contexto GL do NVDEC/VAAPI, senão
        # só o GC cíclico (em momento imprevisível) liberaria o antigo.
        bus.disconnect_by_func(self._on_error)
        bus.disconnect_by_func(self._on_warning)
        bus.remove_signal_watch()
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_state(Gst.CLOCK_TIME_NONE)

    def set_volume(self, value: float) -> None:
        self._wanted_volume = value
        if self._volume is not None:
            self._volume.set_property("volume", value)

    def set_muted(self, muted: bool) -> None:
        self._wanted_muted = muted
        if self._volume is not None:
            self._volume.set_property("mute", muted)

    def _on_pad_added(self, _element, pad) -> None:
        pbin = Gst.ElementFactory.make("parsebin", None)
        self.pipeline.add(pbin)
        pbin.sync_state_with_parent()
        pbin.connect("pad-added", self._on_parsed_pad)
        if pad.link(pbin.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print("o WHEP não encaixou no parsebin", flush=True)

    def _on_parsed_pad(self, _pbin, pad) -> None:
        caps = pad.get_current_caps()
        if caps is not None and caps.get_size() > 0:
            self._wire_parsed_pad(pad, caps.get_structure(0).get_name())
            return

        # Áudio (Opus) costuma resolver na hora; vídeo (H.264) só resolve um
        # pouco depois, via notify::caps — não dá pra decidir aqui ainda.
        def _on_notify(p, _pspec):
            c = p.get_property("caps")
            if c is None or c.get_size() == 0:
                return
            p.disconnect_by_func(_on_notify)
            self._wire_parsed_pad(p, c.get_structure(0).get_name())

        pad.connect("notify::caps", _on_notify)

    def _wire_parsed_pad(self, pad, media_name: str) -> None:
        if media_name == "video/x-h264":
            self._wire_video(pad)
        elif media_name == "audio/x-opus":
            self._wire_audio(pad)
        else:
            print(f"caps inesperada do WHEP: {media_name}", flush=True)

    def _wire_video(self, pad) -> None:
        dec = _make_decoder()
        sink = Gst.ElementFactory.make("glimagesink", "sink-video")
        _try_set(sink, "sync", True)
        chain = (dec, sink)
        for element in chain:
            self.pipeline.add(element)
        if not dec.link(sink):
            print(f"não liguei {dec.get_name()} em {sink.get_name()}", flush=True)
            return
        if pad.link(dec.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print("o vídeo não encaixou no decode", flush=True)
            return
        for element in chain:
            element.sync_state_with_parent()
        print("recebendo vídeo", flush=True)

    def _wire_audio(self, pad) -> None:
        dec = Gst.ElementFactory.make("opusdec", "dec-audio")
        convert = Gst.ElementFactory.make("audioconvert", "convert-audio")
        resample = Gst.ElementFactory.make("audioresample", "resample-audio")
        # Opus do WHEP sempre chega em stereo; sem travar isso aqui, a saída
        # negocia com as caps do dispositivo PADRÃO do sistema (7.1 surround,
        # 8 canais nesta máquina) e faz upmix sem motivo — o nó real no
        # PipeWire então aparece com 8 entradas + 8 monitores de verdade
        # (visto no qpwgraph), em vez de um stereo normal de 2 portas.
        stereo_caps = Gst.ElementFactory.make("capsfilter", "stereo-caps-audio")
        stereo_caps.set_property("caps", Gst.Caps.from_string("audio/x-raw,channels=2"))
        volume = Gst.ElementFactory.make("volume", "volume-audio")
        volume.set_property("volume", self._wanted_volume)
        volume.set_property("mute", self._wanted_muted)
        sink = Gst.ElementFactory.make("autoaudiosink", "sink-audio")
        _try_set(sink, "sync", True)
        # autoaudiosink é um bin: client-name não existe nele, só no sink real
        # (pulsesink/pipewiresink) que ele cria sozinho ao ir pra PLAYING —
        # daí aparecer "python" em vez de "Project LUCE" no qpwgraph/pavucontrol.
        sink.connect("child-added", lambda _bin, child, _name: _try_set(child, "client-name", "Project LUCE"))
        self._volume = volume
        chain = (dec, convert, resample, stereo_caps, volume, sink)
        for element in chain:
            self.pipeline.add(element)
        for left, right in zip(chain, chain[1:]):
            if not left.link(right):
                print(f"não liguei {left.get_name()} em {right.get_name()}", flush=True)
                return
        if pad.link(dec.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print("o áudio não encaixou no decode", flush=True)
            return
        for element in chain:
            element.sync_state_with_parent()
        print("recebendo áudio", flush=True)

    def _on_error(self, _bus, message) -> None:
        err, debug = message.parse_error()
        print(f"pipeline parou: {err.message}", flush=True)
        log.error("pipeline: %s (%s)", err.message, debug)
        if self._on_pipeline_error is not None:
            self._on_pipeline_error(err.message)

    def _on_warning(self, _bus, message) -> None:
        warn, _debug = message.parse_warning()
        print(f"aviso do vídeo: {warn.message}", flush=True)
