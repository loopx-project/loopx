"""Equivalence, contract-surface, zero-Any, and hot-path I/O tests for ``state_refresh``."""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from loopx import state_refresh
from loopx.canary.maintainability_ratchet import module_metrics
from loopx.control_plane.goals.checkpoint_context_io import (
    CheckpointReadContextRejected,
    require_complete_checkpoint_index,
)
from loopx.control_plane.quota.settlement import (
    QuotaSettlementReadback,
    SettlementIdentity,
)
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.work_items import recommendation_source_io
from loopx.control_plane.work_items.refresh_recommendation import (
    load_refresh_planning_source,
)
from loopx.history import load_registry as history_load_registry
from loopx.rollout_event_log import rollout_event_log_path
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_source_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_STATE_REFRESH_EXPORTS = (
    "DEFAULT_REFRESH_CLASSIFICATION",
    "GOAL_PROGRESS_SCOPE",
    "AGENT_LANE_PROGRESS_SCOPE",
    "PROGRESS_SCOPE_CHOICES",
    "BULLET_PREFIX_RE",
    "CHECKBOX_PREFIX_RE",
    "REPAIR_NOOP_SCHEMA_VERSION",
    "DEFAULT_REFRESH_ACTION",
    "DELIVERY_BATCH_SCALE_CHOICES",
    "DELIVERY_OUTCOME_CHOICES",
    "REPAIR_DELTA_CONTRACT_SCHEMA_VERSION",
    "REPAIR_DELTA_KIND_CHOICES",
    "RECOMMENDED_ACTION_SOURCE_ACTIVE_NEXT_ACTION",
    "RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO",
    "RECOMMENDED_ACTION_SOURCE_AGENT_TODO_FALLBACK",
    "RECOMMENDED_ACTION_SOURCE_DEFAULT",
    "RECOMMENDED_ACTION_SOURCE_EXPLICIT",
    "RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO",
    "derive_recommended_action",
    "derive_recommended_action_with_source",
    "resolve_goal_state",
    "registry_goals",
    "resolve_state_file",
    "run_file_stem",
    "now_local",
    "now_local_iso",
    "load_registry",
    "load_index_snapshot",
    "reserve_unique_run_paths",
    "unique_run_paths",
    "qualify_refresh_replan_writeback",
    "capture_delivery_workspace",
    "load_refresh_planning_source",
    "read_heartbeat_settlement",
    "require_complete_checkpoint_index",
    "book_codex_session_usage",
    "ingest_usage_into_run_record",
    "parse_frontmatter",
    "extract_section_lines",
    "replace_updated_at",
    "normalize_next_action_text",
    "clean_action_line",
    "is_bullet_line",
    "first_action_item",
    "section_list_items",
    "registered_agents_for_goal",
    "normalize_progress_scope",
    "build_state_refresh_record",
    "_build_state_refresh_output_projections",
    "render_state_refresh_markdown",
    "refresh_state_run",
)

STATE_REFRESH_MODIFIED_PYTHON_PATHS = (
    "loopx/state_refresh.py",
    "loopx/refresh_state/__init__.py",
    "loopx/refresh_state/text_parsing.py",
    "loopx/refresh_state/records.py",
    "loopx/refresh_state/admission.py",
    "loopx/refresh_state/qualification.py",
    "loopx/refresh_state/post_commit.py",
    "loopx/presentation/renderers/state_refresh_markdown.py",
    "loopx/control_plane/work_items/recommendation_source_io.py",
    "loopx/control_plane/work_items/refresh_recommendation.py",
    "loopx/control_plane/todos/provider_projection.py",
    "loopx/control_plane/goals/checkpoint_context_io.py",
    "tests/control_plane/test_state_refresh_equivalence.py",
)

TWO_AGENT_FIXTURE_STATE = """---
updated_at: 2026-11-25T00:00:00+08:00
status: active
---

# Active Goal State

## Next Action

- Keep baseline execution progressing safely.

## Agent Todo

- [ ] [P1] advance owned scheduler slice
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-a todo_id=todo_agent_a -->
- [ ] [P1] advance peer release notes slice
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-b todo_id=todo_agent_b -->

## Recent User Feedback

- Preserve existing exports and contracts.

## Progress Ledger

- Completed initial audit.
"""


def _write_turn_receipt(
    runtime_root: Path,
    *,
    identity: SettlementIdentity,
) -> None:
    path = rollout_event_log_path(runtime_root, identity.goal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_rollout_event_v0",
                "event_id": "event-agent-a-turn",
                "event_kind": "quota_should_run",
                "goal_id": identity.goal_id,
                "agent_id": identity.agent_id,
                "run_id": identity.turn_instance_id,
                "details": {
                    "todo_id": identity.todo_id,
                    "settlement_effect_id": identity.effect_id,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _setup_goal_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    goal_id: str = "goal-eq-fixture",
    provider: str = "file",
    handoff_mode: str = "soft_claim",
) -> tuple[Path, Path, Path, Path]:
    if provider == "sqlite":
        monkeypatch.setenv(
            "NODE_OPTIONS",
            (os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite").strip(),
        )
        for env_key in ("TMPDIR", "TEMP", "TMP"):
            monkeypatch.setenv(env_key, str(tmp_path))
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    project = tmp_path / "project"
    state_path = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(TWO_AGENT_FIXTURE_STATE, encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": goal_id,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_path.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["agent-a", "agent-b"],
                        },
                        "workspace_guard_policy": {
                            "peer_independent_worktree_required": False,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    initialize_canonical_source_fixture(
        runtime_root,
        goal_id,
        state_path=state_path,
        provider=provider,
        handoff_mode=handoff_mode,
    )
    return project, state_path, registry_path, runtime_root


def test_public_export_surface_and_mutant_anchors_preserved() -> None:
    for symbol in EXPECTED_STATE_REFRESH_EXPORTS:
        assert hasattr(state_refresh, symbol), f"missing export: {symbol}"

    source = (REPO_ROOT / "loopx" / "state_refresh.py").read_text(encoding="utf-8")
    assert "            if normalized_next_action:" in source
    assert (
        "                runs_dir.mkdir(parents=True, exist_ok=True)\n"
        "                json_path, markdown_path = reserve_unique_run_paths(runs_dir, generated_at)"
    ) in source


def test_refactored_modules_zero_any_zero_ignore_and_under_1000_loc() -> None:
    type_ignore_pattern = re.compile(r"#\s*" + r"type:\s*" + "ignore")
    bare_nofollow_attr = "O_" + "NOFOLLOW"

    for relative_path in STATE_REFRESH_MODIFIED_PYTHON_PATHS:
        file_path = REPO_ROOT / relative_path
        metrics = module_metrics(file_path)
        assert metrics["lines"] < 1000, f"{relative_path} exceeded 1000 LOC: {metrics}"
        assert metrics["any_count"] == 0, f"{relative_path} has AST Any: {metrics}"
        assert metrics["dict_any_count"] == 0, f"{relative_path} has dict Any: {metrics}"

        text = file_path.read_text(encoding="utf-8")
        assert type_ignore_pattern.search(text) is None, f"{relative_path} has type-ignore comment"
        tree = ast.parse(text, filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == bare_nofollow_attr:
                raise AssertionError(f"{relative_path} uses bare os.{bare_nofollow_attr}")


def test_text_parsing_and_progress_scope_parity() -> None:
    assert state_refresh.parse_frontmatter(TWO_AGENT_FIXTURE_STATE) == {
        "updated_at": "2026-11-25T00:00:00+08:00",
        "status": "active",
    }
    assert state_refresh.parse_frontmatter("no frontmatter") == {}
    assert state_refresh.extract_section_lines(
        TWO_AGENT_FIXTURE_STATE, "Recent User Feedback"
    ) == ["- Preserve existing exports and contracts."]

    updated = state_refresh.replace_updated_at(
        TWO_AGENT_FIXTURE_STATE, "2026-11-25T09:00:00+08:00"
    )
    assert "updated_at: 2026-11-25T09:00:00+08:00" in updated
    inserted = state_refresh.replace_updated_at(
        "---\nstatus: active\n---\n\n# Body\n",
        "2026-11-25T09:00:00+08:00",
    )
    assert "updated_at: 2026-11-25T09:00:00+08:00" in inserted
    assert state_refresh.replace_updated_at("plain", "2026-11-25T09:00:00+08:00") == "plain"

    assert state_refresh.clean_action_line("1. [ ] ship zero-Any slice") == "ship zero-Any slice"
    assert state_refresh.is_bullet_line("  - item") is True
    assert state_refresh.is_bullet_line("plain text") is False
    lines = ["- [ ] primary step", "  <!-- skip comment -->", "  continuation"]
    assert state_refresh.first_action_item(lines, 0) == "primary step continuation"
    assert state_refresh.section_list_items(
        ["plain header", "- one", "  more", "- two"]
    ) == [
        "plain header",
        "one more",
        "two",
    ]

    assert state_refresh.normalize_next_action_text("  ship   cleanly ") == "ship cleanly"
    with pytest.raises(ValueError, match="must not be empty"):
        state_refresh.normalize_next_action_text("   ")

    assert state_refresh.normalize_progress_scope(None) == ""
    assert state_refresh.normalize_progress_scope("goal") == "goal"
    assert state_refresh.normalize_progress_scope("agent_lane") == "agent_lane"
    with pytest.raises(ValueError, match="--progress-scope must be one of"):
        state_refresh.normalize_progress_scope("invalid_scope")


def test_record_projection_and_markdown_parity(tmp_path: Path) -> None:
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(TWO_AGENT_FIXTURE_STATE, encoding="utf-8")
    todo_fields = parse_active_state_todos(TWO_AGENT_FIXTURE_STATE, item_limit=None)
    identity = SettlementIdentity(
        goal_id="goal-eq",
        agent_id="agent-a",
        todo_id="todo_agent_a",
        turn_instance_id="turn-eq-1",
    )
    record = state_refresh.build_state_refresh_record(
        goal_id="goal-eq",
        state_file=state_file,
        state_text=TWO_AGENT_FIXTURE_STATE,
        classification="validated_progress",
        recommended_action="[P1] advance owned scheduler slice",
        recommended_action_source=state_refresh.RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO,
        recommended_action_resolution={
            "authority": "settlement",
            "settlement_alignment": "exact",
            "todo_id": "todo_agent_a",
        },
        generated_at="2026-11-25T09:00:00+08:00",
        registry_goal={
            "id": "goal-eq",
            "domain": "engineering",
            "status": "active",
            "adapter": "codex",
            "authority_sources": [{"id": "roadmap"}],
        },
        progress_scope="agent_lane",
        agent_id="agent-a",
        autonomous_replan_recorded=True,
        repair_delta_contract={"delta_present": True, "delta_kinds": ["todo_scope"]},
        vision_checkpoint={
            "agent_id": "agent-a",
            "required": True,
            "satisfied": False,
            "decision": "blocked",
            "acceptance_gaps": [
                {
                    "component_checks": {"tests_verified": False},
                    "resolution_hint": "Verify parity before delivery.",
                }
            ],
        },
        delivery_workspace={
            "task_repository": "loopx",
            "workspace_kind": "independent_git_worktree",
        },
        settlement_identity=identity,
        todo_fields=todo_fields,
    )
    record["runtime_projection_route"] = {"status": "primary", "source_role": "primary"}
    index_record, payload = state_refresh._build_state_refresh_output_projections(
        record=record,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        project=tmp_path,
        json_path=tmp_path / "run.json",
        markdown_path=tmp_path / "run.md",
        index_path=tmp_path / "index.jsonl",
        dry_run=True,
        autonomous_replan_recorded_requested=True,
    )
    assert index_record["goal_id"] == "goal-eq"
    assert index_record["agent_lane"] == "agent-a"
    assert payload["appended"] is False
    payload["projection_outbox"] = {
        "status": "pending",
        "recommended_action": "Run todo project-markdown.",
    }
    markdown = state_refresh.render_state_refresh_markdown(payload)
    assert "# LoopX State Refresh" in markdown
    assert "- Todo display: `pending`" in markdown
    assert "outcome qualification gap: tests_verified." in markdown
    assert "## Recommended Action" in markdown
    assert "## Next Action" in markdown

    error_md = state_refresh.render_state_refresh_markdown(
        {
            "ok": False,
            "dry_run": False,
            "appended": False,
            "goal_id": "goal-eq",
            "error": "boundary_rejected",
            "error_detail": "missing prerequisite",
        }
    )
    assert "- error: boundary_rejected" in error_md
    assert "- detail: missing prerequisite" in error_md


def test_refresh_state_run_dry_run_write_and_negative_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goal_id = "goal-eq-run"
    project, _, registry_path, runtime_root = _setup_goal_fixture(
        tmp_path, monkeypatch, goal_id=goal_id, provider="file"
    )
    identity = SettlementIdentity(
        goal_id=goal_id,
        agent_id="agent-a",
        todo_id="todo_agent_a",
        turn_instance_id="turn-eq-run-1",
    )
    _write_turn_receipt(runtime_root, identity=identity)

    with pytest.raises(ValueError, match="--next-action-basis requires --next-action"):
        state_refresh.refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            goal_id=goal_id,
            project=project,
            state_file=None,
            classification="validated_progress",
            recommended_action=None,
            next_action_basis="explicit_step",
            dry_run=True,
        )

    with pytest.raises(ValueError, match="--agent-lane requires --agent-id"):
        state_refresh.refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            goal_id=goal_id,
            project=project,
            state_file=None,
            classification="validated_progress",
            recommended_action=None,
            agent_lane="worker-lane",
            dry_run=True,
        )

    dry = state_refresh.refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=goal_id,
        project=project,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id,
        agent_id=identity.agent_id,
        dry_run=True,
        sync_global=False,
    )
    assert dry["ok"] is True and dry["appended"] is False
    assert not Path(str(dry["json_path"])).exists()

    written = state_refresh.refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=goal_id,
        project=project,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id,
        agent_id=identity.agent_id,
        dry_run=False,
        sync_global=False,
    )
    assert written["ok"] is True and written["appended"] is True
    assert written["projection_delivery"] == "delivered"
    assert Path(str(written["json_path"])).is_file()
    assert Path(str(written["markdown_path"])).is_file()
    assert Path(str(written["index_path"])).is_file()


def test_load_recommendation_source_goal_reuses_registry_and_reads_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goal_id = "goal-shared-route"
    invoked_registry = tmp_path / "invoked-registry.json"
    mirror_registry = tmp_path / "mirror-registry.json"
    project = tmp_path / "project"
    state_path = project / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(TWO_AGENT_FIXTURE_STATE, encoding="utf-8")

    local_payload: dict[str, object] = {
        "goals": [{"id": goal_id, "status": "active", "repo": str(project)}]
    }
    load_calls: list[Path] = []

    def counted_load(path: Path) -> Mapping[str, object]:
        load_calls.append(path)
        return history_load_registry(path)

    monkeypatch.setattr(recommendation_source_io, "load_registry", counted_load)
    source_path, goal = recommendation_source_io.load_recommendation_source_goal(
        invoked_registry,
        goal_id,
        registry=local_payload,
    )
    assert load_calls == []
    assert source_path == invoked_registry.resolve()
    assert goal is not None and goal["id"] == goal_id

    mirror_registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": goal_id,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    mirror_payload: dict[str, object] = {
        "registry_role": "global-local",
        "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [
            {
                "id": goal_id,
                "status": "active",
                "repo": str(project),
                "source_registry": str(mirror_registry),
            }
        ],
    }
    resolved_mirror, mirror_goal = recommendation_source_io.load_recommendation_source_goal(
        invoked_registry,
        goal_id,
        registry=mirror_payload,
    )
    assert resolved_mirror == mirror_registry.resolve()
    assert mirror_goal is not None and mirror_goal["state_file"] == str(state_path)


def test_load_refresh_planning_source_skips_state_read_when_supplied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goal_id = "goal-plan-io"
    _, state_path, _, runtime_root = _setup_goal_fixture(
        tmp_path, monkeypatch, goal_id=goal_id, provider="file"
    )
    reads: list[Path] = []
    original_read_text = Path.read_text

    def strict_read_text(self: Path, encoding: str = "utf-8", errors: str | None = None) -> str:
        if self.resolve() == state_path.resolve():
            reads.append(self)
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", strict_read_text)
    source = load_refresh_planning_source(
        runtime_root,
        goal_id,
        state_path,
        require_display=True,
        state_text=TWO_AGENT_FIXTURE_STATE,
    )
    assert reads == []
    assert source.state_text == TWO_AGENT_FIXTURE_STATE

    missing = tmp_path / "missing.md"
    with pytest.raises(FileNotFoundError, match="state file does not exist"):
        load_refresh_planning_source(
            runtime_root,
            goal_id,
            missing,
            require_display=True,
        )


def test_require_complete_checkpoint_index_uses_tail_seek_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_path = tmp_path / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True)
    read_bytes_calls: list[Path] = []
    original_read_bytes = Path.read_bytes

    def counted_read_bytes(self: Path) -> bytes:
        if self.resolve() == index_path.resolve():
            read_bytes_calls.append(self)
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    require_complete_checkpoint_index(index_path)
    index_path.write_bytes(b"")
    require_complete_checkpoint_index(index_path)
    index_path.write_bytes(b'{"row": 1}\n{"row": 2}\n')
    require_complete_checkpoint_index(index_path)
    assert read_bytes_calls == []

    index_path.write_bytes(b'{"row": 1}\n{"partial": ')
    with pytest.raises(CheckpointReadContextRejected, match="incomplete tail"):
        require_complete_checkpoint_index(index_path)
    assert read_bytes_calls == []


def test_refresh_state_run_sqlite_next_action_eliminates_redundant_rereads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goal_id = "goal-sqlite-hotpath"
    project, state_path, registry_path, runtime_root = _setup_goal_fixture(
        tmp_path, monkeypatch, goal_id=goal_id, provider="sqlite"
    )
    identity = SettlementIdentity(
        goal_id=goal_id,
        agent_id="agent-a",
        todo_id="todo_agent_a",
        turn_instance_id="turn-sqlite-1",
    )
    _write_turn_receipt(runtime_root, identity=identity)

    state_reads: list[Path] = []
    planning_state_reads: list[Path] = []
    registry_reads: list[Path] = []
    rec_io_load_calls: list[Path] = []
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes

    def strict_read_text(self: Path, encoding: str = "utf-8", errors: str | None = None) -> str:
        if self.resolve() == state_path.resolve():
            state_reads.append(self)
            caller = inspect.currentframe()
            if caller is not None and caller.f_back is not None:
                if Path(caller.f_back.f_code.co_filename).name == "refresh_recommendation.py":
                    planning_state_reads.append(self)
        return original_read_text(self, encoding=encoding, errors=errors)

    def strict_read_bytes(self: Path) -> bytes:
        if self.resolve() == registry_path.resolve():
            registry_reads.append(self)
        return original_read_bytes(self)

    def counted_rec_io_load(path: Path) -> Mapping[str, object]:
        rec_io_load_calls.append(path.resolve())
        return history_load_registry(path)

    monkeypatch.setattr(Path, "read_text", strict_read_text)
    monkeypatch.setattr(Path, "read_bytes", strict_read_bytes)
    monkeypatch.setattr(recommendation_source_io, "load_registry", counted_rec_io_load)

    result = state_refresh.refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=goal_id,
        project=project,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        next_action="Advance the next verified slice cleanly.",
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id,
        agent_id=identity.agent_id,
        dry_run=False,
        sync_global=False,
    )
    assert result["ok"] is True and result["appended"] is True
    assert len(planning_state_reads) == 1, (
        f"load_refresh_planning_source re-read ACTIVE_GOAL_STATE.md: {len(planning_state_reads)}"
    )
    # 3 distinct owners read state: initial planning load, locked recommendation guard,
    # and post-commit legacy_writer_fence projection check (no duplicate planning read).
    assert len(state_reads) <= 3, f"redundant ACTIVE_GOAL_STATE.md reads: {len(state_reads)}"
    # Only the exclusive locked validation inside recommendation_source_guard reloads via rec_io.
    assert rec_io_load_calls == [registry_path.resolve()]
    assert len(registry_reads) <= 8, f"redundant registry.json reads: {len(registry_reads)}"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_same_turn_replay_uses_current_registry_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    goal_id = f"goal-replay-switch-{provider}"
    project, state_a, registry_path, runtime_root = _setup_goal_fixture(
        tmp_path, monkeypatch, goal_id=goal_id, provider=provider
    )
    identity = SettlementIdentity(
        goal_id=goal_id,
        agent_id="agent-a",
        todo_id="todo_agent_a",
        turn_instance_id=f"turn-replay-{provider}",
    )
    _write_turn_receipt(runtime_root, identity=identity)

    first = state_refresh.refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=goal_id,
        project=None,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id,
        agent_id=identity.agent_id,
        dry_run=False,
        sync_global=False,
    )
    assert first["ok"] is True and first["appended"] is True
    assert first["projection_delivery"] == "delivered"
    first_outbox = first.get("projection_outbox")
    assert isinstance(first_outbox, Mapping)
    first_revision = first_outbox.get("provider_revision")
    assert isinstance(first_revision, str) and first_revision

    index_path = Path(str(first["index_path"]))
    index_before = index_path.read_text(encoding="utf-8")
    rollout_before = rollout_event_log_path(runtime_root, goal_id).read_text(encoding="utf-8")

    state_b = state_a.with_name("STATE_B.md")
    blank_narrative = "---\nupdated_at: 2026-11-25T00:00:00+08:00\nstatus: active\n---\n\n# Active Goal State\n\n## Next Action\n\n- Keep baseline execution progressing safely.\n\n## Agent Todo\n"
    state_a.write_text(blank_narrative, encoding="utf-8")
    state_b.write_text(blank_narrative, encoding="utf-8")

    real_read_settlement = state_refresh.read_heartbeat_settlement
    switched: list[bool] = []

    def switching_read_settlement(
        inner_runtime_root: Path,
        *,
        goal_id: str,
        agent_id: str | None,
        todo_id: str | None,
        turn_instance_id: str | None,
        replan_obligation_id: str | None = None,
        infer_turn_instance_id: bool = False,
        allow_unbound_binding: bool = False,
        resolve_original_binding: bool = False,
        pending_closeout: bool = False,
        refresh_retry: dict[str, object] | None = None,
        registry_path: Path | None = None,
        goal_ref: Mapping[str, object] | None = None,
        source_admission: Mapping[str, object] | None = None,
        borrow_source_admission: bool = False,
        heartbeat_reentry_guard: dict[str, object] | None = None,
        include_record_bodies: bool = True,
        tolerate_malformed_history: bool = False,
    ) -> QuotaSettlementReadback | None:
        readback = real_read_settlement(
            inner_runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=replan_obligation_id,
            infer_turn_instance_id=infer_turn_instance_id,
            allow_unbound_binding=allow_unbound_binding,
            resolve_original_binding=resolve_original_binding,
            pending_closeout=pending_closeout,
            refresh_retry=refresh_retry,
            registry_path=registry_path,
            goal_ref=goal_ref,
            source_admission=source_admission,
            borrow_source_admission=borrow_source_admission,
            heartbeat_reentry_guard=heartbeat_reentry_guard,
            include_record_bodies=include_record_bodies,
            tolerate_malformed_history=tolerate_malformed_history,
        )
        if registry_path is not None and not switched:
            switched.append(True)
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            payload["goals"][0]["state_file"] = str(state_b.relative_to(project))
            registry_path.write_text(json.dumps(payload), encoding="utf-8")
        return readback

    monkeypatch.setattr(
        state_refresh, "read_heartbeat_settlement", switching_read_settlement
    )
    replay = state_refresh.refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=goal_id,
        project=None,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id,
        agent_id=identity.agent_id,
        dry_run=False,
        sync_global=False,
    )
    assert switched == [True]
    assert replay["appended"] is False
    assert replay["projection_delivery"] == "delivered"
    replay_outbox = replay.get("projection_outbox")
    assert isinstance(replay_outbox, Mapping)
    assert replay_outbox.get("state_file") == str(state_b.resolve())
    assert replay_outbox.get("provider_revision") == first_revision
    assert "[P1] advance owned scheduler slice" in state_b.read_text(encoding="utf-8")
    assert "[P1] advance owned scheduler slice" not in state_a.read_text(encoding="utf-8")
    assert index_path.read_text(encoding="utf-8") == index_before
    assert rollout_event_log_path(runtime_root, goal_id).read_text(encoding="utf-8") == rollout_before


def test_load_refresh_planning_source_and_goal_context_use_rollout_event_source_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import ExitStack
    from loopx import rollout_event_log as rollout_mod
    from loopx.control_plane.todos.resume_condition import TodoResumeRolloutEvents
    from loopx.refresh_state.admission import load_and_validate_goal_context

    goal_id = "goal-rollout-ptr"
    project, state_path, registry_path, runtime_root = _setup_goal_fixture(
        tmp_path, monkeypatch, goal_id=goal_id, provider="file"
    )
    log_path = rollout_event_log_path(runtime_root, goal_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps({"schema_version": "loopx_rollout_event_v0", "event_kind": "pr_merge", "pr": 1}) + "\n",
        encoding="utf-8",
    )

    def _forbid_full_load(*args, **kwargs):
        raise AssertionError("full load_rollout_events must not run on refresh planning read")

    monkeypatch.setattr(rollout_mod, "load_rollout_events", _forbid_full_load)

    source = load_refresh_planning_source(
        runtime_root,
        goal_id,
        state_path,
        require_display=False,
        registry_path=registry_path,
    )
    assert isinstance(source.events, TodoResumeRolloutEvents)
    assert list(source.events) == []
    assert source.events.source == {"runtime_root": str(runtime_root), "goal_id": goal_id}

    registry = dict(history_load_registry(registry_path))
    with ExitStack() as guard:
        goal_ctx = load_and_validate_goal_context(
            registry=registry,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            project=None,
            state_file=None,
            sync_global=False,
            next_action=None,
            normalized_next_action=None,
            normalized_agent_id="worker-a",
            settlement_readback=None,
            goal_acceptance_preview=None,
            research_write_guard=guard,
            load_refresh_planning_source_fn=load_refresh_planning_source,
            registered_agents_fn=lambda g: list((g or {}).get("registered_agents") or []),
        )
    assert isinstance(goal_ctx.planning_events, TodoResumeRolloutEvents)
    assert list(goal_ctx.planning_events) == []
    assert goal_ctx.planning_events.source == {"runtime_root": str(runtime_root), "goal_id": goal_id}
