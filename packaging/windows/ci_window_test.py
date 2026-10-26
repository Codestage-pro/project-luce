"""Teste do CI (Windows): captura de janela via Windows Graphics Capture (window-handle).

Abre uma janela GTK própria (subprocesso) e confere: listagem, áudio por PID, pipeline WGC e quadros chegando.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TITLE = "LUCE-CI-JANELA"


def child() -> None:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    def activate(app):
        win = Gtk.ApplicationWindow(application=app, title=TITLE, default_width=640, default_height=480)
        win.set_child(Gtk.Label(label="LUCE"))
        win.present()
        GLib.timeout_add_seconds(60, app.quit)

    app = Gtk.Application(application_id="pro.codestage.LuceCi")
    app.connect("activate", activate)
    app.run([])


def count_frames(pipe, sink_name: str, seconds: float) -> tuple[int, list[str]]:
    count, errors = [0], []
    pad = pipe.get_by_name(sink_name).get_static_pad("sink")
    pad.add_probe(Gst.PadProbeType.BUFFER, lambda *_: count.__setitem__(0, count[0] + 1) or Gst.PadProbeReturn.OK)
    bus = pipe.get_bus()
    pipe.set_state(Gst.State.PLAYING)
    time.sleep(seconds)
    while (m := bus.pop_filtered(Gst.MessageType.ERROR)):
        errors.append(m.parse_error()[0].message)
    return count[0], errors


def main() -> int:
    from luce import winsys

    proc = subprocess.Popen([sys.executable, __file__, "--window"])
    try:
        hwnd = None
        for _ in range(60):
            hwnd = next((h for h, t in winsys.list_windows() if t == TITLE), None)
            if hwnd:
                break
            time.sleep(0.5)
        assert hwnd, f"janela não apareceu; lista: {winsys.list_windows()}"
        assert winsys.window_pid(hwnd) == proc.pid, (winsys.window_pid(hwnd), proc.pid)
        print("janela:", hwnd, "rect:", winsys.window_rect(hwnd))

        # 1) CapturePipeline em janela: monta com WGC + window-handle, áudio por PID
        os.environ["LUCE_ENCODER"] = "x264enc"
        from luce.capture_pipeline import CapturePipeline

        p = CapturePipeline("http://127.0.0.1:1/x/whip", width=1280, height=720, fps=30, preview=False, window_handle=hwnd)
        audio = p.pipeline.get_by_name("audio-src")
        mode = audio.get_property("loopback-mode").value_nick
        assert mode == "include-process-tree" and audio.get_property("loopback-target-pid") == proc.pid, mode
        src = p.pipeline.get_by_name("src")
        assert src.get_property("capture-api").value_nick == "wgc" and src.get_property("window-handle") == hwnd
        print("pipeline de janela ok: wgc, window-handle", hwnd, "| áudio", mode, audio.get_property("loopback-target-pid"))
        p.stop()

        # 2) WGC entrega quadros da janela e continua entregando com outra janela por cima dela
        pipe = Gst.parse_launch(
            f"d3d11screencapturesrc capture-api=wgc window-handle={hwnd} ! video/x-raw(memory:D3D11Memory),framerate=30/1 "
            "! d3d11convert add-borders=true "
            "! video/x-raw(memory:D3D11Memory),format=NV12,width=1280,height=720,pixel-aspect-ratio=1/1,framerate=30/1 "
            "! d3d11download ! fakesink name=f sync=false"
        )
        before, errors = count_frames(pipe, "f", 2.0)
        cover = subprocess.Popen([sys.executable, __file__, "--window"])  # outra janela com o mesmo título... por cima
        time.sleep(1.0)
        cover.kill()
        after, more = count_frames(pipe, "f", 1.0)
        pipe.set_state(Gst.State.NULL)
        print("quadros WGC:", before, "->", before + after, "| erros:", errors + more)
        assert before > 0 and not errors, ("WGC não entregou quadros", before, errors)
        assert before + after > before, "a captura parou"
    finally:
        proc.kill()
    print("TUDO OK")
    return 0


if __name__ == "__main__":
    Gst.init(None)
    if "--window" in sys.argv:
        child()
    else:
        sys.exit(main())
