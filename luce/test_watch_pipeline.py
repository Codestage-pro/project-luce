import unittest

from luce.watch_pipeline import Gst, WatchPipeline


class KeyframeRequestTest(unittest.TestCase):
    def test_h264_depayloader_asks_publisher_for_keyframe_after_loss(self):
        # sem PLI, uma perda que o RTX não salvou deixa a imagem rasgada até o próximo IDR (até 1 s)
        Gst.init(None)
        watch = WatchPipeline("http://127.0.0.1:9/x/whep")
        parsed = Gst.Bin.new("parsebin-like")  # o depay nasce dentro do parsebin, depois da negociação
        watch.pipeline.add(parsed)
        depay = Gst.ElementFactory.make("rtph264depay", None)
        parsed.add(depay)
        self.assertTrue(depay.get_property("request-keyframe"))


if __name__ == "__main__":
    unittest.main()
