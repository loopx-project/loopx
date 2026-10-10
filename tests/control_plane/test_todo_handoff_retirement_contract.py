"""Retirement must preserve handoff identity, source references and read authority.

These are compatibility characterizations, not a new handoff specification or
proof that the legacy writer can be deleted. The canonical CLI tests also run
with the old mutation modules physically absent.
"""
from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import promoted_create_fixture
from test_canonical_todo_writer_isolation import without_source_todo_writers  # noqa: F401
from loopx.control_plane.todos.handoff_note import (
    attach_todo_handoff_note,
    build_todo_handoff_note,
    compact_todo_continuation_hint,
)


def test_handoff_keeps_source_identity_and_does_not_invent_completion():
    source = {
        "todo_id": "todo_original", "role": "agent", "status": "open",
        "claimed_by": "Agent A", "text": "Read the complete requirement",
        "note": "Keep the unfinished validation", "evidence": "opaque evidence",
        "successor_todo_ids": ["todo_successor"],
        "resume_when": "todo_done:todo_dependency",
        "excluded_agents": ["agent-b"],
    }
    before = deepcopy(source)
    note = build_todo_handoff_note(source, goal_id="goal-a", source="canonical_provider")
    assert source == before
    assert note == {
        "schema_version": "handoff_note_v0", "handoff_id": "handoff_original",
        "todo_id": "todo_original", "goal_id": "goal-a", "to_agent": "agent-a",
        "intent": "continue", "summary": "Keep the unfinished validation",
        "evidence_refs": ["todo:todo_original:evidence", "todo:todo_original:note"],
        "blocked_on": "todo_done:todo_dependency",
        "suggested_next_action": "Read the complete requirement",
        "source": "canonical_provider", "successor_todo_ids": ["todo_successor"],
        "excluded_agents": ["agent-b"],
    }
    assert attach_todo_handoff_note(source) is source
    assert {key: source[key] for key in before} == before
    assert "status" not in source["handoff_note"]
    assert "resume_ready" not in source["handoff_note"]


def test_plain_work_has_no_handoff_and_nested_fields_remain_authoritative():
    assert build_todo_handoff_note({"todo_id": "todo_plain", "note": "continue"}) is None
    item = {"todo_id": "todo_original", "note": "top-level fallback", "claimed_by": "agent-a",
            "handoff": {"to_agent": "agent-b", "note": "", "reason": "nested reason"}}
    note = build_todo_handoff_note(item)
    assert note["to_agent"] == "agent-b"
    assert note["summary"] == "nested reason"
    assert compact_todo_continuation_hint(item) == "nested reason"


def test_legacy_handoff_identity_does_not_depend_on_continuation_edits():
    item = {"goal_id": "goal-a", "role": "agent", "index": 7, "text": "Legacy work",
            "handoff": {"note": "First continuation"}}
    first = build_todo_handoff_note(item, goal_id="goal-a")
    item["handoff"]["note"] = "Revised continuation"
    revised = build_todo_handoff_note(item, goal_id="goal-a")
    # Golden compatibility identity for the historical tuple encoding; do not
    # derive the expected ID from the function being characterized.
    assert first["handoff_id"] == revised["handoff_id"] == "handoff_7e32bf123341"
    assert first["summary"] != revised["summary"]


@pytest.mark.parametrize("label", ["ak", "sk", "api_key", "access_key_id", "secret_key", "token", "password"])
def test_credential_assignment_is_rejected_before_display_truncation(label):
    # Values are synthetic and deliberately short. Even when the assignment
    # follows the display cap, the entire candidate must be rejected.
    item = {"handoff": {"note": "x" * 400 + " " + label + "=" + "v"}}
    assert compact_todo_continuation_hint(item) is None
    assert "summary" not in build_todo_handoff_note(item)


def test_unicode_compaction_and_bounded_evidence_keep_existing_semantics():
    assert compact_todo_continuation_hint({"note": "a\u0085b\u001cc"}) == "a b c"
    assert compact_todo_continuation_hint({"note": "token budget for validation"}) == "token budget for validation"
    assert compact_todo_continuation_hint({"note": "😀" * 281}) == "😀" * 279 + "..."
    item = {"todo_id": "todo_original", "successor_todo_ids": ["todo_next"],
            "handoff": {"evidence_refs": ["ref:one", "ref:one", "ref:two", "ref:three", "ref:four"]},
            "evidence": "raw evidence is not copied", "note": "continuation",
            "latest_event_kind": "todo_update"}
    assert build_todo_handoff_note(item)["evidence_refs"] == [
        "ref:one", "ref:two", "ref:three", "ref:four", "todo:todo_original:evidence", "todo:todo_original:note"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.usefixtures("without_source_todo_writers")
def test_canonical_handoff_readback_without_legacy_writers(tmp_path, provider):
    registry, _runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    registry_payload = json.loads(registry.read_text())
    registry_payload["goals"][0].update({
        "domain": "handoff-compatibility", "status": "active",
        "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
    })
    registry.write_text(json.dumps(registry_payload))

    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
             *args], cwd=tmp_path, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    successor = cli("todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Independent successor",
                    "--operation-id", "handoff-successor")["todo_id"]
    original = cli("todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Original complete requirement",
                   "--operation-id", "handoff-original")["todo_id"]
    first = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original)
    cli("todo", "update", "--goal-id", "goal-a", "--todo-id", original, "--agent-id", "agent-a",
        "--note", "Continue the original validation", "--successor-todo-id", successor,
        "--update-operation-id", "handoff-continuation",
        "--update-expected-provider-revision", first["authority_read"]["provider_revision"])
    readback = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original)
    row = readback["todo"]
    assert row["text"] == "Original complete requirement"
    assert row["status"] == "open" and row["successor_todo_ids"] == [successor]
    # Exact canonical reads return the source record. Derived handoff context
    # belongs to status consumers and must never become a second stored source.
    assert "handoff_note" not in row
    indexed = next(item for item in cli("status", "--goal-id", "goal-a")["todo_index"]["items"]
                   if item["todo_id"] == original)
    assert indexed["handoff_note"]["handoff_id"] == "handoff_" + original.removeprefix("todo_")
    assert indexed["handoff_note"]["summary"] == "Continue the original validation"
    assert indexed["handoff_note"]["successor_todo_ids"] == [successor]
    # Read from a fresh CLI process with a missing display: canonical source and
    # original operation identities remain, and a read never recreates Markdown.
    state.unlink()
    restarted = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original)
    assert restarted["todo"] == row
    assert restarted["authority_read"]["provider_revision"] == readback["authority_read"]["provider_revision"]
    assert not state.exists()
