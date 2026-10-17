"""Content-free hints for existing local readers of the durable Turn journal."""
from collections.abc import Iterator
from contextlib import contextmanager
import threading


class TurnReleaseWakeup:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: set[threading.Event] = set()

    @contextmanager
    def __call__(self, event: threading.Event) -> Iterator[None]:
        with self._lock:
            if event in self._events:
                raise ValueError("turn release wakeup is already registered")
            self._events.add(event)
        try:
            yield
        finally:
            with self._lock:
                self._events.discard(event)

    def notify(self) -> None:
        # Events do no IO and carry no Session, text or completion authority.
        # Never invoke transport code from a model worker or under its lock.
        with self._lock:
            events = tuple(self._events)
        for event in events:
            event.set()
