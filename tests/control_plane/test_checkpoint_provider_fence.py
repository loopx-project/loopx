"""Real File/SQLite and public writers, not a shadow-maintenance lock probe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals import checkpoint_context_io as context_io
from tests.control_plane.test_checkpoint_read_context import _missing
from tests.control_plane.test_quota_settlement_cli import GOAL_ID, AGENT_ID, TODO_ID, TURN_ID, _run_cli, _spend_run_count
from tests.control_plane.checkpoint_process import REPO, start_probe, wait_for, refresh


def fixture(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, binding, delivery, original = _missing(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    todos = [{"schema_version": "todo_item_v0", "todo_id": name, "index": index,
        "role": "agent", "status": "open", "done": False, "text": f"Synthetic page work {name}",
        "task_class": "advancement_task", "archive_state": "active", "source_section": "Agent Todo",
        "claimed_by": AGENT_ID} for index, name in enumerate((TODO_ID, "todo_dependency", "todo_unrelated"), 1)]
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="hard_lease", leases=[])
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    for todo in todos:
        rc, lease = _run_cli(registry, runtime, "task-lease", "acquire", "--goal-id", GOAL_ID,
            "--todo-id", todo["todo_id"], "--owner", AGENT_ID,
            "--idempotency-key", f"checkpoint-{todo['todo_id']}", "--expected-version", "0",
            "--ttl-seconds", "600", "--write-scope", f"src/{todo['todo_id']}/**", cwd=project)
        assert rc == 0, lease
    def read():
        return context_io.read_checkpoint_context(registry_path=registry, runtime_root_override=str(runtime),
            goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID,
            dependency_todo_ids=["todo_dependency"])
    return project, runtime, registry, state, read, original


def public_writer(registry, runtime, barrier, provider, todo_id=TODO_ID, *, provider_direct=False):
    return subprocess.Popen([sys.executable, "-m", "tests.control_plane.checkpoint_process", json.dumps({
        "mode": "writer", "registry": str(registry), "runtime": str(runtime), "barrier": str(barrier),
        "provider": provider, "todo_id": todo_id, "provider_direct": provider_direct})], cwd=REPO, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", env={**os.environ, "PYTHONPATH": str(REPO)})


def finish(child):
    stdout, stderr = child.communicate(timeout=30)
    assert child.returncode == 0, stdout + stderr
    result = json.loads(stdout)
    assert result["ok"], result
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_update_before_final_read_rejects_then_reread_succeeds(tmp_path, monkeypatch, provider):
    _, runtime, registry, _, read, original = fixture(tmp_path, monkeypatch, provider)
    context = read()
    index = runtime / f"goals/{GOAL_ID}/runs/index.jsonl"
    before = index.read_bytes()
    barrier = tmp_path / "writer"
    barrier.mkdir()
    finish(public_writer(registry, runtime, barrier, provider, "todo_dependency"))
    current = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    assert next(todo for todo in current["todos"] if todo["todo_id"] == "todo_dependency")["note"] == "peer-result-v2"
    with pytest.raises(context_io.CheckpointReadContextRejected) as rejected:
        refresh(registry, runtime, context["read_context_id"])
    assert rejected.value.code == "checkpoint_read_context_stale"
    assert "dependencies" in rejected.value.payload["checkpoint_read_context"]["changed_components"]
    assert index.read_bytes() == before
    fresh = read()
    assert fresh["provider_revision"] != context["provider_revision"]
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    finish(public_writer(registry, runtime, unrelated, provider, "todo_unrelated"))
    result = refresh(registry, runtime, fresh["read_context_id"])
    assert result["vision_checkpoint"]["satisfied"] and result["appended"]
    after = index.read_bytes()
    replay = refresh(registry, runtime, fresh["read_context_id"])
    assert replay["idempotent_replay"] and index.read_bytes() == after
    assert result["settlement_identity"] == original["settlement_identity"]
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_existing_cli_maintenance_guard_precedes_provider_commit(tmp_path, monkeypatch, provider):
    """Characterize the reviewed head accurately: normal CLI already holds M."""
    _, runtime, registry, state, _, _ = fixture(tmp_path, monkeypatch, provider)
    barrier = tmp_path / "cli-guard"
    barrier.mkdir()
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)["provider_revision"]
    with context_io._source_guard(runtime, GOAL_ID, state):
        writer = public_writer(registry, runtime, barrier, provider)
        # The canonical writer now waits up to 30s for a real retained File
        # history commit. Give the subprocess enough time to return its typed
        # lock error rather than timing out the test harness first.
        stdout, stderr = writer.communicate(timeout=60)
        assert writer.returncode == 1, stdout + stderr
        assert "lock timed out" in json.loads(stdout)["error"]
        assert not (barrier / "writer-entered").exists()
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)["provider_revision"] == before
    assert finish(public_writer(registry, runtime, barrier, provider))["ok"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_provider_transaction_cannot_commit_between_final_head_and_checkpoint(tmp_path, monkeypatch, provider):
    _, runtime, registry, _, read, _ = fixture(tmp_path, monkeypatch, provider)
    context = read()
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    original = context_io.effect_runtime_result
    observed = []

    def native(method, params):
        if method != "goal.checkpoint_read_context.commit":
            return original(method, params)
        checkpoint = start_probe({"mode": "checkpoint", "provider": provider, "barrier": str(barrier),
                                  "params": params, "repeat": True})
        writer = None
        try:
            wait_for(barrier / "head-read", checkpoint)
            writer = public_writer(registry, runtime, barrier, provider, provider_direct=True)
            wait_for(barrier / "writer-entered", writer)
            # Observe canonical storage independently while the writer is inside
            # its actual commit method. A blocked projection/CLI is not proof.
            time.sleep(0.1)
            current = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
            held = json.loads((barrier / "head-read").read_text())
            assert current["provider_revision"] == held["provider_revision"]
            assert not (barrier / "provider-result").exists()
            observed.append(current["provider_revision"])
            (barrier / "release").touch()
            stdout, stderr = checkpoint.communicate(timeout=20)
            assert checkpoint.returncode == 0, stdout + stderr
            saved = json.loads(stdout)
            assert saved["ok"] and not saved["replayed"]
            assert json.loads((barrier / "runtime-replay").read_text())["replayed"]
            finish(writer)
            assert json.loads((barrier / "provider-result").read_text())["status"] == "applied"
            return saved
        finally:
            (barrier / "release").touch()
            for child in (checkpoint, writer):
                if child is not None and child.poll() is None:
                    child.kill()
                    child.communicate()

    monkeypatch.setattr(context_io, "effect_runtime_result", native)
    result = refresh(registry, runtime, context["read_context_id"])
    assert result["vision_checkpoint"]["satisfied"] and len(observed) == 1
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)["provider_revision"] != observed[0]
    rows = [json.loads(line) for line in (runtime / f"goals/{GOAL_ID}/runs/index.jsonl").read_text().splitlines()]
    assert sum(row.get("vision_checkpoint", {}).get("read_context", {}).get("read_context_id") == context["read_context_id"] for row in rows) == 1
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_provider_commit_after_python_check_is_revalidated_at_save(tmp_path, monkeypatch, provider):
    """Provider contract: a transaction without the CLI's outer M protection.

    The reviewed implementation accepts this old checkpoint. The native save
    must observe the committed update and reject. Ordinary CLI writers already
    hold M before committing; this test does not pretend to reproduce that path.
    """
    from loopx import state_refresh
    _, runtime, registry, _, read, _ = fixture(tmp_path, monkeypatch, provider)
    token = read()["read_context_id"]
    index = runtime / f"goals/{GOAL_ID}/runs/index.jsonl"
    before = index.read_bytes()
    barrier = tmp_path / "race"
    barrier.mkdir()
    reserve = state_refresh.reserve_unique_run_paths
    writers = []

    def race(*args):
        writer = public_writer(registry, runtime, barrier, provider, provider_direct=True)
        writers.append(writer)
        wait_for(barrier / "provider-result", writer)
        assert json.loads((barrier / "provider-result").read_text())["status"] == "applied"
        current = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert next(todo for todo in current["todos"] if todo["todo_id"] == TODO_ID)["note"] == "peer-result-v2"
        return reserve(*args)

    monkeypatch.setattr(state_refresh, "reserve_unique_run_paths", race)
    try:
        with pytest.raises(context_io.CheckpointReadContextRejected) as rejected:
            refresh(registry, runtime, token)
        assert rejected.value.code == "checkpoint_read_context_stale"
        assert index.read_bytes() == before
    finally:
        for writer in writers:
            finish(writer)
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["throw", "kill"])
def test_failed_save_releases_provider_and_requires_fresh_comparison(tmp_path, monkeypatch, provider, fault):
    _, runtime, registry, _, read, _ = fixture(tmp_path, monkeypatch, provider)
    token = read()["read_context_id"]
    barrier = tmp_path / "fault"
    barrier.mkdir()
    original = context_io.effect_runtime_result

    def native(method, params):
        if method != "goal.checkpoint_read_context.commit":
            return original(method, params)
        child = start_probe({"mode": "checkpoint", "provider": provider, "barrier": str(barrier),
                             "params": params, "fault": fault})
        try:
            wait_for(barrier / "head-read", child)
            if fault == "kill":
                child.kill()
            else:
                (barrier / "release").touch()
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode != 0, stdout + stderr
            raise RuntimeError("synthetic lost checkpoint response")
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

    monkeypatch.setattr(context_io, "effect_runtime_result", native)
    index = runtime / f"goals/{GOAL_ID}/runs/index.jsonl"
    before = index.read_bytes()
    with pytest.raises(RuntimeError, match="synthetic lost"):
        refresh(registry, runtime, token)
    assert index.read_bytes() == before
    monkeypatch.setattr(context_io, "effect_runtime_result", original)
    # Real public writes prove both SQLite rollback/close and File stale-owner
    # reclaim. Reserved JSON files from the failed attempt cannot authorize save.
    finish(public_writer(registry, runtime, barrier, provider))
    with pytest.raises(context_io.CheckpointReadContextRejected) as rejected:
        refresh(registry, runtime, token)
    assert rejected.value.code == "checkpoint_read_context_stale"
    assert refresh(registry, runtime, read()["read_context_id"])["appended"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_exit_keeps_native_claims_until_save(tmp_path, monkeypatch, provider):
    _, runtime, registry, state, read, _ = fixture(tmp_path, monkeypatch, provider)
    token = read()["read_context_id"]
    barrier = tmp_path / "caller-exit"
    barrier.mkdir()
    owner = subprocess.Popen([sys.executable, "-m", "tests.control_plane.checkpoint_process", json.dumps({
        "mode": "caller-exit", "registry": str(registry), "runtime": str(runtime), "barrier": str(barrier),
        "provider": provider, "token": token})], cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(REPO)})
    contenders = []
    try:
        wait_for(barrier / "orphan-pid", owner)
        assert owner.wait(timeout=10) == 0
        from loopx.file_lock import exclusive_mutation_file_lock, LockAcquireTimeoutError
        index = runtime / f"goals/{GOAL_ID}/runs/index.jsonl"
        for path in (index, state):
            with pytest.raises(LockAcquireTimeoutError):
                with exclusive_mutation_file_lock(path, timeout_seconds=0):
                    pytest.fail("dead caller's native claim was stolen")
        for mode in ("reader", "append"):
            contenders.append(subprocess.Popen([sys.executable, "-m", "tests.control_plane.checkpoint_process", json.dumps({
                "mode": mode, "registry": str(registry), "runtime": str(runtime), "barrier": str(barrier)})],
                cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                env={**os.environ, "PYTHONPATH": str(REPO)}))
        time.sleep(0.2)
        assert all(child.poll() is None for child in contenders)
        (barrier / "release").touch()
        wait_for(barrier / "checkpoint-saved")
        deadline = time.monotonic() + 15
        while not (barrier / "orphan-result").read_text() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert json.loads((barrier / "orphan-result").read_text())["ok"]
        assert refresh(registry, runtime, token)["idempotent_replay"]
        stdout, stderr = contenders[0].communicate(timeout=20)
        assert contenders[0].returncode == 0, stdout + stderr
        assert json.loads(stdout)["error_code"] == "checkpoint_context_not_missing"
        finish(contenders[1])
        assert "synthetic_observation" in index.read_text()
        assert _spend_run_count(runtime) == 0
    finally:
        (barrier / "release").touch()
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        for child in contenders:
            if child.poll() is None:
                child.kill()
                child.communicate()


@pytest.mark.parametrize("damage", ["tail", "artifact", "conflict"])
def test_uncertain_committed_checkpoint_is_not_silently_replayed(tmp_path, damage):
    _, runtime, registry, _, _, _ = _missing(tmp_path)
    context = context_io.read_checkpoint_context(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
    result = refresh(registry, runtime, context["read_context_id"])
    index = Path(result["index_path"])
    if damage == "tail":
        index.write_bytes(index.read_bytes().rstrip(b"\r\n"))
    elif damage == "artifact":
        record = json.loads(Path(result["json_path"]).read_text())
        record["vision_checkpoint"]["satisfied"] = False
        Path(result["json_path"]).write_text(json.dumps(record))
    else:
        row = json.loads(index.read_text().splitlines()[-1])
        row["vision_checkpoint"]["read_context"]["read_context_id"] = "conflicting-token"
        with index.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    before = index.read_bytes()
    with pytest.raises(Exception) as error:
        refresh(registry, runtime, context["read_context_id"])
    assert getattr(error.value, "code", None) == "checkpoint_commit_unknown"
    assert index.read_bytes() == before
