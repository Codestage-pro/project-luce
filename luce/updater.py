"""Atualização do app no Windows: acha a Release mais nova no GitHub, baixa o instalador e roda.

O Flatpak se atualiza sozinho; no Windows o Luce-Setup.exe da Release oficial (tag v*) faz isso.
Prerelease (windows-test) não conta: só quem instala dela atualiza dela, na mão.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

API = "https://api.github.com/repos/Codestage-pro/project-luce/releases/latest"
ROOT = Path(__file__).resolve().parents[1]  # no bundle: a pasta com version.txt e luce.ico


def _numbers(version: str) -> tuple[int, ...]:
    """'v1.0.16' / '1.0.15-probe.7' -> (1, 0, 16) / (1, 0, 15)."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0].lstrip("v")))


def current_version() -> str | None:
    """None fora do bundle (rodando do repositório): nesse caso não há o que atualizar."""
    try:
        return (ROOT / "version.txt").read_text().strip()
    except OSError:
        return None


def find_update() -> tuple[str, str, str | None] | None:
    """(versão nova, url do instalador, sha256) se há Release mais nova que a instalada."""
    mine = current_version()
    if mine is None:
        return None
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "project-luce"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        release = json.load(resp)
    if _numbers(release["tag_name"]) <= _numbers(mine):
        return None
    for asset in release["assets"]:
        if asset["name"] == "Luce-Setup.exe":
            digest = asset.get("digest") or ""
            return release["tag_name"].lstrip("v"), asset["browser_download_url"], digest.removeprefix("sha256:") or None
    return None


def download(url: str, sha256: str | None, progress) -> Path:
    """Baixa o instalador para a pasta temporária; progress(fração) a cada pedaço."""
    dest = Path(tempfile.gettempdir()) / "Luce-Setup.exe"
    hasher = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "project-luce"}), timeout=30) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                if total:
                    progress(done / total)
    if sha256 and hasher.hexdigest() != sha256:
        dest.unlink(missing_ok=True)
        raise RuntimeError("o instalador baixado não confere com o publicado (sha256)")
    return dest


def run_installer(path: Path) -> None:
    """Instala por cima (mesma pasta, mesmo AppId); o instalador fecha o app e abre de novo no fim."""
    subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS"], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
