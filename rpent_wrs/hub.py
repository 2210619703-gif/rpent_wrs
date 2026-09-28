"""In-process event log for the dashboard."""

from __future__ import annotations

import threading
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._events: list[dict[str, Any]] = []

    def emit(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._cv:
            event = {"id": len(self._events) + 1, **payload}
            self._events.append(event)
            self._cv.notify_all()
            return event

    def since(self, after_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if int(e["id"]) > after_id]

    def wait(self, after_id: int = 0, timeout: float = 20.0) -> list[dict[str, Any]]:
        with self._cv:
            ready = [e for e in self._events if int(e["id"]) > after_id]
            if ready:
                return list(ready)
            self._cv.wait(timeout)
            return [e for e in self._events if int(e["id"]) > after_id]
