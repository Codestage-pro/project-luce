"""Captura de tela pelo xdg-desktop-portal.

O portal devolve um fd do PipeWire e o id do nó. O GStreamer lê esse fd;
a imagem não passa por nenhum navegador. Portado de golive-linux/golive/portal.py
sem alterar a lógica (já validada).
"""

from __future__ import annotations

import logging
import os

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

log = logging.getLogger("luce.portal")

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"


class PortalError(RuntimeError):
    pass


class ScreenCast:
    def __init__(self) -> None:
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.portal = self.bus.get_object(PORTAL_NAME, PORTAL_PATH)
        self.iface = dbus.Interface(self.portal, "org.freedesktop.portal.ScreenCast")
        self.fd: int | None = None
        self.node_id: int | None = None
        self._session: str | None = None
        self._response: tuple | None = None
        self._open_error: str | None = None

    def open(self) -> tuple[int, int]:
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self._request(sender, f"luce_create_{os.getpid()}", self._on_session, self._create)
        self._wait("sessão")
        if not self._session:
            raise PortalError("o portal não abriu a sessão de captura")

        self._request(sender, f"luce_select_{os.getpid()}", self._on_ok, self._select)
        self._wait("fontes")

        self._request(sender, f"luce_start_{os.getpid()}", self._on_start, self._start)
        self._wait("captura")
        if self.fd is None or self.node_id is None:
            raise PortalError("o portal não devolveu o vídeo")
        return self.fd, self.node_id

    def close(self) -> None:
        if self.fd is None:
            return
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None

    def _request(self, sender: str, token: str, callback, call) -> None:
        self._response = None
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        self.bus.add_signal_receiver(
            callback,
            signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path=path,
        )
        call(token)

    def _create(self, token: str) -> None:
        self.iface.CreateSession({
            "handle_token": dbus.String(token),
            "session_handle_token": dbus.String(f"luce_s_{os.getpid()}"),
        })

    def _select(self, token: str) -> None:
        options = {
            "handle_token": dbus.String(token),
            "types": dbus.UInt32(1 | 2),  # 1=monitor, 2=janela — deixa escolher os dois
            "multiple": dbus.Boolean(False),
            "cursor_mode": dbus.UInt32(2),
            "persist_mode": dbus.UInt32(0),  # sem restore_token: sempre mostra o seletor de tela
        }
        self.iface.SelectSources(self._session, options)

    def _start(self, token: str) -> None:
        self.iface.Start(self._session, "", {"handle_token": dbus.String(token)})

    def _wait(self, step: str) -> None:
        while self._response is None:
            GLib.MainContext.default().iteration(True)
        code, results = self._response
        self._response = None
        if code != 0:
            raise PortalError(f"o portal recusou a {step} (código {code})")
        if self._open_error:
            raise PortalError(self._open_error)
        self._last = results

    def _on_session(self, code, results) -> None:
        self._response = (int(code), results)
        if int(code) == 0:
            self._session = str(results["session_handle"])

    def _on_ok(self, code, results) -> None:
        self._response = (int(code), results)

    def _on_start(self, code, results) -> None:
        try:
            if int(code) != 0:
                return
            streams = list(results.get("streams") or [])
            if not streams:
                return
            node_id, _props = streams[0]
            self.node_id = int(node_id)
            raw = self.iface.OpenPipeWireRemote(self._session, dbus.Dictionary({}, signature="sv"))
            self.fd = _owned_fd(raw)
            log.info("captura portal nó %s fd %s", self.node_id, self.fd)
        except Exception as exc:
            self._open_error = f"não consegui abrir o PipeWire do portal: {exc}"
        finally:
            self._response = (int(code), results)


def _owned_fd(raw) -> int:
    """O portal devolve um UnixFd. Nesta versão do dbus a classe não está em `dbus`."""
    take = getattr(raw, "take", None)
    if callable(take):
        return int(take())
    return os.dup(int(raw))
