"""Visual do Project LUCE: temas personalizados dos membros da BDK.

Tudo o mais fica quieto de propósito; o olho vai pra o que está no ar. As animações
somem sozinhas quando o sistema pede menos movimento (gtk-enable-animations).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

THEMES: dict[str, tuple[str, dict[str, str]]] = {
    "luce": (
        "Padrão (LUCE)",
        {
            "noite": "#0a0c1f",
            "painel": "#12153a",
            "linha": "#232859",
            "ciano": "#00e5ff",
            "violeta": "#7c4dff",
            "magenta": "#ff2d95",
            "texto": "#e8ebff",
            "mudo": "#8d93c7",
        },
    ),
    "murilo": (
        "MuriloX06 (Roxo)",
        {
            "noite": "#0d0718",
            "painel": "#1a0f2e",
            "linha": "#341d59",
            "ciano": "#a855f7",
            "violeta": "#6366f1",
            "magenta": "#ec4899",
            "texto": "#f3e8ff",
            "mudo": "#a78bfa",
        },
    ),
    "tirigam": (
        "Tirigam (Vermelho)",
        {
            "noite": "#0f0406",
            "painel": "#1c070b",
            "linha": "#45101a",
            "ciano": "#ff1744",    # Vermelho Puro Vibrante
            "violeta": "#b71c1c",  # Vermelho Sangue Profundo (sem laranja!)
            "magenta": "#ff0033",  # Carmesim Ao Vivo
            "texto": "#ffeef0",
            "mudo": "#c97a85",
        },
    ),
    "ninja_som": (
        "Ninja / SOM (Azul)",
        {
            "noite": "#050d1a",
            "painel": "#0b1930",
            "linha": "#183461",
            "ciano": "#3b82f6",
            "violeta": "#06b6d4",
            "magenta": "#38bdf8",
            "texto": "#e5f0ff",
            "mudo": "#7da2d6",
        },
    ),
    "dark": (
        "Dark (Escuro OLED)",
        {
            "noite": "#050507",
            "painel": "#101014",
            "linha": "#26262e",
            "ciano": "#e4e4e7",
            "violeta": "#52525b",
            "magenta": "#ef4444",
            "texto": "#fafafa",
            "mudo": "#8e8e99",
        },
    ),
}

_BASE_CSS = """
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
.avatar { min-width: 34px; min-height: 34px; border-radius: 999px; font-weight: 800; color: @noite;
          background-image: linear-gradient(135deg, @ciano, @violeta); }
.avatar-box { min-width: 34px; min-height: 34px; border-radius: 999px; }
.avatar-img { border-radius: 999px; }
.header-avatar { min-width: 22px; min-height: 22px; border-radius: 999px; }
.on-air { min-width: 7px; min-height: 7px; border-radius: 999px; background: @magenta; }

scale trough { background: @linha; border-radius: 4px; min-height: 4px; }
scale highlight { background-image: linear-gradient(to right, @ciano, @violeta); border-radius: 4px; }
scale slider { background: @texto; border-radius: 999px; min-width: 14px; min-height: 14px; }

dropdown > button, switch { border-radius: 10px; }
switch { background: @linha; }
switch:checked { background-image: linear-gradient(135deg, @ciano, @violeta); }

.update-card { background: @painel; border: 1px solid alpha(@ciano, 0.4); border-radius: 16px; padding: 24px; }
.update-badge { background: alpha(@magenta, 0.2); color: @magenta; border: 1px solid @magenta; border-radius: 999px; padding: 4px 12px; font-weight: 700; font-size: 11px; }
.update-title { font-size: 18px; font-weight: 800; color: @texto; }
.update-version { font-size: 14px; font-weight: 700; color: @ciano; }
.update-desc { font-size: 13px; color: @mudo; }
.online-pill { background: @painel; border: 1px solid alpha(#22c55e, 0.4); border-radius: 999px; padding: 4px 10px; }
.online-pill:hover { border-color: #22c55e; background: alpha(#22c55e, 0.12); }
.online-dot { min-width: 7px; min-height: 7px; border-radius: 999px; background: #22c55e; box-shadow: 0 0 8px #22c55e; }
.online-label { font-size: 11px; font-weight: 700; color: #22c55e; }

progressbar trough { background: @linha; border-radius: 6px; min-height: 8px; }
progressbar progress { background-image: linear-gradient(to right, @ciano, @violeta); border-radius: 6px; }
"""

_provider: Gtk.CssProvider | None = None


def _build_css(colors: dict[str, str]) -> str:
    defs = "\n".join(f"@define-color {k} {v};" for k, v in colors.items())
    return f"{defs}\n{_BASE_CSS}"


def load(theme_name: str | None = None) -> None:
    """Instala ou atualiza o tema visual dinamicamente para todas as janelas."""
    global _provider
    from . import config

    if theme_name is None:
        theme_name = config.load().get("theme", "luce")
    if theme_name not in THEMES:
        theme_name = "luce"

    _label, colors = THEMES[theme_name]
    css = _build_css(colors)

    if _provider is None:
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    _provider.load_from_string(css)
