"""Native SForge IO for the toolkit's existing duration and Goal acceptance owners.

The host selects exact solver receipts and supplies real process liveness. No
clock or continuation state is added to the worker or its checkpoint capsule.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time


class NativeExecutionEvidence:
    def __init__(
        self,
        *,
        run_id: str,
        task_id: str,
        trial: Path,
        target_seconds: int,
        runner_active: Callable[[], bool],
        node: str | None = None,
        previous: tuple[tuple[Path, str], ...] = (),
    ):
        if type(target_seconds) is not int or target_seconds <= 0:
            raise ValueError("A finite positive solver allowance is required")
        self.run_id, self.task_id = run_id, task_id
        self.trial, self.target_seconds = Path(trial), target_seconds
        self.runner_active, self.previous = runner_active, previous
        self.node = node or shutil.which("node")
        if not self.node:
            raise ValueError("The duration owner requires the qualified Node runtime")

    def fulfilled(self) -> bool | None:
        """Project native intervals through the same owner used by campaign reports."""
        now = time.time()
        intervals = []
        active = self.runner_active()
        if type(active) is not bool:
            return None
        sources = [*self.previous, (self.trial / "solver_execution.json", None)]
        for path, digest in sources:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                return None
            raw = path.read_bytes()
            if digest is not None and hashlib.sha256(raw).hexdigest() != digest:
                return None
            execution = json.loads(raw)
            if (
                not isinstance(execution, dict)
                or execution.get("schema_version") != 1
                or execution.get("status") == "unknown"
            ):
                return None
            segments = execution.get("segments")
            if not isinstance(segments, list) or not segments:
                return None
            for segment in segments:
                if not isinstance(segment, dict):
                    return None
                start, end = segment.get("started_at"), segment.get("finished_at")
                if end is None:
                    if (
                        digest is not None
                        or not active
                        or segment.get("status") != "pending"
                    ):
                        return None
                    end = now
                if any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in (start, end)
                ):
                    return None
                if not 0 <= start <= end <= now:
                    return None
                # The typed owner parses observed_at at millisecond precision.
                # Round inward so an active tail is never in the future and
                # fractional setup/teardown time cannot become execution credit.
                first, last = math.ceil(start * 1000), math.floor(end * 1000)
                if first > last:
                    continue  # A sub-millisecond segment contributes no credit.
                intervals.append(
                    dict(
                        start_ms=first,
                        end_ms=last,
                        evidence_ref="native:solver-execution",
                    )
                )
        request = dict(
            schema_version="benchmark_campaign_report_input_v1",
            observed_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
            title="Execution commitment",
            case_ids=[self.task_id],
            arms=[dict(arm_id="worker", label="Worker")],
            selection=[
                dict(
                    arm_id="worker",
                    case_id=self.task_id,
                    run_id=self.run_id,
                    status="running" if active else "completed",
                    score=None,
                    protocol_id="native",
                    exclude_reason=None,
                    rerun_reason=None,
                    active_intervals=intervals,
                )
            ],
            incidents=[],
            notes=[],
            execution_audit=[],
            duration_policy=dict(
                target_ms=self.target_seconds * 1000, allow_goal_completion=[]
            ),
        )
        owner = (
            Path(__file__).resolve().parents[2] / "scripts/benchmark-campaign-report.ts"
        )
        code = (
            "import {readFileSync} from 'node:fs'; import {buildReport} from "
            + json.dumps(owner.as_uri())
            + "; console.log(JSON.stringify(buildReport("
            "JSON.parse(readFileSync(0,'utf8'))).duration_coverage[0]));"
        )
        result = subprocess.run(
            [self.node, "--input-type=module", "-e", code],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        coverage = json.loads(result.stdout)
        return None if coverage["active_ms"] is None else coverage["remaining_ms"] == 0


def install_execution_commitment(
    app, *, run_id: str, task_id: str, observe: Callable[[], bool | None]
) -> None:
    """Install an explicit read-only extension on one native feedback relay.

    This packet is local to the SForge provider. It adds no Core vocabulary or
    scoring operation; unrelated registrations and default-off apps keep their
    original routes and behavior.
    """
    from fastapi import HTTPException

    if not run_id or not task_id or not callable(observe):
        raise ValueError("An exact native identity and observer are required")
    identity = (run_id, task_id)
    observers = getattr(app.state, "execution_commitment_observers", None)
    if observers is not None:
        previous = observers.get(identity)
        if previous is not None and previous != observe:
            raise ValueError("Native execution observer is already installed")
        observers[identity] = observe
        return
    observers = {identity: observe}
    app.state.execution_commitment_observers = observers

    @app.get("/api/v1/feedback/{relay_id}/execution-commitment")
    def execution_commitment(relay_id: str):
        try:
            _, info = app.state.judge.resolve_feedback_relay(relay_id)
        except KeyError:
            raise HTTPException(
                status_code=404, detail="Feedback relay not found"
            ) from None
        observer = observers.get((info["run_id"], info["task_id"]))
        if observer is None:
            raise HTTPException(status_code=404, detail="Feedback relay not found")
        try:
            fulfilled = observer()
        except (OSError, ValueError, subprocess.SubprocessError):
            fulfilled = None
        if fulfilled is not None and type(fulfilled) is not bool:
            raise HTTPException(
                status_code=503, detail="Native execution observation unavailable"
            )
        return dict(
            schema_version="sforge_execution_commitment_observation_v0",
            task_id=info["task_id"],
            observed_at_epoch=time.time(),
            fulfilled=fulfilled,
        )
