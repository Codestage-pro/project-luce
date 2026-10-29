"""Atualização do app no Windows e no Linux (Flatpak).

No Windows: acha a Release mais nova no GitHub, baixa o Luce-Setup.exe e roda.
No Linux (Flatpak): avisa quando há versão nova no GitHub e dispara a atualização via portal.
Prerelease (windows-test) não conta: só quem instala dela atualiza dela, na mão.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

API_URLS = [
    "https://api.github.com/repos/Codestage-pro/project-luce/releases?per_page=15",
    "https://api.github.com/repos/MuriloX06/project-luce/releases?per_page=15",
]
ROOT = Path(__file__).resolve().parents[1]  # no bundle: a pasta com version.txt e luce.ico
BUILTIN_VERSION = "1.0.32"


def is_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def _numbers(version: str) -> tuple[int, ...]:
    """'v1.0.16' / '1.0.15-probe.7' -> (1, 0, 16) / (1, 0, 15)."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0].lstrip("v")))


def current_version() -> str:
    """Retorna a versão instalada atual procurando metainfo, version.txt ou builtin."""
    candidates = [
        Path("/app/share/metainfo/pro.codestage.Luce.metainfo.xml"),
        Path("/usr/share/metainfo/pro.codestage.Luce.metainfo.xml"),
        ROOT / "packaging" / "pro.codestage.Luce.metainfo.xml",
    ]
    for p in candidates:
        if p.exists():
            try:
                xml = p.read_text(encoding="utf-8")
                m = re.search(r'<release\s+version="([^"]+)"', xml)
                if m:
                    return m.group(1).strip()
            except OSError:
                pass

    version_file = ROOT / "version.txt"
    if version_file.exists():
        try:
            v = version_file.read_text(encoding="utf-8").strip()
            if v:
                return v
        except OSError:
            pass

    return BUILTIN_VERSION


def find_update() -> tuple[str, str, str | None] | None:
    """(versão nova, url do instalador/release, sha256) se há Release mais nova que a instalada."""
    mine = current_version()
    releases = []
    for api_url in API_URLS:
        try:
            req = urllib.request.Request(
                api_url,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "project-luce"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if isinstance(data, list) and data:
                    releases = data
                    break
        except Exception:
            continue

    if not releases:
        return None

    for release in releases:
        if release.get("draft"):
            continue
        tag = release.get("tag_name", "")
        if tag in ("windows-test", "android-test", "android-latest"):
            continue
        # Ignora releases exclusivas de Android
        if tag.startswith("android-") or "android" in tag.lower():
            continue
        if not (tag.startswith("v") or tag.startswith("windows-v") or tag.startswith("linux-v")):
            continue

        clean_tag = tag.removeprefix("windows-v").removeprefix("linux-v").removeprefix("v")
        if _numbers(clean_tag) > _numbers(mine):
            if sys.platform != "win32":
                url = release.get("html_url") or "https://github.com/Codestage-pro/project-luce/releases/latest"
                return tag, url, None

            # No Windows procura o Luce-Setup.exe
            if release.get("prerelease"):
                continue
            for asset in release.get("assets", []):
                if asset.get("name") == "Luce-Setup.exe":
                    url = asset.get("browser_download_url", "")
                    if not url.startswith("https://"):
                        continue
                    digest = asset.get("digest") or ""
                    sha256 = digest.removeprefix("sha256:").strip() or None
                    if not sha256:
                        m = re.search(r"(?i)sha256[:\s=]+([a-f0-9]{64})", release.get("body", ""))
                        if m:
                            sha256 = m.group(1).lower()
                    return tag, url, sha256
    return None


def download(url: str, sha256: str | None, progress) -> Path:
    """Baixa o instalador para a pasta temporária privada; progress(fração) a cada pedaço."""
    if not url.startswith("https://"):
        raise ValueError("URL de download insegura (deve ser HTTPS)")
    temp_dir = Path(tempfile.mkdtemp(prefix="luce_update_"))
    dest = temp_dir / "Luce-Setup.exe"
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
    if sha256 and hasher.hexdigest().lower() != sha256.lower():
        dest.unlink(missing_ok=True)
        try:
            temp_dir.rmdir()
        except OSError:
            pass
        raise RuntimeError("o instalador baixado não confere com o publicado (sha256)")
    return dest


def run_installer(path: Path) -> None:
    """Instala por cima (mesma pasta, mesmo AppId); o instalador fecha o app e abre de novo no fim."""
    subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART"], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def flatpak_update(progress_cb, done_cb) -> None:
    """Atualiza o Flatpak via portal e avisa progress_cb(0..100) e done_cb(erro | None)."""
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()

    # Se a gente tiver acesso ao PermissionStore (finish-args), já garante a permissão
    try:
        ps = bus.get_object("org.freedesktop.impl.portal.PermissionStore", "/org/freedesktop/impl/portal/PermissionStore")
        iface_ps = dbus.Interface(ps, "org.freedesktop.impl.portal.PermissionStore")
        iface_ps.SetPermission("flatpak", dbus.Boolean(True), "updates", "pro.codestage.Luce", ["yes"])
    except Exception:
        pass

    portal = bus.get_object("org.freedesktop.portal.Flatpak", "/org/freedesktop/portal/Flatpak")
    portal_iface = dbus.Interface(portal, "org.freedesktop.portal.Flatpak")
    monitor_path = portal_iface.CreateUpdateMonitor({})
    monitor = bus.get_object("org.freedesktop.portal.Flatpak", monitor_path)
    monitor_iface = dbus.Interface(monitor, "org.freedesktop.portal.Flatpak.UpdateMonitor")

    finished = False

    def on_progress(info):
        nonlocal finished
        if finished:
            return
        status = int(info.get("status", 0))
        prog = int(info.get("progress", 0))
        if status == 0:  # RUNNING
            GLib.idle_add(progress_cb, prog)
        elif status in (1, 2):  # 1: EMPTY, 2: DONE
            finished = True
            GLib.idle_add(done_cb, None)
        elif status == 3:  # ERROR
            finished = True
            err_msg = str(info.get("error_message") or info.get("error") or "Erro ao atualizar Flatpak")
            GLib.idle_add(done_cb, RuntimeError(err_msg))

    bus.add_signal_receiver(
        on_progress,
        dbus_interface="org.freedesktop.portal.Flatpak.UpdateMonitor",
        signal_name="Progress",
        path=monitor_path,
    )

    try:
        monitor_iface.Update("", {})
    except Exception as exc:
        if not finished:
            finished = True
            GLib.idle_add(done_cb, exc)


def restart_flatpak() -> None:
    """Abre a versão nova via portal Spawn (LATEST_VERSION = 2); quem chama encerra o app logo depois."""
    import dbus
    bus = dbus.SessionBus()
    portal = bus.get_object("org.freedesktop.portal.Flatpak", "/org/freedesktop/portal/Flatpak")
    iface = dbus.Interface(portal, "org.freedesktop.portal.Flatpak")
    # Cada argumento precisa do NUL no fim: sem ele o portal devolve um PID e nunca roda o comando.
    # O sleep deixa a instância velha soltar o nome no D-Bus (senão a nova só ativa a velha e sai).
    cmd = [dbus.ByteArray(arg + b"\0") for arg in (b"sh", b"-c", b"sleep 2 && exec luce")]
    iface.Spawn(dbus.ByteArray(b"/\0"), cmd, {}, {}, dbus.UInt32(2), {})
