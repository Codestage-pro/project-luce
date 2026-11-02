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


class PreviewPauseTest(unittest.TestCase):
    def _frames_reaching_preview(self, paused: bool) -> int:
        cap = CapturePipeline(whip_endpoint="http://127.0.0.1:1/t/whip", width=320, height=240, fps=30,
                              bitrate_kbps=500, pattern=True, preview=True, audio_enabled=False,
                              preview_sink=lambda: Gst.ElementFactory.make("fakesink", None))
        self.addCleanup(cap.stop)
        cap.preview_paused = paused
        count = [0]
        out = cap.pipeline.get_by_name("preview-out").get_static_pad("sink")
        out.add_probe(Gst.PadProbeType.BUFFER, lambda *_: (count.__setitem__(0, count[0] + 1), Gst.PadProbeReturn.OK)[1])
        cap.pipeline.set_state(Gst.State.PLAYING)
        cap.pipeline.get_state(2 * Gst.SECOND)
        import time
        time.sleep(1)
        return count[0]

    def test_paused_preview_gets_no_frames(self):
        # app em segundo plano: ninguém olha a prévia, então nem converte/desenha (economia que o Discord faz)
        self.assertGreater(self._frames_reaching_preview(paused=False), 0)  # o teste não passa à toa
        self.assertEqual(self._frames_reaching_preview(paused=True), 0)


if __name__ == "__main__":
    unittest.main()
