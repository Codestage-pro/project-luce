"""Testes do sfu_proxy com um SFU falso: `python -m unittest server/test_sfu_proxy.py`."""

import json
import sys
import threading
import time
import unittest
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

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/sessions/new":
            return {"sessionId": f"s{len(self.calls)}"}
        if path.endswith("/tracks/new"):
            if body["tracks"][0]["location"] == "remote" and self.not_found:
                self.not_found -= 1
                raise p.SfuError("SFU 400: not_found_track_error")
            return {"sessionDescription": {"type": "answer", "sdp": "ANSWER"}, "tracks": []}
        return {}


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.sfu = FakeSfu()
        p.sfu = self.sfu
        p.cfg.update(password="segredo", secret="test_secret", discord_guild_id="12345")
        p.publishers.clear()
        p.viewers.clear()
        p.online_users.clear()
        p.discord_sessions.clear()
        p.WHEP_DELAY = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), p.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()

    def call(self, method, path, body=b"", auth="Bearer bdk:segredo"):
        req = urllib.request.Request(self.url + path, method=method, data=body or None,
                                     headers={"Authorization": auth} if auth else {})
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

    def test_auth_required(self):
        self.assertEqual(self.call("GET", "/v3/paths/list", auth=None)[0], 401)
        self.assertEqual(self.call("GET", "/v3/paths/list", auth="Bearer bdk:errada")[0], 401)

    def test_publish_roster_delete(self):
        code, body, headers = self.call("POST", "/ana/whip", OFFER.encode())
        self.assertEqual((code, body), (201, "ANSWER"))
        sid = headers["Location"].rsplit("/", 1)[1]
        self.assertTrue(headers["Location"].startswith("/ana/whip/"))
        roster = json.loads(self.call("GET", "/v3/paths/list")[1])
        self.assertEqual(roster["items"], [{"name": "ana", "ready": True, "viewers": []}])
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

    def test_online_users_tracking(self):
        # Transmissão gera publisher e viewer
        self.call("POST", "/ana/whip", OFFER.encode())
        self.call("PATCH", "/ana/watch?viewer=bob")

        roster = json.loads(self.call("GET", "/v3/paths/list")[1])
        self.assertIn("ana", roster["online_users"])
        self.assertIn("bob", roster["online_users"])
        self.assertEqual(roster["online_count"], len(roster["online_users"]))


if __name__ == "__main__":
    unittest.main()
