"""Compatibilidade: os módulos Linux agora residem em luce.linux."""

from .linux.portal import *  # noqa: F401, F403
from .linux.portal import (
    PortalError,
    ScreenCast,
    screencast_available,
    x11_session,
)

__all__ = [
    "PortalError",
    "ScreenCast",
    "screencast_available",
    "x11_session",
]
