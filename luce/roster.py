"""Quem está transmitindo agora, via API nativa do MediaMTX (sem sala/sinalização própria)."""

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


def list_paths(api_url: str, timeout: float = 2.0) -> list[str]:
    """Nomes dos caminhos ativos no relay.

    Levanta a exceção de rede/HTTP em vez de devolver lista vazia: "ninguém
    no ar" e "não falei com o servidor" são coisas diferentes, e confundir as
    duas foi o que escondeu o 403 acima. Quem chama decide o que mostrar.
    """
    req = urllib.request.Request(
        f"{api_url}/v3/paths/list", headers={"User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return [item["name"] for item in data.get("items", []) if item.get("ready")]
