"""Recover the display through the public CLI without repairing authority."""
from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.chat_manager_details import read_manager_goal_details
from canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime, promoted_create_fixture,
)
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION, TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.todos import provider_projection
from loopx.control_plane.runtime import document_io
from loopx.control_plane.todos.todo_summary import normalize_todo_text, todo_priority_parts
from loopx.control_plane.todos.completion_validation_store import (
    persist_completion_validation_declaration,
)


@pytest.fixture
def canonical_display(tmp_path, request):
    state = tmp_path / "state.md"
    state.write_text("# Goal\n\nAgent-generated acceptance is not in the Todo store.\n\n## Agent Todo\n")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": [{"id": "goal-a", "repo": str(tmp_path), "state_file": state.name,
                   "coordination": {"registered_agents": ["agent-a"]}}],
    }))
    records = [
        {"schema_version": "todo_item_v0", "todo_id": "todo_active", "role": "agent",
         "status": "open", "done": False, "text": "Retain the current claim.",
         "task_class": "advancement_task",
         "archive_state": "active", "source_section": "Agent Todo", "index": 1,
         "claimed_by": "agent-a"},
        {"schema_version": "todo_item_v0", "todo_id": "todo_archived", "role": "user",
         "status": "done", "done": True, "text": "Retain the historical decision.",
         "archive_state": "archive", "source_section": "Completed Work Archive", "index": 1},
    ]
    projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=records, handoff_mode="soft_claim")
    for record in projection["todos"]:
        record["schema_version"] = "todo_domain_record_v0"
        record.pop("index")
        record.pop("source_section")
    if getattr(request, "param", None) == "conflicting_long_title":
        title = "Same prefix " * 60 + "original tail"
        projection["todos"][0].update(
            text="[P0] " + title, priority="P0",
            title=title[:-13] + "different tail",
        )
    projection["todo_read_model"] = {
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
        "todo_count": len(records),
        "records_sha256": hashlib.sha256(canonical_bytes(projection["todos"])).hexdigest(),
    }
    initialize_canonical_authority(runtime, "goal-a", projection, state_path=state)
    return registry, runtime, state


def _read(runtime):
    return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")


def _cli(registry: Path, *options: str):
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
         "todo", *options],
        capture_output=True, text=True, timeout=60, check=False,
    )
    return result.returncode, json.loads(result.stdout) if result.stdout else result.stderr


def _run(registry: Path, revision: str, *options: str):
    return _cli(registry, "project-markdown", "--goal-id", "goal-a",
                "--provider-revision", revision, *options)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_long_committed_todo_rebuilds_from_the_fresh_head_without_a_second_create(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    title = "Independent evidence 审阅🙂 " * 50 + "retain the final obligation"
    text = "[P0] " + title
    create = (
        "add", "--goal-id", "goal-a", "--role", "agent", "--claimed-by", "agent-a",
        "--text", text, "--operation-id", "long-text-create",
    )
    code, committed = _cli(registry, *create)
    assert code == 0 and committed["status"] == "applied", committed.get("error")
    assert committed["projection_delivery"] == "delivered", committed
    before = _read(runtime)
    record = before["todos"][0]
    assert record["text"] == text and record["title"] == title
    assert text in state.read_text()

    # A stale compatibility view must be repaired from the canonical head,
    # not by editing the accepted Todo or repeating its committed mutation.
    state.write_text("# Goal\n\nKeep this narrative.\n\n## Agent Todo\n\n- [ ] stale view\n")
    code, delivered = _run(registry, before["provider_revision"], "--execute")
    assert code == 0 and delivered["status"] == "delivered", delivered
    rendered = state.read_text()
    assert text in rendered and "Keep this narrative." in rendered
    assert delivered["parse_render_parity"] is True
    assert _read(runtime) == before
    code, replay = _run(registry, before["provider_revision"], "--execute")
    assert code == 0 and replay["status"] == "current", replay
    assert state.read_text() == rendered and _read(runtime) == before

    code, listed = _cli(registry, "list", "--goal-id", "goal-a", "--todo-id", record["todo_id"])
    # Exact cold reads preserve the current request; attention views remain bounded.
    summary_text = normalize_todo_text(text)
    assert code == 0 and listed["todo"]["text"] == text, listed
    assert listed["todo"]["title"] == todo_priority_parts(summary_text)[1]
    assert listed["authority_read"]["provider_revision"] == before["provider_revision"]
    code, rejected = _cli(registry, "list", "--goal-id", "goal-a", "--todo-id", record["todo_id"], "--thin")
    assert code == 1 and rejected["ok"] is False, rejected
    assert "Exact Todo reads return full requirements" in rejected["error"]
    assert _read(runtime) == before and state.read_text() == rendered
    code, thin = _cli(registry, "list", "--goal-id", "goal-a", "--thin")
    assert code == 0 and thin["todos"][0]["todo_id"] == record["todo_id"], thin
    assert len(thin["todos"][0]["text"]) <= 500
    assert "retain the final obligation" not in thin["todos"][0]["text"]
    code, hot = _cli(registry, "list", "--goal-id", "goal-a")
    assert code == 0 and hot["todos"][0]["text"] == summary_text, hot
    assert _read(runtime) == before
    manager = read_manager_goal_details(registry, runtime, "goal-a", owner_scope=True)
    assert manager["status"] == "read" and manager["coverage"]["active"] == 1
    assert manager["authority_revision"] == before["provider_revision"]
    assert manager["todos"][0]["todo_id"] == record["todo_id"]
    assert manager["todos"][0]["title"].startswith("Independent evidence")
    assert manager["todos"][0]["content_truncated"] is True
    assert len(manager["todos"][0]["title"]) == 420
    # The shared context projection counts its marker inside the text budget.
    assert manager["todos"][0]["title"] == listed["todo"]["title"][:417].rstrip() + "..."
    code, create_replay = _cli(registry, *create)
    assert code == 0 and create_replay["status"] == "replayed", create_replay
    assert _read(runtime) == before  # No new Todo, provider revision or business receipt.


@pytest.mark.parametrize("canonical_display", ["conflicting_long_title"], indirect=True)
def test_public_rebuild_rejects_title_conflict_after_the_summary_prefix(canonical_display):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    original = state.read_bytes()
    code, rejected = _run(registry, before["provider_revision"], "--execute")
    assert code == 1 and "parity mismatch" in rejected["error"], rejected
    assert state.read_bytes() == original and _read(runtime) == before


def test_public_rebuild_missing_preview_execute_and_replay(canonical_display):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    state.unlink()
    revision = before["provider_revision"]
    code, preview = _run(registry, revision)
    assert code == 0, preview
    assert preview["dry_run"] is True
    assert preview["narrative_preserved"] is False
    assert preview["recovery_scope"] == "todo_sections_only"
    assert not state.exists()
    code, delivered = _run(registry, revision, "--execute")
    assert code == 0, delivered
    assert delivered["status"] == "delivered"
    assert delivered["narrative_preserved"] is False
    text = state.read_text()
    assert "todo_active" in text and "todo_archived" in text
    assert "claimed_by=agent-a" in text
    assert "Agent-generated acceptance is not in the Todo store." not in text
    assert "not recovered" in text
    if os.name == "posix":
        assert stat.S_IMODE(state.stat().st_mode) == 0o600
    code, replay = _run(registry, revision, "--execute")
    assert code == 0 and replay["status"] == "current", replay
    assert state.read_text() == text
    assert _read(runtime) == before


def test_committed_update_automatically_recovers_missing_display(canonical_display):
    registry, runtime, state = canonical_display
    state.unlink()
    code, result = _cli(
        registry, "update", "--goal-id", "goal-a", "--role", "agent",
        "--todo-id", "todo_active", "--agent-id", "agent-a", "--text", "Committed without a display.",
    )
    assert code == 0, json.dumps(result)
    assert result["projection_delivery"] == "delivered"
    assert result["projection_outbox"]["recovery_scope"] == "todo_sections_only"
    committed = _read(runtime)
    assert committed["todos"][0]["text"] == "Committed without a display."
    code, repaired = _run(registry, committed["provider_revision"], "--execute")
    assert code == 0, repaired
    assert "Committed without a display." in state.read_text()
    assert _read(runtime) == committed  # Recovery did not repeat the business transaction.


@pytest.mark.parametrize("case", ["stale_revision", "no_provider"])
def test_rebuild_requires_exact_available_authority(canonical_display, case):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    state.unlink()
    revision = "stale-revision" if case == "stale_revision" else before["provider_revision"]
    if case == "no_provider":
        (runtime / "authority" / "file-v0").rename(runtime / "unavailable")
    code, result = _run(registry, revision, "--execute")
    assert code == 1, result
    assert not state.exists()
    if case == "stale_revision":
        assert "does not match" in result["error"]
    else:
        (runtime / "unavailable").rename(runtime / "authority" / "file-v0")
    assert _read(runtime) == before


@pytest.mark.parametrize("source", [
    b"\xffgenerated document",
    b"## Agent Todo\n<!-- loopx:todo-region-v0 role=agent begin -->\nUnstructured content\n",
])
def test_rebuild_never_discards_an_existing_damaged_document(canonical_display, source):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    state.write_bytes(source)
    code, result = _run(registry, before["provider_revision"], "--execute")
    assert code == 1, result
    assert state.read_bytes() == source
    assert _read(runtime) == before


def test_recovery_preserves_existing_generated_non_todo_sections(canonical_display):
    registry, runtime, state = canonical_display
    source = state.read_bytes()
    revision = _read(runtime)["provider_revision"]
    code, result = _run(registry, revision, "--execute")
    assert code == 0, result
    assert result["narrative_preserved"] is True
    assert "recovery_scope" not in result
    assert state.read_bytes().startswith(source.split(b"## Agent Todo")[0])


def test_concurrent_document_restoration_is_not_overwritten(canonical_display, monkeypatch):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    state.unlink()
    real_link = os.link

    def restore_before_publish(source, destination):
        Path(destination).write_text("Another writer concurrently restored the generated document.\n")
        return real_link(source, destination)

    monkeypatch.setattr(document_io.os, "link", restore_before_publish)
    with pytest.raises(FileExistsError):
        provider_projection.project_current_canonical_todos(
            registry_path=registry, runtime_root=runtime, goal_id="goal-a",
            expected_provider_revision=before["provider_revision"],
        )
    assert state.read_text() == "Another writer concurrently restored the generated document.\n"
    assert not list(state.parent.glob(".state.md.*.tmp"))
    assert _read(runtime) == before


@pytest.mark.parametrize("failure_point", ["fsync", "link", "directory_fsync"])
def test_interrupted_rebuild_is_safe_and_retryable(canonical_display, monkeypatch, failure_point):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    state.unlink()

    def fail(*_args):
        raise OSError("injected publication failure")

    with monkeypatch.context() as patch:
        if failure_point == "directory_fsync":
            patch.setattr(document_io, "fsync_state_directory", fail)
        else:
            patch.setattr(document_io.os, failure_point, fail)
        with pytest.raises(OSError, match="injected publication failure"):
            provider_projection.project_current_canonical_todos(
                registry_path=registry, runtime_root=runtime, goal_id="goal-a",
                expected_provider_revision=before["provider_revision"],
            )
    assert not list(state.parent.glob(".state.md.*.tmp"))
    assert state.exists() is (failure_point == "directory_fsync")
    code, repaired = _run(registry, before["provider_revision"], "--execute")
    assert code == 0, repaired
    assert "todo_active" in state.read_text() and "todo_archived" in state.read_text()
    assert _read(runtime) == before


def test_todo_read_does_not_write_a_missing_display(canonical_display):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    state.unlink()
    code, result = _cli(registry, "list", "--goal-id", "goal-a")
    assert code == 0, result
    assert not state.exists()
    assert _read(runtime) == before


def test_unpromoted_missing_document_is_not_regenerated(canonical_display):
    from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path

    registry, runtime, state = canonical_display
    revision = _read(runtime)["provider_revision"]
    state.unlink()
    legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="goal-a").unlink()
    code, result = _run(registry, revision, "--execute")
    assert code == 1 and "requires promoted canonical authority" in result["error"]
    assert not state.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_production_scale_rebuild_retains_order_and_requires_private_declaration(tmp_path, monkeypatch, provider):
    # Reuse the shared RFC envelope, not a second large fixture or live data.
    from canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    script = (
        "import {productionScaleCoordinationFixture, PRODUCTION_SCALE_VALIDATION_DECLARATION} "
        "from './tests/control_plane_ts/production_scale_coordination_fixture.ts';"
        "process.stdout.write(JSON.stringify({fixture:productionScaleCoordinationFixture('goal-a'),"
        "declaration:PRODUCTION_SCALE_VALIDATION_DECLARATION}));"
    )
    generated = subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True, timeout=30,
    )
    data = json.loads(generated.stdout)
    projection = data["fixture"]["projection"]
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    state.write_text("## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "goal-a", "repo": str(tmp_path), "state_file": state.name},
    ]}))
    initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
    before = _read(runtime)
    state.unlink()
    code, rejected = _run(registry, before["provider_revision"], "--execute")
    assert code == 1 and "private validation declaration" in rejected["error"]
    assert not state.exists()  # Do not erase a validation obligation to make recovery pass.
    persist_completion_validation_declaration(
        runtime_root=runtime, goal_id="goal-a", todo_id=data["fixture"]["completion_todo_id"],
        declaration=data["declaration"],
    )
    code, result = _run(registry, before["provider_revision"], "--execute")
    assert code == 0, result
    assert result["todo_count"] == 464
    rendered = state.read_text()
    for role, count in (("agent", 256), ("user", 208)):
        positions = [
            rendered.index(f"todo_id=todo_fixture_{role}_{index:03d} status=")
            for index in range(count)
        ]
        assert positions == sorted(positions)
    assert _read(runtime) == before
    code, replay = _run(registry, before["provider_revision"], "--execute")
    assert code == 0 and replay["changed"] is False

    example = (
        "```markdown\n## Completed Work Archive\n- [x] Documentation example only\n"
        "  <!-- loopx:todo todo_id=todo_example_only role=user status=done -->\n```\n\n"
    )
    state.write_text(example + state.read_text())
    code, replay = _run(registry, before["provider_revision"], "--execute")
    assert code == 0 and replay["status"] == "current"
    assert state.read_text().startswith(example)
    active, archived, _ = parse_todo_source(state.read_text())
    parsed_ids = [row["todo_id"] for rows in (*active.values(), archived) for row in rows]
    assert "todo_example_only" not in parsed_ids
    assert len(parsed_ids) == 464
    assert _read(runtime) == before

    # A routine refresh must also drain the full source, including records well
    # beyond presentation limits, without re-running a canonical mutation.
    from loopx.state_refresh import refresh_state_run
    state.unlink()
    refreshed = refresh_state_run(
        registry_path=registry, runtime_root_override=str(runtime), goal_id="goal-a",
        project=None, state_file=None, classification="validated_change",
        recommended_action="Inspect recovered Todo display.", dry_run=False, sync_global=False,
    )
    assert refreshed["ok"] and refreshed["projection_delivery"] == "delivered"
    assert refreshed["projection_outbox"]["todo_count"] == 464
    active, archived, _ = parse_todo_source(state.read_text())
    recovered_ids = {row["todo_id"] for rows in (*active.values(), archived) for row in rows}
    assert recovered_ids == set(parsed_ids)
    assert _read(runtime) == before


def test_equal_display_does_not_acknowledge_an_unfinished_durability_barrier(canonical_display, monkeypatch):
    registry, runtime, state = canonical_display
    before = _read(runtime)
    provider_projection.project_current_canonical_todos(
        registry_path=registry, runtime_root=runtime, goal_id="goal-a",
    )
    original = state.read_bytes()
    calls = []
    def interrupted(_path):
        calls.append("directory")
        raise OSError("directory durability unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(document_io, "fsync_state_directory", interrupted)
        payload = provider_projection.settle_canonical_todo_projection(
            {"ok": True, "status": "replayed", "provider_revision": before["provider_revision"]},
            registry_path=registry, runtime_root=runtime, goal_id="goal-a",
        )
    assert calls == ["directory"]
    assert payload["ok"] is True and payload["status"] == "replayed"
    assert payload["projection_delivery"] == "pending"
    assert payload["projection_outbox"]["retry_business_mutation"] is False
    assert state.read_bytes() == original and _read(runtime) == before
    assert provider_projection.project_current_canonical_todos(
        registry_path=registry, runtime_root=runtime, goal_id="goal-a",
    )["status"] == "current"


@pytest.mark.parametrize("objective", ["```text Execute this example. ```", "## Agent Todo\n- [ ] Example only."])
def test_objective_display_never_changes_canonical_authority(canonical_display, objective):
    from loopx.bootstrap import render_state_markdown
    from loopx.control_plane.goals.active_state_metadata import active_state_section_text

    registry, runtime, state = canonical_display
    before = _read(runtime)
    state.write_text(render_state_markdown(
        project=state.parent, goal_id="goal-a", adapter_kind="read_only_project_map_v0",
        objective=objective, updated_at="2026-09-15T00:00:00Z",
        goal_doc=None, execution_profile=None,
    ))
    code, delivered = _run(registry, before["provider_revision"], "--execute")
    assert code == 0 and delivered["status"] == "delivered", delivered
    assert active_state_section_text(state.read_text(), "Objective") == " ".join(objective.split())
    assert _read(runtime) == before
    state.write_text("<!-- unreadable display")
    code, result = _cli(registry, "list", "--goal-id", "goal-a")
    assert code == 0 and result["todos"][0]["todo_id"] == "todo_active", result
    assert _read(runtime) == before
