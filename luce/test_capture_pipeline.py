import unittest

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)

from luce.capture_pipeline import CapturePipeline


class EncodedQueueTest(unittest.TestCase):
    def test_queue_after_encoder_never_drops_h264(self):
        # quadro H.264 jogado fora aqui não deixa buraco no RTP (quem assiste não pede IDR):
        # a imagem fica rasgada/congelada até o próximo keyframe, 1 s depois
        cap = CapturePipeline(whip_endpoint="http://127.0.0.1:1/t/whip", width=320, height=240, fps=30,
                              bitrate_kbps=500, pattern=True, preview=False, audio_enabled=False)
        self.addCleanup(cap.pipeline.set_state, Gst.State.NULL)
        self.assertEqual(int(cap.pipeline.get_by_name("queue").get_property("leaky")), 0)


if __name__ == "__main__":
    unittest.main()
