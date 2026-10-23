"""Quem está transmitindo agora, via API nativa do MediaMTX (sem sala/sinalização própria)."""

from __future__ import annotations

import json
import urllib.request


def list_paths(api_url: str, timeout: float = 2.0) -> list[str]:
    """Nomes dos caminhos ativos no relay. Lista vazia se o relay não responder."""
    try:
        with urllib.request.urlopen(f"{api_url}/v3/paths/list", timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return []
    return [item["name"] for item in data.get("items", []) if item.get("ready")]
