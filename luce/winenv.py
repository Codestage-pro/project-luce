"""Windows empacotado: aponta PATH, GStreamer, typelibs e o registro para o bundle.

Chamar ANTES de importar `gi`. O registro do GStreamer fica em %LOCALAPPDATA%\\LUCE: ele é
gerado na primeira abertura, no PC da pessoa (um registro copiado de outra máquina lista
plugins de GPU que lá não existem).
"""

from __future__ import annotations

import os
from pathlib import Path


def setup(root: Path) -> Path:
    """`root` é a pasta que contém ucrt64/. Devolve a pasta de dados do app."""
    prefix = root / "ucrt64"
    data = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LUCE"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["PATH"] = f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    os.add_dll_directory(str(prefix / "bin"))
    # Plugin e scanner só do bundle: um GStreamer instalado por fora não pode entrar na mistura.
    for var in ("GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0"):
        os.environ.pop(var, None)
    os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = str(prefix / "lib" / "gstreamer-1.0")
    os.environ["GST_PLUGIN_SCANNER"] = str(prefix / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner.exe")
    os.environ["GST_REGISTRY"] = str(data / "gst-registry.bin")
    os.environ["GI_TYPELIB_PATH"] = str(prefix / "lib" / "girepository-1.0")
    return data
