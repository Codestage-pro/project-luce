"""Captura + encode + publish via WHIP. Portado de golive-linux/golive/media.py.

Só existe um destino (o server/sfu_proxy.py), então a cadeia é linear — sem o `tee`
por-espectador do protótipo original, que servia pra abrir um `webrtcbin`
por pessoa (mesh). O WHIP delega toda a negociação SDP/ICE pro `whipsink`.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("GstGL", "1.0")
from gi.repository import GLib, Gst, GstGL, GstVideo

WINDOWS = sys.platform == "win32"

from . import roster, rtc
if WINDOWS:
    from .windows import winsys
else:
    from .linux import linux_audio

log = logging.getLogger("luce.capture")

# O servidor tira do roster quem passa de 30s sem avisar (queda sem DELETE).
HEARTBEAT_SECONDS = 10
# O WHIP normal conecta em ~1 s; passou disso sem sair do "new", a negociação não começou.
HANDSHAKE_SECONDS = 6
FLOW_SECONDS = 3

Gst.init(None)


def _try_set(element: Gst.Element, name: str, value) -> None:
    if element.find_property(name) is None:
        return
    try:
        element.set_property(name, value)
    except TypeError:
        try:
            Gst.util_set_object_arg(element, name, str(value))
        except Exception as exc:
            log.debug("%s=%r ignorado: %s", name, value, exc)
    except Exception as exc:
        log.debug("%s=%r ignorado: %s", name, value, exc)


FOLLOW_MS = 500  # de quanto em quanto tempo checa se a janela transmitida ainda existe
# Ordem de preferência. No Windows o elemento só registra se o driver da GPU existir
# (o nvcodec sem nvcuda.dll tem 0 elementos), então a escolha é feita em runtime.
def _set_loopback(src: Gst.Element, mode: str, pid: int) -> None:
    """Áudio por processo no wasapi2src. Falha alto: sem o modo certo a voz de quem assiste volta pra transmissão."""
    problems = [n for n in ("loopback", "loopback-mode", "loopback-target-pid") if src.find_property(n) is None]
    if not problems:
        try:
            src.set_property("loopback", True)
            Gst.util_set_object_arg(src, "loopback-mode", mode)
            src.set_property("loopback-target-pid", pid)
            got = src.get_property("loopback-mode").value_nick
            if got != mode or src.get_property("loopback-target-pid") != pid:
                problems.append(f"ficou {got}/{src.get_property('loopback-target-pid')}")
        except Exception as exc:
            problems.append(str(exc))
    if problems:
        log.warning("áudio por processo (%s, pid %d) não aplicou: %s", mode, pid, ", ".join(problems))
        print(f"aviso: áudio por processo não aplicou ({', '.join(problems)})", flush=True)


LINUX_ENCODERS = (
    ("nvh264enc", "nvenc", "NVENC (GPU NVIDIA)"),
    ("vah264enc", "va", "VA-API (GPU AMD/Intel)"),
    ("vah264lpenc", "va", "VA-API low-power (GPU Intel/AMD)"),
    ("x264enc", "x264", "x264 (CPU, sem encoder de GPU disponível)"),
)
WINDOWS_ENCODERS = (
    ("nvd3d11h264enc", "nvd3d11", "NVENC D3D11 (GPU NVIDIA)"),
    ("amfh264enc", "amf", "AMF (GPU AMD)"),
    ("qsvh264enc", "qsv", "Quick Sync (GPU Intel)"),
    ("mfh264enc", "mf", "Media Foundation (GPU)"),
    ("x264enc", "x264", "x264 (CPU, sem encoder de GPU disponível)"),
)
# Encoders de GPU que leem a textura D3D11 direto (d3d11screencapturesrc ! d3d11convert ! enc)
D3D11_KINDS = ("nvd3d11", "amf", "qsv", "mf")
NVENC_KINDS = ("nvenc", "nvd3d11")  # os dois têm VBV por quadro


# Encoders que falharam nesta sessão (driver recusa, caps não negocia): o restart automático da GUI
# recria o pipeline e _make_encoder segue pro próximo da tabela em vez de repetir o mesmo erro.
_BAD_ENCODERS: set[str] = set()
# factory -> (use_d3d11_memory, use_basic_props) validado em _encoder_works
_ENCODER_MODES: dict[str, tuple[bool, bool]] = {}
# Nível de fallback do áudio no Windows: 0 = process-loopback, 1 = loopback padrão, 2 = silêncio
_WIN_AUDIO_FALLBACK: int = 0


def _configure_encoder(enc: Gst.Element, enc_kind: str, bitrate_kbps: int, fps: int, basic: bool = False) -> None:
    """Configura propriedades do encoder. Se basic=True, aplica apenas bitrate (para drivers AMD/MF restritivos)."""
    _try_set(enc, "bitrate", bitrate_kbps)
    if basic and enc_kind in ("amf", "qsv", "mf", "nvd3d11"):
        return
    if enc_kind in NVENC_KINDS:
        _try_set(enc, "gop-size", fps)
        _try_set(enc, "vbv-buffer-size", max(bitrate_kbps // 10, 1))
        _try_set(enc, "zerolatency", True)
        _try_set(enc, "rc-mode", "cbr")
        _try_set(enc, "preset", "p4")
        _try_set(enc, "tune", "ultra-low-latency")
        _try_set(enc, "bframes", 0)
        _try_set(enc, "cabac", False)
    elif enc_kind == "amf":
        # Na RX 580 (Polaris VCE 3.4), tocar em b-frames ou usage pode falhar com AMF_NOT_SUPPORTED.
        _try_set(enc, "rate-control", "cbr")
        _try_set(enc, "gop-size", fps)
    elif enc_kind in ("qsv", "mf"):
        _try_set(enc, "rate-control" if enc_kind != "mf" else "rc-mode", "cbr")
        _try_set(enc, "gop-size", fps)
        _try_set(enc, "b-frames" if enc_kind == "qsv" else "bframes", 0)
        _try_set(enc, "low-latency", True)
    elif enc_kind == "x264":
        _try_set(enc, "tune", "zerolatency")
        _try_set(enc, "speed-preset", "veryfast")
        _try_set(enc, "key-int-max", fps)
        _try_set(enc, "bframes", 0)
        _try_set(enc, "byte-stream", True)
        _try_set(enc, "vbv-buf-capacity", 1000)
    else:
        _try_set(enc, "rate-control", "cbr")
        _try_set(enc, "key-int-max", fps)
        _try_set(enc, "b-frames", 0)
        _try_set(enc, "target-usage", 6)


def _encoder_works(factory: str, kind: str) -> bool:
    """Codifica quadros reais testando D3D11Memory vs SystemMemory e propriedades completas vs básicas."""
    if factory in _ENCODER_MODES:
        return True
    sink_pad = Gst.ElementFactory.make(factory, None).get_static_pad("sink")
    tmpl = sink_pad.get_pad_template_caps().to_string() if sink_pad else ""
    can_d3d11 = "memory:D3D11Memory" in tmpl
    modes = [(True, False), (True, True), (False, False), (False, True)] if can_d3d11 else [(False, False), (False, True)]
    why = "sem resposta em 5 s"
    for use_d3d11, basic in modes:
        if use_d3d11:
            launch = (
                f"videotestsrc num-buffers=5 ! video/x-raw,format=BGRx,width=1280,height=720,framerate=30/1 ! "
                f"d3d11upload ! d3d11convert ! video/x-raw(memory:D3D11Memory),format=NV12,width=1280,height=720,framerate=30/1 ! "
                f"{factory} name=test-enc ! video/x-h264,stream-format=byte-stream,alignment=au ! h264parse ! fakesink"
            )
        else:
            launch = (
                f"videotestsrc num-buffers=5 ! video/x-raw,format=BGRx,width=1280,height=720,framerate=30/1 ! "
                f"d3d11upload ! d3d11convert ! video/x-raw(memory:D3D11Memory),format=NV12,width=1280,height=720,framerate=30/1 ! "
                f"d3d11download ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 ! "
                f"{factory} name=test-enc ! video/x-h264,stream-format=byte-stream,alignment=au ! h264parse ! fakesink"
            )
        try:
            pipe = Gst.parse_launch(launch)
            test_enc = pipe.get_by_name("test-enc")
            if test_enc is not None:
                _configure_encoder(test_enc, kind, 4000, 30, basic=basic)
        except GLib.Error as exc:
            why = exc.message
            continue
        msg = None
        try:
            if pipe.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE:
                msg = pipe.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        finally:
            pipe.set_state(Gst.State.NULL)
        if msg is not None and msg.type == Gst.MessageType.EOS:
            _ENCODER_MODES[factory] = (use_d3d11, basic)
            print(f"encoder {factory} validado (d3d11={use_d3d11}, basic={basic})", flush=True)
            return True
        if msg is not None and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            why = f"{err.message} ({dbg.splitlines()[-1].strip() if dbg else ''})"
    print(f"encoder {factory} não funcionou neste PC ({why}); tentando o próximo", flush=True)
    log.warning("encoder %s descartado no teste: %s", factory, why)
    return False


def _make_encoder() -> tuple[Gst.Element, str]:
    """GPU primeiro (NVENC, depois AMF/QSV/MF/VA-API), CPU só se não houver GPU que codifique."""
    table = WINDOWS_ENCODERS if WINDOWS else LINUX_ENCODERS
    forced = os.environ.get("LUCE_ENCODER")  # ex.: LUCE_ENCODER=x264enc se o encoder da GPU travar
    print("encoders presentes: " + (", ".join(r[0] for r in table if Gst.ElementFactory.find(r[0])) or "nenhum"), flush=True)
    for factory, kind, label in [row for row in table if row[0] == forced] or table:
        if factory in _BAD_ENCODERS and kind != "x264":
            continue
        # No Windows o override do gst-python lança exceção em ElementFactory.make se o elemento não existir!
        if Gst.ElementFactory.find(factory) is None:
            continue
        try:
            enc = Gst.ElementFactory.make(factory, "enc")
        except Exception:
            continue
        if enc is None:
            continue
        if WINDOWS and kind != "x264" and not forced and not _encoder_works(factory, kind):
            _BAD_ENCODERS.add(factory)
            continue
        print(f"codificando com {label}", flush=True)
        return enc, kind
    raise RuntimeError(f"nenhum encoder H.264 instalado ({', '.join(row[0] for row in table)})")


def _printwindow_works(hwnd: int) -> bool:
    """O PrintWindow devolve imagem (não preta) dessa janela? Se não, a captura cai no recorte do monitor."""
    grabber = winsys.WindowGrabber(hwnd)
    try:
        for _ in range(5):  # o app pode demorar a desenhar o primeiro quadro
            if grabber.grab() is not None and not grabber.black:
                return True
            time.sleep(0.1)
        print(f"PrintWindow não serve para esta janela ({grabber.reason or 'quadro preto'}): capturando o trecho do monitor sob ela", flush=True)
    except OSError as exc:
        print(f"PrintWindow falhou ({exc}): capturando o trecho do monitor sob a janela", flush=True)
    finally:
        grabber.close()
    return False


def _window_crop(hwnd: int) -> tuple[int, int, int, int, int] | None:
    """(HMONITOR, x, y, largura, altura) da parte visível da janela no monitor onde ela está; None se não der."""
    try:
        hmon, (ml, mt, mr, mb) = winsys.monitor_of_window(hwnd)
        left, top, right, bottom = winsys.window_rect(hwnd)
    except OSError:
        return None
    x, y = max(left, ml) - ml, max(top, mt) - mt
    w, h = (min(right, mr) - ml - x) & ~1, (min(bottom, mb) - mt - y) & ~1
    return (hmon, x, y, w, h) if w >= 64 and h >= 64 else None


def _set_crop(src: Gst.Element, crop: tuple[int, int, int, int, int]) -> None:
    for prop, value in zip(("crop-x", "crop-y", "crop-width", "crop-height"), crop[1:]):
        src.set_property(prop, value)


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
        x11: bool = False,
        x11_region: tuple[int, int, int, int] | None = None,
        portal_fd: int | None = None,
        portal_node: int | None = None,
        monitor_handle: int | None = None,  # Windows: HMONITOR; None = monitor principal
        window_handle: int | None = None,  # Windows: HWND; captura só a janela (PrintWindow)
        preview: bool = True,
        preview_sink=None,  # função que cria o gtk4paintablesink (ver _preview_bridge)
        audio_enabled: bool = True,
        audio_bitrate_kbps: int = 128,
        audio_device: str | None = None,
        mute_app: str = "",
        auth_token: str | None = None,
        on_pipeline_error=None,
        gl_display: GstGL.GLDisplay | None = None,
    ) -> None:
        self._on_pipeline_error = on_pipeline_error
        self._whip_endpoint = whip_endpoint
        self._auth_token = auth_token
        self._stop_heartbeat = threading.Event()
        self._gl_display = gl_display
        self._preview_pipeline: Gst.Pipeline | None = None
        self._preview_make = None
        self._system_audio: linux_audio.SystemAudioExcept | None = None
        self._fps = fps
        self._pattern = pattern
        self._x11 = x11
        self._d3d11 = WINDOWS and not pattern
        self._window = window_handle if self._d3d11 else None
        self._follow_id: int | None = None
        self._crop: tuple[int, int, int, int, int] | None = None  # (HMONITOR, x, y, largura, altura)
        if self._window and os.environ.get("LUCE_WINDOW_MODE") == "crop":
            self._crop = _window_crop(self._window)
        self._grab = bool(self._window) and self._crop is None  # quadros vêm do WindowGrabber (WGC ou PrintWindow)
        if self._window and not (width and height):  # "Fonte": tamanho fixo = o da janela agora; depois a GPU escala
            left, top, right, bottom = winsys.window_rect(self._window)
            width, height = max(64, (right - left) & ~1), max(64, (bottom - top) & ~1)
        self._monitor = monitor_handle
        self._frames = 0
        self._reported = -1
        self._last_pts = 0
        self._src_width: int | None = None
        self._src_height: int | None = None
        self._report_id: int | None = None
        self._handshake_id: int | None = None
        self._flow_id: int | None = None
        self._sent = 0
        self.encoded_frames = 0  # o que o encoder de fato entrega: base do fps/resolução na GUI
        self.encoded_size: tuple[int, int] | None = None
        self._probe_id: int | None = None
        self._last_frame: Gst.Buffer | None = None  # o que o _keepalive_loop reenvia com a tela parada
        self._last_frame_at = 0.0
        self.pipeline = Gst.Pipeline.new("luce-capture")

        # ximagesrc e videotestsrc entregam quadro cru na memória comum; o
        # portal entrega DMA-BUF/GL. A diferença vale pra cadeia inteira.
        raw_source = pattern or x11
        if pattern:
            src = Gst.ElementFactory.make("videotestsrc", "src")
            src.set_property("is-live", True)
        elif x11:
            # Sem portal de captura (XFCE, Cinnamon, i3 — só o backend gtk,
            # que não implementa ScreenCast) o X11 ainda deixa ler a tela
            # inteira direto do servidor gráfico. use-damage=false porque o
            # modo por região suja manda quadro parcial e trava o encoder.
            src = Gst.ElementFactory.make("ximagesrc", "src")
            if src is None:
                raise RuntimeError("falta o ximagesrc (gst-plugins-good)")
            _try_set(src, "use-damage", False)
            if x11_region:  # sem isso o ximagesrc pega a área virtual com todos os monitores juntos
                x, y, w, h = x11_region
                for prop, value in ("startx", x), ("starty", y), ("endx", x + w - 1), ("endy", y + h - 1):
                    src.set_property(prop, value)
            _try_set(src, "show-pointer", True)
            _try_set(src, "do-timestamp", True)
        elif WINDOWS:
            src = Gst.ElementFactory.make("d3d11screencapturesrc", "src")
            if src is None:
                raise RuntimeError("falta o d3d11screencapturesrc (gst-plugins-bad)")
            if self._grab:
                src = Gst.ElementFactory.make("appsrc", "src")  # alimentado por _grab_loop (ver winsys.WindowGrabber)
                _try_set(src, "is-live", True)
                _try_set(src, "do-timestamp", True)
                src.set_property("format", Gst.Format.TIME)
                _try_set(src, "block", False)
                # leaky: se o encoder atrasar, perde quadro velho em vez de acumular
                _try_set(src, "leaky-type", 2)
                _try_set(src, "max-buffers", 2)
            elif self._crop:
                src.set_property("monitor-handle", self._crop[0])
                _set_crop(src, self._crop)
            elif monitor_handle:
                src.set_property("monitor-handle", monitor_handle)
            if not self._grab:
                _try_set(src, "show-cursor", True)
        else:
            src = Gst.ElementFactory.make("pipewiresrc", "src")
            src.set_property("fd", int(portal_fd))
            # O portal entrega o node.id. target-object é o serial do PipeWire;
            # repetir o id aí faz o WirePlumber responder "target not found".
            src.set_property("path", str(portal_node))
            src.set_property("do-timestamp", True)
            _try_set(src, "provide-clock", False)
            _try_set(src, "client-name", "ProjectLUCE")
            # Tela parada = o compositor não manda quadro: quem reenvia é o _keepalive_loop.
            # O keepalive-time do pipewiresrc não serve: o quadro reenviado sai com PTS = hora
            # absoluta do relógio e, com o GstSystemClock (o áudio "Silenciar app" não dá relógio),
            # o basesrc espera horas por ele: a captura congela em ~5 s e tudo fica preto.
            # shortcut: capturando uma janela específica, se ela for redimensionada
            # no meio da transmissão o pipewiresrc derruba o pipeline com
            # "gst_video_frame_map_id: assertion 'info->width <= meta->width'
            # failed" (o buffer novo chega menor do que o GstVideoInfo negociado no
            # início, sem renegociação de caps em cima do DMA-BUF). Sem fix aqui —
            # é a reconexão com retry da Fase 4 do README que resolveria de verdade.

        rate = Gst.ElementFactory.make("videorate", "rate")
        # drop-only deixava passar de 60 a 100 quadros/s (e 12 Mbps com 8000 de
        # alvo): o portal entrega na taxa do monitor com PTS irregular, e só
        # descartar não segura o ritmo. Sem ele o videorate emite exatamente
        # `fps` quadros por segundo (duplica se a tela parar de mudar).
        _try_set(rate, "drop-only", False)
        # Sem teto, depois de um engasgo o videorate preenche o buraco inteiro de
        # uma vez (rajada de 100 quadros/s). 100 ms cobre o ritmo normal.
        _try_set(rate, "max-duplication-time", 100 * Gst.MSECOND)
        _try_set(rate, "skip-to-first", True)
        _try_set(rate, "qos", False)
        caps = Gst.ElementFactory.make("capsfilter", "caps")
        enc, enc_kind = _make_encoder()
        self.encoder_kind = enc_kind
        self._enc_factory = enc.get_factory().get_name()

        # x264 lê memória comum: o mesmo caminho do ximagesrc, só que com o pipewiresrc na frente.
        sysmem = raw_source or (enc_kind == "x264" and not self._d3d11)
        mixer = None
        if not raw_source and not self._d3d11:  # o d3d11screencapturesrc já carimba o PTS direito
            self._probe_id = src.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._restamp)
        if sysmem:
            raw_preview = False
            convert = Gst.ElementFactory.make("videoconvert", "convert")
            scale = Gst.ElementFactory.make("videoscale", "scale")
            front: list[Gst.Element] = [src, convert, scale, rate]
            # width/height=0 é a opção "Fonte" das Configurações: sem tamanho
            # na caps, o ximagesrc transmite a tela no tamanho nativo dela.
            size = f"width={width},height={height}," if width and height else ""
            caps.set_property(
                "caps",
                Gst.Caps.from_string(
                    f"video/x-raw,format=NV12,{size}framerate={fps}/1"
                ),
            )
        elif self._d3d11:
            # Tudo na VRAM: o d3d11convert converte BGRA->NV12 e escala (com barras, sem esticar)
            # sem a CPU tocar no pixel. Só o x264 baixa a textura (d3d11download, mais abaixo).
            raw_preview = False
            # pixel-aspect-ratio=1/1 é o que faz as barras valerem: sem ele o d3d11convert estica a imagem
            # e avisa a diferença num PAR que o WebRTC ignora (visto no CI: 4:3 virava 16:9 com PAR 3/4).
            conv = Gst.ElementFactory.make("d3d11convert", "d3d11conv")
            src_caps = Gst.ElementFactory.make("capsfilter", "src-caps")
            if conv is None or src_caps is None:
                raise RuntimeError("falta o d3d11convert (gst-plugins-bad)")
            _try_set(conv, "add-borders", True)
            # o framerate na saída da fonte faz ela entregar `fps`, não a taxa do monitor
            src_caps.set_property(
                "caps", Gst.Caps.from_string(f"video/x-raw(memory:D3D11Memory),framerate={fps}/1")
            )
            size = f"width={width},height={height}," if width and height else ""
            front = [src, src_caps, conv, rate]
            if self._grab:  # quadros chegam em memória comum: sobe pra VRAM antes do d3d11convert
                upload = Gst.ElementFactory.make("d3d11upload", "d3d11up")
                if upload is None:
                    raise RuntimeError("falta o d3d11upload (gst-plugins-bad)")
                front.insert(1, upload)
            caps.set_property(
                "caps",
                Gst.Caps.from_string(f"video/x-raw(memory:D3D11Memory),format=NV12,{size}pixel-aspect-ratio=1/1,framerate={fps}/1"),
            )
        else:
            # O portal manda DMA-BUF e todos os quadros chegam com o mesmo PTS;
            # sem remarcar (probe acima), o videorate joga fora tudo depois do
            # primeiro e a imagem congela.
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

        _use_d3d11_enc, _basic_enc = _ENCODER_MODES.get(self._enc_factory, (True, False))
        _configure_encoder(enc, enc_kind, bitrate_kbps, fps, basic=_basic_enc)
        h264caps = Gst.ElementFactory.make("capsfilter", "h264caps")
        # No Windows deixa o encoder de hardware negociar o seu profile nativo (main/baseline/constrained-baseline)
        # sem forçar um profile específico que o driver AMF/MF possa rejeitar no Init().
        h264_str = (
            "video/x-h264,stream-format=byte-stream,alignment=au"
            if WINDOWS
            else "video/x-h264,profile=constrained-baseline,stream-format=byte-stream,alignment=au"
        )
        h264caps.set_property("caps", Gst.Caps.from_string(h264_str))
        parse = Gst.ElementFactory.make("h264parse", "parse")
        _try_set(parse, "config-interval", -1)
        parse.get_static_pad("src").add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.EVENT_DOWNSTREAM, self._count_encoded
        )
        queue = Gst.ElementFactory.make("queue", "queue")
        _try_set(queue, "max-size-buffers", 3)
        _try_set(queue, "leaky", "downstream")
        pay = Gst.ElementFactory.make("rtph264pay", "pay")
        _try_set(pay, "pt", 96)
        _try_set(pay, "config-interval", -1)
        _try_set(pay, "aggregate-mode", "zero-latency")
        pay.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._count_sent)
        whip = Gst.ElementFactory.make("whipsink", "whip")
        if whip is None:
            raise RuntimeError("whipsink não está instalado (gst-plugin-webrtchttp)")
        whip.set_property("whip-endpoint", whip_endpoint)
        # whipsink é um GstBin com um webrtcbin interno: sem isso, um erro do
        # webrtcbin (ICE caiu, DTLS falhou) fica preso dentro do bin e nunca
        # chega no bus do pipeline externo — a conexão morre em silêncio sem
        # disparar on_pipeline_error (visto em teste real: servidor loga
        # "closed: terminated" mas a GUI nunca soube).
        _try_set(whip, "message-forward", True)
        if auth_token:
            _try_set(whip, "auth-token", auth_token)

        chain = [*front, caps]
        for element in chain:
            self.pipeline.add(element)
        for left, right in zip(chain, chain[1:]):
            if not left.link(right):
                raise RuntimeError(f"não liguei {left.get_name()} em {right.get_name()}")

        if not raw_source and enc_kind == "nvenc" and mixer is not None:
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
            preview_out = self._preview_bridge(preview_sink)
            self.pipeline.add(prev_queue)
            self.pipeline.add(preview_out)
            if not raw_tee.link(prev_queue) or not prev_queue.link(preview_out):
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
                if WINDOWS:
                    conv = Gst.ElementFactory.make("videoconvert", "preview-convert")
                    rgba = Gst.ElementFactory.make("capsfilter", "preview-rgba")
                    rgba.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGBA"))
                    prev_tail = [conv, rgba, self._preview_bridge(preview_sink)]
                    if self._d3d11:
                        # a GTK lê memória comum: baixa da VRAM só o ramo da preview, a 15 fps
                        rate15 = Gst.ElementFactory.make("videorate", "preview-rate")
                        _try_set(rate15, "drop-only", True)
                        cap15 = Gst.ElementFactory.make("capsfilter", "preview-rate-caps")
                        cap15.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:D3D11Memory),framerate=15/1"))
                        prev_tail = [rate15, cap15, Gst.ElementFactory.make("d3d11download", "preview-download"), *prev_tail]
                else:
                    prev_tail = [self._preview_bridge(preview_sink)]
            elif sysmem:
                prev_tail = [
                    Gst.ElementFactory.make("videoconvert", "preview-convert"),
                    Gst.ElementFactory.make("autovideosink", "preview-sink"),
                ]
            else:
                prev_tail = [Gst.ElementFactory.make("glimagesink", "preview-sink")]
            _try_set(prev_tail[-1], "sync", False)
            if None in prev_tail:
                raise RuntimeError("faltam elementos da preview (d3d11download, videoconvert ou videorate)")
            for element in (tee, enc_queue, prev_queue, *prev_tail):
                self.pipeline.add(element)
            if not caps.link(tee) or not tee.link(enc_queue) or not tee.link(prev_queue):
                raise RuntimeError("não liguei o tee do preview")
            preview_chain = (prev_queue, *prev_tail)
            for left, right in zip(preview_chain, preview_chain[1:]):
                if not left.link(right):
                    raise RuntimeError(f"não liguei {left.get_name()} em {right.get_name()}")
            encode_src = enc_queue

        sink_pad = enc.get_static_pad("sink")
        tmpl = sink_pad.get_pad_template_caps().to_string() if sink_pad else ""
        use_d3d11_enc = _ENCODER_MODES.get(self._enc_factory, ("memory:D3D11Memory" in tmpl, False))[0]
        needs_download = self._d3d11 and (enc_kind == "x264" or not use_d3d11_enc or "memory:D3D11Memory" not in tmpl)
        download = Gst.ElementFactory.make("d3d11download", "enc-download") if needs_download else None
        head = (encode_src, download, enc) if download is not None else (encode_src, enc)
        encode_chain = (*head, h264caps, parse, queue, pay, whip)
        for element in (*head[1:], h264caps, parse, queue, pay, whip):
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
        elif not WINDOWS and audio_device and audio_device.startswith("app:"):
            # Áudio de um app específico (Spotify, navegador, etc) em vez de
            # microfone: pipewiresrc com target-object=<serial> liga direto
            # nesse stream de playback (confirmado com nível real de áudio,
            # não só buffers chegando — alvo inválido cai silenciosamente pro
            # dispositivo padrão em vez de dar erro, então se o app fechar no
            # meio a transmissão não quebra, só volta a pegar o microfone).
            app_src = Gst.ElementFactory.make("pipewiresrc", None)
            _try_set(app_src, "target-object", audio_device[len("app:"):])
            # Sem isso o nó aparece no qpwgraph como "python3.14" (nome
            # genérico do processo) em vez de algo reconhecível.
            _try_set(app_src, "client-name", "Project LUCE")
            audio_src = linux_audio.with_silence(app_src)  # app pausado não trava a transmissão
        elif WINDOWS:
            if _WIN_AUDIO_FALLBACK >= 2:
                audio_src = Gst.ElementFactory.make("audiotestsrc", "audio-src")
                audio_src.set_property("wave", 4)  # silence
                audio_src.set_property("is-live", True)
            else:
                wasapi = Gst.ElementFactory.make("wasapi2src", "audio-src")
                if wasapi is None:
                    raise RuntimeError("falta o wasapi2src (gst-plugins-bad)")
                _try_set(wasapi, "provide-clock", False)
                audio_device = audio_device or ""
                app_pid = winsys.find_process_pid(audio_device[4:]) if audio_device.startswith("app:") else None
                if audio_device.startswith("app:") and not app_pid:
                    print(f"aviso: {audio_device[4:]} não está aberto, transmitindo o áudio do sistema", flush=True)
                if audio_device and not audio_device.startswith("app:"):  # microfone escolhido nas Configurações
                    _try_set(wasapi, "device", audio_device)
                    _try_set(wasapi, "low-latency", True)
                elif _WIN_AUDIO_FALLBACK == 1:
                    _try_set(wasapi, "loopback", True)
                else:
                    target_pid = winsys.find_process_pid(mute_app) if mute_app else None
                    if self._window:
                        _set_loopback(wasapi, "include-process-tree", winsys.window_pid(self._window))
                    elif app_pid:
                        _set_loopback(wasapi, "include-process-tree", app_pid)
                    elif target_pid:
                        _set_loopback(wasapi, "exclude-process-tree", target_pid)
                        print(f"silenciando {mute_app} (PID {target_pid}) na transmissão", flush=True)
                    else:
                        _set_loopback(wasapi, "exclude-process-tree", os.getpid())
                audio_src = wasapi
        elif mute_app and not audio_device:  # sem microfone escolhido: áudio do sistema menos o app silenciado
            self._system_audio = linux_audio.SystemAudioExcept(mute_app)
            audio_src = self._system_audio.build()
            print(f"silenciando {mute_app} na transmissão", flush=True)
        else:
            mic = Gst.ElementFactory.make("pulsesrc", None)
            # client-name é o que aparece no qpwgraph/pavucontrol pra rotear
            # o áudio; funciona tanto em PulseAudio quanto em PipeWire (via
            # pipewire-pulse), sem código separado pros dois.
            _try_set(mic, "client-name", "Project LUCE")
            if audio_device:
                _try_set(mic, "device", audio_device)
            # microfone mudo ou ligado pelo WirePlumber num app parado não trava a transmissão
            audio_src = linux_audio.with_silence(mic)
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
        rtc.enable_nack(whip)

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

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(HEARTBEAT_SECONDS):
            try:
                roster.heartbeat(self._whip_endpoint, self._auth_token)
            except OSError as exc:  # queda de rede passageira: o próximo tenta de novo
                log.debug("heartbeat falhou: %s", exc)

    def _keepalive_loop(self) -> None:
        """Tela parada: reenvia o último quadro do portal.

        O compositor só manda quadro quando a tela muda (niri, GNOME...). Sem quadro o
        encoder não solta IDR: 0 kbps pra quem transmite e tela preta pra quem entra
        depois. A cópia passa pela _restamp (PTS = agora) e o videorate corta o excesso.
        O chain do pad seguinte trava o stream lock, então não cruza com o quadro real.
        """
        interval = max(1000 // self._fps, 33) / 1000
        while not self._stop_heartbeat.wait(interval):
            frame = self._last_frame
            if frame is None or time.monotonic() - self._last_frame_at < interval:
                continue
            src = self.pipeline.get_by_name("src")
            if src is not None:
                src.get_static_pad("src").push(frame.copy())

    def start(self) -> bool:
        self.pipeline.set_state(Gst.State.PLAYING)
        if self._preview_make is not None:
            self._start_preview()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        # O contador vive na sonda _restamp, que só existe no caminho do
        # portal; sem ela o relatório imprimiria "0 frames" pra sempre e
        # pareceria que a captura (ximagesrc/--pattern) está morta.
        if self._probe_id is not None:
            threading.Thread(target=self._keepalive_loop, daemon=True).start()
            self._report_id = GLib.timeout_add_seconds(2, self._report_frames)
        self._handshake_id = GLib.timeout_add_seconds(HANDSHAKE_SECONDS, self._check_handshake)
        if self._window:
            self._follow_id = GLib.timeout_add(FOLLOW_MS, self._follow_window)
        if self._grab:
            threading.Thread(target=self._grab_loop, daemon=True).start()
        return False

    def _grab_loop(self) -> None:
        """Lê a janela a `fps` e empurra no appsrc; minimizada = repete o último quadro."""
        grabber = winsys.WindowGrabber(self._window)
        src = self.pipeline.get_by_name("src")
        backend = "Windows.Graphics.Capture (GPU D3D11)" if grabber.is_wgc else "PrintWindow (GDI)"
        print(f"captura da janela via {backend}", flush=True)
        interval = 1 / (self._fps if grabber.is_wgc else min(self._fps, 60))
        last, shape, next_at, told = None, None, time.monotonic(), ""
        try:
            while not self._stop_heartbeat.is_set():
                try:
                    frame = grabber.grab()
                except OSError as exc:
                    log.warning("captura da janela falhou: %s", exc)
                    frame = None
                if frame is None and grabber.reason != told:  # sem isso o luce.log não diz por que a janela não tem imagem
                    told = grabber.reason
                    print(f"captura da janela sem quadro: {told}", flush=True)
                if frame is not None:
                    last = frame
                if last is not None:
                    w, h, data = last
                    if (w, h) != shape:  # janela redimensionada: o d3d11convert reescala pro tamanho fixo
                        shape = (w, h)
                        src.set_property("caps", Gst.Caps.from_string(
                            f"video/x-raw,format=BGRx,width={w},height={h},framerate={self._fps}/1,pixel-aspect-ratio=1/1"))
                    src.emit("push-buffer", Gst.Buffer.new_wrapped(data))
                next_at += interval
                if (delay := next_at - time.monotonic()) > 0:
                    self._stop_heartbeat.wait(delay)
                else:
                    next_at = time.monotonic()
        finally:
            grabber.close()

    def _follow_window(self) -> bool:
        """Avisa quando a janela transmitida é fechada (a captura só congelaria no último quadro); no recorte, acompanha a janela."""
        if winsys.is_window(self._window):
            if self._crop:
                crop = _window_crop(self._window)
                src = self.pipeline.get_by_name("src")
                if crop and crop != self._crop and crop[0] == self._crop[0] and src is not None:  # outro monitor: o src não troca em voo
                    self._crop = crop
                    _set_crop(src, crop)
            return True
        self._follow_id = None
        if self._on_pipeline_error is not None:
            self._on_pipeline_error("a janela transmitida foi fechada")
        return False

    def _check_handshake(self) -> bool:
        # Corrida dentro do whipsink: às vezes o webrtcbin nunca dispara a oferta e fica em
        # "new" pra sempre, com o app dizendo "transmitindo" e 0 pacotes saindo. Reiniciar resolve.
        self._handshake_id = None
        webrtc = self.pipeline.get_by_name("whip-webrtcbin")
        state = webrtc.get_property("connection-state").value_nick if webrtc is not None else "connected"
        if state in ("new", "failed") and self._on_pipeline_error is not None:
            log.warning("whip: conexão em '%s' após %ss, reiniciando", state, HANDSHAKE_SECONDS)
            self._on_pipeline_error("a conexão com o servidor não iniciou")
        else:
            self._flow_id = GLib.timeout_add_seconds(FLOW_SECONDS, self._check_flow, self._sent)
        return False

    def _count_encoded(self, _pad, info) -> Gst.PadProbeReturn:
        if info.type & Gst.PadProbeType.BUFFER:
            self.encoded_frames += 1
        else:
            event = info.get_event()
            if event.type == Gst.EventType.CAPS:
                st = event.parse_caps().get_structure(0)
                ok_w, w = st.get_int("width")
                ok_h, h = st.get_int("height")
                if ok_w and ok_h:
                    self.encoded_size = (w, h)
        return Gst.PadProbeReturn.OK

    def _count_sent(self, _pad, _info) -> Gst.PadProbeReturn:
        self._sent += 1
        return Gst.PadProbeReturn.OK

    def _check_flow(self, sent_before: int) -> bool:
        # Outra corrida do webrtcbin: conecta (ICE "completed") e mesmo assim para de aceitar
        # quadros após os 4 primeiros, 0 pacotes saindo. Vídeo a 30+ fps passa de 10 em 3 s.
        self._flow_id = None
        if not self.encoded_frames:  # o encoder nunca soltou um quadro, sem erro no bus (driver travado)
            self._demote_encoder("sem quadros codificados")
        if self._sent - sent_before < 10 and self._on_pipeline_error is not None:
            log.warning("whip: só %d quadros enviados em %ss, reiniciando", self._sent - sent_before, FLOW_SECONDS)
            self._on_pipeline_error("o envio travou")
        return False

    def stop(self) -> None:
        self._stop_heartbeat.set()
        self._last_frame = None  # segurar o buffer do PipeWire trava a ida pra NULL
        if self._follow_id is not None:
            GLib.source_remove(self._follow_id)
            self._follow_id = None
        if self._handshake_id is not None:
            GLib.source_remove(self._handshake_id)
            self._handshake_id = None
        if self._flow_id is not None:
            GLib.source_remove(self._flow_id)
            self._flow_id = None
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
        if self._system_audio is not None:
            self._system_audio.stop()
            self._system_audio = None
        if self._preview_pipeline is not None:
            self._preview_pipeline.set_state(Gst.State.NULL)
            self._preview_pipeline = None

    def _preview_bridge(self, make_sink) -> Gst.Element:
        """Devolve o appsink que entra no pipeline; o gtk4paintablesink roda num pipeline só dele.

        No mesmo pipeline do glupload o sink do GTK compartilha o contexto GL e,
        da segunda transmissão em diante, o glupload leva EGL_BAD_CONTEXT.
        O sink só nasce em start(), com a captura já no ar: criado antes, o
        gtk4paintablesink faz o glupload importar DMA-BUF preto (niri + NVIDIA).
        """
        appsink = Gst.ElementFactory.make("appsink", "preview-out")
        appsink.set_property("emit-signals", True)
        appsink.set_property("max-buffers", 2)
        appsink.set_property("drop", True)
        appsrc = Gst.ElementFactory.make("appsrc", "preview-in")
        appsrc.set_property("is-live", True)
        appsrc.set_property("format", Gst.Format.TIME)
        appsink.connect("new-sample", lambda sink: appsrc.emit("push-sample", sink.emit("pull-sample")))
        self._preview_make = (make_sink, appsrc)
        return appsink

    def _start_preview(self) -> None:
        make_sink, appsrc = self._preview_make
        preview_sink = make_sink()
        if preview_sink is None:  # sem o plugin: o appsink descarta os quadros
            return
        _try_set(preview_sink, "sync", False)
        pipeline = Gst.Pipeline.new("luce-preview")
        pipeline.add(appsrc)
        pipeline.add(preview_sink)
        if WINDOWS:
            conv = Gst.ElementFactory.make("videoconvert", "preview-pipe-conv")
            rgba = Gst.ElementFactory.make("capsfilter", "preview-pipe-rgba")
            rgba.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGBA"))
            pipeline.add(conv)
            pipeline.add(rgba)
            appsrc.link(conv)
            conv.link(rgba)
            rgba.link(preview_sink)
        else:
            appsrc.link(preview_sink)
        pipeline.set_state(Gst.State.PLAYING)
        self._preview_pipeline = pipeline

    def set_audio_enabled(self, enabled: bool) -> None:
        self._audio_volume.set_property("mute", not enabled)

    def set_bitrate(self, kbps: int) -> None:
        """Muda a qualidade do vídeo sem reiniciar (o encoder reconfigura em voo)."""
        enc = self.pipeline.get_by_name("enc")
        _try_set(enc, "bitrate", kbps)
        if self.encoder_kind in NVENC_KINDS:
            _try_set(enc, "vbv-buffer-size", max(kbps // 10, 1))

    def set_audio_bitrate(self, kbps: int) -> None:
        _try_set(self.pipeline.get_by_name("audio-enc"), "bitrate", kbps * 1000)

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
        if self._pattern or self._x11 or self._d3d11:
            raise RuntimeError("switch_source só existe com o portal (pipewiresrc)")
        old_src = self.pipeline.get_by_name("src")
        pad = old_src.get_static_pad("src")
        peer = pad.get_peer()
        if self._probe_id is not None:
            pad.remove_probe(self._probe_id)
            self._probe_id = None
        self._last_frame = None
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
            self._last_frame = buf
            self._last_frame_at = time.monotonic()
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
        global _WIN_AUDIO_FALLBACK
        err, debug = message.parse_error()
        src_name = message.src.get_name() if message.src else "pipeline"
        dbg_tail = ""
        if debug:
            lines = [l.strip() for l in debug.splitlines() if l.strip()]
            if lines:
                dbg_tail = lines[-1]
        detail = f"{err.message} [{src_name}: {dbg_tail}]" if dbg_tail else f"{err.message} [{src_name}]"
        print(f"pipeline parou: {detail}", flush=True)
        log.error("pipeline [%s]: %s (%s)", src_name, err.message, debug)
        if WINDOWS and src_name.startswith("audio"):
            _WIN_AUDIO_FALLBACK = min(_WIN_AUDIO_FALLBACK + 1, 2)
            print(f"áudio no Windows falhou ({detail}); recuando nível de áudio para {_WIN_AUDIO_FALLBACK}", flush=True)
        elif not self.encoded_frames and not src_name.startswith(("whip", "src", "d3d11screencapturesrc")):
            self._demote_encoder(detail)
        if self._on_pipeline_error is not None:
            self._on_pipeline_error(detail)

    def _demote_encoder(self, reason: str) -> None:
        if self.encoder_kind != "x264" and self._enc_factory not in _BAD_ENCODERS:
            _BAD_ENCODERS.add(self._enc_factory)
            log.warning("encoder %s falhou (%s): o próximo da lista assume", self._enc_factory, reason)
            print(f"encoder {self._enc_factory} falhou ({reason}); tentando outro", flush=True)

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
