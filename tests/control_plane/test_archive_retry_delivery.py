"""Archive recovery through the public CLI and a real canonical file store."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos import provider_projection, provider_terminal_lifecycle
from loopx.control_plane.todos.active_state_editing import TODO_SECTION_HEADINGS
from loopx.todos import archive_completed_todos, complete_goal_todo, update_goal_todo


REPOSITORY = Path(__file__).resolve().parents[2]
CRASH_BEFORE_PROJECTION = """
import json, os, sys
from pathlib import Path
from loopx.control_plane.todos import provider_terminal_lifecycle
from loopx.cli import main
evidence = Path(sys.argv.pop(1))
def lose_response(payload, **kwargs):
    evidence.write_text(json.dumps(payload), encoding='utf-8')
    os._exit(73)
provider_terminal_lifecycle.settle_canonical_todo_projection = lose_response
raise SystemExit(main(sys.argv[1:]))
"""


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n## Completed Work Archive\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "archive-goal",
                        "repo": str(project),
                        "state_file": state.name,
                        "coordination": {"registered_agents": ["agent-a"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="archive-goal",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "done": True,
                "text": "A completed delivery awaiting archival",
                "todo_id": "todo_completed",
                "role": "agent",
                "status": "done",
                "archive_state": "active",
                "source_section": TODO_SECTION_HEADINGS["agent"],
                "task_class": "advancement_task",
                "claimed_by": "agent-a",
            },
            {
                "schema_version": "todo_item_v0",
                "done": False,
                "text": "The next delivery",
                "todo_id": "todo_next",
                "role": "agent",
                "status": "open",
                "archive_state": "active",
                "source_section": TODO_SECTION_HEADINGS["agent"],
                "task_class": "advancement_task",
                "claimed_by": "agent-a",
            },
        ],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime, "archive-goal", projection, state_path=state
    )
    return registry, runtime, state


def _archive_cli(
    registry: Path,
    *,
    crash_evidence: Path | None = None,
    execute: bool = True,
    maximum: int = 0,
) -> subprocess.CompletedProcess[str]:
    launcher = (
        [sys.executable, "-c", CRASH_BEFORE_PROJECTION, str(crash_evidence)]
        if crash_evidence
        else [sys.executable, "-m", "loopx.cli"]
    )
    return subprocess.run(
        [
            *launcher,
            "--format",
            "json",
            "--registry",
            str(registry),
            "todo",
            "archive-completed",
            "--goal-id",
            "archive-goal",
            "--role",
            "agent",
            "--max-active-done",
            str(maximum),
            *(["--execute"] if execute else []),
        ],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )


@pytest.mark.parametrize("missing_display", [False, True], ids=["existing", "missing"])
def test_archive_cli_recovers_committed_result_after_process_exit(
    tmp_path: Path, missing_display: bool,
) -> None:
    registry, runtime, state = _fixture(tmp_path)
    original_markdown = state.read_bytes()
    evidence = tmp_path / "committed-result.json"
    lost = _archive_cli(registry, crash_evidence=evidence)
    assert lost.returncode == 73, lost.stderr
    committed = json.loads(evidence.read_text(encoding="utf-8"))
    assert committed["status"] == "applied"
    assert committed["moved_count"] == 1
    assert committed["moved_todo_ids"] == ["todo_completed"]
    assert state.read_bytes() == original_markdown
    if missing_display:
        state.unlink()

    retry = _archive_cli(registry)
    assert retry.returncode == 0, retry.stderr or retry.stdout
    recovered = json.loads(retry.stdout)
    assert recovered["status"] == "replayed"
    assert recovered["moved_count"] == 1
    assert recovered["moved_todo_ids"] == committed["moved_todo_ids"]
    assert recovered["original_receipt"] == committed["original_receipt"]
    assert recovered["provider_revision"] == committed["provider_revision"]
    assert recovered["changed"] is False
    assert recovered["projection_delivery"] in {"delivered", "current"}
    assert state.exists()
    assert recovered["archive_delivery_ack"]["status"] == "acknowledged"
    if missing_display:
        assert recovered["projection_outbox"]["recovery_scope"] == "todo_sections_only"
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id="archive-goal"
    )
    assert canonical is not None
    assert canonical["provider_revision"] == committed["provider_revision"]
    assert canonical["todos"][0]["archive_state"] == "archive"

    # A delivered operation must not become the permanent identity for all
    # future archive calls with the same role/retention arguments.
    fresh = _archive_cli(registry)
    assert fresh.returncode == 0, fresh.stderr
    no_change = json.loads(fresh.stdout)
    assert no_change["status"] == "no_change"
    assert no_change["moved_count"] == 0
    assert no_change["provider_revision"] == committed["provider_revision"]

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id="archive-goal",
        todo_id="todo_next",
        agent_id="agent-a",
        next_agent_todo="Continue after the next delivery",
        next_claimed_by="agent-a",
    )
    assert completed["completed"] is True
    next_batch = _archive_cli(registry)
    assert next_batch.returncode == 0, next_batch.stderr or next_batch.stdout
    next_result = json.loads(next_batch.stdout)
    assert next_result["status"] == "applied"
    assert next_result["moved_todo_ids"] == ["todo_next"]
    assert (
        next_result["original_receipt"]["operation_id"]
        != committed["original_receipt"]["operation_id"]
    )


@pytest.mark.parametrize("missing_display", [False, True], ids=["existing", "missing"])
def test_pending_projection_and_preview_preserve_archive_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_display: bool,
) -> None:
    registry, runtime, state = _fixture(tmp_path)
    original_markdown = state.read_text(encoding="utf-8")
    if missing_display:
        state.unlink()

    def reject_projection(*_args, **_kwargs):
        raise OSError("injected projection write failure")

    with monkeypatch.context() as patch:
        patch.setattr(provider_projection, "atomic_write_state_text", reject_projection)
        committed = archive_completed_todos(
            registry_path=registry,
            goal_id="archive-goal",
            max_active_done=0,
            dry_run=False,
        )
    assert committed["status"] == "applied"
    assert committed["projection_delivery"] == "pending"

    preview = _archive_cli(registry, execute=False)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["moved_count"] == 0
    if missing_display:
        assert not state.exists()
    else:
        assert state.read_text(encoding="utf-8") == original_markdown
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id="archive-goal"
    )
    assert canonical is not None
    assert canonical["provider_revision"] == committed["provider_revision"]

    different = _archive_cli(registry, maximum=1)
    assert different.returncode != 0
    retry = _archive_cli(registry)
    assert retry.returncode == 0, retry.stderr or retry.stdout
    replay = json.loads(retry.stdout)
    assert replay["status"] == "replayed"
    assert replay["original_receipt"] == committed["original_receipt"]
    assert replay["projection_delivery"] in {"delivered", "current"}
    assert replay["archive_delivery_ack"]["status"] == "acknowledged"


def test_archive_rejects_snapshot_drift_before_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, runtime, _ = _fixture(tmp_path)
    read = provider_terminal_lifecycle.read_canonical_todos_if_promoted

    def read_then_edit(**kwargs):
        observed = read(**kwargs)
        update_goal_todo(
            registry_path=registry,
            goal_id="archive-goal",
            todo_id="todo_next",
            agent_id="agent-a",
            note="An independently committed correction",
        )
        return observed

    monkeypatch.setattr(
        provider_terminal_lifecycle, "read_canonical_todos_if_promoted", read_then_edit
    )
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as rejected:
        archive_completed_todos(
            registry_path=registry,
            goal_id="archive-goal",
            max_active_done=0,
            dry_run=False,
        )
    assert rejected.value.payload["status"] == "conflict"
    canonical = read(runtime_root=runtime, goal_id="archive-goal")
    assert canonical is not None
    assert all(todo["archive_state"] == "active" for todo in canonical["todos"])
    monkeypatch.setattr(
        provider_terminal_lifecycle, "read_canonical_todos_if_promoted", read
    )
    retry = _archive_cli(registry)
    assert retry.returncode == 0, retry.stderr or retry.stdout
    assert json.loads(retry.stdout)["moved_todo_ids"] == ["todo_completed"]


def test_archive_ack_transport_failure_preserves_committed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, runtime, _ = _fixture(tmp_path)
    execute = provider_terminal_lifecycle.effect_runtime_result

    def unavailable_ack(method, params, **kwargs):
        if method == "coordination.local_authority.todo_archive_ack":
            raise OSError("injected acknowledgement transport failure")
        return execute(method, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(
            provider_terminal_lifecycle, "effect_runtime_result", unavailable_ack
        )
        committed = archive_completed_todos(
            registry_path=registry,
            goal_id="archive-goal",
            max_active_done=0,
            dry_run=False,
        )
    assert committed["status"] == "applied"
    assert committed["moved_todo_ids"] == ["todo_completed"]
    assert committed["projection_delivery"] in {"delivered", "current"}
    assert committed["archive_delivery_ack"]["status"] == "pending"
    assert committed["archive_delivery_ack"]["retryable"] is True
    retry = _archive_cli(registry)
    assert retry.returncode == 0, retry.stderr or retry.stdout
    recovered = json.loads(retry.stdout)
    assert recovered["status"] == "replayed"
    assert recovered["original_receipt"] == committed["original_receipt"]
    assert recovered["archive_delivery_ack"]["status"] == "acknowledged"
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id="archive-goal"
    )
    assert canonical is not None
    assert canonical["provider_revision"] == committed["provider_revision"]


def test_unpromoted_archive_does_not_rebuild_missing_display(tmp_path: Path) -> None:
    from loopx.control_plane.coordination.legacy_writer_fence import (
        legacy_coordination_writer_fence_path,
    )

    registry, runtime, state = _fixture(tmp_path)
    state.unlink()
    legacy_coordination_writer_fence_path(
        runtime_root=runtime, goal_id="archive-goal"
    ).unlink()
    authority = runtime / "authority" / "file-v0"
    before = {path: path.read_bytes() for path in authority.rglob("*") if path.is_file()}
    result = _archive_cli(registry)
    assert result.returncode == 1
    assert "active state file does not exist" in json.loads(result.stdout)["error"]
    assert not state.exists()
    assert {path: path.read_bytes() for path in authority.rglob("*") if path.is_file()} == before
