"""Captura + encode + publish via WHIP. Portado de golive-linux/golive/media.py.

Só existe um destino (o MediaMTX), então a cadeia é linear — sem o `tee`
por-espectador do protótipo original, que servia pra abrir um `webrtcbin`
por pessoa (mesh). O WHIP delega toda a negociação SDP/ICE pro `whipsink`.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("GstGL", "1.0")
from gi.repository import GLib, Gst, GstGL, GstVideo

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
        audio_device: str | None = None,
        auth_token: str | None = None,
        on_pipeline_error=None,
        gl_display: GstGL.GLDisplay | None = None,
    ) -> None:
        self._on_pipeline_error = on_pipeline_error
        self._gl_display = gl_display
        self._fps = fps
        self._pattern = pattern
        self._frames = 0
        self._reported = -1
        self._last_pts = 0
        self._src_width: int | None = None
        self._src_height: int | None = None
        self._report_id: int | None = None
        self._probe_id: int | None = None
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
            # shortcut: capturando uma janela específica, se ela for redimensionada
            # no meio da transmissão o pipewiresrc derruba o pipeline com
            # "gst_video_frame_map_id: assertion 'info->width <= meta->width'
            # failed" (o buffer novo chega menor do que o GstVideoInfo negociado no
            # início, sem renegociação de caps em cima do DMA-BUF). Sem fix aqui —
            # é a reconexão com retry da Fase 4 do README que resolveria de verdade.

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
            self._probe_id = src.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._restamp)
            # width/height=0 é a opção "Fonte" das Configurações: transmite
            # no tamanho nativo de quem foi capturado, sem forçar nenhuma
            # resolução (nem letterbox, que só existe pra caber num tamanho
            # fixo — aqui não tem tamanho fixo nenhum).
            native = not width or not height
            size_caps = "" if native else f"width={width},height={height},"
            if enc_kind == "nvenc":
                # glupload importa o buffer da placa e o NVENC lê a textura
                # GL direto: a CPU não toca no pixel.
                upload = Gst.ElementFactory.make("glupload", "upload")
                color = Gst.ElementFactory.make("glcolorconvert", "glcolor")
                if upload is None or color is None:
                    raise RuntimeError("faltam os elementos GL (pacote gst-plugins-base com OpenGL)")
                mixer = None
                post_chain = [upload, color]
                if not native:
                    # glcolorscale (usado antes) sempre estica pro tamanho
                    # exato do caps, sem olhar pro aspect ratio da fonte —
                    # capturando uma janela específica (raramente 16:9) a
                    # imagem saía esticada pra quem assiste. glvideomixer com
                    # sizing-policy=keep-aspect-ratio faz letterbox de
                    # verdade (barras pretas, sem distorcer); confirmado com
                    # teste real de pipeline (fonte 300x600 -> alvo 640x480,
                    # caps negociada certa, sem erro).
                    mixer = Gst.ElementFactory.make("glvideomixer", "glmixer")
                    if mixer is None:
                        raise RuntimeError("falta glvideomixer (pacote gst-plugins-base com OpenGL)")
                    _try_set(mixer, "background", "black")
                    post_chain.append(mixer)
                front_caps = f"video/x-raw(memory:GLMemory),format=RGBA,{size_caps}framerate={fps}/1"
            else:
                # vapostproc faz o convert/scale em cima do DMA-BUF sem
                # tocar em GL, pro vah264enc (AMD/Intel) consumir direto.
                post = Gst.ElementFactory.make("vapostproc", "vapost")
                if post is None:
                    raise RuntimeError("falta vapostproc (pacote gst-plugin-va)")
                post_chain = [post]
                front_caps = f"video/x-raw(memory:DMABuf),{size_caps}framerate={fps}/1"
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

        if not pattern and enc_kind == "nvenc" and mixer is not None:
            # O pad de entrada do mixer só existe depois do link acima
            # (gst_element_link pede ele sozinho); aqui é que dá pra definir
            # o retângulo de destino (a tela toda) e o letterbox.
            for pad in mixer.sinkpads:
                pad.set_property("width", width)
                pad.set_property("height", height)
                _try_set(pad, "sizing-policy", "keep-aspect-ratio")

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
        audio_src_caps = None
        if pattern:
            audio_src = Gst.ElementFactory.make("audiotestsrc", "audio-src")
            audio_src.set_property("is-live", True)
        elif audio_device and audio_device.startswith("app:"):
            # Áudio de um app específico (Spotify, navegador, etc) em vez de
            # microfone: pipewiresrc com target-object=<serial> liga direto
            # nesse stream de playback (confirmado com nível real de áudio,
            # não só buffers chegando — alvo inválido cai silenciosamente pro
            # dispositivo padrão em vez de dar erro, então se o app fechar no
            # meio a transmissão não quebra, só volta a pegar o microfone).
            audio_src = Gst.ElementFactory.make("pipewiresrc", "audio-src")
            _try_set(audio_src, "target-object", audio_device[len("app:"):])
            # Sem isso o nó aparece no qpwgraph como "python3.14" (nome
            # genérico do processo) em vez de algo reconhecível.
            _try_set(audio_src, "client-name", "Project LUCE")
            audio_src_caps = Gst.ElementFactory.make("capsfilter", "audio-src-caps")
            audio_src_caps.set_property("caps", Gst.Caps.from_string("audio/x-raw,channels=2"))
        else:
            audio_src = Gst.ElementFactory.make("pulsesrc", "audio-src")
            # client-name é o que aparece no qpwgraph/pavucontrol pra rotear
            # o áudio; funciona tanto em PulseAudio quanto em PipeWire (via
            # pipewire-pulse), sem código separado pros dois.
            _try_set(audio_src, "client-name", "Project LUCE")
            if audio_device:
                _try_set(audio_src, "device", audio_device)
        audio_convert = Gst.ElementFactory.make("audioconvert", "audio-convert")
        audio_resample = Gst.ElementFactory.make("audioresample", "audio-resample")
        self._audio_volume = Gst.ElementFactory.make("volume", "audio-volume")
        self._audio_volume.set_property("mute", not audio_enabled)
        audio_enc = Gst.ElementFactory.make("opusenc", "audio-enc")
        _try_set(audio_enc, "bitrate", audio_bitrate_kbps * 1000)
        _try_set(audio_enc, "audio-type", "restricted-lowdelay")
        audio_pay = Gst.ElementFactory.make("rtpopuspay", "audio-pay")
        _try_set(audio_pay, "pt", 97)
        audio_chain = (
            (audio_src, audio_src_caps, audio_convert, audio_resample, self._audio_volume, audio_enc, audio_pay)
            if audio_src_caps is not None
            else (audio_src, audio_convert, audio_resample, self._audio_volume, audio_enc, audio_pay)
        )
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

        # NVENC usa GL (glupload/glcolorconvert) pra chegar no encoder sem
        # copiar pra CPU. Cada pipeline recriado (restart por troca de
        # resolução) pediria um GLDisplay novo nesse NEED_CONTEXT — e criar
        # EGL/Wayland display novo rápido demais depois do antigo é o que
        # parece causar o EGL_BAD_CONTEXT. Respondendo com o mesmo GLDisplay
        # (compartilhado pela GUI entre recriações) em vez de deixar o
        # GStreamer criar um novo, essa troca de EGL deixa de acontecer.
        if enc_kind == "nvenc" and self._gl_display is not None:
            bus.enable_sync_message_emission()
            bus.connect("sync-message::need-context", self._on_need_context)

    def start(self) -> bool:
        self.pipeline.set_state(Gst.State.PLAYING)
        self._report_id = GLib.timeout_add_seconds(2, self._report_frames)
        return False

    def stop(self) -> None:
        if self._report_id is not None:
            GLib.source_remove(self._report_id)
            self._report_id = None

        if self._probe_id is not None:
            src = self.pipeline.get_by_name("src")
            if src is not None:
                pad = src.get_static_pad("src")
                if pad is not None:
                    pad.remove_probe(self._probe_id)
            self._probe_id = None

        bus = self.pipeline.get_bus()
        # Desconecta os handlers do bus antes de mais nada: eles seguram uma
        # referência a self, e self segura o pipeline (que segura o bus) —
        # ciclo que só o GC cíclico do Python desfaria, em momento
        # imprevisível. Sem quebrar o ciclo aqui, um "Transmitir" rápido logo
        # após "Parar" cria o contexto GL/EGL novo antes do pipeline antigo
        # (e o contexto da NVIDIA que ele segura) serem liberados de verdade,
        # e a NVIDIA devolve EGL_BAD_CONTEXT.
        bus.disconnect_by_func(self._on_error)
        bus.disconnect_by_func(self._on_warning)
        if self.encoder_kind == "nvenc" and self._gl_display is not None:
            bus.disconnect_by_func(self._on_need_context)
        bus.remove_signal_watch()
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_state(Gst.CLOCK_TIME_NONE)

    def set_audio_enabled(self, enabled: bool) -> None:
        self._audio_volume.set_property("mute", not enabled)

    def switch_source(self, portal_fd: int, portal_node: int) -> None:
        """Troca o monitor/janela capturado sem derrubar o resto do pipeline.

        "Trocar tela" antes derrubava o CapturePipeline inteiro e criava
        outro — exatamente o "Transmitir logo após Parar" que o comentário
        em stop() descreve, e é isso que reimporta DMA-BUF num glupload/NVENC
        novo e dispara EGL_BAD_CONTEXT (visto mesmo com GLDisplay
        compartilhado). Aqui só o pipewiresrc é substituído; glupload,
        NVENC e whipsink nunca saem de PLAYING, então nunca pedem contexto
        GL de novo. shortcut: só serve pra um pipewiresrc já existente (não
        --pattern); não se aplica à reconexão automática de _on_share_error,
        que já recebeu um erro do bus e precisa recriar tudo mesmo.
        """
        if self._pattern:
            raise RuntimeError("switch_source não vale pra --pattern")
        old_src = self.pipeline.get_by_name("src")
        pad = old_src.get_static_pad("src")
        peer = pad.get_peer()
        if self._probe_id is not None:
            pad.remove_probe(self._probe_id)
            self._probe_id = None
        pad.unlink(peer)
        old_src.set_state(Gst.State.NULL)
        self.pipeline.remove(old_src)

        new_src = Gst.ElementFactory.make("pipewiresrc", "src")
        new_src.set_property("fd", int(portal_fd))
        new_src.set_property("path", str(portal_node))
        new_src.set_property("do-timestamp", True)
        _try_set(new_src, "provide-clock", False)
        _try_set(new_src, "client-name", "ProjectLUCE")
        self.pipeline.add(new_src)
        # Gst.Pad.link (diferente de Gst.Element.link) devolve um enum onde
        # OK=0 — "if not resultado" trataria sucesso como falha.
        if new_src.get_static_pad("src").link(peer) != Gst.PadLinkReturn.OK:
            raise RuntimeError("não liguei a fonte nova no resto do pipeline")
        self._probe_id = new_src.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._restamp)
        self._src_width = None
        self._src_height = None
        new_src.sync_state_with_parent()

    def _restamp(self, pad, info):
        """Cada quadro do portal recebe o tempo corrente do pipeline.

        O pipewiresrc entrega todos com o mesmo PTS. O buffer é recém-criado
        pelo próprio pipewiresrc, então o carimbo é escrito nele mesmo: o
        DMA-BUF não aceita cópia.
        """
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK

        meta = GstVideo.buffer_get_video_meta(buf)
        if meta is not None:
            w, h = meta.width, meta.height
            if self._src_width is None:
                self._src_width = w
                self._src_height = h
            elif w != self._src_width or h != self._src_height:
                # Niri (cast target dinâmico) ou um resize de janela troca o
                # tamanho real do buffer sem o pipewiresrc renegociar caps
                # sozinho — o resto da cadeia ficaria mapeando o frame com o
                # GstVideoInfo antigo e travaria. Em vez de destruir o
                # pipeline inteiro (e o contexto GL/EGL da NVIDIA — era a
                # causa do EGL_BAD_CONTEXT ao recriar), empurramos pelo mesmo
                # pad a caps corrigida antes deste buffer: GL e o NVENC
                # renegociam a geometria sozinhos (testado isoladamente,
                # nvh264enc aceita width/height mudando em pleno PLAYING sem
                # erro). Resolução fixa usa glvideomixer logo depois, que já
                # escala/letterboxa qualquer tamanho de entrada pro alvo
                # configurado; "Fonte" não tem caps de tamanho nenhuma daqui
                # pra frente, então a troca passa direto.
                print(f"troca de resolução detectada: {self._src_width}x{self._src_height} -> {w}x{h}", flush=True)
                old_caps = pad.get_current_caps()
                if old_caps is not None:
                    new_caps = old_caps.copy()
                    new_caps.set_value("width", w)
                    new_caps.set_value("height", h)
                    pad.push_event(Gst.Event.new_caps(new_caps))
                self._src_width = w
                self._src_height = h

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

    def _on_need_context(self, _bus, message) -> None:
        _ok, ctx_type = message.parse_context_type()
        if ctx_type != GstGL.GL_DISPLAY_CONTEXT_TYPE:
            return
        ctx = Gst.Context.new(GstGL.GL_DISPLAY_CONTEXT_TYPE, True)
        GstGL.context_set_gl_display(ctx, self._gl_display)
        message.src.set_context(ctx)
