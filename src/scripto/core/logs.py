"""Logging: the full log always goes to disk; memory keeps a small ring for UI.

The disk log rotates by size so long batches can't fill the disk; the ring
buffer is hard-capped so a long run can't grow UI memory (a my-transcriptor
lesson: its unbounded in-memory event list plus full rebuilds froze the app).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import paths

LOGGER_NAME = "scripto"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DEFAULT_RING_CAPACITY = 500
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5


class RingBufferHandler(logging.Handler):
    """Keeps the last ``capacity`` formatted lines; thread-safe snapshots."""

    def __init__(self, capacity: int = DEFAULT_RING_CAPACITY):
        super().__init__()
        self._lines: deque[str] = deque(maxlen=capacity)
        self._snapshot_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with self._snapshot_lock:
            self._lines.append(line)

    def lines(self) -> list[str]:
        with self._snapshot_lock:
            return list(self._lines)


def setup_logging(
    log_dir: Path | None = None,
    *,
    ring_capacity: int = DEFAULT_RING_CAPACITY,
    level: int = logging.INFO,
) -> RingBufferHandler:
    """Configure the ``scripto`` logger tree; safe to call more than once."""
    directory = log_dir or paths.log_dir()
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        directory / "scripto.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    ring = RingBufferHandler(ring_capacity)
    ring.setFormatter(formatter)
    root.addHandler(ring)

    return ring
