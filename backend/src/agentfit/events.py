"""In-process publish/subscribe bus feeding the UI's live event stream."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from typing import Any


class EventBus:
    def __init__(self, backlog: int = 200) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self._backlog: list[dict[str, Any]] = []
        self._backlog_size = backlog
        self._seq = 0

    def publish(self, topic: str, payload: dict[str, Any] | None = None, *, keep: bool = True) -> dict[str, Any]:
        """Send an event to every subscriber. ``keep=False`` leaves it out of the backlog:
        for text a model is generating, which comes many times a second and is worth nothing later."""
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "topic": topic, "ts": time.time(), "data": payload or {}}
            if keep:
                self._backlog.append(event)
                del self._backlog[: -self._backlog_size]
            subscribers = list(self._subscribers)
        for sub in subscribers:
            with contextlib.suppress(queue.Full):
                sub.put_nowait(event)
        return event

    def subscribe(self, maxsize: int = 1000) -> queue.Queue[dict[str, Any]]:
        sub: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    def recent(self, topic_prefix: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._backlog if e["topic"].startswith(topic_prefix)]


bus = EventBus()
