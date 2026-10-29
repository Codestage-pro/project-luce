"""Configurações do Project LUCE, lidas/salvas em ~/.config/project-luce/config.toml."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

# XDG_CONFIG_HOME: no Flatpak ele aponta pra ~/.var/app/<id>/config; o ~/.config de
# verdade não existe lá dentro e o que se gravasse nele não ficaria.
_CONFIG_HOME = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
CONFIG_FILE = Path(_CONFIG_HOME or Path.home() / ".config") / "project-luce" / "config.toml"
# Default de sistema (pacote Arch, ver packaging/config.toml) — deixa o
# pacote já vir configurado pro servidor certo, sem a pessoa editar nada.
# /app/etc é onde o Flatpak instala o equivalente (o /etc dele é do runtime).
SYSTEM_CONFIG_FILES = [Path("/etc/project-luce/config.toml"), Path("/app/etc/project-luce/config.toml")]
if os.name == "nt":  # o instalador do Windows põe o default ao lado de luce/
    SYSTEM_CONFIG_FILES.append(Path(__file__).resolve().parents[1] / "config.toml")

DEFAULTS: dict[str, Any] = {
    "base_url": "http://127.0.0.1:8889",  # server/sfu_proxy.py (WHIP, WHEP e roster)
    "name": "",
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "bitrate": 4000,
    "audio": True,
    "audio_bitrate": 128,  # kbps, Opus
    "audio_device": "",  # dispositivo de entrada (pulsesrc device); vazio = padrão do sistema
    "mute_app": "",  # aplicativo a silenciar na transmissão (ex: Discord); vazio = nenhum
    "keep_in_tray": True,  # fechar a janela deixa o app na bandeja; false = fechar encerra o app
    "auth_password": "",  # senha do grupo (bdk) — BDK_PASSWORD do servidor
    "theme": "luce",  # tema visual: luce, murilo, tirigam, ninja_som, dark
    "discord_token": "",  # token OAuth2 autenticado no Discord (membro BDK)
    "discord_user": "",  # tag / username no Discord
}


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    for system_file in SYSTEM_CONFIG_FILES:
        if system_file.exists():
            try:
                data.update(tomllib.loads(system_file.read_text()))
            except Exception as exc:
                print(f"[config] erro ao ler {system_file}: {exc}", file=sys.stderr)
    if CONFIG_FILE.exists():
        try:
            data.update(tomllib.loads(CONFIG_FILE.read_text()))
        except Exception as exc:
            print(f"[config] {CONFIG_FILE} corrompido ou inválido ({exc}), usando padrões", file=sys.stderr)
    return data


def save(data: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            clean = value.replace("\r", "").replace("\n", "").replace('"', '\\"')
            lines.append(f'{key} = "{clean}"')
        else:
            lines.append(f"{key} = {value}")
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            CONFIG_FILE.parent.chmod(0o700)
        except OSError:
            pass
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass


def whip_endpoint(path: str) -> str:
    return f"{load()['base_url']}/{path}/whip"


def whep_endpoint(path: str) -> str:
    return f"{load()['base_url']}/{path}/whep"


def auth_token() -> str | None:
    """Header Authorization para WHIP/WHEP: discord:<token> ou bdk:<password>."""
    cfg = load()
    token = cfg.get("discord_token", "").strip()
    if token:
        return token
    password = cfg.get("auth_password", "").strip()
    return f"bdk:{password}" if password else None
