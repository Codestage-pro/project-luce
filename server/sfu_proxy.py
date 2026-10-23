#!/usr/bin/env python3
"""Servidor do Project LUCE: WHIP/WHEP -> Cloudflare Realtime SFU + roster.

O cliente (`whipsink`/`whepsrc` do GStreamer) fala WHIP/WHEP com este proxy
pelo Cloudflare Tunnel; o proxy traduz pra API do SFU (que não é WHIP) e é o
único que conhece o CF_SFU_APP_SECRET. A mídia (RTP) vai direto do cliente pro
IP anycast da Cloudflare — nenhuma porta UDP no servidor nem no roteador.

Rotas (todas exigem `Authorization: Bearer bdk:<BDK_PASSWORD>`):
  POST   /{nome}/whip              publica; 201 + Location /{nome}/whip/{sessão}
  PATCH  /{nome}/whip[/{sessão}]   heartbeat (e trickle ICE, ignorado)
  DELETE /{nome}/whip[/{sessão}]   encerra e tira do roster
  POST   /{nome}/whep              assiste; 201 + Location /{nome}/whep/{sessão}
  DELETE /{nome}/whep/{sessão}     no-op (o SFU recolhe a sessão do viewer)
  GET    /v3/paths/list            roster, mesmo JSON que o MediaMTX devolvia

Quem para de mandar heartbeat por TTL segundos (kill -9, queda de rede) sai do
roster sozinho. Estado só em memória: reiniciar o proxy esquece quem estava no
ar até a pessoa transmitir de novo.
"""

import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TTL = 30  # segundos sem heartbeat até tirar do roster (o cliente manda a cada 10)
WHEP_TRIES = 6  # o ICE do publisher pode estar subindo: o SFU responde not_found_track_error
WHEP_DELAY = 1.5
MAX_BODY = 64 * 1024
NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
NOT_FOUND_TRACK = "not_found_track_error"
# O Cloudflare na frente da API barra o User-Agent padrão do urllib (403).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

cfg = {"base": "", "secret": "", "password": ""}
publishers: dict[str, dict] = {}  # nome -> {session, tracks: [{trackName, mid}], seen}
lock = threading.Lock()


class SfuError(RuntimeError):
    pass


class HttpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def sfu(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        cfg["base"] + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {cfg['secret']}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise SfuError(f"SFU {method} {path} -> {exc.code}: {exc.read().decode()[:300]}") from exc
    except OSError as exc:
        raise SfuError(f"SFU {method} {path}: {exc}") from exc


def tracks_new(session: str, body: dict) -> dict:
    """tracks/new que também levanta erro quando só uma track vem com errorCode."""
    res = sfu("POST", f"/sessions/{session}/tracks/new", body)
    for track in res.get("tracks", []):
        if track.get("errorCode"):
            raise SfuError(f"track {track.get('trackName')}: {track['errorCode']} {track.get('errorDescription')}")
    return res


def offer_tracks(sdp: str) -> list[dict]:
    """Um track local por m-line de vídeo/áudio: mid vem do a=mid:, nome pelo tipo."""
    tracks = []
    for block in re.split(r"(?m)^(?=m=)", sdp)[1:]:
        kind = block.split(" ", 1)[0][2:]
        mid = re.search(r"(?m)^a=mid:(\S+)", block)
        if kind in ("video", "audio"):
            if mid is None:
                raise HttpError(400, "SDP sem a=mid")
            tracks.append({"location": "local", "mid": mid.group(1), "trackName": kind})
    if not tracks:
        raise HttpError(400, "SDP sem vídeo nem áudio")
    return tracks


def whep_offer(sdp: str) -> str:
    """Ajusta o offer do `whepsrc` pro que o SFU aceita.

    O whepsrc oferece "OPUS/48000" sem os canais (o SFU só reconhece
    opus/48000/2) e VP8/VP9/H265/AV1 junto com o H264; com qualquer um deles o
    SFU recusa tudo com "missing the published track's codec".
    """
    sdp = re.sub(r"(?im)^(a=rtpmap:\d+) opus/48000\r?$", r"\1 opus/48000/2", sdp)
    drop = {pt for pt, codec in re.findall(r"a=rtpmap:(\d+) (\w+)/", sdp) if codec in ("VP8", "VP9", "H265", "AV1")}
    lines = []
    for line in sdp.splitlines():
        if line.startswith("m=video"):
            head = line.split(" ")
            line = " ".join(head[:3] + [pt for pt in head[3:] if pt not in drop])
        attr = re.match(r"a=(rtpmap|fmtp|rtcp-fb):(\d+) ", line)
        if not (attr and attr.group(2) in drop):
            lines.append(line)
    return "\r\n".join(lines) + "\r\n"


def close_tracks(pub: dict) -> None:
    try:
        sfu("PUT", f"/sessions/{pub['session']}/tracks/close",
            {"tracks": [{"mid": t["mid"]} for t in pub["tracks"]], "force": True})
    except SfuError as exc:
        log(f"aviso: não consegui fechar as tracks: {exc}")


def publish(stream: str, offer: str) -> tuple[str, str]:
    tracks = offer_tracks(offer)
    session = sfu("POST", "/sessions/new")["sessionId"]
    res = tracks_new(session, {"sessionDescription": {"type": "offer", "sdp": offer}, "tracks": tracks})
    with lock:
        old = publishers.get(stream)
        publishers[stream] = {"session": session, "tracks": tracks, "seen": time.monotonic()}
    if old:  # reiniciou sem o DELETE antigo ter chegado
        close_tracks(old)
    log(f"no ar: {stream} (sessão {session[:8]})")
    return session, res["sessionDescription"]["sdp"]


def unpublish(stream: str, session: str | None) -> bool:
    """Sem `session`, derruba quem estiver no ar; com ela, só se ainda for a atual
    (o DELETE do `luce share` antigo não pode derrubar o novo que acabou de subir)."""
    with lock:
        pub = publishers.get(stream)
        if pub is None or (session and pub["session"] != session):
            return False
        del publishers[stream]
    close_tracks(pub)
    log(f"fora do ar: {stream}")
    return True


def heartbeat(stream: str) -> bool:
    with lock:
        pub = publishers.get(stream)
        if pub:
            pub["seen"] = time.monotonic()
        return pub is not None


def subscribe(stream: str, offer: str) -> tuple[str, str]:
    with lock:
        pub = publishers.get(stream)
    if pub is None:
        raise HttpError(404, f"{stream} não está transmitindo")
    sub = sfu("POST", "/sessions/new")["sessionId"]
    body = {
        "sessionDescription": {"type": "offer", "sdp": whep_offer(offer)},
        "tracks": [{"location": "remote", "sessionId": pub["session"], "trackName": t["trackName"]}
                   for t in pub["tracks"]],
    }
    for attempt in range(1, WHEP_TRIES + 1):
        try:
            res = tracks_new(sub, body)
            return sub, res["sessionDescription"]["sdp"]
        except SfuError as exc:
            if NOT_FOUND_TRACK not in str(exc) or attempt == WHEP_TRIES:
                raise
            log(f"{stream}: publisher ainda subindo, tentativa {attempt}/{WHEP_TRIES}")
            time.sleep(WHEP_DELAY)
    raise AssertionError("unreachable")


def expire() -> None:
    now = time.monotonic()
    with lock:
        stale = [name for name, pub in publishers.items() if now - pub["seen"] > TTL]
    for name in stale:
        log(f"{name}: sem heartbeat há {TTL}s")
        unpublish(name, None)


def reap() -> None:
    while True:
        time.sleep(5)
        expire()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> str:
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            raise HttpError(413, "corpo grande demais")
        return self.rfile.read(size).decode("utf-8", "replace")

    def _handle(self, fn) -> None:
        try:
            token = self.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(token, f"Bearer bdk:{cfg['password']}".encode()):
                raise HttpError(401, "não autorizado")
            fn([p for p in self.path.split("?")[0].split("/") if p])
        except HttpError as exc:
            self._reply(exc.code, str(exc).encode())
        except SfuError as exc:
            log(f"ERRO {self.command} {self.path}: {exc}")
            self._reply(502, str(exc).encode())
        except (BrokenPipeError, ConnectionResetError):  # cliente morreu antes da resposta
            log(f"cliente desconectou: {self.command} {self.path}")
        except Exception as exc:  # bug nosso: não derruba a conexão sem resposta
            log(f"ERRO interno {self.command} {self.path}: {exc!r}")
            self._reply(500, b"erro interno")

    @staticmethod
    def _route(parts: list[str], kind: str) -> str:
        if len(parts) < 2 or parts[1] != kind or len(parts) > 3 or not NAME.fullmatch(parts[0]):
            raise HttpError(404, "rota desconhecida")
        return parts[0]

    def do_GET(self):
        def run(parts):
            if parts != ["v3", "paths", "list"]:
                raise HttpError(404, "rota desconhecida")
            with lock:
                items = [{"name": name, "ready": True} for name in publishers]
            self._reply(200, json.dumps({"items": items}).encode(), "application/json")
        self._handle(run)

    def do_POST(self):
        def run(parts):
            kind = parts[1] if len(parts) == 2 else ""
            if kind not in ("whip", "whep"):
                raise HttpError(404, "rota desconhecida")
            stream = self._route(parts, kind)
            offer = self._body()
            if kind == "whip":
                session, answer = publish(stream, offer)
            else:
                session, answer = subscribe(stream, offer)
            if kind == "whep":
                log(f"assistindo: {stream}")
            self._reply(201, answer.encode(), "application/sdp", {"Location": f"/{stream}/{kind}/{session}"})
        self._handle(run)

    def do_PATCH(self):
        def run(parts):
            stream = self._route(parts, "whip")
            self._body()
            if not heartbeat(stream):
                raise HttpError(404, f"{stream} não está no ar")
            self._reply(204)
        self._handle(run)

    def do_DELETE(self):
        def run(parts):
            if len(parts) >= 2 and parts[1] == "whep":
                return self._reply(200)
            stream = self._route(parts, "whip")
            unpublish(stream, parts[2] if len(parts) == 3 else None)
            self._reply(200)
        self._handle(run)


def load_env() -> None:
    path = Path(__file__).resolve().parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_env()
    missing = [k for k in ("CF_SFU_APP_ID", "CF_SFU_APP_SECRET", "BDK_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"faltam no ambiente/server/.env: {', '.join(missing)}")
    cfg["base"] = f"https://rtc.live.cloudflare.com/v1/apps/{os.environ['CF_SFU_APP_ID']}"
    cfg["secret"] = os.environ["CF_SFU_APP_SECRET"]
    cfg["password"] = os.environ["BDK_PASSWORD"]
    threading.Thread(target=reap, daemon=True).start()
    addr = (os.environ.get("LUCE_BIND", "127.0.0.1"), int(os.environ.get("LUCE_PORT", "8889")))
    log(f"LUCE WHIP/WHEP -> SFU em http://{addr[0]}:{addr[1]}")
    ThreadingHTTPServer(addr, Handler).serve_forever()


if __name__ == "__main__":
    main()
