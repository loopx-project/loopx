"""Python transport for the TS-owned managed Host process lifecycle."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..effect_runtime import _node_executable

# Keep Python's Windows executable/batch launcher compatibility. No timeout,
# buffering or lifecycle decision lives in this transport-only child.
_WINDOWS_COMMAND_RELAY = "import subprocess,sys;sys.exit(subprocess.call(sys.argv[1:]))"


class HostOutputLines:
    """Frame LF records without retaining raw trajectories or an unbounded line."""

    def __init__(self, consume: Callable[[str], None], max_chars: int = 1_048_576):
        self.consume = consume
        self.max_chars = max_chars
        self.pending = ""
        self.dropping = False
        self.complete = True

    def feed(self, text: str) -> None:
        pieces = text.split("\n")
        for index, piece in enumerate(pieces):
            if not self.dropping:
                if len(self.pending) + len(piece) > self.max_chars:
                    self.complete = False
                    self.pending = ""
                    self.dropping = True
                else:
                    self.pending += piece
            if index < len(pieces) - 1:
                if not self.dropping:
                    self.consume(self.pending)
                self.pending = ""
                self.dropping = False

    def finish(self) -> None:
        if self.pending and not self.dropping:
            self.consume(self.pending)
        self.pending = ""


def run_host_process(
    argv: Sequence[str],
    *,
    project: Path,
    input_text: str,
    timeout_seconds: float,
    stdout_limit_bytes: int | None = None,
    drain_timeout_seconds: float = 2,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    delegated_lease: dict[str, Any] | None = None,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Keep the control pipe open until exit; EOF cancels the owned process group.

    Callbacks observe transient chunks. They must not persist raw Host output.
    TS owns deadlines, byte budgets, termination and the final observation.
    """
    command = list(argv)
    if sys.platform == "win32":
        command = [sys.executable, "-c", _WINDOWS_COMMAND_RELAY, *command]
    request = {
        "argv": command,
        "cwd": str(project),
        "input": input_text,
        "timeout_ms": max(1.0, timeout_seconds) * 1000,
        "drain_timeout_ms": drain_timeout_seconds * 1000,
        "stdout_limit_bytes": stdout_limit_bytes,
    }
    if delegated_lease is not None:
        request["delegated_lease"] = delegated_lease
    bridge = Path(__file__).with_name("host_process_bridge.ts")
    with subprocess.Popen(
        [
            _node_executable(),
            "--no-warnings",
            "--experimental-strip-types",
            str(bridge),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="strict",
        start_new_session=True,
        env=environment,
    ) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        result = None
        try:
            proc.stdin.write(
                json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            proc.stdin.flush()
            for line in proc.stdout:
                event = json.loads(line)
                kind = event.get("kind")
                if kind in {"stdout", "stderr"} and isinstance(event.get("text"), str):
                    consume = on_stdout if kind == "stdout" else on_stderr
                    if consume is not None:
                        consume(event["text"])
                elif kind == "result" and event.get("outcome") in {
                    "exited",
                    "timeout",
                    "cancelled",
                    "output_limit",
                    "spawn_failed",
                }:
                    result = event
                else:
                    raise RuntimeError("Invalid managed Host process observation")
        finally:
            # Also runs on callback failure / Ctrl-C. Do not kill the supervisor
            # before it has had a chance to terminate its owned Host group.
            proc.stdin.close()
            try:
                # The leased CLI gets six seconds to acknowledge nested Host
                # cleanup. Keep this control transport alive through its forced
                # group kill as well, including callback failure / Ctrl-C.
                proc.wait(timeout=8 if delegated_lease is not None else 5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.returncode != 0 or result is None:
            raise RuntimeError("Managed Host process supervision returned no result")
        return result
