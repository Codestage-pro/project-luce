"""Compatibilidade: os módulos Windows agora residem em luce.windows."""

from .windows.winsys import *  # noqa: F401, F403
from .windows.winsys import (
    apply_icon,
    find_process_pid,
    is_window,
    list_audio_sources,
    list_monitors,
    list_mutable_apps,
    list_windows,
    process_name,
    window_pid,
    window_rect,
)

__all__ = [
    "apply_icon",
    "find_process_pid",
    "is_window",
    "list_audio_sources",
    "list_monitors",
    "list_mutable_apps",
    "list_windows",
    "process_name",
    "window_pid",
    "window_rect",
]
