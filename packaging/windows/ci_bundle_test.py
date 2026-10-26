"""Teste do bundle do Windows, rodado com o python.exe DE DENTRO do bundle e PATH limpo.

Uso: ucrt64\\bin\\python.exe ci_bundle_test.py <pasta-do-bundle>
Simula a primeira abertura no PC da pessoa: sem MSYS2, sem registro do GStreamer.
"""

import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from luce import winenv  # noqa: E402

data = winenv.setup(root)
registry = data / "gst-registry.bin"
assert not registry.exists(), "o registro já existia antes da primeira abertura"
assert not list(root.rglob("registry*.bin")), "o bundle não pode levar registro do runner"

assert (root / "ucrt64/bin/luce.exe").stat().st_size > 1000, "falta o luce.exe"
assert (root / "version.txt").read_text().strip(), "falta o version.txt"
for icon in ("luce.ico", "ucrt64/share/icons/hicolor/256x256/apps/pro.codestage.Luce.png"):
    assert (root / icon).stat().st_size > 1000, f"falta o ícone {icon}"

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gst, Gtk  # noqa: E402

Gst.init(None)
assert registry.exists(), "o registro não foi gerado na primeira abertura"
assert str(root) not in str(registry), registry
print("GStreamer", Gst.version_string(), "| registro novo em", registry, registry.stat().st_size, "bytes")

need = ["x264enc", "h264parse", "rtph264pay", "opusenc", "whipsink", "whepsrc", "gtk4paintablesink",
        "d3d11screencapturesrc", "d3d11convert", "d3d11download", "wasapi2src", "avdec_h264", "videorate",
        "opusdec", "audioconvert", "audioresample", "volume", "autoaudiosink", "parsebin", "tee",
        "appsrc", "d3d11upload", "webrtcbin", "videoconvert", "queue", "capsfilter"]
missing = [e for e in need if Gst.ElementFactory.find(e) is None]
assert not missing, f"faltam no bundle: {missing}"
print("elementos ok:", len(need))
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

for icon in ("luce.ico", "ucrt64/share/icons/hicolor/256x256/apps/pro.codestage.Luce.png"):  # loaders podados
    GdkPixbuf.Pixbuf.new_from_file(str(root / icon))
print("plugins:", len(Gst.Registry.get().get_plugin_list()))

os.environ["LUCE_ENCODER"] = "x264enc"
from luce.capture_pipeline import CapturePipeline  # noqa: E402

p = CapturePipeline("http://127.0.0.1:1/x/whip", width=1280, height=720, fps=30, preview=False)
print("CapturePipeline monta com", p.encoder_kind)
p.stop()
from luce import gui  # noqa: E402,F401  (imports de Gtk/winsys/config)

print("BUNDLE OK")
