"""Compatibilidade: os módulos Linux agora residem em luce.linux."""

from .linux.linux_audio import *  # noqa: F401, F403
from .linux.linux_audio import (
    LinuxAudioMuter,
    SystemAudioExcept,
    is_excluded,
    list_audio_sources,
    list_mutable_apps,
    wanted_streams,
    with_silence,
)

__all__ = [
    "LinuxAudioMuter",
    "SystemAudioExcept",
    "is_excluded",
    "list_audio_sources",
    "list_mutable_apps",
    "wanted_streams",
    "with_silence",
]
