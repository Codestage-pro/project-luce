"""CLI do Project LUCE: luce share | luce watch <nome> | luce doctor."""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import sys
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

from . import config
from .capture_pipeline import CapturePipeline
from .watch_pipeline import WatchPipeline


class _TeeStream:
    def __init__(self, original, file_obj):
        self.original = original
        self.file_obj = file_obj

    def write(self, data):
        if self.original:
            try:
                self.original.write(data)
                self.original.flush()
            except Exception:
                pass
        if self.file_obj:
            try:
                self.file_obj.write(data)
                self.file_obj.flush()
            except Exception:
                pass

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except Exception:
                pass
        if self.file_obj:
            try:
                self.file_obj.flush()
            except Exception:
                pass


def _setup_linux_logging() -> None:
    if sys.platform == "win32":
        return
    log_dir = config.log_dir()
    os.environ.setdefault("GST_DEBUG", "2")
    os.environ.setdefault("GST_DEBUG_NO_COLOR", "1")
    os.environ.setdefault("GST_DEBUG_FILE", str(log_dir / "gst.log"))

    try:
        log_file = open(log_dir / "luce.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = _TeeStream(sys.stdout, log_file)
        sys.stderr = _TeeStream(sys.stderr, log_file)
    except Exception as exc:
        print(f"[log] erro ao abrir arquivo de log: {exc}", file=sys.stderr)


def cmd_gui(_args: argparse.Namespace) -> int:
    _setup_linux_logging()
    from .gui import main as gui_main

    return gui_main()


def _run_loop(stop, on_ready=None) -> int:
    loop = GLib.MainLoop()
    exit_code = [0]

    def _stop(_signum=None, _frame=None):
        loop.quit()

    def _fail(code: int) -> None:
        exit_code[0] = code
        loop.quit()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    if on_ready is not None:
        on_ready(_fail)
    try:
        loop.run()
    finally:
        stop()
    return exit_code[0]


def cmd_share(args: argparse.Namespace) -> int:
    fd = node = None
    portal = None
    if not args.pattern and sys.platform != "win32":  # no Windows a CLI captura o monitor principal
        from .linux.portal import PortalError, ScreenCast

        print("escolha o monitor na janela do sistema…", flush=True)
        portal = ScreenCast()
        try:
            fd, node = portal.open()
        except PortalError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"capturando o monitor (nó PipeWire {node})", flush=True)

    endpoint = config.whip_endpoint(args.path)
    fail = [lambda _code: None]

    def _failed(message: str) -> None:
        print(f"encerrando: {message}", file=sys.stderr)
        fail[0](1)

    pipeline = CapturePipeline(
        whip_endpoint=endpoint,
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate_kbps=args.bitrate,
        pattern=args.pattern,
        portal_fd=fd,
        portal_node=node,
        audio_enabled=not args.no_audio,
        audio_device=args.audio_device,
        auth_token=config.auth_token(),
        on_pipeline_error=_failed,
    )
    print(f"{args.width}x{args.height}@{args.fps} {args.bitrate} kbps -> {endpoint}", flush=True)
    pipeline.start()

    def _stop_all():
        pipeline.stop()
        if portal is not None:
            portal.close()

    return _run_loop(_stop_all, on_ready=lambda quit_fn: fail.__setitem__(0, quit_fn))


def cmd_watch(args: argparse.Namespace) -> int:
    endpoint = config.whep_endpoint(args.path)
    pipeline = WatchPipeline(whep_endpoint=endpoint, auth_token=config.auth_token())
    print(f"assistindo {endpoint}", flush=True)
    pipeline.start()
    return _run_loop(pipeline.stop)


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Codifica um padrão de teste 5s com o encoder que o app escolheria; com NVIDIA mostra o nvidia-smi."""
    import subprocess

    from .capture_pipeline import _make_encoder

    Gst.init(None)
    missing = [name for name in ("whipsink", "whepsrc") if Gst.ElementFactory.find(name) is None]
    if missing:
        print(f"faltam elementos GStreamer: {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        enc, kind = _make_encoder()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    name = enc.get_factory().get_name()
    pipeline = Gst.Pipeline.new("doctor")
    caps = Gst.ElementFactory.make("capsfilter")
    caps.set_property("caps", Gst.Caps.from_string("video/x-raw,width=1280,height=720,framerate=30/1"))
    chain = [Gst.ElementFactory.make("videotestsrc"), caps, Gst.ElementFactory.make("videoconvert"), enc,
             Gst.ElementFactory.make("h264parse"), Gst.ElementFactory.make("fakesink")]
    chain[0].set_property("is-live", True)
    for el in chain:
        pipeline.add(el)
    if not all(Gst.Element.link(a, b) for a, b in zip(chain, chain[1:])):
        print(f"{name} não aceita o padrão de teste (caps incompatíveis)", file=sys.stderr)
        return 1
    sampler = None
    if kind in ("nvenc", "nvd3d11"):
        try:
            sampler = subprocess.Popen(
                ["nvidia-smi", "--query-gpu=utilization.encoder,utilization.gpu,name", "--format=csv,noheader", "-l", "1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError:
            pass  # no Flatpak a extensão NVIDIA traz as libs, não o nvidia-smi
    pipeline.set_state(Gst.State.PLAYING)
    time.sleep(5)
    pipeline.set_state(Gst.State.NULL)
    if sampler is None:
        print(f"codificou 5s com {name}", flush=True)
        return 0
    sampler.terminate()
    output, _ = sampler.communicate(timeout=3)
    for line in output.splitlines():
        if line.strip():
            print(line.strip(), flush=True)
    return 0


def _name_process() -> None:
    """Linux: btop/top/htop mostram o nome do executável ("python3"); troca por "luce" (PR_SET_NAME)."""
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL(None).prctl(15, b"luce", 0, 0, 0)
        except (OSError, AttributeError):
            pass


def main() -> int:
    _name_process()
    parser = argparse.ArgumentParser(prog="luce")
    sub = parser.add_subparsers(dest="command", required=True)

    share = sub.add_parser("share", help="transmite a tela")
    share.add_argument("--path", required=True, help="nome do canal no relay (ex: test)")
    share.add_argument("--pattern", action="store_true", help="usa videotestsrc em vez do portal")
    share.add_argument("--width", type=int, default=1920)
    share.add_argument("--height", type=int, default=1080)
    share.add_argument("--fps", type=int, default=30)
    share.add_argument("--bitrate", type=int, default=4000, help="kbps")
    share.add_argument("--no-audio", action="store_true", help="transmite mudo (áudio continua indo, só sem som)")
    share.add_argument("--audio-device", default=None, help="nome do dispositivo de entrada (pulsesrc device); padrão = do sistema")
    share.set_defaults(func=cmd_share)

    watch = sub.add_parser("watch", help="assiste a tela de alguém")
    watch.add_argument("path", help="nome do canal no relay (ex: test)")
    watch.set_defaults(func=cmd_watch)

    doctor = sub.add_parser("doctor", help="testa NVENC + elementos necessários")
    doctor.set_defaults(func=cmd_doctor)

    gui = sub.add_parser("gui", help="abre a janela do Project LUCE")
    gui.set_defaults(func=cmd_gui)

    args = parser.parse_args()
    _setup_linux_logging()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
