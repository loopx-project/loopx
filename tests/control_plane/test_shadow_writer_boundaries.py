from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.control_plane.coordination.legacy_writer_fence import (
    LegacyCoordinationWriterFenced,
    legacy_coordination_writer_fence_path,
    legacy_todo_write_transaction,
)
from loopx.control_plane.todos.handoff_mode import set_goal_handoff_mode
from loopx.todos import add_goal_todo


GOAL = "writer-boundary"
REPO = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.stage2c_e2e


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\ngoal_id: writer-boundary\nhandoff_mode: legacy\n"
        "updated_at: 2026-09-05T00:00:00Z\n---\n\n"
        "## Agent Todo\n\n## Progress Ledger\n\n## Next Action\n\n- Review.\n",
        encoding="utf-8",
    )
    root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(root), "goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"agent_model": "peer_v1", "registered_agents": ["agent-a"]},
    }]}), encoding="utf-8")
    return registry, state, root


def test_handoff_writer_refuses_a_fence_before_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state, root = fixture(tmp_path)
    fence = legacy_coordination_writer_fence_path(runtime_root=root, goal_id=GOAL)
    fence.parent.mkdir(parents=True)
    fence.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "loopx.control_plane.coordination.legacy_writer_fence.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "status": "blocked", "reason_code": "legacy_coordination_writer_fenced",
        },
    )
    before = state.read_bytes()
    with pytest.raises(LegacyCoordinationWriterFenced):
        set_goal_handoff_mode(registry_path=registry, goal_id=GOAL, mode="soft_claim")
    assert state.read_bytes() == before
    assert not (root / "authority-shadow").exists()


def test_handoff_mode_rechecks_legacy_fence_when_canonical_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider outage must not bypass an already-present legacy fence."""

    registry, state, root = fixture(tmp_path)
    fence = legacy_coordination_writer_fence_path(runtime_root=root, goal_id=GOAL)
    fence.parent.mkdir(parents=True)
    fence.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_handoff_mode.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "status": "unavailable",
            "reason_code": "canonical_provider_unavailable",
            "reason": "canonical provider is unavailable",
        },
    )
    monkeypatch.setattr(
        "loopx.control_plane.coordination.legacy_writer_fence.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "status": "blocked",
            "reason_code": "legacy_coordination_writer_fenced",
        },
    )
    before = state.read_bytes()
    with pytest.raises(LegacyCoordinationWriterFenced) as error:
        set_goal_handoff_mode(registry_path=registry, goal_id=GOAL, mode="soft_claim")
    assert error.value.code == "legacy_coordination_writer_fenced"
    assert error.value.payload["write_check"]["reason_code"] == "legacy_coordination_writer_fenced"
    assert state.read_bytes() == before
    assert not (root / "authority-shadow").exists()


def test_corrupt_management_state_blocks_before_transaction_body(tmp_path: Path) -> None:
    registry, state, root = fixture(tmp_path)
    digest = hashlib.sha256(GOAL.encode()).hexdigest()[:16]
    management = root / "authority-transition" / "file-v0" / f"shadow-management-{digest}" / "state.json"
    management.parent.mkdir(parents=True)
    management.write_text("{corrupt", encoding="utf-8")
    reached = False
    try:
        with legacy_todo_write_transaction(registry, GOAL, state, None, "test", False):
            reached = True
    except RuntimeError as error:
        assert getattr(error, "reason_code", "")
    assert not reached, "maintenance errors must not fall through to a primary write"


def test_atomic_state_writer_keeps_original_on_failed_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.todos import active_state_editing

    write = getattr(active_state_editing, "atomic_write_state_text", None)
    assert callable(write), "all state writers need the shared durable text primitive"
    state = tmp_path / "state.md"
    state.write_bytes(b"original\r\n")
    state.chmod(0o640)
    def fail_replace(*_args: object) -> None:
        raise OSError("replacement unavailable")
    monkeypatch.setattr(active_state_editing.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement unavailable"):
        write(state, "replacement\r\n")
    assert state.read_bytes() == b"original\r\n"
    assert state.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.iterdir()) == [state]


def reward_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    registry, state, root = fixture(tmp_path)
    index = root / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({
        "generated_at": "2026-09-05T00:00:00Z", "json_path": "run.json",
        "markdown_path": "run.md", "classification": "continue",
    }) + "\n", encoding="utf-8")
    reward = {"recorded_at": "2026-09-05T00:00:01Z", "decision": "continue",
              "reward": "positive", "reason_summary": "Review accepted."}
    return registry, state, index, reward


def test_reward_summary_cannot_inject_a_canonical_todo(tmp_path: Path) -> None:
    from loopx.feedback import append_human_reward
    from loopx.control_plane.coordination.runtime_shadow_writer_adapter import ActiveStateAuthorityMutationError

    registry, state, index, reward = reward_fixture(tmp_path)
    # Insert before the first recognized Todo source, rather than after it.
    state.write_text("---\nhandoff_mode: legacy\n---\n\n## Progress Ledger\n\n## Agent Todo\n", encoding="utf-8")
    reward["reason_summary"] = "Review.\n## Agent Todo\n- [ ] Injected canonical task."
    before = state.read_bytes(), index.read_bytes()
    with pytest.raises(ActiveStateAuthorityMutationError):
        append_human_reward(registry_path=registry, runtime_root_override=None,
            goal_id=GOAL, run_generated_at=None, reward=reward,
            actor_kind="owner",
            write_active_state_summary=True)
    assert (state.read_bytes(), index.read_bytes()) == before


def test_reward_rebases_its_owned_paragraph_after_a_concurrent_todo_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import feedback

    registry, state, _index, reward = reward_fixture(tmp_path)
    plan = feedback.plan_active_state_update
    def plan_then_edit(**kwargs: object):
        result = plan(**kwargs)
        state.write_text(state.read_text().replace("## Agent Todo\n", "## Agent Todo\n\n- [ ] Concurrent task.\n"))
        return result
    monkeypatch.setattr(feedback, "plan_active_state_update", plan_then_edit)
    feedback.append_human_reward(registry_path=registry, runtime_root_override=None,
        goal_id=GOAL, run_generated_at=None, reward=reward,
        actor_kind="owner",
        write_active_state_summary=True)
    assert "Concurrent task." in state.read_text()
    assert "Review accepted." in state.read_text()


@pytest.mark.parametrize("preserve", [False, True])
def test_force_bootstrap_cannot_erase_an_active_shadow_binding(tmp_path: Path, preserve: bool) -> None:
    from loopx.bootstrap import bootstrap_project

    registry, state, root = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0",
    }
    registry.write_text(json.dumps(value))
    before = registry.read_bytes(), state.read_bytes()
    with pytest.raises(RuntimeError, match="shadow"):
        bootstrap_project(project=tmp_path, registry_path=registry, runtime_root=root,
            goal_id=GOAL, objective="Rebuild safely.", domain="test", role="primary",
            parent_goal_id=None, state_file=state, goal_doc=None, adapter_kind="generic_project_goal_v0",
            adapter_status="connected", next_probe=None, spawn_allowed=False, max_children=0,
            allowed_domains=[], write_scope=[],
            force=True, preserve_todos=preserve, dry_run=False, sync_global=False)
    assert (registry.read_bytes(), state.read_bytes()) == before


def cli(registry: Path, *args: str) -> dict:
    result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry), "--format", "json", *args],
        cwd=REPO, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_real_cli_handoff_and_todo_add_have_one_receipt_each(tmp_path: Path) -> None:
    registry, state, root = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0",
    }
    registry.write_text(json.dumps(value))
    cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
    handoff = cli(registry, "handoff-mode", "set", "--goal-id", GOAL, "--mode", "soft_claim")
    added = cli(registry, "todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--text", "Inspect the read path.", "--task-class", "advancement_task",
        "--evidence", "review fixture")
    assert handoff["coordination_runtime_shadow"]["outcome"] == "delivered", handoff
    assert added["coordination_runtime_shadow"]["outcome"] == "delivered", added
    assert added["added"] is True
    digest = hashlib.sha256(GOAL.encode()).hexdigest()[:16]
    candidate = json.loads((root / "authority-shadow" / "file-v0" / f"authority-store-{digest}.json").read_text())
    assert candidate["cursor"] == "3", "bootstrap plus two primary writes must not get CLI mirror receipts"
    assert len(candidate["committed"]) == 3
    assert "Inspect the read path." in state.read_text()


@pytest.mark.parametrize("phase", ["before", "after"])
def test_real_process_kill_around_state_replace_preserves_complete_bytes(tmp_path: Path, phase: str) -> None:
    state = tmp_path / "state.md"
    state.write_bytes(b"original\r\n")
    code = """
import sys
from pathlib import Path
from loopx.control_plane.todos import active_state_editing as editing
original = editing.os.replace
def replace(source, target):
    if sys.argv[2] == 'after': original(source, target)
    print('replace-cut', flush=True)
    sys.stdin.readline()
    if sys.argv[2] == 'before': original(source, target)
editing.os.replace = replace
editing.atomic_write_state_text(Path(sys.argv[1]), 'replacement\\r\\n')
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(state), phase], cwd=REPO,
        text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "replace-cut"
        child.kill()
        child.communicate(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    assert state.read_bytes() == (b"original\r\n" if phase == "before" else b"replacement\r\n")


def test_cli_waiting_for_todo_mutex_rechecks_fence_after_engagement(tmp_path: Path) -> None:
    from loopx.file_lock import exclusive_cross_runtime_file_lock
    from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_todo_lock_path

    registry, state, root = fixture(tmp_path)
    before = state.read_bytes()
    code = """
import sys
from contextlib import contextmanager
from loopx.control_plane.coordination import legacy_writer_fence as guard
original = guard.exclusive_cross_runtime_file_lock
@contextmanager
def traced(path, **kwargs):
    if path.name.startswith('legacy-todo-writer-'): print('waiting-for-todo-lock', flush=True)
    with original(path, **kwargs) as held: yield held
guard.exclusive_cross_runtime_file_lock = traced
from loopx.entrypoint import main
sys.argv = ['loopx', *sys.argv[1:]]
raise SystemExit(main())
"""
    target = legacy_coordination_todo_lock_path(runtime_root=root, goal_id=GOAL)
    lease_lock = root / "goals" / GOAL / "task-leases" / ".task-leases"
    request = {"schema_version": "loopx_legacy_coordination_writer_fence_engage_request_v0",
        "runtime_root": str(root), "goal_id": GOAL, "state_path": str(state),
        "fence": {"schema_version": "loopx_legacy_coordination_writer_fence_v0",
            "state": "engaged", "goal_id": GOAL, "fence_id": "race-fence",
            "source_version": "race-source", "source_projection_sha256": "a" * 64,
            "expected_shadow_provider_revision": "file:1:aaaaaaaaaaaaaaaaaaaaaaaa"}}
    engage_code = "from loopx.control_plane.effect_runtime import effect_runtime_result; import sys,json; print(json.dumps(effect_runtime_result('coordination.local_authority.legacy_writer_fence.engage', json.loads(sys.argv[1]))))"
    children = []
    try:
        # Real engagement owns T and waits behind K; the public writer then waits
        # behind engagement's T. No fixture writes a fence marker on its behalf.
        with exclusive_cross_runtime_file_lock(lease_lock):
            engage = subprocess.Popen([sys.executable, "-c", engage_code, json.dumps(request)],
                cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            children.append(engage)
            deadline = time.monotonic() + 10
            while not Path(str(target) + ".ts-effect.lock").exists():
                assert engage.poll() is None, engage.communicate(timeout=1)
                assert time.monotonic() < deadline, "engagement did not acquire the Todo lock"
                time.sleep(0.01)
            child = subprocess.Popen([sys.executable, "-c", code, "--registry", str(registry), "--format", "json",
                "todo", "add", "--goal-id", GOAL, "--role", "agent", "--text", "Must be fenced.",
                "--evidence", "race fixture"], cwd=REPO,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            children.append(child)
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "waiting-for-todo-lock"
            assert child.poll() is None
        engage_output, engage_error = engage.communicate(timeout=30)
        assert json.loads(engage_output)["status"] == "applied", engage_output + engage_error
        output, error = child.communicate(timeout=30)
        payload = json.loads(output)
        assert child.returncode == 1, output + error
        assert payload["error_code"] == "legacy_coordination_writer_fenced"
        assert state.read_bytes() == before
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)


@pytest.mark.parametrize("override_root", [False, True])
def test_real_writer_commits_before_a_later_fence_is_published(tmp_path: Path, override_root: bool) -> None:
    registry, state, root = fixture(tmp_path)
    writer_code = """
import sys
from loopx.control_plane.todos import active_state_editing
original = active_state_editing.atomic_write_state_text
def paused(*args):
    print('primary-write-cut', flush=True)
    sys.stdin.readline()
    return original(*args)
active_state_editing.atomic_write_state_text = paused
from loopx.entrypoint import main
raise SystemExit(main(sys.argv[1:]))
"""
    request = {"schema_version": "loopx_legacy_coordination_writer_fence_engage_request_v0",
        "runtime_root": str(root), "goal_id": GOAL, "state_path": str(state),
        "fence": {"schema_version": "loopx_legacy_coordination_writer_fence_v0",
            "state": "engaged", "goal_id": GOAL, "fence_id": "later-fence",
            "source_version": "source-after-write", "source_projection_sha256": "a" * 64,
            "expected_shadow_provider_revision": "file:1:aaaaaaaaaaaaaaaaaaaaaaaa"}}
    children = []
    try:
        writer = subprocess.Popen([sys.executable, "-c", writer_code, "--registry", str(registry),
            "--runtime-root", str(tmp_path / "override" if override_root else root),
            "--format", "json", "todo", "add", "--goal-id", GOAL, "--role", "agent",
            "--text", "Primary won the lock.", "--evidence", "ordering fixture"],
            cwd=REPO, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(writer)
        assert writer.stdout is not None
        assert writer.stdout.readline().strip() == "primary-write-cut"
        engage_code = "from loopx.control_plane.effect_runtime import effect_runtime_result; import sys,json; print(json.dumps(effect_runtime_result('coordination.local_authority.legacy_writer_fence.engage', json.loads(sys.argv[1]))))"
        engager = subprocess.Popen([sys.executable, "-c", engage_code, json.dumps(request)],
            cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(engager)
        from loopx.control_plane.coordination.shadow_management import shadow_maintenance_lock_target
        mutex = Path(str(shadow_maintenance_lock_target(root, GOAL)) + ".ts-effect.lock")
        deadline = time.monotonic() + 10
        while not mutex.exists():
            assert engager.poll() is None, engager.communicate(timeout=1)
            assert time.monotonic() < deadline
            time.sleep(0.01)
        time.sleep(.2)
        assert not legacy_coordination_writer_fence_path(runtime_root=root, goal_id=GOAL).exists()
        assert engager.poll() is None
        output, error = writer.communicate("continue\n", timeout=30)
        assert writer.returncode == 0, output + error
        assert json.loads(output)["added"] is True
        output, error = engager.communicate(timeout=30)
        assert json.loads(output)["status"] == "applied", output + error
        assert "Primary won the lock." in state.read_text()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)


def test_failed_primary_replace_never_marks_shadow_committed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from loopx.control_plane.todos import active_state_editing
    registry, state, root = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0"}
    registry.write_text(json.dumps(value))
    cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
    before = state.read_bytes()
    original = active_state_editing.os.replace
    def fail_primary(source: object, target: object) -> None:
        if Path(str(target)) == state:
            raise OSError("primary replace refused")
        original(source, target)
    monkeypatch.setattr(active_state_editing.os, "replace", fail_primary)
    with pytest.raises(OSError, match="primary replace refused"):
        add_goal_todo(
            registry_path=registry, goal_id=GOAL, role="agent",
            text="Must stay prepared.",
        )
    assert state.read_bytes() == before
    directory = root / "authority-shadow" / "outbox" / GOAL / "todos"
    assert len(list(directory.glob("*.prepared.json"))) == 1
    assert list(directory.glob("*.committed.json")) == []


def test_prose_only_reward_remains_allowed_under_a_legacy_fence(tmp_path: Path) -> None:
    from loopx.feedback import append_human_reward
    registry, state, _index, reward = reward_fixture(tmp_path)
    root = tmp_path / "runtime"
    fence = legacy_coordination_writer_fence_path(runtime_root=root, goal_id=GOAL)
    fence.parent.mkdir(parents=True)
    # Prose never reads this authority marker, even if invalid; its projection
    # comparison proves that no Todo/lease field is changed.
    fence.write_text("{invalid", encoding="utf-8")
    result = append_human_reward(registry_path=registry, runtime_root_override=None,
        goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
        write_active_state_summary=True)
    assert result["appended"] is True
    assert "Review accepted." in state.read_text()
    assert not (root / "authority-shadow").exists()


def test_prose_only_reward_holds_before_index_append_during_maintenance(tmp_path: Path) -> None:
    from loopx.feedback import append_human_reward
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    registry, state, index, reward = reward_fixture(tmp_path)
    path = shadow_management_state_path(tmp_path / "runtime", GOAL)
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    before = state.read_bytes(), index.read_bytes()
    with pytest.raises(ShadowManagementError):
        append_human_reward(registry_path=registry, runtime_root_override=None,
            goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
            write_active_state_summary=True)
    assert (state.read_bytes(), index.read_bytes()) == before


def test_override_root_is_the_only_maintenance_authority(tmp_path: Path) -> None:
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    registry, state, root = fixture(tmp_path)
    override = tmp_path / "override"
    management = shadow_management_state_path(override, GOAL)
    management.parent.mkdir(parents=True)
    management.write_text("{}")
    with pytest.raises(ShadowManagementError):
        add_goal_todo(
            registry_path=registry, goal_id=GOAL, runtime_root_arg=str(override),
            role="agent", text="Hold override.",
        )
    assert "Hold override." not in state.read_text()
    assert not (root / "authority-transition").exists()
    result = add_goal_todo(
        registry_path=registry, goal_id=GOAL, role="agent",
        text="Default root remains writable.",
    )
    assert result["added"] is True


@pytest.mark.parametrize("writer", ["todo", "prose"])
def test_override_root_cannot_bypass_registry_source_maintenance(tmp_path: Path, writer: str) -> None:
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    from loopx.state_refresh import refresh_state_run
    registry, state, root = fixture(tmp_path)
    override = tmp_path / "override"
    management = shadow_management_state_path(root, GOAL)
    management.parent.mkdir(parents=True)
    management.write_text("{}")
    before = state.read_bytes()
    with pytest.raises(ShadowManagementError):
        if writer == "todo":
            add_goal_todo(
                registry_path=registry, goal_id=GOAL, runtime_root_arg=str(override),
                role="agent", text="Cannot bypass source maintenance.",
            )
        else:
            refresh_state_run(registry_path=registry, runtime_root_override=str(override), goal_id=GOAL,
                project=None, state_file=None, classification="continue", recommended_action="Continue inspection.",
                next_action="Cannot bypass source maintenance.", dry_run=False, sync_global=False)
    assert state.read_bytes() == before


def test_override_root_keeps_prose_writable_with_an_active_source_binding(tmp_path: Path) -> None:
    from loopx.state_refresh import refresh_state_run
    registry, state, _root = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0"}
    registry.write_text(json.dumps(value))
    cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
    override = tmp_path / "override"
    refreshed = refresh_state_run(registry_path=registry, runtime_root_override=str(override), goal_id=GOAL,
        project=None, state_file=None, classification="continue", recommended_action="Continue inspection.",
        next_action="Only this owned prose changes.", dry_run=False, sync_global=False)
    assert refreshed["ok"] is True
    assert "Only this owned prose changes." in state.read_text()
    assert not (override / "authority-shadow" / "outbox" / GOAL).exists()


def test_waiting_override_writer_rechecks_registry_binding_inside_shared_state_lock(tmp_path: Path) -> None:
    registry, state, _root = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0"}
    registry.write_text(json.dumps(value))
    before = state.read_bytes()
    waiting, proceed = tmp_path / "waiting", tmp_path / "proceed"
    code = """
import sys,time
from contextlib import contextmanager
from pathlib import Path
from loopx.control_plane.coordination import legacy_writer_fence as guard
state,waiting,proceed = map(Path, sys.argv[1:4])
original = guard.exclusive_cross_runtime_file_lock
@contextmanager
def traced(path, **kwargs):
    if path == state:
        waiting.write_text('waiting for actual shared state lock')
        deadline = time.monotonic()+20
        while not proceed.exists():
            if time.monotonic()>deadline: raise TimeoutError('parent did not release writer')
            time.sleep(.01)
    with original(path, **kwargs) as held: yield held
guard.exclusive_cross_runtime_file_lock = traced
from loopx.entrypoint import main
sys.argv = ['loopx', *sys.argv[4:]]
raise SystemExit(main())
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(state), str(waiting), str(proceed),
        "--registry", str(registry), "--runtime-root", str(tmp_path / "override"), "--format", "json",
        "todo", "add", "--goal-id", GOAL, "--role", "agent", "--text", "Must observe the new source binding.",
        "--evidence", "cross-root race"], cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not waiting.exists():
            assert child.poll() is None, child.communicate(timeout=1)
            assert time.monotonic() < deadline, "writer did not reach shared state lock"
            time.sleep(.01)
        bootstrap = cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
        assert bootstrap["bootstrap"]["status"] == "applied"
        proceed.write_text("source root is now active")
        output, error = child.communicate(timeout=30)
        payload = json.loads(output)
        assert child.returncode == 1, output + error
        assert payload["error_code"] == "shadow_source_runtime_root_mismatch"
        assert state.read_bytes() == before
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_migration_refuses_overwriting_a_managed_target_before_copy(tmp_path: Path) -> None:
    from loopx.state_migration import migrate_legacy_state
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_registry, source_state, source_root = fixture(source)
    target_registry, target_state, target_root = fixture(target)
    target_state.write_text(target_state.read_text() + "\nProtected target history.\n")
    value = json.loads(target_registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {"enabled": True}
    target_registry.write_text(json.dumps(value))
    before = target_state.read_bytes(), target_registry.read_bytes(), source_state.read_bytes()
    with pytest.raises(RuntimeError, match="shadow source"):
        migrate_legacy_state(legacy_registry_path=source_registry, target_registry_path=target_registry,
            legacy_runtime_root=source_root, target_runtime_root=target_root, goal_ids=[GOAL],
            goal_id_map={}, path_map={str(source): str(target)}, copy_active_state=True,
            copy_runtime=True, execute=True)
    assert (target_state.read_bytes(), target_registry.read_bytes(), source_state.read_bytes()) == before


def test_registry_missing_state_reconstruction_respects_maintenance(tmp_path: Path) -> None:
    from loopx.control_plane.projects.registry import register_project_goal
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    registry = tmp_path / "registry.json"
    root = tmp_path / "runtime"
    args = dict(registry_path=registry, runtime_root=root, project_id="project-a", project_kind="work",
        knowledge_root=tmp_path, goal_id=GOAL, objective="Keep the source intact.", non_goals=[],
        acceptance=["State exists."], unknowns=[], next_effect="Inspect state.", stop_condition="Done.",
        repository_bindings=[], external_locator_bindings=[])
    created = register_project_goal(**args)
    state = Path(created["state_file"])
    state.unlink()
    before = registry.read_bytes()
    management = shadow_management_state_path(root, GOAL)
    management.parent.mkdir(parents=True, exist_ok=True)
    management.write_text("{}")
    with pytest.raises(ShadowManagementError):
        register_project_goal(**args)
    assert not state.exists()
    assert registry.read_bytes() == before


def test_refresh_owned_next_action_holds_before_state_change(tmp_path: Path) -> None:
    from loopx.state_refresh import refresh_state_run
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    registry, state, root = fixture(tmp_path)
    management = shadow_management_state_path(root, GOAL)
    management.parent.mkdir(parents=True)
    management.write_text("{}")
    before = state.read_bytes()
    with pytest.raises(ShadowManagementError):
        refresh_state_run(registry_path=registry, runtime_root_override=None, goal_id=GOAL,
            project=None, state_file=None, classification="continue", recommended_action="Continue inspection.",
            next_action="Read the next source.", dry_run=False, sync_global=False)
    assert state.read_bytes() == before
    assert not (root / "goals" / GOAL / "runs" / "index.jsonl").exists()


def test_active_capture_prepare_failure_holds_primary_before_any_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError
    registry, state, root = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0"}
    registry.write_text(json.dumps(value))
    cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
    before = state.read_bytes()
    original = outbox.durable_write_json
    def refuse_prepare(path: Path, record: object) -> None:
        if path.name.endswith(".prepared.json"):
            raise OSError("prepared durability unavailable")
        original(path, record)
    monkeypatch.setattr(outbox, "durable_write_json", refuse_prepare)
    for mode in ["soft_claim", "hard_lease"]:
        with pytest.raises(ShadowManagementError) as error:
            set_goal_handoff_mode(registry_path=registry, goal_id=GOAL, mode=mode)
        assert error.value.reason_code == "shadow_capture_prepare_failed"
        assert state.read_bytes() == before
    directory = root / "authority-shadow" / "outbox" / GOAL / "todos"
    assert list(directory.glob("*.committed.json")) == []


@pytest.mark.parametrize("operation", ["add", "update", "complete", "supersede", "archive"])
def test_all_todo_transaction_owners_enforce_active_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    from loopx import todos
    from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError
    registry, state, root = fixture(tmp_path)
    seed = todos.add_goal_todo(registry_path=registry, goal_id=GOAL, role="agent",
        text="Characterize this transaction owner.", status="open",
        task_class="advancement_task", action_kind="analyze", agent_id="agent-a")
    if operation == "archive":
        completed = todos.complete_goal_todo(registry_path=registry, goal_id=GOAL, todo_id=seed["todo_id"],
            evidence="Seed completion.", next_agent_todo="Next bounded seed.",
            next_task_class="advancement_task", agent_id="agent-a")
        assert completed["ok"] is True
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0"}
    registry.write_text(json.dumps(value))
    cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
    original = outbox.durable_write_json
    def fail_prepare(path: Path, record: object) -> None:
        if path.name.endswith(".prepared.json"):
            raise OSError("prepare is unavailable")
        original(path, record)
    monkeypatch.setattr(outbox, "durable_write_json", fail_prepare)
    before = state.read_bytes()
    identity = dict(registry_path=registry, goal_id=GOAL)
    with pytest.raises(ShadowManagementError) as held:
        if operation == "add":
            todos.add_goal_todo(**identity, role="agent", text="A new obligation.", task_class="advancement_task")
        elif operation == "update":
            todos.update_goal_todo(**identity, todo_id=seed["todo_id"], note="Changed note.", agent_id="agent-a")
        elif operation == "complete":
            todos.complete_goal_todo(**identity, todo_id=seed["todo_id"], evidence="Check completed.",
                next_agent_todo="Next bounded check.", next_task_class="advancement_task", agent_id="agent-a")
        elif operation == "supersede":
            todos.supersede_goal_todo(**identity, todo_id=seed["todo_id"], reason="Replace the approach.",
                next_agent_todo="Use a better check.", next_task_class="advancement_task", agent_id="agent-a")
        else:
            todos.archive_completed_todos(**identity, max_active_done=0, dry_run=False)
    assert held.value.reason_code == "shadow_capture_prepare_failed"
    assert state.read_bytes() == before


def test_concurrent_public_refresh_preserves_the_newer_owned_paragraph(tmp_path: Path) -> None:
    registry, state, _root = fixture(tmp_path)
    code = """
import sys
from contextlib import contextmanager
from pathlib import Path
from loopx import state_refresh
original = state_refresh.exclusive_cross_runtime_file_lock
observed = 0
@contextmanager
def paused(path, *args, **kwargs):
    global observed
    with original(path, *args, **kwargs) as held:
        yield held
    if Path(path).name == 'ACTIVE_GOAL_STATE.md':
        observed += 1
        if observed == 1:
            print('refresh-plan-ready', flush=True)
            sys.stdin.readline()
state_refresh.exclusive_cross_runtime_file_lock = paused
from loopx.entrypoint import main
raise SystemExit(main(sys.argv[1:]))
"""
    command = ["--registry", str(registry), "--format", "json", "refresh-state", "--goal-id", GOAL,
        "--classification", "continue", "--recommended-action", "Retain the selected next action.",
        "--next-action", "Stale planned paragraph.", "--no-global-sync"]
    child = subprocess.Popen([sys.executable, "-c", code, *command], cwd=REPO,
        text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "refresh-plan-ready"
        second = cli(registry, "--runtime-root", str(tmp_path / "parallel-runtime"),
            "refresh-state", "--goal-id", GOAL, "--classification", "continue",
            "--recommended-action", "Retain the selected next action.", "--next-action",
            "Newer committed paragraph.", "--no-global-sync")
        assert second["ok"] is True
        before = state.read_bytes()
        output, error = child.communicate("continue\n", timeout=30)
        assert child.returncode == 1, output + error
        assert "changed while refresh-state was qualifying" in json.loads(output)["error"]
        assert state.read_bytes() == before
        assert "Newer committed paragraph." in state.read_text()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
