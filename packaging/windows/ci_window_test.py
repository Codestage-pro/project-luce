"""Teste do CI (Windows): captura de janela via PrintWindow (WindowGrabber -> appsrc).

Abre uma janela GTK própria (subprocesso) e confere: listagem, áudio por PID, quadros não pretos e chegando no encoder.
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

        # 1) CapturePipeline em janela: monta com appsrc, áudio por PID
        os.environ["LUCE_ENCODER"] = "x264enc"
        from luce.capture_pipeline import CapturePipeline

        p = CapturePipeline("http://127.0.0.1:1/x/whip", width=1280, height=720, fps=30, preview=False, window_handle=hwnd)
        audio = p.pipeline.get_by_name("audio-src")
        mode = audio.get_property("loopback-mode").value_nick
        assert mode == "include-process-tree" and audio.get_property("loopback-target-pid") == proc.pid, mode
        src = p.pipeline.get_by_name("src")
        assert src.get_factory().get_name() == "appsrc", src.get_factory().get_name()
        print("pipeline de janela ok: appsrc | áudio", mode, audio.get_property("loopback-target-pid"))
        p.stop()

        # janela que o PrintWindow não copia: recorte do monitor pelo d3d11screencapturesrc
        os.environ["LUCE_WINDOW_MODE"] = "crop"
        p = CapturePipeline("http://127.0.0.1:1/x/whip", width=1280, height=720, fps=30, preview=False, window_handle=hwnd)
        src = p.pipeline.get_by_name("src")
        assert src.get_factory().get_name() == "d3d11screencapturesrc", src.get_factory().get_name()
        print("recorte ok:", p._crop, [src.get_property(k) for k in ("crop-x", "crop-y", "crop-width", "crop-height")])
        assert p._crop and src.get_property("crop-width") == p._crop[3], p._crop
        p.stop()
        del os.environ["LUCE_WINDOW_MODE"]

        # 2) WindowGrabber devolve o conteúdo da janela (não preto) e os quadros atravessam upload -> convert -> download
        grabber = winsys.WindowGrabber(hwnd)
        print(f"WindowGrabber backend: {'WGC (GPU D3D11)' if grabber.is_wgc else 'PrintWindow (GDI CPU)'}")
        frame = None
        for _ in range(20):  # o GTK demora um pouco pra desenhar a primeira vez
            frame = grabber.grab()
            if frame and any(frame[2][i] for i in range(0, len(frame[2]), 4097)):
                break
            time.sleep(0.25)
        assert frame, f"WindowGrabber não devolveu quadro ({grabber.reason})"
        w, h, data = frame
        assert w % 2 == 0 and h % 2 == 0 and len(data) == w * h * 4, (w, h, len(data))
        assert any(data[i] for i in range(0, len(data), 4097)), "quadro todo preto"
        print(f"grab ok ({'wgc' if grabber.is_wgc else 'gdi'}):", w, "x", h)

        # cursor: informativo (o runner pode não ter cursor visível)
        left, top, right, bottom = winsys.window_rect(hwnd)
        winsys.user32.SetCursorPos(0, 0)
        away = grabber.grab()
        winsys.user32.SetCursorPos((left + right) // 2, (top + bottom) // 2)
        over = grabber.grab()
        print("cursor aparece no quadro:", bool(away and over and away[2] != over[2]))

        pipe = Gst.parse_launch(
            "appsrc name=a is-live=true format=time do-timestamp=true "
            f"caps=video/x-raw,format=BGRx,width={w},height={h},framerate=30/1 "
            "! d3d11upload ! d3d11convert "
            "! video/x-raw(memory:D3D11Memory),format=NV12,width=1280,height=720 ! d3d11download ! fakesink name=f sync=false"
        )
        feed_stop = []

        def feed():
            while not feed_stop:
                got = grabber.grab()
                if got:
                    pipe.get_by_name("a").emit("push-buffer", Gst.Buffer.new_wrapped(got[2]))
                time.sleep(1 / 30)

        import threading
        threading.Thread(target=feed, daemon=True).start()
        n, errors = count_frames(pipe, "f", 3.0)
        feed_stop.append(1)
        pipe.set_state(Gst.State.NULL)
        grabber.close()
        print("quadros no pipeline:", n, "| erros:", errors)
        assert n > 10 and not errors, (n, errors)
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
