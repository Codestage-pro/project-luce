import socket
import sys
import unittest

from luce import rtc


def _rcvbuf(sock: socket.socket) -> int:
    return sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)


@unittest.skipUnless(sys.platform.startswith("linux"), "usa /proc/self/fd")
class BoostUdpBuffersTest(unittest.TestCase):
    def test_udp_sockets_of_the_process_get_bigger_buffers(self):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(udp.close)
        small = 64 * 1024
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, small)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, small)
        before = _rcvbuf(udp)
        rtc.boost_udp_buffers(1024 * 1024)
        self.assertGreater(_rcvbuf(udp), before)
        self.assertGreater(udp.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF), before)
        self.assertGreaterEqual(udp.fileno(), 0)  # o fd continua nosso e aberto

    def test_tcp_and_unix_sockets_are_left_alone(self):
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        unix = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(tcp.close)
        self.addCleanup(unix.close)
        before = (_rcvbuf(tcp), _rcvbuf(unix))
        rtc.boost_udp_buffers(1024 * 1024)
        self.assertEqual((_rcvbuf(tcp), _rcvbuf(unix)), before)


class LipSyncTest(unittest.TestCase):
    def test_rtpbin_never_shifts_stream_offsets(self):
        # o lip-sync empurrava o ts-offset do áudio ~137 ms a cada RTCP SR: cada salto virava um buraco no som
        rtc.Gst.init(None)
        outer = rtc.Gst.Bin.new("whep")
        webrtc = rtc.Gst.ElementFactory.make("webrtcbin", None)
        outer.add(webrtc)
        rtc.enable_nack(outer)
        self.assertEqual(webrtc.get_by_name("rtpbin").get_property("max-ts-offset"), 1)


class JitterBufferTest(unittest.TestCase):
    def test_jitterbuffer_waits_long_enough_for_retransmission(self):
        # com 120 ms o RTX chegava tarde a 2% de perda (centenas de quadros rasgados até o próximo IDR); 250 ms recuperou tudo
        rtc.Gst.init(None)
        outer = rtc.Gst.Bin.new("whep")
        webrtc = rtc.Gst.ElementFactory.make("webrtcbin", None)
        outer.add(webrtc)
        rtc.enable_nack(outer)
        self.assertGreaterEqual(webrtc.get_property("latency"), 250)


if __name__ == "__main__":
    unittest.main()
