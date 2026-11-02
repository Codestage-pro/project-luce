import unittest
from unittest import mock

from luce import gui


def _sharing_window(**attrs):
    """MainWindow falso no meio de uma transmissão pelo portal (só os atributos que o restart lê)."""
    win = mock.MagicMock()
    win._portal = mock.MagicMock()
    win._x11 = False
    win._monitor_handle = win._window_handle = None
    win._name_entry.get_text.return_value = "amigo"
    win._restart_id = None
    for key, value in attrs.items():
        setattr(win, key, value)
    return win


class RestartTest(unittest.TestCase):
    def test_restart_does_not_start_a_second_pipeline_over_a_running_one(self):
        # visto no log de um amigo: duas capturas ao mesmo tempo = upload em dobro, perda, pixelado e áudio robotizado
        running = mock.MagicMock()
        win = _sharing_window(_capture=running)
        with mock.patch.object(gui, "CapturePipeline") as pipeline, mock.patch.object(gui, "config"), \
                mock.patch.object(gui, "_viewer_name", return_value="amigo"):
            gui.MainWindow._do_restart_sharing(win)
            pipeline.assert_not_called()
            self.assertIs(win._capture, running)
            win._capture = None  # sem captura rodando o restart tem que montar uma (o teste não passa à toa)
            gui.MainWindow._do_restart_sharing(win)
            pipeline.assert_called_once()

    def test_share_button_during_pending_restart_stops_instead_of_opening_a_second_share(self):
        # o botão segue "Parar transmissão" enquanto reconecta; antes ele abria outra transmissão por cima
        win = _sharing_window(_capture=None, _restart_id=42)
        with mock.patch.object(gui, "config"), mock.patch.object(gui, "SettingsWindow"), mock.patch.object(gui, "roster"):
            gui.MainWindow._on_share_clicked(win, None)
        win._stop_sharing.assert_called_once_with()

    def test_stop_cancels_pending_restart(self):
        win = _sharing_window(_capture=None, _restart_id=42)
        with mock.patch.object(gui.GLib, "source_remove") as remove:
            gui.MainWindow._stop_sharing(win)
        remove.assert_called_once_with(42)
        self.assertIsNone(win._restart_id)


if __name__ == "__main__":
    unittest.main()
