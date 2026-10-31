"""Ajustes no webrtcbin que mora dentro do `whipsink`/`whepsrc` (filho único, via GstChildProxy)."""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import stat
import sys

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import GLib, Gst, GstWebRTC  # noqa: F401  (GstWebRTC registra os tipos do webrtcbin)

log = logging.getLogger("luce.rtc")


def _nack(transceiver) -> None:
    # Sem do-nack o webrtcbin anuncia "nack" na SDP mas nunca pede nem reenvia
    # pacote: cada perda vira quadro corrompido até o próximo IDR (visto na SDP:
    # nenhum rtx/90000 oferecido). Com ele a Cloudflare aceita o RTX.
    transceiver.set_property("do-nack", True)


def boost_udp_buffers(size_bytes: int = 4 * 1024 * 1024) -> None:
    """SO_RCVBUF/SO_SNDBUF maiores nos sockets UDP (IPv4/IPv6) abertos pelo processo.

    A 16-32 Mbps / 60 fps o IDR sai (ou chega) em rajada e o buffer padrão do kernel (~200 KB) descarta
    pacote. O socket é do libnice (ler `ice-agent` pelo PyGObject dá SIGSEGV), então varre os fds: só
    setsockopt, sem dup/close. O kernel limita ao net.core.rmem_max/wmem_max.
    shortcut: só Linux (Windows fica no padrão); pega também UDP de outras libs do processo, inofensivo.
    """
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    so_domain = getattr(socket, "SO_DOMAIN", 39)

    def getint(fd: int, opt: int) -> int:
        val, size = ctypes.c_int(0), ctypes.c_uint(ctypes.sizeof(ctypes.c_int))
        if libc.getsockopt(fd, socket.SOL_SOCKET, opt, ctypes.byref(val), ctypes.byref(size)) != 0:
            return -1
        return val.value

    value = ctypes.c_int(size_bytes)
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            if not stat.S_ISSOCK(os.stat(f"/proc/self/fd/{name}").st_mode):
                continue
            if getint(fd, socket.SO_TYPE) != socket.SOCK_DGRAM:
                continue
            if getint(fd, so_domain) not in (socket.AF_INET, socket.AF_INET6):
                continue  # AF_UNIX dgram (syslog, pipewire) não é tráfego de mídia
            for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
                libc.setsockopt(fd, socket.SOL_SOCKET, opt, ctypes.byref(value), ctypes.sizeof(value))
        except (OSError, ValueError):
            continue  # fd fechado entre o listdir e o stat


def enable_nack(element: Gst.Element) -> None:
    """NACK/RTX nos transceivers de `element`.

    O whipsink já tem os transceivers quando o pipeline é montado; o whepsrc só
    cria o webrtcbin (e os transceivers) depois do start, daí o hook.
    """
    def on_webrtcbin(rtc) -> None:
        # jitterbuffer do rtpbin: o padrão (200 ms) soma atraso fixo; 120 ms cobre a ida e volta de um NACK/RTX
        if rtc.find_property("latency") is not None:
            rtc.set_property("latency", 120)
        for i in range(2):  # vídeo e áudio
            tr = rtc.emit("get-transceiver", i)
            if tr is not None:
                _nack(tr)
        rtc.connect("on-new-transceiver", lambda _rtc, tr: _nack(tr))

    # o libnice abre os sockets UDP ao coletar candidatos, um pouco depois do início
    for delay_ms in (1500, 6000):
        GLib.timeout_add(delay_ms, lambda: (boost_udp_buffers(), False)[1])
    if element.get_children_count():
        on_webrtcbin(element.get_child_by_index(0))
    else:
        element.connect("child-added", lambda _bin, child, _name: on_webrtcbin(child))
