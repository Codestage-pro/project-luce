r"""Ponto de entrada do Project LUCE no Windows (atalho do Menu Iniciar).

pythonw não tem console: tudo que o app imprime vai para %LOCALAPPDATA%\LUCE\luce.log, e o
GStreamer (aviso/erro) para gst.log. Mande esses dois arquivos quando algo não funcionar.
"""

import ctypes
import os
import sys
from pathlib import Path

# mesmo ID do atalho do instalador: a barra de tarefas agrupa e mostra o ícone dele
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pro.codestage.Luce")

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from luce import winenv  # noqa: E402

data = winenv.setup(root)
sys.stdout = sys.stderr = open(data / "luce.log", "w", encoding="utf-8", buffering=1)
os.environ.setdefault("GST_DEBUG", "2")
os.environ.setdefault("GST_DEBUG_NO_COLOR", "1")
os.environ.setdefault("GST_DEBUG_FILE", str(data / "gst.log"))

from luce.__main__ import main  # noqa: E402

sys.argv = ["luce", *(sys.argv[1:] or ["gui"])]
sys.exit(main())
