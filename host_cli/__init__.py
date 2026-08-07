"""Unified PaperHid host CLI package.

Public entry points:
  - ``python cli.py …``
  - ``python -m host_cli …``

Implementation lives here; ``keyboard_cli`` and ``paperpointer.cli`` are
compatibility re-exports.
"""
from __future__ import annotations

from host_cli.app import build_parser, main
from host_cli.errors import CliError

__all__ = ["CliError", "build_parser", "main"]
