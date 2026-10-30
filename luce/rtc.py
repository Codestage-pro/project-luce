"""Ajustes no webrtcbin que mora dentro do `whipsink`/`whepsrc` (filho único, via GstChildProxy)."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstWebRTC  # noqa: F401  (GstWebRTC registra os tipos do webrtcbin)

log = logging.getLogger("luce.rtc")


def _nack(transceiver) -> None:
    # Sem do-nack o webrtcbin anuncia "nack" na SDP mas nunca pede nem reenvia
    # pacote: cada perda vira quadro corrompido até o próximo IDR (visto na SDP:
    # nenhum rtx/90000 oferecido). Com ele a Cloudflare aceita o RTX.
    transceiver.set_property("do-nack", True)


def enable_nack(element: Gst.Element) -> None:
    """NACK/RTX nos transceivers de `element`.

    O whipsink já tem os transceivers quando o pipeline é montado; o whepsrc só
    cria o webrtcbin (e os transceivers) depois do start, daí o hook.
    """
    def on_webrtcbin(rtc) -> None:
        # jitterbuffer do rtpbin: o padrão (200 ms) soma atraso fixo; 120 ms cobre o jitter da internet e a
        # ida e volta de um NACK/RTX até o SFU (80 ms era justo demais e vira travada em Wi-Fi ruim)
        if rtc.find_property("latency") is not None:
            rtc.set_property("latency", 120)
        for i in range(2):  # vídeo e áudio
            tr = rtc.emit("get-transceiver", i)
            if tr is not None:
                _nack(tr)
        rtc.connect("on-new-transceiver", lambda _rtc, tr: _nack(tr))

    if element.get_children_count():
        on_webrtcbin(element.get_child_by_index(0))
    else:
        element.connect("child-added", lambda _bin, child, _name: on_webrtcbin(child))
