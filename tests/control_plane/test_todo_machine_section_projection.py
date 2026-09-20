from __future__ import annotations

import stat
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from loopx.control_plane.todos.machine_section_projection import (
    TodoSectionProjectionError,
    inspect_todo_section_projection,
    render_canonical_todo_sections,
)
from loopx.cli import build_parser
from loopx.cli_commands import todo as todo_command
from loopx.control_plane.todos import provider_projection, active_state_editing
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from canonical_authority_fixture import initialize_canonical_authority


SOURCE = """---
status: active
---

# Goal

Human rationale stays here.

## User Todo / Owner Review Reading Queue

- [ ] stale user text
  <!-- loopx:todo todo_id=todo_stale status=open -->

## Notes

Do not rewrite this paragraph.

## Agent Todo

- [ ] stale agent text
  <!-- loopx:todo todo_id=todo_old status=open -->

## Next Action

- Continue the approved migration.
"""


def _records() -> list[dict[str, object]]:
    return [
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_agent",
            "role": "agent",
            "status": "open",
            "done": False,
            "text": "[P0] Move one complete transaction.",
            "archive_state": "active",
            "source_section": "Agent Todo",
            "index": 1,
            "priority": "P0",
            "title": "Move one complete transaction.",
            "task_class": "advancement_task",
            "action_kind": "migrate_transaction",
            "required_capabilities": ["filesystem_write"],
            "claimed_by": "codex-worker",
            "updated_at": "2026-09-05T00:00:00Z",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_user",
            "role": "user",
            "status": "blocked",
            "done": False,
            "text": "Approve the bounded cutover.",
            "archive_state": "active",
            "source_section": "User Todo / Owner Review Reading Queue",
            "index": 1,
            "task_class": "user_gate",
            "decision_scope": {
                "schema_version": "decision_scope_v0",
                "kind": "direction",
                "granularity": "action",
                "scope_key": "authority_cutover",
            },
            "global_gate": True,
        },
    ]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_projection_replaces_only_machine_sections_and_is_idempotent(newline: str) -> None:
    projected = render_canonical_todo_sections(
        SOURCE.replace("\n", newline),
        _records(),
        provider_revision="sha256:abc123",
    )

    assert projected.changed is True
    assert "Human rationale stays here." in projected.markdown
    assert "Do not rewrite this paragraph." in projected.markdown
    assert "Continue the approved migration." in projected.markdown
    assert "stale user text" not in projected.markdown
    assert "todo_agent" in projected.markdown
    assert "todo_user" in projected.markdown
    markers = inspect_todo_section_projection(projected.markdown)
    assert markers["section_count"] == 2
    assert {item["revision"] for item in markers["sections"]} == {"sha256:abc123"}

    replay = render_canonical_todo_sections(
        projected.markdown,
        _records(),
        provider_revision="sha256:abc123",
    )
    assert replay.changed is False
    assert replay.markdown == projected.markdown
    assert replay.rendered_sha256 == projected.rendered_sha256


def test_projection_creates_missing_machine_owned_role_section() -> None:
    source = "# Goal\n\nHuman introduction.\n\n## Agent Todo\n\n- [ ] old\n\n## Next Action\n\n- Continue.\n"
    projected = render_canonical_todo_sections(
        source,
        [_records()[0]],
        provider_revision="rev-9",
    )
    assert projected.changed is True
    assert "## User Todo / Owner Review Reading Queue" in projected.markdown
    assert "Human introduction." in projected.markdown
    assert "## Next Action\n\n- Continue." in projected.markdown
    assert {item["role"] for item in inspect_todo_section_projection(
        projected.markdown
    )["sections"]} == {"user", "agent"}
    replay = render_canonical_todo_sections(
        projected.markdown,
        [_records()[0]],
        provider_revision="rev-9",
    )
    assert replay.changed is False
    assert replay.markdown == projected.markdown


def test_projection_assigns_display_only_provenance_to_native_records() -> None:
    records = _records()
    for record in records:
        record["schema_version"] = "todo_domain_record_v0"
        del record["source_section"]
        del record["index"]
    projected = render_canonical_todo_sections(
        SOURCE,
        records,
        provider_revision="rev-native",
    )
    assert projected.changed is True
    assert projected.todo_count == 2
    assert {item["revision"] for item in inspect_todo_section_projection(
        projected.markdown
    )["sections"]} == {"rev-native"}
    assert all("source_section" not in record and "index" not in record for record in records)


@pytest.mark.parametrize("schema", ["todo_item_v0", "todo_domain_record_v0"])
def test_priority_text_has_a_lossless_display_without_persisted_derived_fields(schema):
    record = deepcopy(_records()[0])
    record.update(schema_version=schema, text="[P2] Observe a public target.")
    record.pop("priority", None)
    record.pop("title", None)
    if schema == "todo_domain_record_v0":
        record.pop("source_section", None)
        record.pop("index", None)
    original = deepcopy(record)
    result = render_canonical_todo_sections(SOURCE, [record], provider_revision="priority-display")
    assert "[P2] Observe a public target." in result.markdown
    assert record == original
    assert not render_canonical_todo_sections(result.markdown, [record], provider_revision="priority-display").changed
    with pytest.raises(ValueError, match="parity mismatch"):
        render_canonical_todo_sections(SOURCE, [{**record, "priority": "P0"}], provider_revision="priority-conflict")


def test_projection_rejects_unknown_fields_but_allows_known_read_model_fields() -> None:
    unknown = deepcopy(_records())
    unknown[0]["future_field"] = "must-not-disappear"
    with pytest.raises(ValueError, match="unversioned fields: future_field"):
        render_canonical_todo_sections(SOURCE, unknown, provider_revision="rev-1")

    derived = deepcopy(_records())
    derived[0]["resume_ready"] = True
    projected = render_canonical_todo_sections(
        SOURCE,
        derived,
        provider_revision="rev-1",
    )
    assert projected.changed is True
    assert "resume_ready" not in projected.markdown


def test_projection_renders_native_archive_with_role_and_replays() -> None:
    source = SOURCE + "\n## Completed Work Archive\n\n- [x] stale archive\n"
    records = _records()
    for record in records:
        record["schema_version"] = "todo_domain_record_v0"
        record.pop("source_section")
        record.pop("index")
    records.append(
        {
            "schema_version": "todo_domain_record_v0",
            "todo_id": "todo_archived",
            "role": "agent",
            "status": "done",
            "done": True,
            "text": "Completed provider-owned work.",
            "archive_state": "archive",
            "task_class": "advancement_task",
            "created_by": "codex-worker",
            "last_actor_agent_id": "codex-worker",
        }
    )

    projected = render_canonical_todo_sections(
        source,
        records,
        provider_revision="rev-archive",
    )

    assert "stale archive" not in projected.markdown
    assert "Completed provider-owned work." in projected.markdown
    assert "role=agent" in projected.markdown
    markers = inspect_todo_section_projection(projected.markdown)
    assert {item["role"] for item in markers["sections"]} == {
        "user",
        "agent",
        "archive",
    }
    replay = render_canonical_todo_sections(
        projected.markdown,
        records,
        provider_revision="rev-archive",
    )
    assert replay.changed is False


def test_projection_creates_archive_region_for_archived_records() -> None:
    archived = {
        "schema_version": "todo_domain_record_v0",
        "todo_id": "todo_archived",
        "role": "agent",
        "status": "done",
        "done": True,
        "text": "Completed provider-owned work.",
        "archive_state": "archive",
    }
    projected = render_canonical_todo_sections(
        SOURCE,
        [archived],
        provider_revision="rev-archive",
    )
    assert "## Completed Work Archive" in projected.markdown
    assert "Completed provider-owned work." in projected.markdown
    assert {item["role"] for item in inspect_todo_section_projection(
        projected.markdown
    )["sections"]} == {"user", "agent", "archive"}
    replay = render_canonical_todo_sections(
        projected.markdown,
        [archived],
        provider_revision="rev-archive",
    )
    assert replay.changed is False


@pytest.mark.parametrize("corrupt_scope", [False, True])
def test_optional_scope_version_is_display_only_not_permission_to_change_scope(monkeypatch, corrupt_scope):
    from loopx.control_plane.todos import machine_section_projection as module

    records = _records()
    records[1]["decision_scope"].pop("schema_version")
    before = deepcopy(records)
    original = module._render_record

    def render(record, **kwargs):
        if corrupt_scope and record.get("decision_scope"):
            record = {**record, "decision_scope": {**record["decision_scope"], "scope_key": "different_scope"}}
        return original(record, **kwargs)

    monkeypatch.setattr(module, "_render_record", render)
    if corrupt_scope:
        with pytest.raises(TodoSectionProjectionError, match="parity mismatch"):
            render_canonical_todo_sections(SOURCE, records, provider_revision="scope-version")
    else:
        rendered = render_canonical_todo_sections(SOURCE, records, provider_revision="scope-version")
        assert "direction:action:authority_cutover" in rendered.markdown
    assert records == before


@pytest.mark.parametrize("schema_version", ["decision_scope_v1", "", None, 7])
def test_projection_rejects_explicit_invalid_scope_version_without_mutation(
    schema_version: object,
) -> None:
    records = _records()
    records[1]["decision_scope"]["schema_version"] = schema_version
    before = deepcopy(records)

    with pytest.raises(
        TodoSectionProjectionError,
        match=r"decision_scope\.schema_version must be 'decision_scope_v0'",
    ):
        render_canonical_todo_sections(
            SOURCE,
            records,
            provider_revision="scope-version",
        )

    assert records == before


@pytest.mark.parametrize(
    ("decision_id", "message"),
    [
        ("todo_bad!", "must be a public-safe Todo id"),
        ("", "must be a public-safe Todo id"),
        (None, "must be a public-safe Todo id"),
        (7, "must be a public-safe Todo id"),
        ("todo_decision", "cannot be represented by the current Markdown projection"),
    ],
)
def test_projection_rejects_unrepresentable_decision_id_without_mutation(
    decision_id: object,
    message: str,
) -> None:
    records = _records()
    records[1]["decision_scope"]["decision_id"] = decision_id
    before = deepcopy(records)

    with pytest.raises(TodoSectionProjectionError, match=message):
        render_canonical_todo_sections(
            SOURCE,
            records,
            provider_revision="scope-decision-id",
        )

    assert records == before


def test_projection_rejects_non_object_or_unknown_scope_shape() -> None:
    for value, message in (
        ("direction:action:authority_cutover", "must be an object"),
        (
            {**_records()[1]["decision_scope"], "future_field": "value"},
            "unsupported fields",
        ),
    ):
        records = _records()
        records[1]["decision_scope"] = value
        before = deepcopy(records)

        with pytest.raises(TodoSectionProjectionError, match=message):
            render_canonical_todo_sections(
                SOURCE,
                records,
                provider_revision="scope-shape",
            )

        assert records == before


def test_projection_rejects_duplicate_sections_and_unsafe_revision() -> None:
    duplicate = SOURCE + "\n## Agent Todo\n\n- [ ] duplicate\n"
    with pytest.raises(TodoSectionProjectionError, match="multiple agent"):
        render_canonical_todo_sections(duplicate, _records(), provider_revision="rev-1")
    with pytest.raises(TodoSectionProjectionError, match="public-safe token"):
        render_canonical_todo_sections(SOURCE, _records(), provider_revision="rev 1")


def test_project_markdown_cli_requires_promoted_exact_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(SOURCE, encoding="utf-8")
    parser = build_parser()

    def run(
        extra: list[str], payload: dict[str, object] | None
    ) -> tuple[int, dict[str, object]]:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            todo_command,
            "load_registry",
            lambda _path: {"common_runtime_root": str(tmp_path / "runtime")},
        )
        monkeypatch.setattr(
            provider_projection,
            "read_canonical_todos_if_promoted",
            lambda **_kwargs: payload,
        )
        monkeypatch.setattr(
            provider_projection,
            "resolve_goal_state",
            lambda **_kwargs: (object(), tmp_path, state_path),
        )
        result = todo_command.handle_todo_command(
            parser.parse_args(
                [
                    "todo",
                    "project-markdown",
                    "--goal-id",
                    "goal-a",
                    "--provider-revision",
                    "rev-1",
                    *extra,
                ]
            ),
            registry_path=tmp_path / "registry.json",
            runtime_root_arg=None,
            print_payload=lambda value, *_args: captured.update(value),
            append_cli_rollout_event=lambda *_args, **_kwargs: None,
        )
        return result, captured

    failed, failure = run([], None)
    assert failed == 1
    assert "requires promoted canonical authority" in str(failure["error"])
    assert state_path.read_text(encoding="utf-8") == SOURCE

    canonical_payload = {
        "todos": _records(),
        "source_authority": "file_v0",
        "provider_revision": "rev-1",
        "cursor": "7",
    }
    preview_result, preview = run([], canonical_payload)
    assert preview_result == 0
    assert preview["dry_run"] is True
    assert preview["parse_render_parity"] is True
    assert state_path.read_text(encoding="utf-8") == SOURCE

    mismatch_result, mismatch = run(
        [], {**canonical_payload, "provider_revision": "rev-2"}
    )
    assert mismatch_result == 1
    assert "does not match" in str(mismatch["error"])
    assert state_path.read_text(encoding="utf-8") == SOURCE

    write_result, written = run(["--execute"], canonical_payload)
    assert write_result == 0
    assert written["executed"] is True
    assert written["narrative_preserved"] is True
    assert "todo_agent" in state_path.read_text(encoding="utf-8")


def test_project_markdown_cli_uses_raw_provider_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(SOURCE, encoding="utf-8")
    raw_records = _records()
    monkeypatch.setattr(
        todo_command,
        "load_registry",
        lambda _path: {"common_runtime_root": str(tmp_path / "runtime")},
    )
    monkeypatch.setattr(
        provider_projection,
        "read_canonical_todos_if_promoted",
        lambda **_kwargs: {
            "todos": raw_records,
            "source_authority": "file_v0",
            "provider_revision": "rev-1",
        },
    )
    monkeypatch.setattr(
        todo_command,
        "list_goal_todos",
        lambda **_kwargs: pytest.fail("projection must not consume the enriched list view"),
    )
    monkeypatch.setattr(
        provider_projection,
        "resolve_goal_state",
        lambda **_kwargs: (object(), tmp_path, state_path),
    )
    captured: dict[str, object] = {}

    result = todo_command.handle_todo_command(
        build_parser().parse_args(
            [
                "todo",
                "project-markdown",
                "--goal-id",
                "goal-a",
                "--provider-revision",
                "rev-1",
            ]
        ),
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        print_payload=lambda value, *_args: captured.update(value),
        append_cli_rollout_event=lambda *_args, **_kwargs: None,
    )

    assert result == 0
    assert captured["todo_count"] == len(raw_records)


def test_project_markdown_cli_publishes_with_atomic_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(SOURCE, encoding="utf-8")
    state_path.chmod(0o640)
    original_mode = stat.S_IMODE(state_path.stat().st_mode)
    parent_syncs: list[object] = []
    monkeypatch.setattr(
        active_state_editing,
        "fsync_state_directory",
        lambda path: parent_syncs.append(path),
    )
    monkeypatch.setattr(
        todo_command,
        "load_registry",
        lambda _path: {"common_runtime_root": str(tmp_path / "runtime")},
    )
    monkeypatch.setattr(
        provider_projection,
        "read_canonical_todos_if_promoted",
        lambda **_kwargs: {
            "todos": _records(),
            "source_authority": "file_v0",
            "provider_revision": "rev-1",
        },
    )
    monkeypatch.setattr(
        provider_projection,
        "resolve_goal_state",
        lambda **_kwargs: (object(), tmp_path, state_path),
    )
    replacements: list[tuple[object, object]] = []
    real_replace = active_state_editing.os.replace

    def record_replace(source, target) -> None:
        if Path(target) == state_path:
            replacements.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr(active_state_editing.os, "replace", record_replace)

    result = todo_command.handle_todo_command(
        build_parser().parse_args(
            [
                "todo",
                "project-markdown",
                "--goal-id",
                "goal-a",
                "--provider-revision",
                "rev-1",
                "--execute",
            ]
        ),
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        print_payload=lambda *_args: None,
        append_cli_rollout_event=lambda *_args, **_kwargs: None,
    )

    assert result == 0
    assert len(replacements) == 1
    temporary, target = replacements[0]
    assert target == state_path
    assert temporary != target
    assert "todo_agent" in state_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(state_path.stat().st_mode) == original_mode
    assert parent_syncs == [state_path]


@pytest.mark.parametrize("operation", ["chmod", "fsync", "replace"])
def test_atomic_projection_failure_preserves_original(monkeypatch, tmp_path, operation):
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_bytes(b"original\r\n")
    opened = []
    real_fdopen = active_state_editing.os.fdopen

    def capture_handle(*args, **kwargs):
        handle = real_fdopen(*args, **kwargs)
        opened.append(handle)
        return handle

    def fail(*_args, **_kwargs):
        raise OSError("injected pre-publication failure")

    monkeypatch.setattr(active_state_editing.os, "fdopen", capture_handle)
    monkeypatch.setattr(active_state_editing.os, operation, fail)
    with pytest.raises(OSError, match="injected pre-publication failure"):
        active_state_editing.atomic_write_state_text(state_path, "replacement\n")
    assert state_path.read_bytes() == b"original\r\n"
    assert opened and all(handle.closed for handle in opened)
    assert list(tmp_path.iterdir()) == [state_path]


@pytest.mark.parametrize(
    "heading",
    ["## Next Action", "# Next Action", "Next Action\n===========",
     "Next Action\n-----------", "   # Next Action", "  ## Next Action"],
)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_project_markdown_cli_preserves_narrative_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    heading: str,
    newline: str,
) -> None:
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    source_bytes = SOURCE.replace("## Next Action", heading).replace("\n", newline).encode("utf-8")
    state_path.write_bytes(source_bytes)
    monkeypatch.setattr(
        todo_command,
        "load_registry",
        lambda _path: {"common_runtime_root": str(tmp_path / "runtime")},
    )
    monkeypatch.setattr(
        provider_projection,
        "read_canonical_todos_if_promoted",
        lambda **_kwargs: {
            "todos": _records(),
            "source_authority": "file_v0",
            "provider_revision": "rev-1",
        },
    )
    monkeypatch.setattr(
        provider_projection,
        "resolve_goal_state",
        lambda **_kwargs: (object(), tmp_path, state_path),
    )

    captured: dict[str, object] = {}
    result = todo_command.handle_todo_command(
        build_parser().parse_args(
            [
                "todo",
                "project-markdown",
                "--goal-id",
                "goal-a",
                "--provider-revision",
                "rev-1",
                "--execute",
            ]
        ),
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        print_payload=lambda value, *_args: captured.update(value),
        append_cli_rollout_event=lambda *_args, **_kwargs: None,
    )

    projected = state_path.read_bytes()
    assert result == 0
    assert captured["narrative_preserved"] is True
    assert "Human rationale stays here.".encode() in projected
    assert "Do not rewrite this paragraph.".encode() in projected
    assert (heading.replace("\n", newline) + newline + newline + "- Continue the approved migration." + newline).encode() in projected
    if newline == "\r\n":
        assert b"\n" not in projected.replace(b"\r\n", b"")


def test_real_promoted_provider_to_cli_projection(tmp_path: Path) -> None:
    """Real file authority, TS bridge, CLI process and publication; no live Goal."""
    runtime = tmp_path / "runtime"
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    source = SOURCE.replace("\n", "\r\n").encode()
    state.write_bytes(source)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "common_runtime_root": str(runtime),
        "goals": [{"id": "goal-a", "status": "active", "repo": str(tmp_path),
                   "state_file": state.name}],
    }))

    def run(revision: str, *extra: str) -> tuple[int, dict]:
        process = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", "todo",
             "project-markdown", "--goal-id", "goal-a", "--provider-revision",
             revision, *extra],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return process.returncode, json.loads(process.stdout)

    code, result = run("unpromoted", "--execute")
    assert code == 1
    assert "requires promoted canonical authority" in result["error"]
    assert state.read_bytes() == source

    projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=_records())
    initialize_canonical_authority(runtime, "goal-a", projection, state_path=state)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    revision = before["provider_revision"]
    code, preview = run(revision)
    assert code == 0 and preview["dry_run"] is True
    assert state.read_bytes() == source
    code, mismatch = run("stale-revision", "--execute")
    assert code == 1 and "does not match" in mismatch["error"]
    assert state.read_bytes() == source

    for heading in ("# Next Action", "Next Action\n===========", "Next Action\n-----------"):
        variant = SOURCE.replace("## Next Action", heading).replace("\n", "\r\n").encode()
        state.write_bytes(variant)
        code, preserved = run(revision, "--execute")
        assert code == 0 and preserved["narrative_preserved"] is True
        suffix = (heading + "\n\n- Continue the approved migration.\n").replace("\n", "\r\n").encode()
        assert state.read_bytes().endswith(suffix)

    state.write_bytes(source)
    code, written = run(revision, "--execute")
    assert code == 0 and written["changed"] is True
    published = state.read_bytes()
    assert b"todo_agent" in published and b"todo_user" in published
    assert published.endswith(b"## Next Action\r\n\r\n- Continue the approved migration.\r\n")
    assert b"\n" not in published.replace(b"\r\n", b"")
    code, replay = run(revision, "--execute")
    assert code == 0 and replay["changed"] is False
    assert state.read_bytes() == published
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
