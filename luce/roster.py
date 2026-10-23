"""Fala com o servidor LUCE (server/sfu_proxy.py): quem está no ar e heartbeat."""

from __future__ import annotations

import json
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


def _request(url: str, token: str | None, method: str = "GET") -> urllib.request.Request:
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, method=method, headers=headers)


def list_paths(base_url: str, token: str | None, timeout: float = 2.0) -> list[str]:
    """Nomes de quem está transmitindo agora.

    Levanta a exceção de rede/HTTP em vez de devolver lista vazia: "ninguém
    no ar" e "não falei com o servidor" são coisas diferentes, e confundir as
    duas foi o que escondeu o 403 acima. Quem chama decide o que mostrar.
    """
    with urllib.request.urlopen(_request(f"{base_url}/v3/paths/list", token), timeout=timeout) as resp:
        data = json.load(resp)
    return [item["name"] for item in data.get("items", []) if item.get("ready")]


def heartbeat(whip_endpoint: str, token: str | None, timeout: float = 5.0) -> None:
    """Avisa o servidor que o publisher segue vivo (sem isso ele some do roster em ~30s)."""
    urllib.request.urlopen(_request(whip_endpoint, token, "PATCH"), timeout=timeout).close()
