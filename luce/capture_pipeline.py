"""Captura + encode + publish via WHIP. Portado de golive-linux/golive/media.py.

Só existe um destino (o MediaMTX), então a cadeia é linear — sem o `tee`
por-espectador do protótipo original, que servia pra abrir um `webrtcbin`
por pessoa (mesh). O WHIP delega toda a negociação SDP/ICE pro `whipsink`.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

log = logging.getLogger("luce.capture")

Gst.init(None)


def _try_set(element: Gst.Element, name: str, value) -> None:
    if element.find_property(name) is None:
        return
    try:
        element.set_property(name, value)
    except Exception as exc:
        log.debug("%s=%r ignorado: %s", name, value, exc)


def _make_encoder() -> tuple[Gst.Element, str]:
    """NVENC primeiro; sem NVIDIA, cai pro VAAPI (AMD/Intel via gst-plugin-va).

    shortcut: o ramo VAAPI nunca rodou num encoder VAAPI de verdade (esta
    máquina só tem NVIDIA — o driver VA dela cobre decode, não encode); só a
    construção do pipeline foi validada. Se os nomes de propriedade abaixo
    estiverem errados, _try_set ignora e cai nos defaults do driver.
    """
    enc = Gst.ElementFactory.make("nvh264enc", "enc")
    if enc is not None:
        print("codificando com NVENC (GPU NVIDIA)", flush=True)
        return enc, "nvenc"
    enc = Gst.ElementFactory.make("vah264enc", "enc")
    if enc is not None:
        print("codificando com VAAPI (GPU AMD/Intel)", flush=True)
        return enc, "va"
    raise RuntimeError(
        "faltam nvh264enc (NVIDIA, gst-plugins-bad com nvcodec) e "
        "vah264enc (AMD/Intel, gst-plugin-va)"
    )


class CapturePipeline:
    """Captura (portal ou padrão de teste) e publica H.264 num endpoint WHIP."""

    def __init__(
        self,
        whip_endpoint: str,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        bitrate_kbps: int = 4000,
        pattern: bool = False,
        portal_fd: int | None = None,
        portal_node: int | None = None,
        preview: bool = True,
        preview_sink: Gst.Element | None = None,
        audio_enabled: bool = True,
        audio_bitrate_kbps: int = 128,
        auth_token: str | None = None,
        on_pipeline_error=None,
    ) -> None:
        self._on_pipeline_error = on_pipeline_error
        self._fps = fps
        self._pattern = pattern
        self._frames = 0
        self._reported = -1
        self._last_pts = 0
        self.pipeline = Gst.Pipeline.new("luce-capture")

        if pattern:
            src = Gst.ElementFactory.make("videotestsrc", "src")
            src.set_property("is-live", True)
        else:
            src = Gst.ElementFactory.make("pipewiresrc", "src")
            src.set_property("fd", int(portal_fd))
            # O portal entrega o node.id. target-object é o serial do PipeWire;
            # repetir o id aí faz o WirePlumber responder "target not found".
            src.set_property("path", str(portal_node))
            src.set_property("do-timestamp", True)
            _try_set(src, "provide-clock", False)
            _try_set(src, "client-name", "ProjectLUCE")

        rate = Gst.ElementFactory.make("videorate", "rate")
        _try_set(rate, "drop-only", True)
        _try_set(rate, "skip-to-first", True)
        _try_set(rate, "qos", False)
        caps = Gst.ElementFactory.make("capsfilter", "caps")
        enc, enc_kind = _make_encoder()
        self.encoder_kind = enc_kind

        if pattern:
            raw_preview = False
            convert = Gst.ElementFactory.make("videoconvert", "convert")
            scale = Gst.ElementFactory.make("videoscale", "scale")
            front: list[Gst.Element] = [src, convert, scale, rate]
            caps.set_property(
                "caps",
                Gst.Caps.from_string(
                    f"video/x-raw,format=NV12,width={width},height={height},framerate={fps}/1"
                ),
            )
        else:
            # O portal manda DMA-BUF e todos os quadros chegam com o mesmo
            # PTS; sem remarcar, o videorate joga fora tudo depois do
            # primeiro e a imagem congela.
            src.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._restamp)
            if enc_kind == "nvenc":
                # glupload importa o buffer da placa e o NVENC lê a textura
                # GL direto: a CPU não toca no pixel.
                upload = Gst.ElementFactory.make("glupload", "upload")
                color = Gst.ElementFactory.make("glcolorconvert", "glcolor")
                scale = Gst.ElementFactory.make("glcolorscale", "glscale")
                if upload is None or color is None or scale is None:
                    raise RuntimeError("faltam os elementos GL (pacote gst-plugins-base com OpenGL)")
                post_chain = [upload, color, scale]
                front_caps = (
                    "video/x-raw(memory:GLMemory),format=RGBA,"
                    f"width={width},height={height},framerate={fps}/1"
                )
            else:
                # vapostproc faz o convert/scale em cima do DMA-BUF sem
                # tocar em GL, pro vah264enc (AMD/Intel) consumir direto.
                post = Gst.ElementFactory.make("vapostproc", "vapost")
                if post is None:
                    raise RuntimeError("falta vapostproc (pacote gst-plugin-va)")
                post_chain = [post]
                front_caps = f"video/x-raw(memory:DMABuf),width={width},height={height},framerate={fps}/1"
            front = [src, *post_chain, rate]
            # Preview embutida na GUI (preview_sink = gtk4paintablesink): o
            # GTK roda em Vulkan por padrão, e textura GL exige um bridge
            # GL->Vulkan que travou e derrubou o contexto GL da captura (ver
            # git). DMA-BUF o Vulkan importa nativo, sem GL no meio — então
            # esse ramo desvia direto da saída crua do portal, antes do
            # convert/scale. shortcut: não validado ainda com captura real (só
            # dá pra testar escolhendo o monitor de verdade); se travar de
            # novo, a correção anterior era forçar GSK_RENDERER=gl.
            raw_preview = preview and preview_sink is not None
            if raw_preview:
                raw_tee = Gst.ElementFactory.make("tee", "raw-tee")
                raw_queue = Gst.ElementFactory.make("queue", "raw-enc-queue")
                front = [src, raw_tee, raw_queue, *post_chain, rate]
            caps.set_property("caps", Gst.Caps.from_string(front_caps))

        if enc_kind == "nvenc":
            _try_set(enc, "bitrate", bitrate_kbps)
            _try_set(enc, "gop-size", fps * 2)
            _try_set(enc, "zerolatency", True)
            _try_set(enc, "rc-mode", "cbr")
            _try_set(enc, "preset", "p4")
            _try_set(enc, "tune", "ultra-low-latency")
            _try_set(enc, "bframes", 0)
            _try_set(enc, "cabac", False)
        else:
            _try_set(enc, "bitrate", bitrate_kbps)
            _try_set(enc, "rate-control", "cbr")
            _try_set(enc, "key-int-max", fps * 2)
            _try_set(enc, "b-frames", 0)
            _try_set(enc, "target-usage", 6)
        h264caps = Gst.ElementFactory.make("capsfilter", "h264caps")
        h264caps.set_property(
            "caps",
            Gst.Caps.from_string(
                "video/x-h264,profile=constrained-baseline,stream-format=byte-stream,alignment=au"
            ),
        )
        parse = Gst.ElementFactory.make("h264parse", "parse")
        _try_set(parse, "config-interval", -1)
        queue = Gst.ElementFactory.make("queue", "queue")
        _try_set(queue, "max-size-buffers", 3)
        _try_set(queue, "leaky", "downstream")
        pay = Gst.ElementFactory.make("rtph264pay", "pay")
        _try_set(pay, "pt", 96)
        _try_set(pay, "config-interval", -1)
        _try_set(pay, "aggregate-mode", "zero-latency")
        whip = Gst.ElementFactory.make("whipsink", "whip")
        if whip is None:
            raise RuntimeError("whipsink não está instalado (gst-plugin-webrtchttp)")
        whip.set_property("whip-endpoint", whip_endpoint)
        if auth_token:
            _try_set(whip, "auth-token", auth_token)

        chain = [*front, caps]
        for element in chain:
            self.pipeline.add(element)
        for left, right in zip(chain, chain[1:]):
            if not left.link(right):
                raise RuntimeError(f"não liguei {left.get_name()} em {right.get_name()}")

        if raw_preview:
            prev_queue = Gst.ElementFactory.make("queue", "preview-queue")
            _try_set(prev_queue, "max-size-buffers", 2)
            _try_set(prev_queue, "leaky", "downstream")
            _try_set(preview_sink, "sync", False)
            self.pipeline.add(prev_queue)
            self.pipeline.add(preview_sink)
            if not raw_tee.link(prev_queue) or not prev_queue.link(preview_sink):
                raise RuntimeError("não liguei o preview bruto (DMA-BUF) no tee")

        # Preview local: quem transmite não via a própria tela até agora
        # (feedback do usuário). `tee` logo após as caps — antes do encode —
        # manda um ramo pro NVENC/WHIP (inalterado) e outro pra um preview.
        # `preview_sink` (gtk4paintablesink) embute na janela principal da
        # GUI; sem ele (uso via CLI), cai numa janela GL própria — glimagesink
        # lê direto da memória GL, só o caminho --pattern (memória comum)
        # passa por videoconvert. Caminho raw_preview (acima) já cuidou do
        # split quando é a preview embutida real (GL->Vulkan era o problema).
        encode_src = caps
        if preview and not raw_preview:
            tee = Gst.ElementFactory.make("tee", "tee")
            enc_queue = Gst.ElementFactory.make("queue", "enc-queue")
            prev_queue = Gst.ElementFactory.make("queue", "preview-queue")
            _try_set(prev_queue, "max-size-buffers", 2)
            _try_set(prev_queue, "leaky", "downstream")
            if preview_sink is not None:
                prev_tail = [preview_sink]
            elif pattern:
                prev_tail = [
                    Gst.ElementFactory.make("videoconvert", "preview-convert"),
                    Gst.ElementFactory.make("autovideosink", "preview-sink"),
                ]
            else:
                prev_tail = [Gst.ElementFactory.make("glimagesink", "preview-sink")]
            _try_set(prev_tail[-1], "sync", False)
            for element in (tee, enc_queue, prev_queue, *prev_tail):
                self.pipeline.add(element)
            if not caps.link(tee) or not tee.link(enc_queue) or not tee.link(prev_queue):
                raise RuntimeError("não liguei o tee do preview")
            preview_chain = (prev_queue, *prev_tail)
            for left, right in zip(preview_chain, preview_chain[1:]):
                if not left.link(right):
                    raise RuntimeError(f"não liguei {left.get_name()} em {right.get_name()}")
            encode_src = enc_queue

        encode_chain = (encode_src, enc, h264caps, parse, queue, pay, whip)
        for element in (enc, h264caps, parse, queue, pay, whip):
            self.pipeline.add(element)
        for left, right in zip(encode_chain, encode_chain[1:]):
            if not left.link(right):
                raise RuntimeError(f"não liguei {left.get_name()} em {right.get_name()}")

        # Áudio sempre é publicado (mudo ou não) num 2º sink_% do whipsink: o
        # whepsrc de quem assiste tem um bug ao montar o m-line de Opus
        # quando o publisher não manda áudio nenhum, e o MediaMTX rejeita a
        # oferta toda com "codecs not supported by client" (ver watch_pipeline.py).
        # "transmitir áudio ou não" é então mute no `volume`, não a decisão de
        # montar o ramo.
        if pattern:
            audio_src = Gst.ElementFactory.make("audiotestsrc", "audio-src")
            audio_src.set_property("is-live", True)
        else:
            audio_src = Gst.ElementFactory.make("pulsesrc", "audio-src")
            # client-name é o que aparece no qpwgraph/pavucontrol pra rotear
            # o áudio; funciona tanto em PulseAudio quanto em PipeWire (via
            # pipewire-pulse), sem código separado pros dois.
            _try_set(audio_src, "client-name", "Project LUCE")
        audio_convert = Gst.ElementFactory.make("audioconvert", "audio-convert")
        audio_resample = Gst.ElementFactory.make("audioresample", "audio-resample")
        self._audio_volume = Gst.ElementFactory.make("volume", "audio-volume")
        self._audio_volume.set_property("mute", not audio_enabled)
        audio_enc = Gst.ElementFactory.make("opusenc", "audio-enc")
        _try_set(audio_enc, "bitrate", audio_bitrate_kbps * 1000)
        _try_set(audio_enc, "audio-type", "restricted-lowdelay")
        audio_pay = Gst.ElementFactory.make("rtpopuspay", "audio-pay")
        _try_set(audio_pay, "pt", 97)
        audio_chain = (audio_src, audio_convert, audio_resample, self._audio_volume, audio_enc, audio_pay)
        for element in audio_chain:
            self.pipeline.add(element)
        for left, right in zip(audio_chain, audio_chain[1:]):
            if not left.link(right):
                raise RuntimeError(f"não liguei {left.get_name()} em {right.get_name()}")
        audio_sink_pad = whip.request_pad(whip.get_pad_template("sink_%u"), None, None)
        if audio_pay.get_static_pad("src").link(audio_sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("não liguei o áudio no whip")

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

    def start(self) -> bool:
        self.pipeline.set_state(Gst.State.PLAYING)
        GLib.timeout_add_seconds(2, self._report_frames)
        return False

    def stop(self) -> None:
        self.pipeline.set_state(Gst.State.NULL)
        # Espera o NULL de verdade terminar (o contexto GL/EGL da NVIDIA é
        # destruído numa thread própria, fora do set_state); sem isso, um
        # "Transmitir" rápido em seguida cria o contexto novo enquanto o
        # antigo ainda está sendo liberado, e a NVIDIA devolve EGL_BAD_CONTEXT.
        self.pipeline.get_state(Gst.CLOCK_TIME_NONE)

    def set_audio_enabled(self, enabled: bool) -> None:
        self._audio_volume.set_property("mute", not enabled)

    def _restamp(self, _pad, info):
        """Cada quadro do portal recebe o tempo corrente do pipeline.

        O pipewiresrc entrega todos com o mesmo PTS. O buffer é recém-criado
        pelo próprio pipewiresrc, então o carimbo é escrito nele mesmo: o
        DMA-BUF não aceita cópia.
        """
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK
        try:
            step = Gst.SECOND // max(self._fps, 1)
            clock = self.pipeline.get_clock()
            base = self.pipeline.get_base_time()
            if clock is not None and base != Gst.CLOCK_TIME_NONE:
                running = clock.get_time() - base
            else:
                running = self._last_pts + step
            if running <= self._last_pts:
                running = self._last_pts + 1
            self._last_pts = running
            buf.pts = running
            buf.dts = Gst.CLOCK_TIME_NONE
            buf.duration = step
            self._frames += 1
        except Exception as exc:
            if not getattr(self, "_restamp_warned", False):
                print(f"aviso do vídeo: {exc}", flush=True)
                self._restamp_warned = True
        return Gst.PadProbeReturn.OK

    def _report_frames(self) -> bool:
        if self._frames != self._reported:
            print(f"captura: {self._frames} frames", flush=True)
            self._reported = self._frames
        return True

    def _on_error(self, _bus, message) -> None:
        err, debug = message.parse_error()
        print(f"pipeline parou: {err.message}", flush=True)
        log.error("pipeline: %s (%s)", err.message, debug)
        if self._on_pipeline_error is not None:
            self._on_pipeline_error(err.message)

    def _on_warning(self, _bus, message) -> None:
        warn, _debug = message.parse_warning()
        print(f"aviso do vídeo: {warn.message}", flush=True)
