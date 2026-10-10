"""Bounded transient dispatch for one provider stream, not Chat admission authority."""

from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import threading


class ProfileEventDispatch:
    """Keep conversation order without letting one answer block every audience.

    A lane is only a transport scheduling key. The handler must read fresh
    binding/authority and persist through the existing Inbox/Chat owners.
    Pending lines are transient, just like the consumer's pipe buffer.
    """

    def __init__(
        self,
        handle: Callable[[str], None],
        stop: threading.Event,
        *,
        workers: int = 4,
        capacity: int = 64,
    ) -> None:
        if workers < 1 or capacity < workers:
            raise ValueError("dispatch capacity must cover its positive worker limit")
        self._handle = handle
        self._stop = stop
        self._workers = workers
        self._capacity = capacity
        self._condition = threading.Condition()
        self._waiting: dict[str, deque[tuple[tuple[str, ...], str]]] = {}
        self._ready: deque[str] = deque()
        self._active: dict[str, tuple[str, ...]] = {}
        self._pending = 0
        self._closing = False
        self._cancelled = False
        self._failure: Exception | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="loopx-lark-dispatch"
        )

    @property
    def failed(self) -> bool:
        """Expose failure while the stream reader is idle; close still owns it."""

        with self._condition:
            return self._failure is not None

    def submit(self, lane: tuple[str, ...], line: str) -> bool:
        with self._condition:
            while self._pending >= self._capacity and not self._closing:
                if self._stop.is_set():
                    return False
                self._condition.wait(0.1)
            if self._failure is not None:
                raise self._failure
            if self._closing or self._stop.is_set():
                return False
            source = lane[0]
            queue = self._waiting.setdefault(source, deque())
            queue.append((lane, line))
            self._pending += 1
            if source not in self._active and source not in self._ready:
                self._ready.append(source)
            self._start_ready()
            return True

    def _start_ready(self) -> None:
        if self._stop.is_set():
            self._discard_waiting()
        # A stable source keeps FIFO across rebinds; an optional Session key
        # also fences different sources that share the same conversation owner.
        for _ in range(len(self._ready)):
            if self._cancelled or len(self._active) >= self._workers:
                break
            source = self._ready.popleft()
            lane, line = self._waiting[source][0]
            occupied = {key for active in self._active.values() for key in active}
            if occupied.intersection(lane):
                self._ready.append(source)
                continue
            self._waiting[source].popleft()
            self._active[source] = lane
            self._executor.submit(self._run, source, line)

    def _discard_waiting(self) -> None:
        self._cancelled = True
        self._pending -= sum(len(queue) for queue in self._waiting.values())
        self._waiting.clear()
        self._ready.clear()

    def _run(self, source: str, line: str) -> None:
        try:
            self._handle(line)
        except Exception as exc:
            with self._condition:
                if self._failure is None:
                    self._failure = exc
                self._closing = True
                self._discard_waiting()
        finally:
            with self._condition:
                self._pending -= 1
                self._active.pop(source)
                queue = self._waiting.get(source)
                if queue:
                    self._ready.append(source)
                else:
                    self._waiting.pop(source, None)
                self._start_ready()
                self._condition.notify_all()

    def close(self, *, cancel_pending: bool = False) -> None:
        """Retain ownership until active handlers settle; never replay a handler."""

        with self._condition:
            self._closing = True
            while self._pending:
                if cancel_pending or self._stop.is_set():
                    self._discard_waiting()
                self._condition.wait(0.1)
        self._executor.shutdown(wait=True)
        if self._failure is not None:
            raise self._failure
