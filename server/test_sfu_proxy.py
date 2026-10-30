"""Testes do sfu_proxy com um SFU falso: `python -m unittest server/test_sfu_proxy.py`."""

import json
import sys
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sfu_proxy as p

OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=mid:audio1\r\na=rtpmap:111 OPUS/48000\r\n" \
        "m=video 9 UDP/TLS/RTP/SAVPF 96 97 98 99 100\r\na=mid:video0\r\na=rtpmap:96 VP8/90000\r\n" \
        "a=rtcp-fb:96 nack\r\na=rtpmap:97 H264/90000\r\na=fmtp:97 packetization-mode=1\r\n" \
        "a=rtpmap:98 AV1/90000\r\na=rtpmap:99 rtx/90000\r\na=fmtp:99 apt=96\r\n" \
        "a=rtpmap:100 rtx/90000\r\na=fmtp:100 apt=97\r\n"


class FakeSfu:
    def __init__(self):
        self.calls = []
        self.not_found = 0  # quantas vezes o tracks/new remoto falha antes de dar certo
        self.audio_empty = False  # publisher sem RTP de áudio: o SFU recusa a track remota de áudio
        self.answer = "ANSWER"

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/sessions/new":
            return {"sessionId": f"s{len(self.calls)}"}
        if path.endswith("/tracks/new"):
            if body["tracks"][0]["location"] == "remote" and self.not_found:
                self.not_found -= 1
                raise p.SfuError("SFU 400: not_found_track_error")
            if self.audio_empty and any(t["location"] == "remote" and t["trackName"] == "audio" for t in body["tracks"]):
                raise p.SfuError("track audio: empty_track_error No track data from remote peer")
            return {"sessionDescription": {"type": "answer", "sdp": self.answer}, "tracks": []}
        return {}


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.sfu = FakeSfu()
        p.sfu = self.sfu
        p.cfg.update(password="segredo", secret="test_secret", discord_guild_id="12345")
        p.publishers.clear()
        p.viewers.clear()
        p.online_by_id.clear()
        p.discord_sessions.clear()
        p.user_avatars.clear()
        p.uid_names.clear()
        p.pending_logins.clear()
        p.stream_owner.clear()
        p.WHEP_DELAY = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), p.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()

    def call(self, method, path, body=b"", auth="Bearer bdk:segredo", headers=None):
        hdrs = {"Authorization": auth} if auth else {}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(self.url + path, method=method, data=body or None,
                                     headers=hdrs)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def test_whep_offer_keeps_only_h264_and_fixes_opus(self):
        out = p.whep_offer(OFFER)
        self.assertIn("a=rtpmap:111 opus/48000/2\r\n", out)
        self.assertNotIn("VP8", out)
        self.assertNotIn("AV1", out)
        self.assertNotIn("a=rtcp-fb:96", out)
        self.assertIn("a=fmtp:97", out)
        self.assertIn("a=rtpmap:100 rtx/90000", out)  # RTX do H264 fica
        self.assertNotIn("a=rtpmap:99 rtx", out)  # RTX do VP8 sai junto
        self.assertNotIn("apt=96", out)
        self.assertIn("m=video 9 UDP/TLS/RTP/SAVPF 100 97\r\n", out)

    def test_subscribe_follows_viewer_mline_order_and_forces_recvonly(self):
        # publisher (GStreamer) = [audio, video]; o viewer é um SDP real do libwebrtc/Android
        self.call("POST", "/ana/whip", OFFER.encode())
        viewer = ("v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111 63 9 0 8 13 110 126\r\na=mid:0\r\na=sendrecv\r\n"
                  "a=rtpmap:111 opus/48000/2\r\na=rtcp-fb:111 transport-cc\r\n"
                  "a=fmtp:111 minptime=10;useinbandfec=1;stereo=1;maxaveragebitrate=128000\r\n"
                  "a=rtpmap:63 red/48000/2\r\na=fmtp:63 111/111\r\na=rtpmap:9 G722/8000\r\na=rtpmap:0 PCMU/8000\r\n"
                  "a=rtpmap:8 PCMA/8000\r\na=rtpmap:13 CN/8000\r\na=rtpmap:110 telephone-event/48000\r\n"
                  "a=rtpmap:126 telephone-event/8000\r\na=msid:x y\r\na=ssrc:123 cname:a\r\n"
                  "m=video 9 UDP/TLS/RTP/SAVPF 96 97 98 99 100 101\r\na=mid:1\r\na=sendonly\r\n"
                  "a=rtpmap:96 VP8/90000\r\na=rtpmap:97 H264/90000\r\n"
                  "a=fmtp:97 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640c1f\r\n"
                  "a=rtpmap:98 H264/90000\r\n"
                  "a=fmtp:98 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f\r\n"
                  "a=rtpmap:99 rtx/90000\r\na=fmtp:99 apt=97\r\na=rtpmap:100 rtx/90000\r\na=fmtp:100 apt=98\r\n"
                  "a=rtpmap:101 ulpfec/90000\r\na=ssrc-group:FID 123 456\r\n")
        code, body, _ = self.call("POST", "/ana/whep", viewer.encode(), headers={"X-Luce-Viewer": "bia"})
        self.assertEqual((code, body), (201, "ANSWER"))
        tracks_new = [c for c in self.sfu.calls if c[1].endswith("/tracks/new") and c[2]["tracks"][0]["location"] == "remote"]
        sent = tracks_new[-1][2]
        sess = sent["tracks"][0]["sessionId"]
        self.assertEqual(sent["tracks"], [
            {"location": "remote", "sessionId": sess, "trackName": "audio"},
            {"location": "remote", "sessionId": sess, "trackName": "video"},
        ])  # sem "mid"
        sdp = sent["sessionDescription"]["sdp"]
        self.assertIn("m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n", sdp)
        self.assertIn("m=video 9 UDP/TLS/RTP/SAVPF 100 98\r\n", sdp)
        for gone in ("G722", "PCMU", "PCMA", "CN/", "telephone-event", "red/", "VP8", "ulpfec", "640c1f",
                     "sendrecv", "sendonly", "a=ssrc", "a=msid", "stereo", "maxaveragebitrate"):
            self.assertNotIn(gone, sdp)
        self.assertIn("a=fmtp:111 minptime=10;useinbandfec=1\r\n", sdp)
        self.assertEqual(sdp.count("a=recvonly"), 2)

    def test_chunked_body_is_read(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.putrequest("POST", "/ana/whip")
        conn.putheader("Authorization", "Bearer bdk:segredo")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        data = OFFER.encode()
        conn.send(b"%x\r\n%s\r\n0\r\n\r\n" % (len(data), data))
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.read().decode()), (201, "ANSWER"))
        self.assertEqual(resp.getheader("Connection"), "close")
        conn.close()

    def test_auth_required(self):
        self.assertEqual(self.call("GET", "/v3/paths/list", auth=None)[0], 401)
        self.assertEqual(self.call("GET", "/v3/paths/list", auth="Bearer bdk:errada")[0], 401)

    def test_publish_roster_delete(self):
        code, body, headers = self.call("POST", "/ana/whip", OFFER.encode())
        self.assertEqual((code, body), (201, "ANSWER"))
        sid = headers["Location"].rsplit("/", 1)[1]
        self.assertTrue(headers["Location"].startswith("/ana/whip/"))
        roster = json.loads(self.call("GET", "/v3/paths/list")[1])
        self.assertEqual(roster["items"], [{"name": "ana", "ready": True, "viewers": [], "avatar": ""}])
        self.assertEqual(roster["online_count"], 1)
        self.assertEqual(roster["online_users"], ["ana"])
        self.assertEqual(self.call("PATCH", "/ana/whip")[0], 204)
        self.assertEqual(self.call("DELETE", "/ana/whip/outra")[0], 200)  # sessão antiga: não derruba
        self.assertIn("ana", p.publishers)
        self.assertEqual(self.call("DELETE", f"/ana/whip/{sid}")[0], 200)
        self.assertEqual(json.loads(self.call("GET", "/v3/paths/list")[1])["items"], [])
        close = [c for c in self.sfu.calls if c[1].endswith("/tracks/close")]
        self.assertEqual(close[0][2]["tracks"], [{"mid": "audio1"}, {"mid": "video0"}])
        self.assertEqual(self.call("PATCH", "/ana/whip")[0], 404)

    def test_republish_closes_old_session(self):
        self.call("POST", "/ana/whip", OFFER.encode())
        old = p.publishers["ana"]["session"]
        self.call("POST", "/ana/whip", OFFER.encode())
        self.assertNotEqual(p.publishers["ana"]["session"], old)
        self.assertTrue(any(c[1] == f"/sessions/{old}/tracks/close" for c in self.sfu.calls))

    def test_whep_retries_not_found_then_works(self):
        self.call("POST", "/ana/whip", OFFER.encode())
        self.sfu.not_found = 3
        code, body, _ = self.call("POST", "/ana/whep", OFFER.encode())
        self.assertEqual((code, body), (201, "ANSWER"))
        self.sfu.not_found = 99
        self.assertEqual(self.call("POST", "/ana/whep", OFFER.encode())[0], 502)
        self.assertEqual(self.call("POST", "/ninguem/whep", OFFER.encode())[0], 404)

    def test_whep_falls_back_to_video_only_when_publisher_audio_is_empty(self):
        self.call("POST", "/ana/whip", OFFER.encode())
        self.sfu.audio_empty = True
        self.sfu.answer = "v=0\r\na=group:BUNDLE video0\r\nm=video 9 UDP/TLS/RTP/SAVPF 97\r\na=mid:video0\r\n"
        offer = OFFER.replace("v=0\r\n", "v=0\r\na=group:BUNDLE audio1 video0\r\n", 1)
        code, body, _ = self.call("POST", "/ana/whep", offer.encode())
        self.assertEqual(code, 201)
        sent = [c for c in self.sfu.calls if c[1].endswith("/tracks/new")][-1][2]
        self.assertEqual([t["trackName"] for t in sent["tracks"]], ["video"])
        self.assertNotIn("m=audio", sent["sessionDescription"]["sdp"])
        self.assertIn("BUNDLE video0", sent["sessionDescription"]["sdp"].replace("\r", ""))
        # a resposta ao viewer mantém as m-lines do offer: o áudio vem recusado (porta 0) na posição original
        self.assertLess(body.index("m=audio 0 "), body.index("m=video"))
        self.assertIn("a=mid:audio1", body)

    def test_bad_routes(self):
        self.assertEqual(self.call("POST", "/ana/foo", OFFER.encode())[0], 404)
        self.assertEqual(self.call("POST", "/..%2f/whip", OFFER.encode())[0], 404)

    def test_expired_publisher_leaves_roster(self):
        self.call("POST", "/ana/whip", OFFER.encode())
        p.publishers["ana"]["seen"] = time.monotonic() - p.TTL - 1
        p.expire()
        self.assertEqual(p.publishers, {})

    def test_viewers_listed_and_expire(self):
        self.call("POST", "/ana/whip", OFFER.encode())
        self.assertEqual(self.call("PATCH", "/ana/watch?viewer=bia")[0], 204)
        self.call("PATCH", "/ana/watch?viewer=caio")
        self.assertEqual(self.call("PATCH", "/ana/watch")[0], 400)
        roster = lambda: json.loads(self.call("GET", "/v3/paths/list")[1])["items"][0]["viewers"]
        self.assertEqual(roster(), ["bia", "caio"])
        self.assertEqual(self.call("DELETE", "/ana/watch?viewer=bia")[0], 200)
        self.assertEqual(roster(), ["caio"])
        p.viewers["ana"]["caio"] -= p.TTL + 1
        p.expire()
        self.assertEqual(roster(), [])

    def test_handler_socket_timeout(self):
        self.assertEqual(p.Handler.timeout, 10)

    def test_content_length_validation(self):
        # Content-Length negativo
        req = urllib.request.Request(
            self.url + "/ana/whip",
            data=b"test",
            headers={"Authorization": "Bearer bdk:segredo", "Content-Length": "-1"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 413)

        # Content-Length inválido (não inteiro)
        req = urllib.request.Request(
            self.url + "/ana/whip",
            data=b"test",
            headers={"Authorization": "Bearer bdk:segredo", "Content-Length": "invalid"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)

    def test_watching_requires_active_publisher(self):
        # Stream que não existe
        self.assertEqual(self.call("PATCH", "/inexistente/watch?viewer=bia")[0], 404)
        self.assertNotIn("inexistente", p.viewers)

    def test_viewers_limit_and_empty_cleanup(self):
        self.call("POST", "/ana/whip", OFFER.encode())
        # Limite de espectadores por stream
        original_max = p.MAX_VIEWERS_PER_STREAM
        try:
            p.MAX_VIEWERS_PER_STREAM = 2
            self.assertEqual(self.call("PATCH", "/ana/watch?viewer=v1")[0], 204)
            self.assertEqual(self.call("PATCH", "/ana/watch?viewer=v2")[0], 204)
            self.assertEqual(self.call("PATCH", "/ana/watch?viewer=v3")[0], 429)
        finally:
            p.MAX_VIEWERS_PER_STREAM = original_max

        # Expira todos e verifica se a chave do stream é limpa quando publisher cai
        p.publishers["ana"]["seen"] = time.monotonic() - p.TTL - 1
        p.viewers["ana"]["v1"] -= p.TTL + 1
        p.viewers["ana"]["v2"] -= p.TTL + 1
        p.expire()
        self.assertNotIn("ana", p.viewers)

    def test_max_publishers_limit(self):
        original_max = p.MAX_PUBLISHERS
        try:
            p.MAX_PUBLISHERS = 1
            code1, _, _ = self.call("POST", "/pub1/whip", OFFER.encode())
            self.assertEqual(code1, 201)
            code2, _, _ = self.call("POST", "/pub2/whip", OFFER.encode())
            self.assertEqual(code2, 429)
        finally:
            p.MAX_PUBLISHERS = original_max

    def test_sdp_validation(self):
        # Sem mid
        bad_sdp = "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        self.assertEqual(self.call("POST", "/ana/whip", bad_sdp.encode())[0], 400)

        # Mid com caracteres inválidos
        bad_mid = "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:bad!mid$with spaces\r\n"
        self.assertEqual(self.call("POST", "/ana/whip", bad_mid.encode())[0], 400)

        # Excesso de seções m= (>16)
        many_m = "v=0\r\n" + ("m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:v\r\n" * 20)
        self.assertEqual(self.call("POST", "/ana/whip", many_m.encode())[0], 400)

    def test_discord_token_issuance_and_verification(self):
        token = p.issue_discord_token("987654321", "MuriloX06")
        self.assertTrue(token.startswith("discord:987654321:MuriloX06:"))

        # Verificação válida via Bearer
        self.assertTrue(p.verify_token(f"Bearer {token}"))
        self.assertTrue(p.verify_token(token))  # sem prefixo

        # Legacy BDK token ainda funciona em dev
        self.assertTrue(p.verify_token("Bearer bdk:segredo"))

        # Dupla autenticação combinada: discord:...|bdk:...
        dual_token = f"{token}|bdk:segredo"
        self.assertTrue(p.verify_token(f"Bearer {dual_token}"))
        self.assertTrue(p.verify_token(dual_token))

        # Dupla autenticação com senha errada
        self.assertFalse(p.verify_token(f"Bearer {token}|bdk:errada"))

        # Dupla autenticação com token discord expirado ou adulterado
        corrupt = token[:-4] + "ffff"
        self.assertFalse(p.verify_token(f"Bearer {corrupt}"))
        self.assertFalse(p.verify_token(f"Bearer {corrupt}|bdk:segredo"))

        expired_token = p.issue_discord_token("987654321", "MuriloX06", ttl_days=-1)
        self.assertFalse(p.verify_token(f"Bearer {expired_token}"))
        self.assertFalse(p.verify_token(f"Bearer {expired_token}|bdk:segredo"))

        # Token com formato quebrado
        self.assertFalse(p.verify_token("Bearer discord:bad"))
        self.assertFalse(p.verify_token("Bearer discord:123:user:notanint:fakehmac"))
        self.assertFalse(p.verify_token("Bearer outro:token"))

        # Em produção (quando discord_client_id e discord_guild_id estão definidos),
        # dupla autenticação é estritamente obrigatória (rejeita individual)
        try:
            p.cfg["discord_client_id"] = "mock_client_id"
            p.cfg["discord_guild_id"] = "mock_guild_id"
            self.assertFalse(p.verify_token("Bearer bdk:segredo"))
            self.assertFalse(p.verify_token(f"Bearer {token}"))
            self.assertTrue(p.verify_token(f"Bearer {dual_token}"))
        finally:
            p.cfg.pop("discord_client_id", None)
            p.cfg.pop("discord_guild_id", None)

    def test_discord_poll_endpoint(self):
        # State com caracteres inválidos
        code, _, _ = self.call("GET", "/auth/discord/poll?state=bad!char$")
        self.assertEqual(code, 400)

        # State não encontrado
        code, body, _ = self.call("GET", "/auth/discord/poll?state=valid_test_state_1234")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body).get("status"), "not_found")

        # State pendente
        with p.pending_lock:
            p.pending_logins["valid_test_state_1234"] = {"status": "pending", "created": time.monotonic()}
        code, body, _ = self.call("GET", "/auth/discord/poll?state=valid_test_state_1234")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body).get("status"), "pending")

        # State resolvido com sucesso
        test_token = p.issue_discord_token("111", "Tirigam")
        with p.pending_lock:
            p.pending_logins["valid_test_state_1234"] = {
                "status": "ok",
                "token": test_token,
                "discord_user": "Tirigam",
                "user": "Tirigam"
            }
        code, body, _ = self.call("GET", "/auth/discord/poll?state=valid_test_state_1234")
        self.assertEqual(code, 200)
        res = json.loads(body)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("token"), test_token)

        # Consumido uma vez: próxima chamada deve ser not_found
        code, body, _ = self.call("GET", "/auth/discord/poll?state=valid_test_state_1234")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body).get("status"), "not_found")

    def test_token_encryption_and_decryption(self):
        secret_token = "discord_oauth2_secret_token_abcdef123456"
        encrypted = p._encrypt_discord_token(secret_token)
        self.assertNotEqual(encrypted, secret_token)
        decrypted = p._decrypt_discord_token(encrypted)
        self.assertEqual(decrypted, secret_token)

        # Token vazio retorna vazio
        self.assertEqual(p._encrypt_discord_token(""), "")
        self.assertEqual(p._decrypt_discord_token(""), "")

    def test_live_membership_revocation(self):
        user_id = "55555"
        username = "Alice"
        access_token = "valid_access_token_123"

        # Emite token contendo access_token criptografado (6 partes)
        token = p.issue_discord_token(user_id, username, access_token=access_token)
        parts = token.split(":")
        self.assertEqual(len(parts), 6)

        # Sessão em cache com is_member=True
        p.discord_sessions[user_id] = {
            "access_token": access_token,
            "last_checked": time.time(),
            "is_member": True,
        }
        self.assertTrue(p.verify_token(token))

        # Simula revogação (usuário saiu ou foi banido da guild)
        p.discord_sessions[user_id]["is_member"] = False
        self.assertFalse(p.verify_token(token))

        # Adiciona ana como publisher e verifica se revogação remove ela
        p.publishers["alice"] = {"session": "s_alice", "tracks": [], "seen": time.monotonic()}
        self.assertIn("alice", p.publishers)
        # Ao detectar is_member=False, check_bdk_membership_live fecha a stream e remove de publishers
        p.check_bdk_membership_live(user_id, access_token, "alice")
        self.assertNotIn("alice", p.publishers)

    def test_oauth_callback_emoji_global_name_falls_back_to_username(self):
        class Resp:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return json.dumps(self.payload).encode()

        def fake_urlopen(req, timeout=10):
            url = req.full_url if hasattr(req, "full_url") else req
            if url.endswith("/oauth2/token"):
                return Resp({"access_token": "tok"})
            if url.endswith("/users/@me"):
                return Resp({"id": "42", "global_name": "\U0001F525\U0001F525", "username": "murilox06"})
            return Resp([{"id": "12345"}])

        saved = {k: p.cfg.get(k) for k in ("discord_client_id", "discord_client_secret")}
        p.cfg.update(discord_client_id="cid", discord_client_secret="csecret")
        p.pending_logins["st_emoji_1"] = {"status": "pending", "created": time.monotonic()}
        try:
            with unittest.mock.patch.object(p.urllib.request, "urlopen", fake_urlopen):
                code, _, _ = p.handle_discord_callback("code", "st_emoji_1")
        finally:
            p.cfg.update(saved)
        self.assertEqual(code, 200)
        res = p.pending_logins["st_emoji_1"]
        self.assertEqual(res["user"], "murilox06")
        self.assertEqual(res["token"].split(":")[2], "murilox06")
        self.assertEqual(p._extract_clean_discord_name({"global_name": "\U0001F525", "username": ""}), "user")

    def test_online_user_not_duplicated_when_streaming(self):
        token = p.issue_discord_token("42", "user") + "|bdk:segredo"
        auth = f"Bearer {token}"
        roster = lambda: json.loads(self.call("GET", "/v3/paths/list", auth=auth,
                                              headers={"X-Luce-Viewer": "MuriloX06"})[1])
        self.assertEqual(roster()["online_users"], ["MuriloX06"])
        for stream in ("MuriloX06", "windows-test"):
            # WHIP e heartbeat do cliente antigo: sem X-Luce-Viewer, token com :user:
            self.assertEqual(self.call("POST", f"/{stream}/whip", OFFER.encode(), auth=auth)[0], 201)
            self.assertEqual(self.call("PATCH", f"/{stream}/whip", auth=auth)[0], 204)
        data = json.loads(self.call("GET", "/v3/paths/list", auth=auth)[1])
        self.assertEqual(data["online_users"], ["MuriloX06"])
        self.assertEqual(data["online_count"], 1)
        self.assertNotIn("user", data["online_users"])

    def test_same_discord_account_is_one_online_user_across_name_changes(self):
        token = p.issue_discord_token("42", "murilox06") + "|bdk:segredo"
        auth = f"Bearer {token}"
        p.user_avatars["AndroidGOAT"] = "https://cdn.discordapp.com/avatars/42/a.png"
        for hdr in ({"X-Luce-Viewer": "AndroidGOAT"}, {"X-Luce-Viewer": "MuriloX06"}, {}):
            data = json.loads(self.call("GET", "/v3/paths/list", auth=auth, headers=hdr)[1])
            self.assertEqual(data["online_count"], 1, hdr)
        self.assertEqual(self.call("POST", "/MuriloX06/whip", OFFER.encode(), auth=auth)[0], 201)
        self.assertEqual(self.call("PATCH", "/MuriloX06/whip", auth=auth)[0], 204)
        data = json.loads(self.call("GET", "/v3/paths/list", auth=auth)[1])
        self.assertEqual(data["online_users"], ["MuriloX06"])  # sem header não sobrescreve o nome
        self.assertEqual(data["avatars"].get("MuriloX06"), "https://cdn.discordapp.com/avatars/42/a.png")

    def test_online_user_viewer_header_cannot_take_another_uid_name(self):
        a = f"Bearer {p.issue_discord_token('1', 'ana')}|bdk:segredo"
        b = f"Bearer {p.issue_discord_token('2', 'bia')}|bdk:segredo"
        self.call("GET", "/v3/paths/list", auth=a, headers={"X-Luce-Viewer": "ana"})
        self.call("GET", "/v3/paths/list", auth=b, headers={"X-Luce-Viewer": "ana"})
        data = json.loads(self.call("GET", "/v3/paths/list", auth=b)[1])
        self.assertEqual(sorted(data["online_users"]), ["ana", "bia"])

    def _discord_callback(self, state, guilds, pre_state=True):
        class Resp:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return json.dumps(self.payload).encode()

        def fake_urlopen(req, timeout=10):
            url = req.full_url if hasattr(req, "full_url") else req
            if url.endswith("/oauth2/token"):
                return Resp({"access_token": "tok"})
            if url.endswith("/users/@me"):
                return Resp({"id": "42", "global_name": "Murilo", "username": "murilox06", "avatar": "abc"})
            return Resp(guilds)

        saved = {k: p.cfg.get(k) for k in ("discord_client_id", "discord_client_secret")}
        p.cfg.update(discord_client_id="cid", discord_client_secret="csecret")
        if pre_state:
            p.pending_logins[state] = {"status": "pending", "created": time.monotonic()}
        try:
            with unittest.mock.patch.object(p.urllib.request, "urlopen", fake_urlopen):
                return p.handle_discord_callback("code", state)[0]
        finally:
            p.cfg.update(saved)

    def test_expire_keeps_authorized_pending_login_until_ttl(self):
        for state, guilds, status in (("st_ok_12345", [{"id": "12345"}], "ok"), ("st_den_12345", [], "denied")):
            self._discord_callback(state, guilds)
            self.assertEqual(p.pending_logins[state]["status"], status)
            p.expire()
            self.assertIn(state, p.pending_logins, f"{status} sumiu antes dos 300s")
            p.pending_logins[state]["created"] = time.monotonic() - 301
            p.expire()
            self.assertNotIn(state, p.pending_logins)
        self._discord_callback("st_ok_67890", [{"id": "12345"}])
        res = p.pending_logins["st_ok_67890"]
        self.assertTrue(res["avatar"])
        self.assertEqual(res["avatar"], res["avatar_url"])

    def test_callback_ignores_state_not_started_by_login(self):
        self._discord_callback("st_forged_123", [{"id": "12345"}], pre_state=False)
        self.assertNotIn("st_forged_123", p.pending_logins)

    def test_online_users_tracking(self):
        # Transmissão gera publisher e viewer
        self.call("POST", "/ana/whip", OFFER.encode())
        self.call("PATCH", "/ana/watch?viewer=bob")

        roster = json.loads(self.call("GET", "/v3/paths/list")[1])
        self.assertIn("ana", roster["online_users"])
        self.assertIn("bob", roster["online_users"])
        self.assertEqual(roster["online_count"], len(roster["online_users"]))

    def test_discord_avatar_building_and_ssrf_rejection(self):
        # 1. Montagem com hash customizado
        u1 = {"id": "1234567890", "avatar": "abcdef123456"}
        self.assertEqual(
            p._build_discord_avatar_url(u1),
            "https://cdn.discordapp.com/avatars/1234567890/abcdef123456.png?size=128"
        )

        # 2. Montagem com avatar padrão (sem hash)
        u2 = {"id": "1234567890", "avatar": None}
        idx = (1234567890 >> 22) % 6
        self.assertEqual(
            p._build_discord_avatar_url(u2),
            f"https://cdn.discordapp.com/embed/avatars/{idx}.png"
        )

        # 3. Validação estrita de SSRF (_is_valid_discord_avatar_url)
        self.assertTrue(p._is_valid_discord_avatar_url("https://cdn.discordapp.com/avatars/1/a.png"))
        self.assertTrue(p._is_valid_discord_avatar_url("https://cdn.discordapp.com/embed/avatars/0.png"))

        # Rejeições SSRF
        self.assertFalse(p._is_valid_discord_avatar_url("http://cdn.discordapp.com/avatars/1/a.png"))
        self.assertFalse(p._is_valid_discord_avatar_url("https://evil.com/avatars/1/a.png"))
        self.assertFalse(p._is_valid_discord_avatar_url("https://cdn.discordapp.com.attacker.com/a.png"))
        self.assertFalse(p._is_valid_discord_avatar_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(p._is_valid_discord_avatar_url("file:///etc/passwd"))
        self.assertFalse(p._is_valid_discord_avatar_url(""))
        self.assertFalse(p._is_valid_discord_avatar_url(None))

    def test_roster_avatars(self):
        # Transmissão de ana enviando X-Luce-Avatar válido
        valid_avatar = "https://cdn.discordapp.com/avatars/11111/avatar_ana.png?size=128"
        code, _, _ = self.call("POST", "/ana/whip", OFFER.encode(), headers={"X-Luce-Avatar": valid_avatar})
        self.assertEqual(code, 201)

        code, body, _ = self.call("GET", "/v3/paths/list")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertIn("avatars", data)
        self.assertEqual(data["avatars"].get("ana"), valid_avatar)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["avatar"], valid_avatar)

        # Transmissão com tentativa de SSRF via header X-Luce-Avatar é ignorada
        p.user_avatars.clear()
        malicious_avatar = "http://169.254.169.254/latest/meta-data/"
        self.call("POST", "/ana/whip", OFFER.encode(), headers={"X-Luce-Avatar": malicious_avatar})
        data2 = json.loads(self.call("GET", "/v3/paths/list")[1])
        self.assertNotEqual(data2["avatars"].get("ana"), malicious_avatar)
        self.assertEqual(data2["items"][0]["avatar"], "")


if __name__ == "__main__":
    unittest.main()
