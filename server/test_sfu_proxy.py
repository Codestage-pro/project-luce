"""Testes do sfu_proxy com um SFU falso: `python -m unittest server/test_sfu_proxy.py`."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import sfu_proxy as p

OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=mid:audio1\r\na=rtpmap:111 OPUS/48000\r\n" \
        "m=video 9 UDP/TLS/RTP/SAVPF 96 97 98\r\na=mid:video0\r\na=rtpmap:96 VP8/90000\r\n" \
        "a=rtcp-fb:96 nack\r\na=rtpmap:97 H264/90000\r\na=fmtp:97 packetization-mode=1\r\n" \
        "a=rtpmap:98 AV1/90000\r\n"


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
        p.cfg.update(password="segredo")
        p.publishers.clear()
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
        self.assertIn("m=video 9 UDP/TLS/RTP/SAVPF 97\r\n", out)
        self.assertNotIn("VP8", out)
        self.assertNotIn("AV1", out)
        self.assertNotIn("a=rtcp-fb:96", out)
        self.assertIn("a=fmtp:97", out)

    def test_auth_required(self):
        self.assertEqual(self.call("GET", "/v3/paths/list", auth=None)[0], 401)
        self.assertEqual(self.call("GET", "/v3/paths/list", auth="Bearer bdk:errada")[0], 401)

    def test_publish_roster_delete(self):
        code, body, headers = self.call("POST", "/ana/whip", OFFER.encode())
        self.assertEqual((code, body), (201, "ANSWER"))
        sid = headers["Location"].rsplit("/", 1)[1]
        self.assertTrue(headers["Location"].startswith("/ana/whip/"))
        self.assertEqual(json.loads(self.call("GET", "/v3/paths/list")[1]), {"items": [{"name": "ana", "ready": True}]})
        self.assertEqual(self.call("PATCH", "/ana/whip")[0], 204)
        self.assertEqual(self.call("DELETE", "/ana/whip/outra")[0], 200)  # sessão antiga: não derruba
        self.assertIn("ana", p.publishers)
        self.assertEqual(self.call("DELETE", f"/ana/whip/{sid}")[0], 200)
        self.assertEqual(json.loads(self.call("GET", "/v3/paths/list")[1]), {"items": []})
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


if __name__ == "__main__":
    unittest.main()
