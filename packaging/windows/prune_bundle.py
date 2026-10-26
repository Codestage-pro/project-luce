"""Enxuga o bundle do Windows: só os plugins do GStreamer que o LUCE usa e só as DLLs que eles puxam.

Uso (no shell do MSYS2, depois de extrair o runtime): python prune_bundle.py dist/LUCE
O pacman traz o GStreamer inteiro (AWS, IA, AV1, ImageMagick, tcl/tk...), 900 MB para um app que
só captura, codifica e manda por WebRTC. O teste do bundle (ci_bundle_test.py) garante que nada
necessário saiu.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

# Elementos que o app (ou o webrtcbin por baixo) cria; o plugin de cada um vem do gst-inspect.
ELEMENTS = """
queue tee capsfilter identity appsrc appsink audioconvert audioresample audiotestsrc videotestsrc
videoconvert videoscale videorate volume audiomixer audiorate typefind decodebin3 parsebin
h264parse opusparse opusenc opusdec x264enc openh264dec
rtph264pay rtph264depay rtpopuspay rtpopusdepay rtpbin rtpjitterbuffer rtprtxsend rtprtxreceive
rtpulpfecenc rtpulpfecdec rtpredenc rtpreddec rtpstorage rtpfunnel rtpssrcdemux
webrtcbin nicesrc nicesink dtlssrtpenc dtlssrtpdec srtpenc srtpdec sctpenc sctpdec whipsink whepsrc
d3d11screencapturesrc d3d11convert d3d11download d3d11upload d3d11h264dec
nvh264enc nvh264dec nvd3d11h264enc qsvh264enc mfh264enc amfh264enc
wasapi2src wasapi2sink autoaudiosink autovideosink gtk4paintablesink
""".split()
# Plugins sem elemento próprio que o parsebin/typefind precisam.
EXTRA_PLUGINS = {"typefindfunctions", "playback", "coreelements", "autodetect", "videoparsersbad", "audioparsers",
                 # plugins de GPU: no runner (sem placa) o gst-inspect não os enxerga, mas nos PCs das pessoas funcionam
                 "nvcodec", "qsv", "amfcodec", "mediafoundation", "d3d11"}

# Typelibs de que o app e o GTK dependem (o resto é de bibliotecas que o LUCE não usa).
TYPELIBS = {
    "Gtk", "Gdk", "Gsk", "GdkWin32", "GdkPixbuf", "GdkPixdata", "Gio", "GioWin32", "GLib", "GLibWin32",
    "GModule", "GObject", "Pango", "PangoCairo", "cairo", "Graphene", "HarfBuzz", "freetype2", "fontconfig",
    "Gst", "GstBase", "GstApp", "GstVideo", "GstAudio", "GstPbutils", "GstSdp", "GstWebRTC", "GstRtp",
    "GstNet", "GstTag", "GstGL", "GstAllocators",
}
TOOLS = Path(sys.prefix) / "bin"  # python do MSYS2 UCRT64: gst-inspect e objdump moram ao lado
PIXBUF_LOADERS = ("png", "jpeg", "gif", "ico", "bmp", "svg")  # svg: ícones simbólicos do tema
KEEP_EXES = {"python.exe", "pythonw.exe", "luce.exe", "gspawn-win64-helper.exe", "gspawn-win64-helper-console.exe"}
DROP_DIRS = [
    "lib/frei0r-1", "lib/gst-validate-launcher", "lib/terminfo", "lib/sqlite3*", "lib/tcl*", "lib/tk*",
    "lib/itcl*", "lib/thread*", "lib/tdbc*", "lib/ImageMagick-*", "lib/python3.*/test", "lib/python3.*/idlelib",
    "lib/python3.*/tkinter", "lib/python3.*/turtledemo", "lib/python3.*/ensurepip", "lib/python3.*/pydoc_data",
    "lib/python3.*/lib2to3", "lib/python3.*/site-packages/pip*", "lib/python3.*/site-packages/setuptools*",
    "lib/python3.*/site-packages/pkg_resources", "lib/python3.*/site-packages/imath*", "lib/python3.*/lib-dynload/_tkinter*",
    "share/terminfo", "share/zoneinfo", "share/libwmf", "share/libmysofa", "share/sqlite", "share/xml",
    "share/iso-codes", "share/djvu", "share/ImageMagick-*", "share/ffmpeg", "share/gtk-4.0/emoji",
    "etc/ImageMagick-*",
]


def size_mb(root: Path) -> float:
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) / 1048576


def remove(path: Path) -> None:
    shutil.rmtree(path) if path.is_dir() else path.unlink()


def plugin_files() -> set[str]:
    keep = {f"libgst{name}.dll" for name in EXTRA_PLUGINS}
    for element in ELEMENTS:
        out = subprocess.run([str(TOOLS / "gst-inspect-1.0.exe"), element], capture_output=True, text=True).stdout
        found = re.search(r"^\s*Filename\s+(.+)$", out, re.M)
        if found:
            keep.add(re.split(r"[\\/]", found.group(1).strip())[-1])
        else:
            print(f"aviso: elemento {element} não existe nesta instalação")
    return keep


def imports(dll: Path, cache: dict[Path, list[str]]) -> list[str]:
    if dll not in cache:
        out = subprocess.run([str(TOOLS / "objdump.exe"), "-p", str(dll)], capture_output=True, text=True).stdout
        cache[dll] = [m.lower() for m in re.findall(r"DLL Name:\s*(\S+)", out)]
    return cache[dll]


def main(root: Path) -> None:
    prefix = root / "ucrt64"
    for path in root.rglob("*"):  # o tar do MSYS2 extrai arquivos somente leitura e o Windows não deixa apagar
        path.chmod(0o777)
    before = size_mb(root)

    keep = plugin_files()
    plugins = prefix / "lib/gstreamer-1.0"
    for entry in plugins.iterdir():
        if entry.name not in keep:
            remove(entry)
    print(f"plugins mantidos: {len(list(plugins.glob('*.dll')))}")

    for pattern in DROP_DIRS:
        for path in prefix.glob(pattern):
            remove(path)

    scanner = prefix / "libexec/gstreamer-1.0"
    for entry in scanner.glob("*"):
        if entry.name != "gst-plugin-scanner.exe":
            remove(entry)

    typelibs = prefix / "lib/girepository-1.0"
    for entry in typelibs.glob("*.typelib"):
        if entry.name.rsplit("-", 1)[0] not in TYPELIBS:
            entry.unlink()

    loaders = prefix / "lib/gdk-pixbuf-2.0/2.10.0/loaders"
    for entry in loaders.glob("*.dll"):
        if not any(name in entry.name for name in PIXBUF_LOADERS):
            entry.unlink()
    cache_file = loaders.parent / "loaders.cache"
    if cache_file.exists():  # o cache lista cada loader; entrada de arquivo removido faz o gdk-pixbuf reclamar
        kept = []
        for block in cache_file.read_text().split("\n\n"):
            lines = block.splitlines()
            body = [l for l in lines if not l.startswith("#")]
            if not body or any(name in body[0] for name in PIXBUF_LOADERS):
                kept.append(block)
            elif len(body) < len(lines):  # cabeçalho de comentário colado no primeiro loader
                kept.append("\n".join(l for l in lines if l.startswith("#")))
        cache_file.write_text("\n\n".join(kept) + "\n")

    # Fecho das DLLs: o que os executáveis, plugins, módulos do Python/GTK e typelibs importam, transitivamente.
    bin_dir = prefix / "bin"
    available = {p.name.lower(): p for p in bin_dir.glob("*.dll")}
    roots = [p for p in bin_dir.glob("*.exe") if p.name in KEEP_EXES]
    roots += scanner.glob("*.exe")
    roots += plugins.glob("*.dll")
    roots += [p for p in (prefix / "lib").rglob("*") if p.suffix in (".pyd", ".dll") and p.parent != plugins]
    wanted: set[str] = set()
    for tl in typelibs.glob("*.typelib"):  # a typelib diz qual DLL carregar (shared-library)
        wanted.update(m.decode().lower() for m in re.findall(rb"[\w.+-]+\.dll", tl.read_bytes()))
    cache: dict[Path, list[str]] = {}
    seen: set[Path] = set()
    queue = list(roots) + [available[n] for n in wanted if n in available]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        queue += [available[n] for n in imports(path, cache) if n in available]
    for name, path in available.items():
        if path not in seen:
            path.unlink()
    for exe in bin_dir.glob("*.exe"):
        if exe.name not in KEEP_EXES:
            exe.unlink()

    for folder in sorted(prefix.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    for big in sorted((f for f in root.rglob("*") if f.is_file()), key=lambda f: f.stat().st_size, reverse=True)[:25]:
        print(f"  {big.stat().st_size / 1048576:6.1f} MB  {big.relative_to(root)}")
    print(f"bundle: {before:.0f} MB -> {size_mb(root):.0f} MB, DLLs em bin: {len(list(bin_dir.glob('*.dll')))}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
