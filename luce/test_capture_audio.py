import unittest

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)

from luce.capture_pipeline import CapturePipeline


class OpusStereoTest(unittest.TestCase):
    def test_encoder_only_accepts_stereo(self):
        # sem travar, o rtpopuspay prefere mono e o audioconvert junta L+R: quem assiste ouve igual nos dois lados
        cap = CapturePipeline(whip_endpoint="http://127.0.0.1:1/t/whip", width=320, height=240, fps=30,
                              bitrate_kbps=500, pattern=True, preview=False, audio_enabled=True)
        self.addCleanup(cap.pipeline.set_state, Gst.State.NULL)
        caps = cap.pipeline.get_by_name("audio-enc").get_static_pad("sink").peer_query_caps(None)
        self.assertTrue(caps.is_subset(Gst.Caps.from_string("audio/x-raw,channels=2")), caps.to_string())


if __name__ == "__main__":
    unittest.main()
