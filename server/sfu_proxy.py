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
  PATCH  /{nome}/watch?viewer=X    X segue assistindo {nome} (heartbeat, ~10 s)
  DELETE /{nome}/watch?viewer=X    X parou de assistir
  GET    /v3/paths/list            roster (+ "viewers": [nomes] por transmissão)

Quem para de mandar heartbeat por TTL segundos (kill -9, queda de rede) sai do
roster sozinho. Estado só em memória: reiniciar o proxy esquece quem estava no
ar até a pessoa transmitir de novo.
"""

import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TTL = 30  # segundos sem heartbeat até tirar do roster (o cliente manda a cada 10)
ONLINE_TTL = 15.0  # segundos de inatividade para expirar usuário online
WHEP_TRIES = 6  # o ICE do publisher pode estar subindo: o SFU responde not_found_track_error
WHEP_DELAY = 1.5
MAX_BODY = 64 * 1024
MAX_PUBLISHERS = 100
MAX_VIEWERS_PER_STREAM = 500
NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
NOT_FOUND_TRACK = "not_found_track_error"
# O Cloudflare na frente da API barra o User-Agent padrão do urllib (403).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

cfg = {
    "base": "",
    "secret": "",
    "password": "",
    "discord_client_id": "",
    "discord_client_secret": "",
    "discord_guild_id": "",
    "discord_redirect_uri": "https://luce.codestage.pro/auth/discord/callback",
}
publishers: dict[str, dict] = {}  # nome -> {session, tracks: [{trackName, mid}], seen}
viewers: dict[str, dict[str, float]] = {}  # transmissão -> {quem assiste: último heartbeat}
lock = threading.Lock()

online_users: dict[str, float] = {}  # username -> último ping (para contador de online)
online_users_lock = threading.Lock()

uid_names: dict[str, str] = {}  # discord_uid -> nome exibido (uma conta = um usuário online)
stream_owner: dict[str, str] = {}  # transmissão -> discord_uid de quem publica
uid_lock = threading.Lock()

discord_sessions: dict[str, dict] = {}  # user_id -> {access_token, last_checked, is_member}
discord_sessions_lock = threading.Lock()

STATE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
pending_logins: dict[str, dict] = {}
pending_lock = threading.Lock()

user_avatars: dict[str, str] = {}  # clean_username -> avatar_url
user_avatars_lock = threading.Lock()


def _is_valid_discord_avatar_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("https://cdn.discordapp.com/"):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return parsed.scheme == "https" and parsed.netloc == "cdn.discordapp.com"
    except Exception:
        return False


def _build_discord_avatar_url(user_info: dict) -> str:
    user_id = str(user_info.get("id") or "").strip()
    avatar_hash = str(user_info.get("avatar") or "").strip()
    if user_id and avatar_hash and re.fullmatch(r"[A-Za-z0-9_]{1,64}", avatar_hash) and user_id.isdigit():
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=128"
    if user_id.isdigit():
        idx = (int(user_id) >> 22) % 6
        return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"
    return ""


def clean_username(raw: str) -> str:
    import unicodedata

    ascii_str = unicodedata.normalize("NFKD", raw.strip()).encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"\s+", "_", ascii_str)
    clean = re.sub(r"[^A-Za-z0-9._-]", "", clean).strip("._-")
    return clean[:64] or "user"


def _extract_clean_discord_name(user_info: dict) -> str:
    """global_name só com emoji/Unicode limpa para "user": cai para o username ASCII do Discord."""
    for key in ("global_name", "username"):
        name = clean_username(user_info.get(key) or "")
        if name != "user":
            return name
    return "user"


def _encrypt_discord_token(plaintext: str) -> str:
    if not plaintext:
        return ""
    secret = (cfg.get("secret") or cfg.get("password") or "luce_secret").encode("utf-8")
    k = hmac.new(secret, b"discord_token_enc", "sha256").digest()
    salt = secrets.token_bytes(8)
    data = plaintext.encode("utf-8")
    keystream = bytearray()
    block_idx = 0
    while len(keystream) < len(data):
        block = hmac.new(k, salt + block_idx.to_bytes(4, "big"), "sha256").digest()
        keystream.extend(block)
        block_idx += 1
    cipher = bytes(d ^ k_b for d, k_b in zip(data, keystream[:len(data)]))
    return (salt + cipher).hex()


def _decrypt_discord_token(hex_str: str) -> str:
    if not hex_str:
        return ""
    try:
        raw = bytes.fromhex(hex_str)
        if len(raw) <= 8:
            return ""
        salt, cipher = raw[:8], raw[8:]
        secret = (cfg.get("secret") or cfg.get("password") or "luce_secret").encode("utf-8")
        k = hmac.new(secret, b"discord_token_enc", "sha256").digest()
        keystream = bytearray()
        block_idx = 0
        while len(keystream) < len(cipher):
            block = hmac.new(k, salt + block_idx.to_bytes(4, "big"), "sha256").digest()
            keystream.extend(block)
            block_idx += 1
        plain = bytes(c ^ k_b for c, k_b in zip(cipher, keystream[:len(cipher)]))
        return plain.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _cleanup_revoked(user_id: str, username: str) -> None:
    if username:
        clean_name = clean_username(username)
        with online_users_lock:
            online_users.pop(clean_name, None)
        with lock:
            if clean_name in publishers:
                del publishers[clean_name]
        try:
            sfu("DELETE", f"/v3/paths/delete/{clean_name}")
        except Exception:
            pass


def check_bdk_membership_live(user_id: str, access_token: str = "", username: str = "") -> bool:
    """Verifica com cache de 15s se o usuário ainda pertence à guild BDK no Discord."""
    if not access_token:
        with discord_sessions_lock:
            if user_id in discord_sessions:
                access_token = discord_sessions[user_id].get("access_token", "")
    if not access_token:
        return True

    now = time.time()
    with discord_sessions_lock:
        session = discord_sessions.get(user_id)
        if session and (now - session.get("last_checked", 0) < 15):
            is_mem = session.get("is_member", True)
            if not is_mem:
                _cleanup_revoked(user_id, username)
            return is_mem

    guild_id = cfg.get("discord_guild_id", "").strip()
    if not guild_id:
        return True

    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": USER_AGENT}
    is_member = True
    try:
        req = urllib.request.Request("https://discord.com/api/v10/users/@me/guilds", headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            guilds = json.loads(resp.read().decode("utf-8"))
        is_member = any(str(g.get("id")) == guild_id for g in guilds) if isinstance(guilds, list) else False
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            is_member = False
        elif exc.code == 429 or exc.code >= 500:
            with discord_sessions_lock:
                return discord_sessions.get(user_id, {}).get("is_member", True)
        else:
            is_member = False
    except Exception:
        with discord_sessions_lock:
            return discord_sessions.get(user_id, {}).get("is_member", True)

    with discord_sessions_lock:
        existing_avatar = discord_sessions.get(user_id, {}).get("avatar_url", "")
        discord_sessions[user_id] = {
            "access_token": access_token,
            "last_checked": now,
            "is_member": is_member,
            "avatar_url": existing_avatar,
        }

    if not is_member:
        log(f"acesso revogado: user_id {user_id} (@{username}) não está na guild BDK")
        _cleanup_revoked(user_id, username)
        return False

    clean_name = clean_username(username) if username else ""
    with user_avatars_lock:
        has_avatar = bool(user_avatars.get(clean_name)) if clean_name else False
    if not has_avatar and access_token:
        try:
            u_req = urllib.request.Request(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bearer {access_token}", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(u_req, timeout=5) as u_resp:
                u_info = json.loads(u_resp.read().decode("utf-8"))
                av_url = _build_discord_avatar_url(u_info)
                if av_url:
                    if clean_name:
                        with user_avatars_lock:
                            user_avatars[clean_name] = av_url
                    with discord_sessions_lock:
                        if user_id in discord_sessions:
                            discord_sessions[user_id]["avatar_url"] = av_url
        except Exception as exc:
            log(f"falha ao buscar avatar Discord para {user_id}: {exc}")

    return True


def issue_discord_token(user_id: str, username: str, ttl_days: int = 30, access_token: str = "") -> str:
    """Gera token discord assinado: discord:<user_id>:<username>:<exp>:<enc_token>:<sig>."""
    secret = (cfg.get("secret") or cfg.get("password") or "luce_secret").encode("utf-8")
    exp = int(time.time() + ttl_days * 86400)
    enc_token = _encrypt_discord_token(access_token) if access_token else ""
    if access_token:
        with discord_sessions_lock:
            existing_avatar = discord_sessions.get(user_id, {}).get("avatar_url", "")
            discord_sessions[user_id] = {
                "access_token": access_token,
                "last_checked": time.time(),
                "is_member": True,
                "avatar_url": existing_avatar,
            }
    if enc_token:
        payload = f"discord:{user_id}:{username}:{exp}:{enc_token}"
    else:
        payload = f"discord:{user_id}:{username}:{exp}"
    sig = hmac.new(secret, payload.encode("utf-8"), "sha256").hexdigest()
    return f"{payload}:{sig}"


def _verify_discord_part(raw_discord: str) -> bool:
    if not raw_discord.startswith("discord:"):
        return False
    parts = raw_discord.split(":")
    secret = (cfg.get("secret") or cfg.get("password") or "luce_secret").encode("utf-8")

    if len(parts) == 6:
        _, user_id, username, exp_str, enc_token, sig = parts
        try:
            exp = int(exp_str)
        except ValueError:
            return False
        if exp < time.time():
            return False
        payload = f"discord:{user_id}:{username}:{exp_str}:{enc_token}"
        expected_sig = hmac.new(secret, payload.encode("utf-8"), "sha256").hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        access_token = _decrypt_discord_token(enc_token)
        return check_bdk_membership_live(user_id, access_token, username)

    elif len(parts) == 5:
        _, user_id, username, exp_str, sig = parts
        try:
            exp = int(exp_str)
        except ValueError:
            return False
        if exp < time.time():
            return False
        payload = f"discord:{user_id}:{username}:{exp_str}"
        expected_sig = hmac.new(secret, payload.encode("utf-8"), "sha256").hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        access_token = ""
        with discord_sessions_lock:
            if user_id in discord_sessions:
                access_token = discord_sessions[user_id].get("access_token", "")
        return check_bdk_membership_live(user_id, access_token, username)

    return False


def _verify_password_part(raw_bdk: str) -> bool:
    if not raw_bdk.startswith("bdk:"):
        return False
    passwd = raw_bdk[4:]
    server_pw = cfg.get("password", "")
    return bool(server_pw and hmac.compare_digest(passwd, server_pw))


def verify_token(auth_header: str) -> bool:
    """Valida token de autenticação: formato duplo discord:...|bdk:... ou individual em modo teste."""
    if not auth_header:
        return False
    raw_token = auth_header.strip()
    if raw_token.startswith("Bearer "):
        raw_token = raw_token[7:].strip()

    if "|bdk:" in raw_token:
        discord_part, bdk_part = raw_token.split("|", 1)
        return _verify_discord_part(discord_part) and _verify_password_part(bdk_part)

    # Se Discord estiver configurado no servidor, dupla autenticação é obrigatória
    is_discord_enforced = bool(cfg.get("discord_client_id") and cfg.get("discord_guild_id"))
    if is_discord_enforced:
        return False

    # Fallback para ambiente de desenvolvimento/testes unitários sem Discord configurado
    if raw_token.startswith("discord:"):
        return _verify_discord_part(raw_token)
    elif raw_token.startswith("bdk:"):
        return _verify_password_part(raw_token)
    return False


def _render_auth_page(title: str, message: str, is_error: bool = False) -> bytes:
    accent = "#ff2a4b" if is_error else "#00e5ff"
    status_icon = "⚠️" if is_error else "✨"
    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Project LUCE — {title}</title>
  <style>
    body {{
      background: #0a0c1f;
      color: #e8ebff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 16px;
    }}
    .card {{
      background: #12153a;
      border: 1px solid #232859;
      border-radius: 16px;
      padding: 32px;
      max-width: 440px;
      width: 100%;
      text-align: center;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    }}
    .badge {{
      display: inline-block;
      font-size: 32px;
      margin-bottom: 12px;
    }}
    h1 {{
      font-size: 20px;
      margin: 0 0 12px;
      color: {accent};
      letter-spacing: 1px;
    }}
    p {{
      color: #8d93c7;
      font-size: 14px;
      line-height: 1.6;
      margin: 0 0 24px;
    }}
    .hint {{
      font-size: 12px;
      color: #555c91;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">{status_icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <div class="hint">Você já pode fechar esta aba e voltar para o aplicativo LUCE.</div>
  </div>
</body>
</html>"""
    return html.encode("utf-8")


def handle_discord_callback(code: str, state: str) -> tuple[int, bytes, str]:
    if not cfg.get("discord_client_id") or not cfg.get("discord_client_secret"):
        return (
            503,
            _render_auth_page("Configuração Pendente", "Discord OAuth2 não está configurado no servidor.", True),
            "text/html",
        )

    # só conclui um login iniciado por /auth/discord/login e ainda pendente (impede state forjado ou repetido)
    with pending_lock:
        started = pending_logins.get(state, {}).get("created", time.monotonic())
        if pending_logins.get(state, {}).get("status") != "pending":
            return 400, _render_auth_page("Login Inválido", "Este login expirou ou já foi concluído. Inicie novamente pelo aplicativo.", True), "text/html"

    # 1. Troca code por access_token
    token_url = "https://discord.com/api/v10/oauth2/token"
    data = urllib.parse.urlencode({
        "client_id": cfg["discord_client_id"],
        "client_secret": cfg["discord_client_secret"],
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.get("discord_redirect_uri", ""),
    }).encode("utf-8")
    req = urllib.request.Request(
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return 502, _render_auth_page("Erro de Autenticação", f"Falha ao validar com o Discord: {exc}", True), "text/html"

    access_token = token_data.get("access_token")
    if not access_token:
        return 400, _render_auth_page("Erro de Autenticação", "Resposta inválida do Discord.", True), "text/html"

    # 2. Busca perfil e lista de servidores (guilds)
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": USER_AGENT}
    try:
        user_req = urllib.request.Request("https://discord.com/api/v10/users/@me", headers=headers)
        with urllib.request.urlopen(user_req, timeout=10) as resp:
            user_info = json.loads(resp.read().decode("utf-8"))

        guilds_req = urllib.request.Request("https://discord.com/api/v10/users/@me/guilds", headers=headers)
        with urllib.request.urlopen(guilds_req, timeout=10) as resp:
            guilds = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return 502, _render_auth_page("Erro de Verificação", f"Falha ao consultar perfil no Discord: {exc}", True), "text/html"

    guild_id = cfg.get("discord_guild_id", "").strip()
    is_member = False
    if guild_id and isinstance(guilds, list):
        is_member = any(str(g.get("id")) == guild_id for g in guilds)

    discord_username = user_info.get("username", "usuário")
    if not is_member:
        with pending_lock:
            pending_logins[state] = {
                "status": "denied",
                "error": "Sua conta do Discord não faz parte do servidor BDK.",
                "created": started,  # sem isso o expire() (now - 0 > 300) apagava o login antes do app ler
            }
        return 403, _render_auth_page(
            "Acesso Exclusivo BDK",
            f"Olá, @{discord_username}. O acesso a este servidor é restrito aos membros da BDK.",
            True,
        ), "text/html"

    clean_name = _extract_clean_discord_name(user_info)
    user_id = str(user_info.get("id", "0"))
    avatar_url = _build_discord_avatar_url(user_info)
    if avatar_url:
        with user_avatars_lock:
            user_avatars[clean_name] = avatar_url

    token = issue_discord_token(user_id, clean_name, access_token=access_token)

    with pending_lock:
        pending_logins[state] = {
            "status": "ok",
            "token": token,
            "username": clean_name,
            "user": clean_name,
            "discord_tag": discord_username,
            "discord_user": discord_username,
            "avatar_url": avatar_url,
            "avatar": avatar_url,
            "created": started,
        }

    if user_id:
        with discord_sessions_lock:
            if user_id in discord_sessions:
                discord_sessions[user_id]["avatar_url"] = avatar_url

    return 200, _render_auth_page(
        "Acesso BDK Confirmado!",
        f"Bem-vindo(a), {clean_name}! Sua conta do Discord foi verificada com sucesso.",
        False,
    ), "text/html"


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
    blocks = re.split(r"(?m)^(?=m=)", sdp)[1:]
    if len(blocks) > 16:
        raise HttpError(400, "SDP com excesso de seções m=")
    for block in blocks:
        kind = block.split(" ", 1)[0][2:]
        if kind in ("video", "audio"):
            mid_match = re.search(r"(?m)^a=mid:(.*?)\r?$", block)
            if not mid_match:
                raise HttpError(400, "SDP sem a=mid")
            mid_val = mid_match.group(1).strip()
            if not NAME.fullmatch(mid_val):
                raise HttpError(400, "SDP com a=mid inválido")
            tracks.append({"location": "local", "mid": mid_val, "trackName": kind})
    if not tracks:
        raise HttpError(400, "SDP sem vídeo nem áudio")
    return tracks


def whep_offer(sdp: str) -> str:
    """Ajusta o offer do `whepsrc` pro que o SFU aceita.

    O whepsrc oferece "OPUS/48000" sem os canais (o SFU só reconhece
    opus/48000/2) e VP8/VP9/H265/AV1 junto com o H264; com qualquer um deles o
    SFU recusa tudo com "missing the published track's codec". O RTX do H264
    (retransmissão por NACK) fica, mas o publisher com RTX exige que o viewer
    ofereça o seu (senão 500) e o SFU só aceita um payload por m-line.
    """
    sdp = re.sub(r"(?im)^(a=rtpmap:\d+) opus/48000\r?$", r"\1 opus/48000/2", sdp)
    # o espectador só recebe: sendrecv/sendonly viram recvonly e a=ssrc/a=msid (identidade de quem envia) saem
    sdp = re.sub(r"(?m)^a=(?:sendrecv|sendonly)\r?$", "a=recvonly", sdp)
    sdp = re.sub(r"(?m)^a=(?:ssrc|ssrc-group|msid):[^\r\n]*\r?\n?", "", sdp)
    drop = {pt for pt, codec in re.findall(r"a=rtpmap:(\d+) ([A-Za-z0-9_-]+)/", sdp)
            if codec.upper() in ("VP8", "VP9", "H265", "AV1", "RED", "ULPFEC", "FLEXFEC-03")}
    rtx = {pt for pt, _ in re.findall(r"a=fmtp:(\d+) apt=(\d+)", sdp)}
    # o RTX de um codec descartado ficaria apontando (apt=) pra um payload que não existe mais
    drop |= {pt for pt, apt in re.findall(r"a=fmtp:(\d+) apt=(\d+)", sdp) if apt in drop}
    lines = []
    for line in sdp.splitlines():
        if line.startswith(("m=video", "m=audio")):
            head = line.split(" ")
            # medido: o SFU responde só com o ÚLTIMO payload da m-line; com o rtx por último
            # a resposta perde o H264 e não vem vídeo. Sort estável: rtx primeiro.
            keep = sorted((pt for pt in head[3:] if pt not in drop), key=lambda pt: pt not in rtx)
            line = " ".join(head[:3] + keep)
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
    with lock:
        if len(publishers) >= MAX_PUBLISHERS and stream not in publishers:
            raise HttpError(429, "limite de transmissões simultâneas atingido")
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
        viewers.pop(stream, None)
    close_tracks(pub)
    log(f"fora do ar: {stream}")
    return True


def heartbeat(stream: str) -> bool:
    with lock:
        pub = publishers.get(stream)
        if pub:
            pub["seen"] = time.monotonic()
        return pub is not None


def watching(stream: str, viewer: str | None, on: bool = True) -> None:
    if viewer is None or not NAME.fullmatch(viewer):
        raise HttpError(400, "viewer inválido")
    with lock:
        if stream not in publishers:
            raise HttpError(404, f"{stream} não está transmitindo")
        if on:
            stream_viewers = viewers.setdefault(stream, {})
            if len(stream_viewers) >= MAX_VIEWERS_PER_STREAM and viewer not in stream_viewers:
                raise HttpError(429, "limite de espectadores atingido")
            stream_viewers[viewer] = time.monotonic()
        else:
            viewers.get(stream, {}).pop(viewer, None)


def subscribe(stream: str, offer: str) -> tuple[str, str]:
    with lock:
        pub = publishers.get(stream)
    if pub is None:
        raise HttpError(404, f"{stream} não está transmitindo")
    # as tracks seguem a ordem das m-lines do ESPECTADOR (o Android oferece video+audio, o GStreamer audio+video):
    # fora dessa ordem o SFU casa a track de áudio com a m=video e responde erro (vira 502)
    pub_by_kind = {t["trackName"]: t for t in pub["tracks"]}
    remote_tracks = [
        {"location": "remote", "sessionId": pub["session"], "trackName": st["trackName"], "mid": st["mid"]}
        for st in offer_tracks(offer) if st["trackName"] in pub_by_kind
    ]
    if not remote_tracks:
        remote_tracks = [{"location": "remote", "sessionId": pub["session"], "trackName": t["trackName"]}
                         for t in pub["tracks"]]
    sub = sfu("POST", "/sessions/new")["sessionId"]
    body = {
        "sessionDescription": {"type": "offer", "sdp": whep_offer(offer)},
        "tracks": remote_tracks,
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


def _resolve_online_name(discord_uid: str, viewer: str, token_name: str, path_user: str) -> str:
    """Nome único por conta Discord: X-Luce-Viewer/nome válido do token atualizam o nome do uid; "user" nunca vale."""
    viewer = clean_username(viewer) if NAME.fullmatch(viewer) else ""
    if not discord_uid:
        return viewer or token_name or path_user
    with uid_lock:
        # X-Luce-Viewer é controlado pelo cliente: não pode tomar o nome de outra conta
        def free(n):
            return n and n != "user" and not any(u != discord_uid and v.lower() == n.lower() for u, v in uid_names.items())
        cand = next((n for n in (viewer, token_name) if free(n)), "")
        if cand:
            uid_names[discord_uid] = cand
        if path_user:
            stream_owner[path_user] = discord_uid
        return uid_names.get(discord_uid) or path_user


def _record_online_user(auth_header: str, headers, path_user: str = "") -> None:
    viewer = headers.get("X-Luce-Viewer", "").strip() if headers else ""
    username = ""
    discord_uid = ""
    if auth_header:
        raw = auth_header.strip()
        if raw.startswith("Bearer "):
            raw = raw[7:].strip()
        for p in raw.split("|"):
            if p.startswith("discord:"):
                d_parts = p.split(":")
                if len(d_parts) >= 2 and d_parts[1]:
                    discord_uid = d_parts[1]
                if len(d_parts) >= 3 and d_parts[2]:
                    username = clean_username(d_parts[2])
                    break
    user = _resolve_online_name(discord_uid, viewer, username, path_user)
    if user:
        clean = clean_username(user)
        with online_users_lock:
            online_users[clean] = time.time()

        avatar_hdr = ""
        if headers:
            avatar_hdr = (headers.get("X-Luce-Avatar") or headers.get("x-luce-avatar") or "").strip()
        if avatar_hdr and _is_valid_discord_avatar_url(avatar_hdr):
            with user_avatars_lock:
                user_avatars[clean] = avatar_hdr
        elif discord_uid:
            with discord_sessions_lock:
                session_av = discord_sessions.get(discord_uid, {}).get("avatar_url", "")
            if session_av:
                with user_avatars_lock:
                    user_avatars[clean] = session_av


def expire() -> None:
    now = time.monotonic()
    now_wall = time.time()
    with lock:
        stale = [name for name, pub in publishers.items() if now - pub["seen"] > TTL]
    for name in stale:
        log(f"{name}: sem heartbeat há {TTL}s")
        unpublish(name, None)
    with lock:
        for stream, who in list(viewers.items()):
            for viewer in [v for v, seen in who.items() if now - seen > TTL]:
                del who[viewer]
            if not who and stream not in publishers:
                del viewers[stream]
    with pending_lock:
        expired_states = [s for s, data in pending_logins.items() if now - data.get("created", 0) > 300]
        for s in expired_states:
            del pending_logins[s]
    with lock, uid_lock:
        for stream in [x for x in stream_owner if x not in publishers]:
            del stream_owner[stream]
    with online_users_lock:
        expired_online = [u for u, seen in online_users.items() if now_wall - seen > ONLINE_TTL]
        for u in expired_online:
            del online_users[u]


def reap() -> None:
    while True:
        time.sleep(5)
        expire()


class Handler(BaseHTTPRequestHandler):
    timeout = 10  # 10s de timeout no socket contra conexões lentas / travadas

    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _chunked_body(self) -> str:
        """Corpo com Transfer-Encoding: chunked (o HttpURLConnection sem tamanho fixo usa isso)."""
        data = b""
        try:
            while True:
                size_line = self.rfile.readline(64).split(b";", 1)[0].strip()
                size = int(size_line, 16)
                if size < 0 or len(data) + size > MAX_BODY:
                    raise HttpError(413, "corpo grande demais")
                if size == 0:
                    while self.rfile.readline(1024).strip():  # trailers até a linha em branco
                        pass
                    return data.decode("utf-8", "replace")
                data += self.rfile.read(size)
                self.rfile.readline(8)  # CRLF depois do chunk
        except ValueError as exc:
            raise HttpError(400, "chunk inválido") from exc
        except (TimeoutError, OSError) as exc:
            raise HttpError(408, "timeout aguardando corpo da requisição") from exc

    def _body(self) -> str:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self._chunked_body()
        try:
            raw = self.headers.get("Content-Length") or "0"
            size = int(raw)
        except (ValueError, TypeError):
            raise HttpError(400, "Content-Length inválido")
        if size < 0 or size > MAX_BODY:
            raise HttpError(413, "corpo grande demais")
        try:
            return self.rfile.read(size).decode("utf-8", "replace")
        except (TimeoutError, OSError) as exc:
            raise HttpError(408, "timeout aguardando corpo da requisição") from exc

    def _handle(self, fn, auth_required: bool = True) -> None:
        try:
            token = self.headers.get("Authorization", "")
            p_parts = [p for p in self.path.split("?")[0].split("/") if p]
            path_user = p_parts[0] if (len(p_parts) == 2 and p_parts[1] == "whip") else ""
            if auth_required:
                if not verify_token(token):
                    raise HttpError(401, "não autorizado")
                _record_online_user(token, self.headers, path_user)
            else:
                if token and verify_token(token):
                    _record_online_user(token, self.headers, path_user)
                elif self.headers.get("X-Luce-Viewer") or path_user:
                    _record_online_user("", self.headers, path_user)
            fn(p_parts)
        except HttpError as exc:
            self._reply(exc.code, str(exc).encode())
        except SfuError as exc:
            log(f"ERRO {self.command} {self.path}: {exc}")
            self._reply(502, str(exc).encode())
        except (BrokenPipeError, ConnectionResetError, TimeoutError):  # cliente morreu ou timeout
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
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        is_auth_route = len(parts) >= 2 and parts[0] == "auth" and parts[1] == "discord"

        def run(p_parts):
            query = parse_qs(parsed.query)
            if is_auth_route:
                action = p_parts[2] if len(p_parts) >= 3 else ""
                if action == "login":
                    state = query.get("state", [""])[0]
                    if not STATE_RE.fullmatch(state):
                        raise HttpError(400, "state inválido")
                    if not cfg.get("discord_client_id") or not cfg.get("discord_redirect_uri"):
                        raise HttpError(503, "OAuth2 do Discord não configurado no servidor")
                    with pending_lock:
                        if len(pending_logins) >= 1000 and state not in pending_logins:
                            raise HttpError(429, "muitos logins em andamento")
                        pending_logins[state] = {"status": "pending", "created": time.monotonic()}
                    scope = "identify+guilds"
                    target = (
                        f"https://discord.com/oauth2/authorize?client_id={cfg['discord_client_id']}"
                        f"&response_type=code&redirect_uri={urllib.parse.quote(cfg['discord_redirect_uri'], safe='')}"
                        f"&scope={scope}&state={state}"
                    )
                    return self._reply(302, b"", extra={"Location": target})
                elif action == "callback":
                    code = query.get("code", [""])[0]
                    state = query.get("state", [""])[0]
                    if not code or not state or not STATE_RE.fullmatch(state):
                        raise HttpError(400, "Parâmetros de callback inválidos")
                    code_res, body, ctype = handle_discord_callback(code, state)
                    return self._reply(code_res, body, ctype)
                elif action == "poll":
                    state = query.get("state", [""])[0]
                    if not STATE_RE.fullmatch(state):
                        raise HttpError(400, "state inválido")
                    with pending_lock:
                        info = pending_logins.get(state, {"status": "not_found"})
                        if info.get("status") in ("ok", "denied"):
                            pending_logins.pop(state, None)
                    return self._reply(
                        200,
                        json.dumps(info).encode("utf-8"),
                        "application/json",
                        extra={"Access-Control-Allow-Origin": "*"},
                    )
                raise HttpError(404, "rota não existe")

            if p_parts != ["v3", "paths", "list"]:
                raise HttpError(404, "rota desconhecida")
            now_wall = time.time()
            with lock:
                with user_avatars_lock:
                    avatars_snapshot = dict(user_avatars)
                items = [
                    {
                        "name": name,
                        "ready": True,
                        "viewers": sorted(viewers.get(name, {})),
                        "avatar": avatars_snapshot.get(name) or avatars_snapshot.get(uid_names.get(stream_owner.get(name, ""), ""), ""),
                    }
                    for name in publishers
                ]
                with uid_lock:
                    owned = set(stream_owner)
                with online_users_lock:
                    # publisher com dono Discord já conta pelo nome da conta (não pelo nome técnico da stream)
                    for name in publishers:
                        if name not in owned:
                            online_users[name] = now_wall
                    for who in viewers.values():
                        for v in who:
                            if v != "user":
                                online_users[v] = now_wall
                    active_users = sorted([u for u, seen in online_users.items() if now_wall - seen <= ONLINE_TTL])
            data = {
                "items": items,
                "online_count": len(active_users),
                "online_users": active_users,
                "avatars": avatars_snapshot,
            }
            self._reply(200, json.dumps(data).encode(), "application/json")

        self._handle(run, auth_required=not is_auth_route)

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

    def _viewer(self) -> str | None:
        return parse_qs(urlparse(self.path).query).get("viewer", [None])[0]

    def do_PATCH(self):
        def run(parts):
            if len(parts) == 2 and parts[1] == "watch":
                watching(self._route(parts, "watch"), self._viewer())
                return self._reply(204)
            stream = self._route(parts, "whip")
            self._body()
            if not heartbeat(stream):
                raise HttpError(404, f"{stream} não está no ar")
            self._reply(204)
        self._handle(run)

    def do_DELETE(self):
        def run(parts):
            if len(parts) == 2 and parts[1] == "watch":
                watching(self._route(parts, "watch"), self._viewer(), on=False)
                return self._reply(200)
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
    cfg["discord_client_id"] = os.environ.get("DISCORD_CLIENT_ID", "")
    cfg["discord_client_secret"] = os.environ.get("DISCORD_CLIENT_SECRET", "")
    cfg["discord_guild_id"] = os.environ.get("DISCORD_GUILD_ID", "")
    cfg["discord_redirect_uri"] = os.environ.get("DISCORD_REDIRECT_URI", "")
    threading.Thread(target=reap, daemon=True).start()
    addr = (os.environ.get("LUCE_BIND", "127.0.0.1"), int(os.environ.get("LUCE_PORT", "8889")))
    log(f"LUCE WHIP/WHEP -> SFU em http://{addr[0]}:{addr[1]}")
    ThreadingHTTPServer(addr, Handler).serve_forever()


if __name__ == "__main__":
    main()
