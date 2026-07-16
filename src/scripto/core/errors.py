"""Shared exception types.

Core code raises with an English message plus an optional i18n ``key`` and
``params`` so UI layers can localize (R7) without core importing any UI or
config machinery.
"""

from __future__ import annotations

from typing import Any


class ScriptoError(Exception):
    """A user-facing error with a readable message."""

    def __init__(self, message: str, *, key: str | None = None, **params: Any):
        super().__init__(message)
        self.key = key
        self.params = params


class OperationStopped(Exception):
    """An operation was interrupted by a stop request (not a failure)."""
