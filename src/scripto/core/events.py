"""Event bus: the core layer's only channel to the outside (GUI, CLI, logs).

Contract:
- ``emit()`` is synchronous fan-out; a failing subscriber is logged and never
  breaks the emitter or other subscribers.
- Subscribers must return quickly. A slow consumer (e.g. a UI that refreshes
  at its own pace) must attach through :class:`BufferedSubscriber`, which
  decouples it behind a bounded drop-oldest buffer so the pipeline is never
  blocked and memory is never unbounded.
- Nothing in this module (or anywhere under ``scripto.core``) may import UI
  code.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


class Event:
    """Base class for all bus events."""


@dataclass(frozen=True)
class LogEvent(Event):
    level: str          # "info" | "warning" | "error"
    message: str


@dataclass(frozen=True)
class StatusEvent(Event):
    """A subject (e.g. a file job) changed state."""
    subject: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class ProgressEvent(Event):
    """Progress within a scope (e.g. 'transcribe:<file>' or 'download:<model>')."""
    scope: str
    done: int
    total: int
    detail: str = ""


@dataclass(frozen=True)
class BatchEvent(Event):
    kind: str           # "started" | "finished"
    stats: dict = field(default_factory=dict)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        """Register a handler; returns an idempotent unsubscribe function."""
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._handlers.remove(handler)
                except ValueError:
                    pass

        return unsubscribe

    def emit(self, event: Event) -> None:
        with self._lock:
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("event subscriber failed on %r", event)


class BufferedSubscriber:
    """Bounded drop-oldest buffer between the bus and a slow consumer.

    The consumer calls :meth:`drain` on its own schedule (e.g. a UI timer).
    When the buffer is full the oldest event is dropped and ``dropped`` is
    incremented — the pipeline never waits.
    """

    def __init__(self, bus: EventBus, capacity: int = 1000):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._buffer: deque[Event] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.dropped = 0
        self._unsubscribe = bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        with self._lock:
            if len(self._buffer) == self._buffer.maxlen:
                self.dropped += 1
            self._buffer.append(event)

    def drain(self) -> list[Event]:
        """Return and clear all buffered events (oldest first)."""
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
        return items

    def close(self) -> None:
        self._unsubscribe()
