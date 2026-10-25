"""Teste do CI (Windows): captura de janela = monitor + crop que acompanha a janela.

Abre uma janela GTK própria (subprocesso), e confere: listagem, retângulo, crop, áudio por PID,
crop dinâmico num d3d11screencapturesrc em PLAYING e o _follow_window do CapturePipeline.
"""

import os
import subprocess
import sys
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

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


def main() -> int:
    from luce import winsys
    from luce.winsys import compute_crop

    mon = (1920, 0, 3840, 1080)
    assert compute_crop((1900, -8, 2500, 600), mon) == (0, 0, 580, 600)
    for win in [(3800, 1000, 4200, 1300), (5000, 2000, 5100, 2100), (-500, -500, -400, -400)]:
        x, y, w, h = compute_crop(win, mon)
        assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080 and w % 2 == 0 and h % 2 == 0, (win, x, y, w, h)
    print("compute_crop ok")

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
        print("janela:", hwnd, "rect:", winsys.window_rect(hwnd), "crop:", winsys.crop_for(hwnd))
        winsys.bring_to_front(hwnd)

        # 1) CapturePipeline em janela: monta, áudio por PID, tamanho fixo
        os.environ["LUCE_ENCODER"] = "x264enc"
        from luce.capture_pipeline import CapturePipeline

        p = CapturePipeline("http://127.0.0.1:1/x/whip", width=1280, height=720, fps=30, preview=False, window_handle=hwnd)
        audio = p.pipeline.get_by_name("audio-src")
        mode = audio.get_property("loopback-mode").value_nick
        assert mode == "include-process-tree" and audio.get_property("loopback-target-pid") == proc.pid, mode
        src = p.pipeline.get_by_name("src")
        got = tuple(src.get_property(n) for n in ("crop-x", "crop-y", "crop-width", "crop-height"))
        assert got == p._crop == tuple(winsys.crop_for(hwnd)[1:]), (got, p._crop)
        print("pipeline de janela ok, crop", got, "áudio", mode, audio.get_property("loopback-target-pid"))

        # 2) a janela se mexe e muda de tamanho: o _follow_window acompanha, a saída segue fixa
        winsys.user32.MoveWindow(hwnd, 150, 120, 500, 400, True)
        time.sleep(0.5)
        assert p._follow_window() is True
        new = tuple(src.get_property(n) for n in ("crop-x", "crop-y", "crop-width", "crop-height"))
        assert new == p._crop and new != got, (new, got)
        print("crop acompanhou:", new)
        winsys.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE: pausa o acompanhamento
        time.sleep(0.5)
        assert p._follow_window() is True and tuple(src.get_property(n) for n in ("crop-x", "crop-y", "crop-width", "crop-height")) == new
        winsys.bring_to_front(hwnd)
        assert not winsys.is_iconic(hwnd), "ShowWindow(SW_RESTORE) não restaurou"
        print("minimizar/restaurar ok")

        # 3) crop mudando com o d3d11screencapturesrc em PLAYING (cadeia sem encoder, só a captura)
        hmon, x, y, w, h = winsys.crop_for(hwnd)
        pipe = Gst.parse_launch(
            f"d3d11screencapturesrc name=s monitor-handle={hmon} crop-x={x} crop-y={y} crop-width={w} crop-height={h} "
            "! video/x-raw(memory:D3D11Memory),framerate=30/1 ! d3d11convert add-borders=true "
            "! video/x-raw(memory:D3D11Memory),format=NV12,width=1280,height=720,framerate=30/1 "
            "! d3d11download ! fakesink name=f sync=false"
        )
        errors, count = [], [0]
        pipe.get_by_name("f").get_static_pad("sink").add_probe(
            Gst.PadProbeType.BUFFER, lambda *_: count.__setitem__(0, count[0] + 1) or Gst.PadProbeReturn.OK
        )
        bus = pipe.get_bus()
        pipe.set_state(Gst.State.PLAYING)
        time.sleep(1.5)
        before = count[0]
        s = pipe.get_by_name("s")
        s.set_property("crop-x", 0); s.set_property("crop-y", 0)
        s.set_property("crop-width", 400); s.set_property("crop-height", 300)
        time.sleep(1.5)
        s.set_property("crop-width", 800); s.set_property("crop-height", 600)
        time.sleep(1.5)
        while (m := bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.WARNING)):
            errors.append(m.parse_error()[0].message if m.type == Gst.MessageType.ERROR else m.parse_warning()[0].message)
        caps = pipe.get_by_name("f").get_static_pad("sink").get_current_caps().to_string()
        pipe.set_state(Gst.State.NULL)
        print("quadros antes/depois:", before, count[0], "| avisos:", errors, "| caps na saída:", caps)
        assert count[0] > before > 0, "a captura parou ao mudar o crop"
        assert "width=(int)1280" in caps and "height=(int)720" in caps, caps
        assert not any("rror" in e for e in errors), errors
        p.stop()
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
