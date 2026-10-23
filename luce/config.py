"""Configurações do Project LUCE, lidas/salvas em ~/.config/project-luce/config.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILE = Path.home() / ".config" / "project-luce" / "config.toml"
# Default de sistema (pacote Arch, ver packaging/config.toml) — deixa o
# pacote já vir configurado pro servidor certo, sem a pessoa editar nada.
SYSTEM_CONFIG_FILE = Path("/etc/project-luce/config.toml")

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
    "auth_password": "",  # senha do grupo (bdk) — BDK_PASSWORD do servidor
}


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    if SYSTEM_CONFIG_FILE.exists():
        data.update(tomllib.loads(SYSTEM_CONFIG_FILE.read_text()))
    if CONFIG_FILE.exists():
        data.update(tomllib.loads(CONFIG_FILE.read_text()))
    return data


def save(data: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n")


def whip_endpoint(path: str) -> str:
    return f"{load()['base_url']}/{path}/whip"


def whep_endpoint(path: str) -> str:
    return f"{load()['base_url']}/{path}/whep"


def auth_token() -> str | None:
    """Vira o header `Authorization: Bearer user:pass` do whipsink/whepsrc.

    Usuário fixo "bdk" pra combinar com o server/sfu_proxy.py — só a senha
    precisa ser configurada.
    """
    password = load()["auth_password"]
    return f"bdk:{password}" if password else None
