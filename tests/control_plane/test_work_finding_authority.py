"""A new work-bound graph commit shares the canonical local writer fence."""
import json
from datetime import datetime, timezone
import subprocess
import sys
import time

import pytest

from loopx.capabilities.explore import result_writeback as writer
from loopx.capabilities.explore.result_log import explore_result_log_path
from loopx.cli import main
from loopx.control_plane.work_items.task_lease import acquire_task_lease, release_task_lease, renew_task_lease
from tests.control_plane.test_quota_settlement_cli import GOAL_ID, AGENT_ID, TODO_ID
from tests.control_plane.test_work_yardstick_cli import attachment, binding, context, run, setup


def prepare(tmp_path, provider, handoff_mode="hard_lease"):
    project, runtime, registry = setup(tmp_path, active=True, provider=provider, handoff_mode=handoff_mode)
    lease_args = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                      todo_id=TODO_ID, owner=AGENT_ID, idempotency_key="work-finding-fence")
    lease = acquire_task_lease(**lease_args)["lease"] if handoff_mode == "hard_lease" else None
    source = tmp_path / "finding.json"
    source.write_text(json.dumps(attachment()))
    command = ["explore", "finding", *binding("current-work-fence"), "--result-json", str(source)]
    first = run(project, runtime, registry, *command)
    payload = attachment()
    payload["work_yardstick_decision"]["prior_finding_id"] = first["finding_id"]
    payload["work_yardstick_decision"]["target"]["summary"] = "Revised target under the current work grant"
    source.write_text(json.dumps(payload))
    return project, runtime, registry, command, lease_args, lease


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mutation", ["release", "reacquire", "close", "stop", "disable"])
def test_changed_authority_between_prepare_and_append_has_no_graph_effect(
    tmp_path, monkeypatch, capsys, provider, mutation,
):
    project, runtime, registry, command, lease_args, lease = prepare(tmp_path, provider)
    log = explore_result_log_path(runtime, GOAL_ID)
    original_bytes = log.read_bytes()
    original_source = context(runtime, registry)["graph"]["work_yardsticks"][0]["source_finding_id"]
    append = writer._append_prepared_result

    def change_before_commit(*args, **kwargs):
        if mutation in {"release", "reacquire"}:
            assert release_task_lease(**lease_args, expected_version=lease["version"])["ok"]
            if mutation == "reacquire":
                newer = acquire_task_lease(**{**lease_args, "idempotency_key": "next-work-generation"})
                assert newer["ok"] and newer["lease"]["idempotency_key"] != lease["idempotency_key"]
        elif mutation == "close":
            run(project, runtime, registry, "todo", "complete", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
                "--agent-id", AGENT_ID, "--decision-outcome", "cancel", "--evidence", "Withdrawn work",
                "--task-lease-idempotency-key", lease["idempotency_key"],
                "--task-lease-expected-version", str(lease["version"]))
        elif mutation == "stop":
            run(project, runtime, registry, "goal-lifecycle", "--goal-id", GOAL_ID, "--operation", "stop",
                "--reason", "Stop before new evidence commits", "--actor-kind", "owner", "--execute")
        else:
            run(project, runtime, registry, "configure-goal", "--goal-id", GOAL_ID,
                "--no-execution-yardstick-enabled", "--execute")
        return append(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.chdir(project)
        patch.setattr(writer, "_append_prepared_result", change_before_commit)
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *command])
        packet = json.loads(capsys.readouterr().out)
    assert code == 1 and packet.get("ok") is not True
    assert log.read_bytes() == original_bytes
    if mutation in {"release", "reacquire"}:
        assert context(runtime, registry)["graph"]["work_yardsticks"][0]["source_finding_id"] == original_source


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_same_actor_soft_claim_reacquisition_does_not_reuse_the_old_basis(
    tmp_path, monkeypatch, capsys, provider,
):
    project, runtime, registry, command, _, _ = prepare(tmp_path, provider, "soft_claim")
    log = explore_result_log_path(runtime, GOAL_ID)
    original_bytes = log.read_bytes()
    append = writer._append_prepared_result

    def reclaim_before_commit(*args, **kwargs):
        run(project, runtime, registry, "todo", "update", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
            "--agent-id", AGENT_ID, "--clear-claim")
        run(project, runtime, registry, "todo", "claim", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
            "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID)
        return append(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.chdir(project)
        patch.setattr(writer, "_append_prepared_result", reclaim_before_commit)
        assert main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *command]) == 1
        packet = json.loads(capsys.readouterr().out)
    assert "authority changed" in packet["error"]
    assert log.read_bytes() == original_bytes


# The release is a separate Python client and Node runtime. It cannot borrow
# this process's held marker, and uses the real lifecycle owner rather than a
# fake callback that pretends a release succeeded inside the critical section.
RELEASE_CLIENT = """
import json, sys
from pathlib import Path
from loopx.control_plane.work_items.task_lease import inspect_task_lease, release_task_lease
from loopx.capabilities.explore.result_log import explore_result_log_path, load_explore_result_events
data = json.loads(sys.argv[1])
args = dict(registry_path=Path(data['registry']), runtime_root=Path(data['runtime']),
            goal_id=data['goal'], todo_id=data['todo'])
inspect_task_lease(**args)
Path(data['ready']).write_text('attempting real release')
result = release_task_lease(**args, owner=data['actor'], idempotency_key=data['key'],
                            expected_version=data['version'])
events = load_explore_result_events(explore_result_log_path(args['runtime_root'], data['goal']))
Path(data['result']).write_text(json.dumps(dict(result=result,
    findings=[row['result_id'] for row in events if row.get('event_kind') == 'finding'])))
"""


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_independent_release_waits_until_the_authorized_graph_commit(
    tmp_path, monkeypatch, capsys, provider,
):
    project, runtime, registry, command, lease_args, lease = prepare(tmp_path, provider)
    ready, result = tmp_path / "release-ready", tmp_path / "release-result.json"
    data = dict(registry=str(registry), runtime=str(runtime), goal=GOAL_ID, todo=TODO_ID,
                actor=AGENT_ID, key=lease_args["idempotency_key"], version=lease["version"],
                ready=str(ready), result=str(result))
    append = writer.append_explore_result_events
    child = None

    def append_while_release_waits(*args, **kwargs):
        nonlocal child
        child = subprocess.Popen([sys.executable, "-c", RELEASE_CLIENT, json.dumps(data)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 15
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.025)
        assert ready.exists(), child.communicate(timeout=5)
        # A warmed client is trying the real lifecycle write. The wait is only
        # a synchronization oracle, not a latency/performance assertion.
        try:
            output = child.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        else:
            pytest.fail(f"release crossed the graph commit fence: {output}")
        assert not result.exists()
        return append(*args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.chdir(project)
            patch.setattr(writer, "append_explore_result_events", append_while_release_waits)
            main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *command])
            packet = json.loads(capsys.readouterr().out)
        assert child is not None
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, (stdout, stderr)
        released = json.loads(result.read_text())
        assert released["result"]["ok"] and len(released["findings"]) == 2
        assert packet["primary_committed"] is True
        # Association may finish before or after release. The graph commit was
        # authorized in either ordering and remains a stable historical fact.
        assert packet["finding_id"] in released["findings"]
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.communicate(timeout=10)


LOG_HOLDER = """
import sys, time
from datetime import datetime, timezone
from pathlib import Path
from loopx.file_lock import exclusive_file_lock
with exclusive_file_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text('graph lock held')
    deadline = float(sys.argv[3])
    while datetime.now(timezone.utc).timestamp() <= deadline:
        time.sleep(0.025)
"""


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_expiry_during_graph_lock_wait_is_checked_at_commit_admission(
    tmp_path, monkeypatch, capsys, provider,
):
    project, runtime, registry, command, lease_args, lease = prepare(tmp_path, provider)
    renewed = renew_task_lease(**lease_args, expected_version=lease["version"], ttl_seconds=3)
    assert renewed["ok"]
    expiry = datetime.fromisoformat(renewed["lease"]["expires_at"].replace("Z", "+00:00")).timestamp()
    log = explore_result_log_path(runtime, GOAL_ID)
    original_bytes = log.read_bytes()
    ready = tmp_path / "log-lock-ready"
    append = writer._append_prepared_result
    child = None

    def block_graph_until_after_expiry(*args, **kwargs):
        nonlocal child
        assert datetime.now(timezone.utc).timestamp() < expiry, "fixture must reach append with a live lease"
        child = subprocess.Popen([sys.executable, "-c", LOG_HOLDER, str(log), str(ready), str(expiry + 0.1)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.025)
        assert ready.exists(), child.communicate(timeout=5)
        return append(*args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.chdir(project)
            patch.setattr(writer, "_append_prepared_result", block_graph_until_after_expiry)
            assert main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *command]) == 1
            packet = json.loads(capsys.readouterr().out)
        assert child is not None
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
        assert packet.get("ok") is not True and log.read_bytes() == original_bytes
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.communicate(timeout=10)
