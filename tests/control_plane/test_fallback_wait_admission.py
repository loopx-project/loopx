"""Fallback wait admission must not depend on compact Todo lane capacity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.goals.goal_vision import normalize_goal_vision_packet
from loopx.control_plane.goals.goal_frontier import fallback_disposition, fallback_source
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.todo_summary import TODO_ITEM_SCHEMA_VERSION

GOAL_ID = "fallback-wait-capacity-fixture"
AGENT_ID = "worker"
PRIMARY_TODO_ID = "todo_source_a"
FALLBACK_TODO_ID = "todo_source_b"
PREREQUISITE_TODO_ID = "todo_recovery"


def _state_text(
    *,
    unrelated_deferred_count: int,
    fallback_present: bool = True,
    prerequisite_status: str = "open",
) -> str:
    unrelated = "".join(
        f"""
- [ ] [P1] Wait for unrelated prerequisite {index}.
  <!-- loopx:todo todo_id=todo_unrelated_{index} status=deferred task_class=advancement_task claimed_by={AGENT_ID} resume_when=todo_done:{PREREQUISITE_TODO_ID} -->
"""
        for index in range(unrelated_deferred_count)
    )
    fallback = (
        f"""
- [ ] [P1] Read authorized source B after recovery.
  <!-- loopx:todo todo_id={FALLBACK_TODO_ID} status=deferred task_class=advancement_task claimed_by={AGENT_ID} resume_when=todo_done:{PREREQUISITE_TODO_ID} -->
"""
        if fallback_present
        else ""
    )
    prerequisite_marker = "x" if prerequisite_status == "done" else " "
    return f"""# Active Goal State

## Agent Todo

- [{prerequisite_marker}] [P0] Observe recovery of source A.
  <!-- loopx:todo todo_id={PREREQUISITE_TODO_ID} status={prerequisite_status} task_class=advancement_task claimed_by=observer -->
- [ ] [P0] Read source A after recovery.
  <!-- loopx:todo todo_id={PRIMARY_TODO_ID} status=deferred task_class=advancement_task claimed_by={AGENT_ID} resume_when=todo_done:{PREREQUISITE_TODO_ID} -->
{unrelated}
{fallback}
"""


def _vision_run() -> dict:
    vision = normalize_goal_vision_packet(
        {
            "state": "vision_drift_detected",
            "vision_patch": {
                "acceptance_summary": "Complete the bounded source check.",
                "replan_trigger_summary": "Source A is unavailable.",
            },
            "todo_delta": [f"retain:{PRIMARY_TODO_ID}"],
            "fallback_declarations": [
                {
                    "declaration_id": "source-b",
                    "target_todo_id": FALLBACK_TODO_ID,
                }
            ],
        },
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    return {
        "classification": "source_path_checkpoint",
        "agent_id": AGENT_ID,
        "generated_at": "2026-09-01T00:00:00+00:00",
        "agent_vision": vision,
    }


def _write_fixture(
    tmp_path: Path,
    *,
    unrelated_deferred_count: int,
    fallback_present: bool = True,
    prerequisite_status: str = "open",
) -> list[str]:
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        _state_text(
            unrelated_deferred_count=unrelated_deferred_count,
            fallback_present=fallback_present,
            prerequisite_status=prerequisite_status,
        )
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "domain": "engineering",
                        "waiting_on": "codex",
                        "state_file": str(state_file),
                        "repo": str(tmp_path),
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID, "observer"],
                        },
                    }
                ]
            }
        )
    )
    runtime = tmp_path / "runtime"
    runs = runtime / "goals" / GOAL_ID / "runs"
    runs.mkdir(parents=True)
    run = _vision_run()
    json_path = runs / "source-checkpoint.json"
    markdown_path = runs / "source-checkpoint.md"
    json_path.write_text(json.dumps(run) + "\n")
    markdown_path.write_text("# Source checkpoint\n")
    (runs / "index.jsonl").write_text(
        json.dumps(
            {
                **run,
                "json_path": str(json_path),
                "markdown_path": str(markdown_path),
            }
        )
        + "\n"
    )
    return [
        "--format",
        "json",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "quota",
        "should-run",
        "--verbose",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "ark_managed_agent_goal",
    ]


@pytest.mark.parametrize("unrelated_deferred_count", [0, 8, 20])
def test_real_cli_wait_admission_is_stable_across_compact_lane_capacity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    unrelated_deferred_count: int,
) -> None:
    args = _write_fixture(
        tmp_path,
        unrelated_deferred_count=unrelated_deferred_count,
    )

    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["should_run"] is False
    assert result["execution_obligation"]["must_attempt_work"] is False
    assert "fallback_gaps" not in result["goal_frontier_projection"]
    if unrelated_deferred_count:
        assert (
            result["agent_todo_summary"]["payload_compaction"]["omitted_lanes"][
                "deferred_items"
            ]
            > 0
        )
    assert (
        result["scheduler_hint"]["goal_runtime_continuation"]["disposition"]
        == "defer"
    )


def test_real_cli_missing_declared_fallback_projects_true_unresolved_gap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _write_fixture(
        tmp_path,
        unrelated_deferred_count=20,
        fallback_present=False,
    )

    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    gap = result["goal_frontier_projection"]["fallback_gaps"][0]
    assert gap["kind"] == "vision_fallback_unresolved"
    assert gap["unresolved_todo_ids"] == [FALLBACK_TODO_ID]
    assert "lookup_uncertain_todo_ids" not in gap


@pytest.mark.parametrize("unrelated_deferred_count", [0, 20])
def test_real_cli_authority_read_failure_projects_uncertainty(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    unrelated_deferred_count: int,
) -> None:
    args = _write_fixture(tmp_path, unrelated_deferred_count=unrelated_deferred_count)

    def fail_authority_read(**_kwargs: object) -> dict:
        raise OSError("fixture canonical authority unavailable")

    monkeypatch.setattr(
        "loopx.control_plane.goals.goal_frontier.fallback_source.read_fallback_source_snapshot",
        fail_authority_read,
    )

    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    gap = result["goal_frontier_projection"]["fallback_gaps"][0]
    assert gap["kind"] == "vision_fallback_lookup_uncertain"
    assert gap["lookup_uncertain_todo_ids"] == [FALLBACK_TODO_ID]
    assert "unresolved_todo_ids" not in gap


def test_real_cli_uses_dependency_state_from_exact_canonical_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _write_fixture(
        tmp_path,
        unrelated_deferred_count=20,
        prerequisite_status="done",
    )

    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["should_run"] is True
    assert result["selected_todo"]["todo_id"] in {
        PRIMARY_TODO_ID,
        FALLBACK_TODO_ID,
    }
    assert "fallback_gaps" not in result["goal_frontier_projection"]


@pytest.mark.parametrize("depth", [0, 4, 64, 512])
def test_real_cli_reads_only_direct_fallback_dependencies(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    depth: int,
) -> None:
    args = _write_fixture(tmp_path, unrelated_deferred_count=20)
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state = state_file.read_text()
    if depth:
        state = state.replace(
            f"todo_id={PREREQUISITE_TODO_ID} status=open",
            f"todo_id={PREREQUISITE_TODO_ID} resume_when=todo_done:todo_chain_0 status=deferred",
        )
    for index in range(depth):
        state += (
            f"\n- [ ] [P2] Observe prerequisite {index}.\n"
            f"  <!-- loopx:todo todo_id=todo_chain_{index} status=deferred "
            "task_class=advancement_task claimed_by=observer "
            f"resume_when=todo_done:todo_chain_{index + 1} -->\n"
        )
    state_file.write_text(state)
    reads: list[str] = []
    original_read = fallback_source.read_fallback_source_snapshot

    def record_read(**kwargs: object) -> list[dict]:
        reads.append(str(kwargs["goal_id"]))
        return original_read(**kwargs)

    monkeypatch.setattr(fallback_source, "read_fallback_source_snapshot", record_read)
    transported: list[int] = []
    original_projection = fallback_disposition.effect_runtime_result

    def record_projection(method: str, params: dict) -> object:
        if method == "goal.fallback_disposition.project":
            transported.append(len(params["items"]))
        return original_projection(method, params)

    monkeypatch.setattr(fallback_disposition, "effect_runtime_result", record_projection)
    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["should_run"] is False
    assert "fallback_gaps" not in result["goal_frontier_projection"]
    # The direct prerequisite is deferred regardless of its own dependency.
    # Its chain cannot alter this wait or add canonical lookup calls.
    assert reads == [GOAL_ID]
    assert transported and set(transported) == {2}


@pytest.mark.parametrize("unrelated_deferred_count", [0, 20])
def test_real_cli_malformed_authority_snapshot_is_uncertain(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    unrelated_deferred_count: int,
) -> None:
    args = _write_fixture(tmp_path, unrelated_deferred_count=unrelated_deferred_count)
    original_read = fallback_source.read_fallback_source_snapshot

    def mismatched_read(**kwargs: object) -> list:
        projection = original_read(**kwargs)
        return [*projection, None]

    monkeypatch.setattr(fallback_source, "read_fallback_source_snapshot", mismatched_read)
    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    gap = result["goal_frontier_projection"]["fallback_gaps"][0]
    assert gap["kind"] == "vision_fallback_lookup_uncertain"
    assert gap["lookup_uncertain_todo_ids"] == [FALLBACK_TODO_ID]
    assert "unresolved_todo_ids" not in gap


def test_real_cli_without_declarations_does_not_read_fallback_authority(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _write_fixture(tmp_path, unrelated_deferred_count=20)
    index = tmp_path / "runtime" / "goals" / GOAL_ID / "runs" / "index.jsonl"
    run = json.loads(index.read_text())
    run["agent_vision"].pop("fallback_declarations")
    index.write_text(json.dumps(run) + "\n")
    Path(run["json_path"]).write_text(json.dumps(run) + "\n")

    def unexpected_read(**_kwargs: object) -> dict:
        pytest.fail("No fallback declaration authorizes an exact fallback lookup")

    monkeypatch.setattr(fallback_source, "read_fallback_source_snapshot", unexpected_read)
    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["should_run"] is False
    assert "fallback_gaps" not in result["goal_frontier_projection"]


@pytest.mark.parametrize("fallback_status", ["open", "deferred"])
@pytest.mark.parametrize(
    ("resume_kind", "generation", "expected_gap"),
    [
        ("todo_done", None, "vision_fallback_unresolved"),
        ("monitor_changed", 3, None),
        ("monitor_changed", None, "vision_fallback_unresolved"),
    ],
    ids=["monitor-completion-is-invalid", "generation-wait", "missing-baseline"],
)
def test_real_cli_fallback_monitor_wait_requires_generation_condition(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fallback_status: str,
    resume_kind: str,
    generation: int | None,
    expected_gap: str | None,
) -> None:
    args = _write_fixture(tmp_path, unrelated_deferred_count=0)
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    generation_metadata = (
        f" resume_monitor_generation={generation}" if generation is not None else ""
    )
    state = state_file.read_text().replace(
        f"todo_id={FALLBACK_TODO_ID} status=deferred "
        f"task_class=advancement_task claimed_by={AGENT_ID} "
        f"resume_when=todo_done:{PREREQUISITE_TODO_ID}",
        f"todo_id={FALLBACK_TODO_ID} status={fallback_status} "
        f"task_class=advancement_task claimed_by={AGENT_ID} "
        f"resume_when={resume_kind}:todo_monitor{generation_metadata}",
    )
    state += (
        "\n- [ ] [P2] Observe the fallback dependency.\n"
        "  <!-- loopx:todo todo_id=todo_monitor status=open "
        "task_class=continuous_monitor claimed_by=observer "
        "material_change_generation=3 -->\n"
    )
    state_file.write_text(state)

    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    gaps = result["goal_frontier_projection"].get("fallback_gaps", [])
    if expected_gap is None:
        assert gaps == []
    else:
        assert gaps[0]["kind"] == expected_gap
        assert gaps[0]["unresolved_todo_ids"] == [FALLBACK_TODO_ID]


@pytest.mark.parametrize("duplicate_id", [FALLBACK_TODO_ID, PREREQUISITE_TODO_ID])
def test_real_cli_ambiguous_canonical_fallback_read_is_uncertain(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    duplicate_id: str,
) -> None:
    args = _write_fixture(tmp_path, unrelated_deferred_count=0)
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        state_file.read_text()
        + "\n## User Todo\n\n- [ ] [P2] Resolve a separate user action.\n"
        + f"  <!-- loopx:todo todo_id={duplicate_id} status=open -->\n"
    )

    # The canonical succession gate rejects duplicate identities before a
    # frontier can be projected. Do not fabricate fallback advice from it.
    assert cli_main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["should_run"] is False
    assert result["state"] == "blocked_health"
    assert "duplicate succession identity" in result["verbose_debug"]["error"]
    assert "goal_frontier_projection" not in result



@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("display", ["stale", "missing"])
@pytest.mark.parametrize("dependency_status", ["open", "done"])
def test_real_provider_cli_uses_one_snapshot_including_archived_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    provider: str, display: str, dependency_status: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    args = _write_fixture(tmp_path, unrelated_deferred_count=20,
                          prerequisite_status=dependency_status)
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    runtime_root = tmp_path / "runtime"
    rows = fallback_source.read_fallback_source_snapshot(
        registry_path=tmp_path / "registry.json", runtime_root=runtime_root, goal_id=GOAL_ID,
    )
    for row in rows:
        row["schema_version"] = TODO_ITEM_SCHEMA_VERSION
    if dependency_status == "done":
        for row in rows:
            if row["todo_id"] == PREREQUISITE_TODO_ID:
                row["archive_state"] = "archive"
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, todos=rows, handoff_mode="soft_claim",
    )
    initialize_canonical_authority(runtime_root, GOAL_ID, projection,
                                   state_path=state_file, provider=provider)
    before = fallback_source.read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id=GOAL_ID,
    )
    if display == "missing":
        state_file.unlink()
    else:
        state_file.write_text("# Stale display\n\n## Agent Todo\n")
    saved_display = state_file.read_bytes() if state_file.exists() else None
    snapshots: list[int] = []
    original = fallback_source.read_fallback_source_snapshot

    def read_once(**kwargs: object) -> list[dict]:
        snapshot = original(**kwargs)
        snapshots.append(len(snapshot))
        return snapshot

    monkeypatch.setattr(fallback_source, "read_fallback_source_snapshot", read_once)
    assert cli_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert "fallback_gaps" not in result["goal_frontier_projection"]
    assert snapshots == [len(rows)]
    after = fallback_source.read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id=GOAL_ID,
    )
    assert before == after
    assert (state_file.read_bytes() if state_file.exists() else None) == saved_display
