"""Private POSIX IO process for an authorized completion validator.

The inherited flock stays open through process-group cleanup. The validator
keeps its normal stdin and receives neither the control pipe nor the lock.
"""
from __future__ import annotations

import json
import os
from selectors import DefaultSelector, EVENT_READ
import subprocess
import sys
from time import monotonic

from ...extensions.process_runtime import terminate_process_tree


def _run_validator(control_fd: int, timeout_seconds: float, argv: list[str]) -> tuple[str, int | str | None]:
    with DefaultSelector() as events:
        events.register(control_fd, EVENT_READ)
        if events.select(0):
            return "cancelled", None
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            return "launch_failed", None
        except ValueError as error:
            return "malformed", str(error)

        # The same loop watches caller EOF and drains output without keeping it.
        with process:
            try:
                outcome, code = _observe_validation(process, events, control_fd, timeout_seconds)
            except BaseException:
                terminate_process_tree(process, grace_seconds=0)
                raise
            if outcome != "exited":
                terminate_process_tree(process, grace_seconds=0)
            return outcome, code


def _observe_validation(
    process: subprocess.Popen[bytes], events: DefaultSelector, control_fd: int, timeout_seconds: float,
) -> tuple[str, int | None]:
    deadline = monotonic() + timeout_seconds
    assert process.stdout is not None and process.stderr is not None
    events.register(process.stdout, EVENT_READ)
    events.register(process.stderr, EVENT_READ)
    while True:
        if len(events.get_map()) == 1 and process.poll() is not None:
            return "exited", process.returncode
        remaining = deadline - monotonic()
        if remaining <= 0:
            return "timeout", None
        # Poll the leader only after both output streams are closed.
        delay = min(remaining, 0.05) if len(events.get_map()) == 1 else remaining
        for ready, _ in events.select(delay):
            if ready.fd == control_fd:
                return "cancelled", None
            if not os.read(ready.fd, 65536):
                events.unregister(ready.fileobj)


def _main() -> None:
    control_fd, timeout, *argv = sys.argv[1:]
    result = _run_validator(int(control_fd), float(timeout), argv)
    try:
        print(json.dumps(result), flush=True)
    except BrokenPipeError:
        # The original caller exited. Cleanup is already complete.
        os._exit(0)


if __name__ == "__main__":
    _main()
