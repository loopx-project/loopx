from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination.legacy_writer_fence import (
    ActiveStateAuthorityMutationError,
    legacy_coordination_writer_fence_path,
    require_prose_state_write_allowed,
)


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


def seed_todo(
    state: Path,
    text: str = "Retain canonical facts.",
    *,
    status: str = "open",
    resume_when: str | None = None,
) -> None:
    source = state.read_text(encoding="utf-8")
    resume = f" resume_when={resume_when}" if resume_when else ""
    state.write_text(
        source.replace(
            "## Agent Todo\n",
            "## Agent Todo\n\n"
            f"- [ ] {text}\n"
            f"  <!-- loopx:todo todo_id=todo_fixture status={status} "
            f"task_class=advancement_task{resume} -->\n",
            1,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("registry_directory", [".loopx", "standalone"])
@pytest.mark.parametrize("override", [None, "", "override", Path("path-override")])
def test_runtime_route_keeps_project_relative_precedence_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_directory: str, override,
) -> None:
    from loopx.paths import effective_runtime_root
    from loopx.control_plane.coordination.local_authority_shadow_adapter import effective_runtime_root as compatibility_route

    assert compatibility_route is effective_runtime_root
    project = tmp_path / "project"
    registry = project / registry_directory / "registry.json"
    registry.parent.mkdir(parents=True)
    original = json.dumps({"common_runtime_root": "declared"}).encode()
    registry.write_bytes(original)
    monkeypatch.chdir(tmp_path)
    owner = project if registry_directory == ".loopx" else registry.parent
    assert effective_runtime_root(registry, override) == owner / str(override or "declared")
    assert registry.read_bytes() == original
    assert list(registry.parent.iterdir()) == [registry]
    assert not (owner / "declared").exists()


def test_atomic_state_writer_keeps_original_on_failed_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.runtime import document_io

    write = getattr(document_io, "atomic_write_state_text", None)
    assert callable(write), "all state writers need the shared durable text primitive"
    state = tmp_path / "state.md"
    state.write_bytes(b"original\r\n")
    state.chmod(0o640)
    def fail_replace(*_args: object) -> None:
        raise OSError("replacement unavailable")
    monkeypatch.setattr(document_io.os, "replace", fail_replace)
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


def cli(registry: Path, *args: str) -> dict:
    result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry), "--format", "json", *args],
        cwd=REPO, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


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


def test_prose_guard_ignores_resume_evaluation_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state, root = fixture(tmp_path)
    seed_todo(
        state,
        "Resume the bounded inspection.",
        status="deferred",
        resume_when="resume_at:2099-01-01T00:00:00Z",
    )
    original = state.read_text(encoding="utf-8")
    planned = original + "\nA bounded narrative observation.\n"

    from loopx.control_plane.coordination.authority_source_capture import (
        todo_partition_projector,
    )
    from loopx.control_plane.coordination.local_authority_shadow_projection import (
        partition_comparison_view,
    )

    timestamps = ("2026-09-21T00:00:00Z", "2026-09-21T00:00:01Z")
    ticks = iter(timestamps * 2)

    def ticking_projector(*args, **kwargs):
        project = todo_partition_projector(*args, **kwargs)

        def project_at_distinct_time(text):
            projection = project(text)
            projection["todos"][0]["resume_condition"]["evaluated_at"] = next(ticks)
            return projection

        return project_at_distinct_time

    monkeypatch.setattr(
        "loopx.control_plane.coordination.authority_source_capture.todo_partition_projector",
        ticking_projector,
    )
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    projector = ticking_projector(goal, state_path=state)
    before, after = projector(original), projector(planned)
    assert before != after
    assert before["todos"][0]["resume_condition"]["evaluated_at"] == timestamps[0]
    assert after["todos"][0]["resume_condition"]["evaluated_at"] == timestamps[1]
    assert partition_comparison_view(before) == partition_comparison_view(after)
    # Prove the raw projections differ ONLY in the read-time clock.
    after["todos"][0]["resume_condition"]["evaluated_at"] = timestamps[0]
    assert before == after

    # The real guard gets the same forced one-second difference. Restoring
    # its old raw comparison must fail deterministically, without sleeping.
    require_prose_state_write_allowed(
        registry_path=registry,
        runtime_root=root,
        goal_id=GOAL,
        state_path=state,
        original_text=original,
        planned_text=planned,
    )



@pytest.mark.parametrize("original_value,planned_value", [(True, 1), (False, 0)])
def test_prose_guard_preserves_json_scalar_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    original_value: bool, planned_value: int,
) -> None:
    """A full source projection must retain JSON types even if Python equates them."""
    from loopx.control_plane.coordination import authority_source_capture as adapter
    from loopx.control_plane.coordination.local_authority_shadow_projection import (
        todo_partition_projection,
    )

    registry, state, root = fixture(tmp_path)
    seed_todo(state, "Retain result facts.")
    original = state.read_text(encoding="utf-8")
    planned = original + "\nA narrative observation.\n"
    real_projector = adapter.todo_partition_projector

    def source_projector(*args, **kwargs):
        project = real_projector(*args, **kwargs)

        def project_result(text):
            projection = project(text)
            # Exercise the complete-record adapter contract through the real TS
            # assembler. This injected result fact models an adapter output,
            # not an assertion that the current Markdown codec emits this shape.
            projection["todos"][0]["completion_result"] = {
                "facts": [{"value": original_value if text == original else planned_value}],
            }
            return todo_partition_projection(**projection)

        return project_result

    monkeypatch.setattr(adapter, "todo_partition_projector", source_projector)
    with pytest.raises(ActiveStateAuthorityMutationError, match="would change canonical"):
        require_prose_state_write_allowed(
            registry_path=registry, runtime_root=root, goal_id=GOAL,
            state_path=state, original_text=original, planned_text=planned,
        )
    assert state.read_text(encoding="utf-8") == original


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


def test_prose_guard_rejects_a_handoff_change(tmp_path: Path) -> None:
    registry, state, root = fixture(tmp_path)
    original = state.read_text()
    with pytest.raises(ActiveStateAuthorityMutationError, match="would change canonical"):
        require_prose_state_write_allowed(
            registry_path=registry, runtime_root=root, goal_id=GOAL, state_path=state,
            original_text=original, planned_text=original.replace("handoff_mode: legacy", "handoff_mode: hard_lease"),
        )
    assert state.read_text() == original


@pytest.mark.parametrize("maintenance", [False, True])
def test_prose_projection_failure_preserves_source_and_reward_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, maintenance: bool,
) -> None:
    from loopx.feedback import append_human_reward
    from loopx.control_plane.coordination import authority_source_capture as source
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path

    registry, state, index, reward = reward_fixture(tmp_path)
    called = []

    def unavailable(*_args, **_kwargs):
        called.append(True)
        raise ValueError("source projection unavailable")

    monkeypatch.setattr(source, "todo_partition_projector", unavailable)
    if maintenance:
        path = shadow_management_state_path(tmp_path / "runtime", GOAL)
        path.parent.mkdir(parents=True)
        path.write_text("{}")
    before = state.read_bytes(), index.read_bytes(), registry.read_bytes()
    error = ShadowManagementError if maintenance else ActiveStateAuthorityMutationError
    with pytest.raises(error):
        append_human_reward(registry_path=registry, runtime_root_override=None,
            goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
            write_active_state_summary=True)
    assert called == ([] if maintenance else [True])
    assert (state.read_bytes(), index.read_bytes(), registry.read_bytes()) == before


def test_configuration_and_prose_guard_load_without_capture_modules(tmp_path, monkeypatch):
    from test_cold_source_import_cli import workspace

    _, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    for name in ("runtime_shadow.py", "local_authority_shadow_adapter.py",
                 "local_authority_shadow_outbox.py"):
        (receiver / "loopx/control_plane/coordination" / name).unlink()
    registry = tmp_path / "project/.loopx/registry.json"
    child = subprocess.run([sys.executable, "-c", """
import sys,loopx
from pathlib import Path
import loopx.presentation.configuration_api
from loopx.feedback import require_prose_state_write_allowed
from loopx.control_plane.coordination.legacy_writer_fence import ActiveStateAuthorityMutationError
registry,runtime,state,package = map(Path,sys.argv[1:])
assert Path(loopx.__file__) == package / 'loopx/__init__.py'
original = state.read_text()
args = dict(registry_path=registry,runtime_root=runtime,goal_id='cold',state_path=state,original_text=original)
require_prose_state_write_allowed(**args,planned_text=original+'\\nA narrative observation.\\n')
try:
    require_prose_state_write_allowed(**args,planned_text=original.replace('handoff_mode: legacy','handoff_mode: hard_lease'))
except ActiveStateAuthorityMutationError:
    pass
else:
    raise AssertionError('handoff mutation was accepted')
assert state.read_text() == original
""", str(registry), str(runtime), str(state), str(receiver)], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stdout + child.stderr


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


def test_public_refresh_step_does_not_write_owned_paragraph(tmp_path: Path) -> None:
    registry, state, root = fixture(tmp_path)
    seed_todo(state, "Inspect the bounded result.")
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    import_fixture_authority(registry, GOAL)
    revision = cli(registry, "todo", "list", "--goal-id", GOAL)["authority_read"]["provider_revision"]
    cli(registry, "todo", "project-markdown", "--goal-id", GOAL,
        "--provider-revision", revision, "--execute")
    before = state.read_bytes()
    result = cli(registry, "refresh-state", "--goal-id", GOAL, "--agent-id", "agent-a",
        "--next-action", "Compare the current evidence.", "--no-global-sync")
    assert result["ok"] is True
    assert result["recommended_action_resolution"]["recommended_action_source"] == "agent_lane_step"
    assert state.read_bytes() == before
    assert "Compare the current evidence." not in state.read_text()
    assert not (root / "authority-shadow").exists()
