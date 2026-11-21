"""Bind the shared Docker checkpoint transport to the native EdgeBench judge."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

from benchmark.runtime.docker_checkpoint import capture_docker_worker, validate_worker_request

from .checkpoint_controller import _controller, _physical, _remaining, capture


def capture_worker(controller_file: Path, *, token: str, capture_id: str,
                   destination: Path, request_file: Path, timeout: float = 30,
                   seal_within_phase: bool = False, terminal_scheduler_phases: bool = False) -> dict:
    """Freeze one EdgeBench worker and bind the native evaluator and phase."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Worker capture timeout must be finite and positive")
    deadline = time.monotonic() + timeout
    request = validate_worker_request(json.loads(_physical(request_file).read_bytes()))
    if seal_within_phase and "handoff" not in request:
        raise ValueError("Post-release sealing requires an exact prepared handoff")
    if terminal_scheduler_phases and "handoff" in request:
        raise ValueError("Terminal phase discovery cannot change a prepared handoff")
    controller = _controller(controller_file)
    resolve_components = None
    if terminal_scheduler_phases:
        from .terminal_capture import _scheduler_components_at_barrier

        resolve_components = _scheduler_components_at_barrier

    def capture_native(target, phase, remaining):
        return capture(controller_file, token=token, capture_id=capture_id,
                       destination=target, timeout=remaining, phase_deadline_file=phase)

    return capture_docker_worker(
        capture_id=capture_id, destination=destination, request=request,
        native_bank=controller["bank_root"], capture_native=capture_native,
        timeout=_remaining(deadline), seal_within_phase=seal_within_phase,
        components_at_barrier=resolve_components,
    )
