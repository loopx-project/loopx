"""Bounded byte transport for a process-owned Effect runtime."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from collections.abc import Mapping, Sequence
from threading import Thread


class EffectRuntimeStdio:
    """Exchange one newline-delimited frame at a time with a local child."""

    def __init__(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        self._process = subprocess.Popen(
            argv,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        self._input = self._process.stdin
        self._output = self._process.stdout
        self._owner_pid = os.getpid()
        try:
            os.set_blocking(self._input.fileno(), False)
            os.set_blocking(self._output.fileno(), False)
        except OSError:
            self.close(reap_in_background=True)
            raise

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def alive(self) -> bool:
        return self._owner_pid == os.getpid() and self._process.poll() is None

    def exchange(self, frame: bytes, timeout: float, limit: int) -> bytes:
        deadline = time.monotonic() + timeout
        write_fd = self._input.fileno()
        read_fd = self._output.fileno()
        pending = memoryview(frame)
        with selectors.DefaultSelector() as selector:
            selector.register(write_fd, selectors.EVENT_WRITE)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Effect runtime pipe write timed out")
                try:
                    written = os.write(write_fd, pending)
                except BlockingIOError:
                    continue
                pending = pending[written:]

            selector.unregister(write_fd)
            selector.register(read_fd, selectors.EVENT_READ)
            result = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Effect runtime pipe read timed out")
                try:
                    block = os.read(read_fd, min(64 * 1024, limit + 1 - len(result)))
                except BlockingIOError:
                    continue
                if not block:
                    raise EOFError("Effect runtime pipe closed before its response")
                result.extend(block)
                if len(result) > limit:
                    raise ValueError("Effect runtime pipe response is oversized")
                end = block.find(b"\n")
                if end >= 0:
                    if end != len(block) - 1:
                        raise ValueError("Unexpected additional data on the Effect runtime pipe")
                    return bytes(result)

    def close(self, *, wait_seconds: float = 0.0, reap_in_background: bool = False) -> bool:
        # EOF retires the stream. Never terminate a child that may be finishing
        # an already dispatched mutation. A fork only closes its own copies.
        self._input.close()
        self._output.close()
        if self._owner_pid != os.getpid():
            return False
        try:
            self._process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            if reap_in_background:
                Thread(target=self._process.wait, daemon=True).start()
            return False
        return True
