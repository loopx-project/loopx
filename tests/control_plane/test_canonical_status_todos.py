"""Status reads the real promoted provider, not its Markdown display copy."""
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.status import active_state_todo_fields


@pytest.fixture(params=["legacy", "native"])
def promoted_goal(tmp_path: Path, request):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "# Goal\n\n## Next Action\n\nKeep the human narrative.\n\n"
        "## Agent Todo\n\n- [ ] Stale display task\n"
        "  <!-- loopx:todo todo_id=todo_stale status=open -->\n\n"
        "## User Todo / Owner Review Reading Queue\n\n- [x] Stale approval\n"
        "  <!-- loopx:todo todo_id=todo_gate status=done task_class=user_gate "
        "global_gate=true decision_scope=write_scope:goal:release decision_outcome=approve -->\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    goal = {
        "id": "goal-a", "repo": str(tmp_path), "state_file": str(state),
        "domain": "software", "adapter": {"kind": "read_only_project_map_v0"},
    }
    records = [{
        "schema_version": "todo_item_v0", "todo_id": "todo_canonical",
        "index": 1, "role": "agent", "status": "open", "done": False,
        "text": "Canonical work", "priority": "P1", "archive_state": "active",
        "source_section": "Agent Todo", "claimed_by": "agent-a",
    }, {
        "schema_version": "todo_item_v0", "todo_id": "todo_gate",
        "index": 2, "role": "user", "status": "done", "done": True,
        "text": "Canonical rejection", "priority": "P1", "archive_state": "active",
        "source_section": "User Todo / Owner Review Reading Queue",
        "task_class": "user_gate", "global_gate": True,
        "decision_scope": {
            "schema_version": "decision_scope_v0", "kind": "write_scope",
            "granularity": "goal", "scope_key": "release",
        },
        "decision_outcome": "reject",
    }]
    projection = build_todo_runtime_shadow_projection(goal_id=goal["id"], todos=records)
    if request.param == "native":
        for record in projection["todos"]:
            record["schema_version"] = "todo_domain_record_v0"
            record.pop("index")
            record.pop("source_section")
        projection["todo_read_model"] = {
            "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
            "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
            "todo_count": len(projection["todos"]),
            "records_sha256": hashlib.sha256(canonical_bytes(projection["todos"])).hexdigest(),
        }
    initialize_canonical_authority(runtime, goal["id"], projection, state_path=state)
    return goal, runtime, state


@pytest.mark.parametrize("display", ["stale", "missing", "invalid_utf8"])
def test_status_uses_provider_and_allows_native_monitor_writeback_without_display(promoted_goal, display, monkeypatch):
    goal, runtime, state = promoted_goal
    if display == "invalid_utf8":
        state.write_bytes(b"\xff")
    original = state.read_bytes()
    if display == "missing":
        state.unlink()
    for name in ("parse_active_state_todos",):
        monkeypatch.setattr(
            f"loopx.status.{name}",
            lambda *_args, **_kwargs: pytest.fail("promoted read must not parse legacy Todos"),
        )
    fields = active_state_todo_fields(goal, runtime_root=runtime)
    items = fields["agent_todos"]["items"]
    assert [item["todo_id"] for item in items] == ["todo_canonical"]
    assert items[0]["claimed_by"] == "agent-a"
    assert fields["standing_decision_authority"]["active_count"] == 0
    assert fields["standing_decision_authority"]["entries"][0]["outcome"] == "reject"
    assert "monitor_writeback" not in fields["agent_todos"]  # Native observation owns writeback.
    if display == "missing":
        assert not state.exists()  # A read must not recreate the display.
    else:
        assert state.read_bytes() == original
        if display == "stale":
            assert fields["active_state_next_action"] == "Keep the human narrative."


def test_unavailable_provider_does_not_restore_stale_display_tasks(promoted_goal):
    goal, runtime, state = promoted_goal
    (runtime / "authority" / "file-v0").rename(runtime / "unavailable-provider")
    with pytest.raises(LocalCoordinationAuthorityUnavailable):
        active_state_todo_fields(goal, runtime_root=runtime)
    assert "todo_stale" in state.read_text()


def test_empty_canonical_collection_is_an_explicit_empty_read_model(tmp_path):
    state = tmp_path / "state.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n- [ ] Stale work\n")
    runtime = tmp_path / "runtime"
    goal = {"id": "goal-a", "repo": str(tmp_path), "state_file": str(state)}
    initialize_canonical_authority(
        runtime, goal["id"],
        build_todo_runtime_shadow_projection(goal_id=goal["id"], todos=[]),
        state_path=state,
    )
    fields = active_state_todo_fields(goal, runtime_root=runtime)
    for role in ("user", "agent"):
        assert fields[f"{role}_todos"]["items"] == []
        assert fields[f"{role}_todos"]["total_count"] == 0


def test_unpromoted_status_retains_markdown_without_starting_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        lambda *_args, **_kwargs: pytest.fail("unpromoted reads must not start TS authority"),
    )
    state = tmp_path / "state.md"
    goal = {"id": "goal-a", "repo": str(tmp_path), "state_file": str(state)}
    assert active_state_todo_fields(goal, runtime_root=tmp_path / "runtime") == {}
    state.write_text("# Goal\n\n## Agent Todo\n\n- [ ] Legacy work\n")
    fields = active_state_todo_fields(goal, runtime_root=tmp_path / "runtime")
    assert fields["agent_todos"]["items"][0]["text"] == "Legacy work"


def test_public_status_cli_reads_canonical_attention_without_markdown(promoted_goal, tmp_path):
    goal, runtime, state = promoted_goal
    state.unlink()
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(runtime), "goals": [goal],
    }))
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
         "--format", "json", "status", "--goal-id", goal["id"]],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "todo_canonical" in json.dumps(payload["attention_queue"])
    assert "todo_stale" not in json.dumps(payload["attention_queue"])
    assert not state.exists()
