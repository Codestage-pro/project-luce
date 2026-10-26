"""Compatibilidade: os módulos Windows agora residem em luce.windows."""

from .windows.winenv import *  # noqa: F401, F403
from .windows.winenv import setup

__all__ = ["setup"]
