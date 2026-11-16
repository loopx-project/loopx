from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

WAIT_FOR_FIXTURE_LEASE_RELEASE = """\
import os
import time

release_path = os.environ.get("LOOPX_TEST_LEASE_RELEASED")
if release_path:
    deadline = time.monotonic() + 10
    while not os.path.isfile(release_path):
        if time.monotonic() >= deadline:
            raise RuntimeError("lease writer did not finish while Host work was running")
        time.sleep(0.025)
"""


@contextmanager
def expect_lease_release_after_dispatch(monkeypatch, *, operation, registry, runtime, goal_id,
                                        todo_id, lease, dispatch_marker):
    """Contend with the canonical writer from a separate process at dispatch."""
    import pytest

    from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission

    original = FirstPartyHostGoalAdmission.launch_lifetime
    initial = dispatch_marker.read_bytes() if dispatch_marker.exists() else None
    ready = registry.parent / "lease-release-ready"
    released = registry.parent / "lease-released"
    scope = dict(registry=str(registry), runtime=str(runtime), goal=goal_id, todo=todo_id,
                 owner=lease["owner"], key=lease["idempotency_key"], version=lease["version"],
                 ready=str(ready), released=str(released))
    release_client = """
import json, sys
from pathlib import Path
from loopx.control_plane.work_items.task_lease import inspect_task_lease, release_task_lease

data = json.loads(sys.argv[1])
scope = dict(registry_path=Path(data['registry']), runtime_root=Path(data['runtime']),
             goal_id=data['goal'], todo_id=data['todo'])
assert inspect_task_lease(**scope)['lease']['version'] == data['version']
Path(data['ready']).touch()
result = release_task_lease(**scope, owner=data['owner'], idempotency_key=data['key'],
                            expected_version=data['version'])
assert result['ok'] and result['released'], result
Path(data['released']).touch()
print(json.dumps(result))
"""
    child = None

    @contextmanager
    def contend(admission, *, operation: str):
        nonlocal child
        with original(admission, operation=operation) as expires_at_ms:
            if operation == dispatch_operation:
                assert child is None
                assert (dispatch_marker.read_bytes() if dispatch_marker.exists() else None) == initial
                child = subprocess.Popen([sys.executable, "-c", release_client, json.dumps(scope)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 8
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.025)
                assert ready.exists(), "separate lease writer failed to observe the original lease"
                try:
                    output = child.communicate(timeout=1.5)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    pytest.fail(f"lease release crossed the dispatch guard: {output}")
            yield expires_at_ms

    dispatch_operation = operation
    try:
        with monkeypatch.context() as patch:
            patch.setattr(FirstPartyHostGoalAdmission, "launch_lifetime", contend)
            patch.setenv("LOOPX_TEST_LEASE_RELEASED", str(released))
            yield
        assert child is not None, "expected Host dispatch guard was not entered"
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, (stdout, stderr)
        result = json.loads(stdout)
        assert result["ok"] and result["released"], result
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


@contextmanager
def expect_lease_expiry_during_dispatch(monkeypatch, *, operation, observed_lease):
    """Let an unchanged canonical lease expire after the actual launch read."""
    from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
    from loopx.control_plane.work_items.task_lease import lease_expires_at, lease_is_active

    original = FirstPartyHostGoalAdmission.launch_lifetime
    crossed = []

    @contextmanager
    def pause(admission, *, operation):
        with original(admission, operation=operation) as expires_at_ms:
            if operation == dispatch_operation:
                lease = observed_lease()
                assert lease_is_active(lease), lease
                expires_at = lease_expires_at(lease)
                assert expires_at is not None
                while lease_is_active(lease):
                    time.sleep(min(0.02, max(0, expires_at.timestamp() - time.time())))
                crossed.append(lease["idempotency_key"])
            yield expires_at_ms

    dispatch_operation = operation
    with monkeypatch.context() as patch:
        patch.setattr(FirstPartyHostGoalAdmission, "launch_lifetime", pause)
        yield
    assert len(crossed) == 1, "expected one admitted dispatch to cross the lease deadline"


def assert_host_task_context(model_input, source):
    """Check source parity outside the executor-owned presentation boundary.

    Every task field and unknown addition must survive, including backlog,
    scopes, reads and replan authoring. The original commitment is carried
    separately; a task view cannot masquerade as a complete signed source.
    """
    assert model_input["schema_version"] == "loopx_turn_model_context_v0"
    assert model_input["source_schema_version"] == "loopx_turn_host_request_v1"
    assert not {"work_contract", "turn_envelope", "turn_authority"}.intersection(model_input)
    context = model_input["task_context"]
    outer_fields = {"schema_version", "contract_binding", "projection_envelope", "scheduler_hint",
                    "automation_cadence_readback", "automation_liveness", "scheduler_execution_context"}
    assert set(context) == set(source) - outer_fields
    for field in set(source) - outer_fields - {"interaction_contract"}:
        assert json.dumps(context[field], sort_keys=True, ensure_ascii=False) == json.dumps(
            source[field], sort_keys=True, ensure_ascii=False), field
    interaction = source["interaction_contract"]
    assert context["interaction_contract"] == {k: v for k, v in interaction.items() if k != "cli_channel"}
    if "source_decision_hash" in model_input:
        assert model_input["source_decision_hash"] == source["contract_binding"]["content_hash"]
    return context


def write_python_host_fixture(path: Path, source: str) -> Path:
    """Launch the same fixture through the real Host supervisor on each OS."""
    if os.name != "nt":
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)
        return path

    # Windows cannot execute a POSIX shebang. Use a native console launcher,
    # keeping argv, pipes, exit codes and supervision on the real process path.
    from distlib.scripts import ScriptMaker

    script = path.with_suffix(".py")
    script.write_text(source, encoding="utf-8")
    maker = ScriptMaker(str(path.parent), str(path.parent))
    maker.executable = sys.executable
    maker.force = True
    maker.variants = {""}
    maker.make(script.name)
    return path.with_suffix(".exe")


COUNTER_PROCESS_SOURCE = """
import os
import signal
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
pid_path = Path(sys.argv[2])
interval_seconds = float(sys.argv[3])
pause_path = Path(sys.argv[4]) if len(sys.argv) == 5 else None

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid_path.write_text(str(os.getpid()), encoding="utf-8")
staged = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
counter = 0
while True:
    staged.write_text(str(counter), encoding="utf-8")
    if pause_path is not None:
        pause_path.touch()
        signal.pause()
    os.replace(staged, marker)
    counter += 1
    time.sleep(interval_seconds)
"""
