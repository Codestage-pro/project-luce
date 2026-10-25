"""CLI do Project LUCE: luce share | luce watch <nome> | luce doctor."""

from __future__ import annotations

import argparse
import signal
import sys
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

from . import config
from .capture_pipeline import CapturePipeline
from .watch_pipeline import WatchPipeline


def cmd_gui(_args: argparse.Namespace) -> int:
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
        from .portal import PortalError, ScreenCast

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
    """Codifica um padrão de teste 5s e mostra as amostras do nvidia-smi."""
    import subprocess

    Gst.init(None)
    missing = [name for name in ("nvh264enc", "nvh264dec", "whipsink", "whepsrc") if Gst.ElementFactory.find(name) is None]
    if missing:
        print(f"faltam elementos GStreamer: {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        sampler = subprocess.Popen(
            ["nvidia-smi", "--query-gpu=utilization.encoder,utilization.gpu,name", "--format=csv,noheader", "-l", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        sampler = None  # no Flatpak a extensão NVIDIA traz as libs, não o nvidia-smi
    pipeline = Gst.parse_launch(
        "videotestsrc is-live=true pattern=ball ! "
        "video/x-raw,width=1280,height=720,framerate=30/1 ! "
        "nvh264enc bitrate=4000 gop-size=60 zerolatency=true ! "
        "h264parse ! fakesink sync=true"
    )
    pipeline.set_state(Gst.State.PLAYING)
    time.sleep(5)
    pipeline.set_state(Gst.State.NULL)
    if sampler is None:
        print("codificou 5s com nvh264enc (sem nvidia-smi pra medir a GPU)", flush=True)
        return 0
    sampler.terminate()
    output, _ = sampler.communicate(timeout=3)
    for line in output.splitlines():
        if line.strip():
            print(line.strip(), flush=True)
    return 0


def main() -> int:
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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
