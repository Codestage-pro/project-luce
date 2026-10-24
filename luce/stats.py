"""Estatísticas em tempo real do WebRTC (pacotes, bytes, perda, jitter).

O `whipsink`/`whepsrc` escondem um `webrtcbin` como único filho (GstChildProxy);
o `get-stats` dele devolve as mesmas métricas do `getStats()` do navegador.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst


def request(element: Gst.Element, on_done) -> None:
    """Pede os números ao webrtcbin de `element`; `on_done(dict | None)` roda
    numa thread do GStreamer (quem mexe em widget deve usar GLib.idle_add).

    Chaves: bytes, packets, lost, jitter_ms, time. `None` se o webrtcbin ainda
    não existe (o whepsrc só o cria ao negociar).
    """
    if element is None or element.get_children_count() == 0:
        on_done(None)
        return
    rtc = element.get_child_by_index(0)

    def done(promise, *_):
        promise.wait()
        reply = promise.get_reply()
        out = {"bytes": 0, "packets": 0, "lost": 0, "jitter_ms": 0.0, "time": time.monotonic()}
        for i in range(reply.n_fields()):
            stat = reply.get_value(reply.nth_field_name(i))
            kind = stat.get_name()
            if kind in ("outbound-rtp", "inbound-rtp"):
                out["bytes"] += stat.get_value("bytes-sent" if kind == "outbound-rtp" else "bytes-received") or 0
                out["packets"] += stat.get_value("packets-sent" if kind == "outbound-rtp" else "packets-received") or 0
            if kind in ("remote-inbound-rtp", "inbound-rtp"):  # perda medida por quem recebe
                out["lost"] += max(stat.get_value("packets-lost") or 0, 0)
                out["jitter_ms"] = max(out["jitter_ms"], (stat.get_value("jitter") or 0.0) * 1000)
        on_done(out)

    rtc.emit("get-stats", None, Gst.Promise.new_with_change_func(done, None))


def format_line(arrow: str, now: dict, before: dict | None) -> str:
    """Uma linha pra mostrar na tela, com o bitrate calculado entre duas leituras."""
    kbps = 0.0
    if before and now["time"] > before["time"]:
        kbps = max(now["bytes"] - before["bytes"], 0) * 8 / 1000 / (now["time"] - before["time"])
    rate = f"{kbps / 1000:.1f} Mbps" if kbps >= 1000 else f"{kbps:.0f} kbps"
    return f"{arrow} {rate} · {now['packets']} pacotes · {now['lost']} perdidos · jitter {now['jitter_ms']:.1f} ms"
