"""Visual do Project LUCE: noite azulada, ciano/violeta da marca e UM só brilho — o magenta de "ao vivo".

Tudo o mais fica quieto de propósito; o olho vai pra o que está no ar. As animações
somem sozinhas quando o sistema pede menos movimento (gtk-enable-animations).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

_CSS = """
@define-color noite #0a0c1f;
@define-color painel #12153a;
@define-color linha #232859;
@define-color ciano #00e5ff;
@define-color violeta #7c4dff;
@define-color magenta #ff2d95;
@define-color texto #e8ebff;
@define-color mudo #8d93c7;

window, window.background { background: @noite; color: @texto; }
label { color: @texto; }
.dim { color: @mudo; }

.wordmark { font-size: 22px; font-weight: 800; letter-spacing: 6px; color: @ciano; }
.tagline { font-size: 11px; color: @mudo; }

/* o feixe: fio de luz sob o cabeçalho; vira magenta e respira quando está no ar */
.beam { min-height: 2px; border-radius: 2px;
        background-image: linear-gradient(to right, alpha(@ciano, 0), @ciano, @violeta, alpha(@violeta, 0)); }
window.live .beam { background-image: linear-gradient(to right, alpha(@magenta, 0), @magenta, @violeta, alpha(@violeta, 0));
                    animation: respira 2.4s ease-in-out infinite; }
@keyframes respira { 0% { opacity: 0.45; } 50% { opacity: 1; } 100% { opacity: 0.45; } }

entry { background: @painel; color: @texto; border: 1px solid @linha; border-radius: 10px; min-height: 36px; padding: 0 12px; }
entry:focus-within { border-color: @ciano; box-shadow: 0 0 0 3px alpha(@ciano, 0.18); }

button { background: @painel; color: @texto; border: 1px solid @linha; border-radius: 10px; box-shadow: none;
         min-height: 36px; padding: 0 14px; transition: background 150ms, border-color 150ms; }
button:hover { border-color: @ciano; background: alpha(@ciano, 0.08); }
button:active { background: alpha(@ciano, 0.18); }
button:checked { background: alpha(@ciano, 0.18); border-color: @ciano; color: @ciano; }
button.flat { background: transparent; border-color: transparent; }
button.flat:hover { background: alpha(@ciano, 0.10); }

button.go { background-image: linear-gradient(135deg, @ciano, @violeta); color: @noite; border: none; font-weight: 700; }
button.go:hover { background-image: linear-gradient(135deg, shade(@ciano, 1.15), shade(@violeta, 1.15)); }
button.stop { background: alpha(@magenta, 0.14); color: @magenta; border: 1px solid @magenta; font-weight: 700; }
button.stop:hover { background: alpha(@magenta, 0.26); }

.status { background: @painel; border: 1px solid @linha; border-radius: 999px; padding: 6px 14px; }
.status-dot { min-width: 8px; min-height: 8px; border-radius: 999px; background: @mudo; }
window.live .status { border-color: alpha(@magenta, 0.6); }
window.live .status-dot { background: @magenta; box-shadow: 0 0 10px @magenta; animation: pulsa 1.4s ease-in-out infinite; }
@keyframes pulsa { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }

.stats { font-size: 11px; color: @mudo; }

.stage { background: shade(@noite, 0.7); border: 1px solid @linha; border-radius: 14px; min-height: 140px; }
.stage picture { border-radius: 13px; }
.stage-hint { color: @mudo; font-size: 12px; }

.section { font-size: 12px; font-weight: 700; color: @mudo; letter-spacing: 1px; }
list, row { background: transparent; }
row { border-radius: 12px; }
row:hover { background: alpha(@violeta, 0.10); }
.avatar { min-width: 32px; min-height: 32px; border-radius: 999px; font-weight: 800; color: @noite;
          background-image: linear-gradient(135deg, @ciano, @violeta); }
.on-air { min-width: 7px; min-height: 7px; border-radius: 999px; background: @magenta; }

scale trough { background: @linha; border-radius: 4px; min-height: 4px; }
scale highlight { background-image: linear-gradient(to right, @ciano, @violeta); border-radius: 4px; }
scale slider { background: @texto; border-radius: 999px; min-width: 14px; min-height: 14px; }

dropdown > button, switch { border-radius: 10px; }
switch { background: @linha; }
switch:checked { background-image: linear-gradient(135deg, @ciano, @violeta); }
"""

_loaded = False


def load() -> None:
    """Instala o tema uma vez por processo (vale pra todas as janelas)."""
    global _loaded
    if _loaded:
        return
    Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
    provider = Gtk.CssProvider()
    provider.load_from_string(_CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _loaded = True
