"""Captura de tela pelo xdg-desktop-portal.

O portal devolve um fd do PipeWire e o id do nó. O GStreamer lê esse fd;
a imagem não passa por nenhum navegador. Portado de golive-linux/golive/portal.py
sem alterar a lógica (já validada).
"""

from __future__ import annotations

import logging
import os

import itertools

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

log = logging.getLogger("luce.portal")

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

# Os caminhos de Request e de Session no portal são montados a partir destes
# tokens, então dois ScreenCast do MESMO processo (trocar de tela e reiniciar
# criam um novo sem matar o antigo na hora) não podem compartilhá-los: com
# token fixo por PID, a resposta da sessão nova chegava nos dois objetos, o
# antigo passava a apontar pro session_handle novo e o close() dele matava a
# sessão que acabara de nascer — daí o "AccessDenied: Invalid session".
_instances = itertools.count()


class PortalError(RuntimeError):
    pass


def screencast_available() -> bool:
    """O portal exporta ScreenCast? Só com backend que implemente isso.

    O xdg-desktop-portal sozinho não captura nada: quem captura é o backend
    do ambiente (gnome, kde, wlr, hyprland…). Quem roda XFCE/Cinnamon/i3 e só
    tem o xdg-desktop-portal-gtk instalado NÃO tem ScreenCast — o seletor de
    tela nunca aparece. Em vez de travar esperando, a GUI pergunta isso antes
    e cai pro ximagesrc (ver capture_pipeline, x11=True).
    """
    try:
        bus = dbus.SessionBus()
        props = dbus.Interface(
            bus.get_object(PORTAL_NAME, PORTAL_PATH), "org.freedesktop.DBus.Properties"
        )
        props.Get("org.freedesktop.portal.ScreenCast", "AvailableSourceTypes")
        return True
    except Exception as exc:
        log.info("sem portal de captura: %s", exc)
        return False


def x11_session() -> bool:
    """Dá pra capturar com ximagesrc? Só num X11 de verdade.

    Sob Wayland existe DISPLAY (XWayland), mas a raiz do XWayland só enxerga
    as janelas X — capturar ali devolveria tela preta, então exige que o
    WAYLAND_DISPLAY esteja ausente.
    """
    return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")


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
        self._tag = f"{os.getpid()}_{next(_instances)}"
        self._match = None

    def open(self) -> tuple[int, int]:
        # Qualquer erro de D-Bus vira PortalError: a GUI só trata PortalError,
        # então um UnknownMethod (portal sem backend de captura) escapava pro
        # handler do GTK e a janela ficava parada em "escolha o monitor…".
        try:
            return self._open()
        except PortalError:
            raise
        except Exception as exc:
            raise PortalError(f"o portal de captura falhou: {exc}") from exc

    def _open(self) -> tuple[int, int]:
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self._request(sender, f"luce_create_{self._tag}", self._on_session, self._create)
        self._wait("sessão")
        if not self._session:
            raise PortalError("o portal não abriu a sessão de captura")

        self._request(sender, f"luce_select_{self._tag}", self._on_ok, self._select)
        self._wait("fontes")

        self._request(sender, f"luce_start_{self._tag}", self._on_start, self._start)
        self._wait("captura")
        if self.fd is None or self.node_id is None:
            raise PortalError("o portal não devolveu o vídeo")
        return self.fd, self.node_id

    def reopen_fd(self) -> int:
        """Pede ao portal um novo fd do PipeWire para a sessão existente."""
        if not self._session:
            raise PortalError("sessão do portal não está aberta")
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        raw = self.iface.OpenPipeWireRemote(self._session, dbus.Dictionary({}, signature="sv"))
        self.fd = _owned_fd(raw)
        return self.fd

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self._session:
            try:
                session_obj = self.bus.get_object(PORTAL_NAME, self._session)
                session_iface = dbus.Interface(session_obj, "org.freedesktop.portal.Session")
                session_iface.Close()
            except Exception:
                pass
            self._session = None

    def _request(self, sender: str, token: str, callback, call) -> None:
        self._response = None
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        self._match = self.bus.add_signal_receiver(
            callback,
            signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path=path,
        )
        call(token)

    def _create(self, token: str) -> None:
        self.iface.CreateSession({
            "handle_token": dbus.String(token),
            "session_handle_token": dbus.String(f"luce_s_{self._tag}"),
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
        # Sem isto o receiver fica pendurado no barramento pra sempre (e
        # segurando este objeto vivo junto), escutando um caminho de Request
        # que o portal já descartou.
        if self._match is not None:
            self._match.remove()
            self._match = None
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
