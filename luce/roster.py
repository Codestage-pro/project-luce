"""Fala com o servidor LUCE (server/sfu_proxy.py): quem está no ar e heartbeat."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request


# O Cloudflare (que fica na frente do servidor público) devolve 403 pro
# User-Agent padrão do urllib ("Python-urllib/3.x"), o que deixava o roster
# vazio pra todo mundo mesmo com gente no ar. UA de navegador em vez de um
# nome próprio: as regras de bot do Cloudflare são sobre parecer navegador,
# e um UA próprio pode voltar a ser barrado se elas apertarem.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


LAST_AVATARS: dict[str, str] = {}


def _request(url: str, token: str | None, method: str = "GET") -> urllib.request.Request:
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, method=method, headers=headers)


def fetch_state(
    base_url: str,
    token: str | None,
    viewer_name: str = "",
    timeout: float = 2.0,
    avatar_url: str = "",
) -> tuple[dict[str, list[str]], int, list[str], dict[str, str]]:
    """Consulta o roster completo: (paths_dict, online_count, online_users, avatars)."""
    global LAST_AVATARS
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if viewer_name:
        headers["X-Luce-Viewer"] = viewer_name
    if avatar_url and avatar_url.startswith("https://cdn.discordapp.com/"):
        headers["X-Luce-Avatar"] = avatar_url
    req = urllib.request.Request(f"{base_url}/v3/paths/list", method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    paths = {item["name"]: item.get("viewers", []) for item in data.get("items", []) if item.get("ready")}
    online_count = int(data.get("online_count", len(paths)))
    online_users = [str(u) for u in data.get("online_users", list(paths.keys()))]
    avatars = {str(k): str(v) for k, v in data.get("avatars", {}).items() if isinstance(v, str)}
    for item in data.get("items", []):
        name = item.get("name")
        av = item.get("avatar")
        if name and av and isinstance(av, str):
            avatars[name] = av
    LAST_AVATARS = avatars
    return paths, online_count, online_users, avatars


def list_paths(base_url: str, token: str | None, timeout: float = 2.0) -> dict[str, list[str]]:
    """Quem está transmitindo agora: nome -> nomes de quem assiste.

    Levanta a exceção de rede/HTTP em vez de devolver lista vazia: "ninguém
    no ar" e "não falei com o servidor" são coisas diferentes, e confundir as
    duas foi o que escondeu o 403 acima. Quem chama decide o que mostrar.
    """
    paths, _count, _users, _avatars = fetch_state(base_url, token, timeout=timeout)
    return paths


def heartbeat(whip_endpoint: str, token: str | None, timeout: float = 5.0) -> None:
    """Avisa o servidor que o publisher segue vivo (sem isso ele some do roster em ~30s)."""
    urllib.request.urlopen(_request(whip_endpoint, token, "PATCH"), timeout=timeout).close()


def watching(base_url: str, stream: str, viewer: str, token: str | None, on: bool = True, timeout: float = 5.0) -> None:
    """Avisa o servidor que `viewer` assiste (ou parou de assistir) `stream`.

    Servidor antigo (sem a rota) responde 404: quem chama ignora, assistir
    segue funcionando, só não aparece na lista de espectadores.
    """
    s_safe = urllib.parse.quote(stream, safe="._-")
    v_safe = urllib.parse.quote(viewer, safe="._-")
    url = f"{base_url}/{s_safe}/watch?viewer={v_safe}"
    urllib.request.urlopen(_request(url, token, "PATCH" if on else "DELETE"), timeout=timeout).close()
