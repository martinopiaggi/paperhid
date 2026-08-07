"""Shared CLI error type for PaperHid host commands."""
from __future__ import annotations


class CliError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code
