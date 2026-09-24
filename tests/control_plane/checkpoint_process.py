"""Process driver for public writer and caller-exit checkpoint regressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "tests/control_plane_ts/checkpoint_commit_probe.ts"


def start_probe(request: dict, output=None):
    child = subprocess.Popen(["node", "--no-warnings", "--experimental-strip-types", str(PROBE)],
        stdin=subprocess.PIPE, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", cwd=REPO)
    child.stdin.write(json.dumps(request))
    child.stdin.close()
    child.stdin = None
    return child


def wait_for(path: Path, child=None, timeout=20):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if child is not None and child.poll() is not None:
            raise AssertionError(child.communicate())
        if time.monotonic() >= deadline:
            raise AssertionError(f"test barrier timed out: {path.name}")
        time.sleep(0.01)


def refresh(registry, runtime, token):
    from loopx.state_refresh import refresh_state_run
    from tests.control_plane.test_quota_settlement_cli import GOAL_ID, AGENT_ID, TODO_ID, TURN_ID
    return refresh_state_run(registry_path=Path(registry), runtime_root_override=str(runtime),
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID,
        project=None, state_file=None, classification="validated_change", recommended_action=None,
        delivery_batch_scale="implementation", delivery_outcome="outcome_progress",
        vision_unchanged_reason="The current basis remains applicable.",
        checkpoint_read_context_id=token, dry_run=False, sync_global=False,
        external_delivery={"suppress": True, "resume_key": None})


def main(request):
    from loopx.control_plane.todos import provider_update
    from loopx.control_plane.goals import checkpoint_context_io
    from tests.control_plane.test_quota_settlement_cli import GOAL_ID, AGENT_ID, TODO_ID
    barrier = Path(request["barrier"])
    mode = request["mode"]
    if mode == "reader":
        from loopx.control_plane.goals.checkpoint_context_io import read_checkpoint_context, CheckpointReadContextRejected
        from tests.control_plane.test_quota_settlement_cli import TURN_ID
        try:
            result = read_checkpoint_context(registry_path=Path(request["registry"]),
                runtime_root_override=request["runtime"], goal_id=GOAL_ID, agent_id=AGENT_ID,
                todo_id=TODO_ID, turn_instance_id=TURN_ID)
        except CheckpointReadContextRejected as error:
            result = {"ok": False, "error_code": error.code}
        print(json.dumps(result))
        return 0
    if mode == "append":
        from loopx.history import write_reserved_run_artifacts
        row = {"goal_id": GOAL_ID, "generated_at": "2026-01-02T00:00:00+00:00", "classification": "synthetic_observation"}
        write_reserved_run_artifacts(runs_dir=Path(request["runtime"]) / "goals" / GOAL_ID / "runs",
            generated_at=row["generated_at"], record=row.copy(), index_record=row.copy(), payload={},
            render_markdown=lambda _: "Synthetic observation")
        print(json.dumps({"ok": True}))
        return 0
    adapter = provider_update if mode == "writer" else checkpoint_context_io
    original = adapter.effect_runtime_result

    def native(method, params):
        target = "coordination.local_authority.todo_update" if mode == "writer" else "goal.checkpoint_read_context.commit"
        if method != target:
            return original(method, params)
        envelope = {"mode": "writer" if mode == "writer" else "checkpoint", "barrier": str(barrier),
                    "provider": request["provider"], "method": method, "params": params,
                    "provider_direct": request.get("provider_direct", False)}
        if mode == "caller-exit":
            output = (barrier / "orphan-result").open("w", encoding="utf-8")
            child = start_probe(envelope, output=output)
            wait_for(barrier / "head-read", child)
            (barrier / "orphan-pid").write_text(str(child.pid))
            # Deliberately bypass context manager cleanup. The native claims
            # must keep excluding a new index reader and receipt replacement.
            os._exit(0)
        child = start_probe(envelope)
        # The canonical writer may now wait 30s for the real provider fence;
        # let that typed lock result win over the probe harness deadline.
        stdout, stderr = child.communicate(timeout=45)
        assert child.returncode == 0, stdout + stderr
        return json.loads(stdout)

    adapter.effect_runtime_result = native
    if mode == "writer":
        # Use the public CLI, including its registry adaptation and projection
        # settlement. Only the native process is instrumented at its real CAS.
        from loopx.cli import main as cli
        return cli(["--registry", request["registry"], "--format", "json", "todo", "update",
            "--goal-id", GOAL_ID, "--todo-id", request.get("todo_id", TODO_ID),
            "--agent-id", AGENT_ID, "--note", "peer-result-v2",
            "--task-lease-idempotency-key", f"checkpoint-{request.get('todo_id', TODO_ID)}",
            "--task-lease-expected-version", "1"])
    refresh(request["registry"], request["runtime"], request["token"])


if __name__ == "__main__":
    raise SystemExit(main(json.loads(sys.argv[1])))
