"""Linux: áudio do sistema sem um app (ex: Discord), pra não dar eco na transmissão.

Um pipewiresrc por app que toca áudio (target-object = serial do stream, o mesmo que o modo
"app:" das Configurações), todos somados num audiomixer. O app silenciado e o próprio LUCE
ficam de fora. A cada segundo o pw-dump decide quais apps entraram/saíram e a bin ganha/perde
o ramo na hora, com o pipeline rodando. O app continua tocando nas caixas normalmente.

Ligar link a link com pw-link não serve: o WirePlumber desfaz o link extra em menos de 1s.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

_POLL_S = 0.5
_CAPS = "audio/x-raw,format=F32LE,rate=48000,channels=2"


def is_excluded(props: dict, mute_app: str) -> bool:
    """O stream é do app a silenciar, ou do próprio LUCE (o áudio de quem assiste voltaria na transmissão)?"""
    if str(props.get("application.process.id")) == str(os.getpid()):
        return True
    target = mute_app.lower().removesuffix(".exe")
    names = (props.get("application.name"), props.get("application.process.binary"), props.get("node.name"))
    return any(n and n.lower() == target for n in names)


def wanted_streams(graph: list[dict], mute_app: str) -> tuple[set[int], set[int]]:
    """(todos, tocando agora): object.serial dos streams de playback que entram na transmissão.

    Só os que estão tocando recebem um pipewiresrc: com o stream parado (idle) o pipewiresrc
    espera a negociação pra sempre e trava a troca de estado do ramo.
    """
    present, running = set(), set()
    for obj in graph:
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Stream/Output/Audio" and not is_excluded(props, mute_app):
            present.add(int(props["object.serial"]))
            if obj["info"].get("state") == "running":
                running.add(int(props["object.serial"]))
    return present, running


def _silent_mixer() -> tuple[Gst.Bin, Gst.Element]:
    """Bin "audio-src" com um audiomixer que nunca para: silêncio ao vivo num pad, as fontes nos outros."""
    bin_ = Gst.Bin.new("audio-src")
    silence = Gst.ElementFactory.make("audiotestsrc", None)
    silence.set_property("wave", 4)  # silence
    silence.set_property("is-live", True)
    caps = Gst.ElementFactory.make("capsfilter", None)
    caps.set_property("caps", Gst.Caps.from_string(_CAPS))
    mixer = Gst.ElementFactory.make("audiomixer", None)
    for element in (silence, caps, mixer):
        bin_.add(element)
    silence.link(caps)
    caps.link(mixer)
    bin_.add_pad(Gst.GhostPad.new("src", mixer.get_static_pad("src")))
    return bin_, mixer


def _branch(src: Gst.Element, name: str | None = None) -> Gst.Bin:
    """src -> estéreo -> caps do mixer, numa bin com pad "src"."""
    branch = Gst.Bin.new(name)
    audio = Gst.ElementFactory.make("capsfilter", None)  # sem caps de áudio o pipewiresrc não acha o alvo
    audio.set_property("caps", Gst.Caps.from_string("audio/x-raw,channels=2"))
    convert = Gst.ElementFactory.make("audioconvert", None)
    resample = Gst.ElementFactory.make("audioresample", None)
    caps = Gst.ElementFactory.make("capsfilter", None)
    caps.set_property("caps", Gst.Caps.from_string(_CAPS))
    chain = (src, audio, convert, resample, caps)
    for element in chain:
        branch.add(element)
    for left, right in zip(chain, chain[1:]):
        left.link(right)
    branch.add_pad(Gst.GhostPad.new("src", caps.get_static_pad("src")))
    return branch


def with_silence(src: Gst.Element) -> Gst.Bin:
    """`src` (microfone, app) somado com silêncio: se ele não mandar nada, a transmissão anda igual.

    Fonte muda (app pausado, pulsesrc que o WirePlumber ligou num app parado) segurava o opusenc
    sem caps, o whipsink sem oferta e, se o relógio fosse dela, o vídeo inteiro: tela preta.
    """
    src.set_property("provide-clock", False)
    bin_, mixer = _silent_mixer()
    branch = _branch(src)
    bin_.add(branch)
    branch.get_static_pad("src").link(mixer.request_pad_simple("sink_%u"))
    return bin_


class SystemAudioExcept:
    """`build()` devolve o elemento que o pipeline usa no lugar do pulsesrc; `stop()` para de acompanhar os apps."""

    def __init__(self, mute_app: str) -> None:
        self._mute_app = mute_app
        self._stop = threading.Event()
        self._branches: dict[int, tuple[Gst.Bin, Gst.Pad]] = {}

    def build(self) -> Gst.Bin:
        self._bin, self._mixer = _silent_mixer()
        threading.Thread(target=self._loop, daemon=True).start()
        return self._bin

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._bin.get_state(0).state != Gst.State.PLAYING:  # mexer na bin no meio da troca de estado trava
                    self._stop.wait(0.2)
                    continue
                graph = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=3).stdout)
                present, running = wanted_streams(graph, self._mute_app)
                for serial in running - self._branches.keys():
                    self._add(serial)
                for serial in self._branches.keys() - present:  # app fechou: sai antes do pipewiresrc cair no microfone
                    self._remove(serial)
            except Exception as exc:  # um ciclo ruim não derruba a transmissão; tenta de novo no próximo
                print(f"áudio do sistema: {exc}", flush=True)
            self._stop.wait(_POLL_S)

    def _add(self, serial: int) -> None:
        src = Gst.ElementFactory.make("pipewiresrc", None)
        src.set_property("target-object", str(serial))
        src.set_property("client-name", "Project LUCE")
        # sem isso um alvo que some faz o stream cair no dispositivo padrão (o microfone)
        src.set_property("stream-properties", Gst.Structure.from_string("props,node.dont-fallback=true,node.dont-reconnect=true")[0])
        branch = _branch(src, f"app-{serial}")
        self._bin.add(branch)
        pad = self._mixer.request_pad_simple("sink_%u")
        branch.get_static_pad("src").link(pad)
        branch.sync_state_with_parent()
        self._branches[serial] = (branch, pad)
        print(f"áudio do sistema: + stream {serial}", flush=True)

    def _remove(self, serial: int) -> None:
        branch, pad = self._branches.pop(serial)
        branch.set_state(Gst.State.NULL)
        branch.get_static_pad("src").unlink(pad)
        self._mixer.release_request_pad(pad)
        self._bin.remove(branch)
        print(f"áudio do sistema: - stream {serial}", flush=True)

    def stop(self) -> None:
        self._stop.set()
