import unittest
from unittest import mock

from luce import gui


class PreviewFocusTest(unittest.TestCase):
    def test_preview_pauses_while_another_app_has_focus(self):
        win = mock.MagicMock()
        win._capture = mock.MagicMock(preview_paused=False)
        win.is_active.return_value = False
        gui.MainWindow._sync_preview_pause(win)
        self.assertTrue(win._capture.preview_paused)
        win._preview_paused_label.set_visible.assert_called_with(True)

        win.is_active.return_value = True  # voltou o foco: a prévia segue
        gui.MainWindow._sync_preview_pause(win)
        self.assertFalse(win._capture.preview_paused)
        win._preview_paused_label.set_visible.assert_called_with(False)

    def test_no_capture_hides_paused_label(self):
        win = mock.MagicMock(_capture=None)
        win.is_active.return_value = False
        gui.MainWindow._sync_preview_pause(win)
        win._preview_paused_label.set_visible.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()
