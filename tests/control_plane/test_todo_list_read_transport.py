"""Fresh CLI reads retain provider, filter, error and presentation semantics."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

from loopx.chat.manager_details import read_manager_goal_details
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.list_readback import list_goal_todos

REPO = Path(__file__).resolve().parents[2]
GOAL = "read-transport"
OWN = "todo_owned"


@pytest.fixture(params=["file", "sqlite"])
def read_cli(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project = tmp_path / "project with spaces"
    project.mkdir()
    state, runtime, registry = project / "state.md", tmp_path / "runtime", tmp_path / "registry.json"
    rows = [
        {"todo_id": OWN, "text": "Owned full requirement", "claimed_by": "agent-a"},
        {"todo_id": "todo_unclaimed", "text": "Unclaimed full requirement"},
        {"todo_id": "todo_foreign", "text": "Foreign full requirement", "claimed_by": "agent-b"},
    ]
    state.write_text("# Goal\n\n## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n\n" + "".join(
        f"- [ ] {row['text']}\n  <!-- loopx:todo todo_id={row['todo_id']} status=open "
        f"task_class=advancement_task claimed_by={row.get('claimed_by', '')} -->\n"
        for row in rows
    ) + "\n## Completed Work Archive\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(project), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    if request.param != "unmigrated":
        todos = [{**row, "schema_version": "todo_item_v0", "role": "agent", "status": "open",
                  "done": False, "task_class": "advancement_task", "archive_state": "active",
                  "source_section": "Agent Todo", "index": i} for i, row in enumerate(rows, 1)]
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL, todos=todos, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=request.param)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime)]

    def run(*tail, format_name="json", expected_exit=0, isolated=False):
        args = [*prefix, "--format", format_name, "todo", "list", "--goal-id", GOAL, *tail]
        if isolated:
            script = (
                "import sys;from loopx.entrypoint import main;code=main(sys.argv[1:]);"
                "assert code==0;"
                "assert 'loopx.control_plane.todos.mutation_api' not in sys.modules;"
                "assert 'loopx.history' not in sys.modules;"
                "assert 'loopx.state_refresh' not in sys.modules;"
                "assert 'loopx.cli_commands.todo' not in sys.modules;"
                "assert 'loopx.capabilities.periodic_report.post_writeback_hook' not in sys.modules"
            )
            command = [sys.executable, "-c", script, *args]
        else:
            command = [sys.executable, "-m", "loopx.cli", *args]
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=45,
                                env={**os.environ, "PYTHONPATH": str(REPO)}, check=False)
        assert result.returncode == expected_exit, result.stdout + result.stderr
        return json.loads(result.stdout) if format_name == "json" else result.stdout

    yield request.param, run, prefix, state
    subprocess.run(
        [sys.executable, "-c", ("from loopx.control_plane.effect_runtime import effect_runtime_result;"
         "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)")],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=True,
    )


@pytest.mark.parametrize("read_cli", ["unmigrated"], indirect=True)
def test_unmigrated_query_variants_refuse_without_changing_source(read_cli):
    _, run, _, state = read_cli
    before = state.read_bytes()
    for options in [(), ("--todo-id", OWN), ("--limit", "1"), ("--thin",), ("--summary",)]:
        result = run(*options, expected_exit=1)
        assert result["error_code"] == "canonical_authority_required"
        assert result["legacy_fallback_used"] is False
        assert result["write_admitted"] is False
    assert state.read_bytes() == before


def test_read_filters_errors_and_fresh_provider(read_cli):
    provider, run, prefix, state = read_cli
    state_before = state.read_bytes()
    full = run()
    assert full["ok"] and full["read_only"] and full["dry_run"]
    assert full["authority_read"]["source_authority"] == f"{provider}_v0"
    assert {item["todo_id"] for item in full["todos"]} == {OWN, "todo_unclaimed", "todo_foreign"}
    lane = run("--agent-id", "agent-a")
    assert {item["todo_id"] for item in lane["todos"]} == {OWN, "todo_unclaimed"}
    assert len(run("--thin")["todos"]) == 2
    assert len(run("--limit", "1")["todos"]) == 1
    exact = run("--todo-id", OWN)
    assert exact["todo"]["text"] == "Owned full requirement"
    assert exact["todo_detail_projection"]["source_complete"]
    assert "Owned full requirement" in run("--todo-id", OWN, format_name="markdown")
    assert run("--status", "done")["todo_count"] == 0
    assert run("--role", "user")["todo_count"] == 0
    for tail, error in [
        (["--limit", "0"], "todo list --limit must be at least 1"),
        (["--todo-id", OWN, "--thin"], "Exact Todo reads return full requirements; remove --thin or omit --todo-id for a bounded list"),
        (["--turn-instance-id", "unrelated-turn"], "--turn-instance-id is supported only by todo complete/supersede settlement"),
    ]:
        assert run(*tail, expected_exit=1)["error"] == error
    assert "unsupported: --text" in run("--text", "mutation", expected_exit=1)["error"]
    assert state.read_bytes() == state_before
    after = run()
    assert after["todos"] == full["todos"]
    assert after.get("authority_read") == full.get("authority_read")
    added = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *prefix, "--format", "json", "todo", "add",
         "--goal-id", GOAL, "--role", "agent", "--text", "Fresh canonical requirement",
         "--claimed-by", "agent-a"], cwd=REPO, capture_output=True, text=True, timeout=45, check=False,
    )
    assert added.returncode == 0, added.stdout + added.stderr
    fresh = run()
    assert fresh["todo_count"] == 4
    current = next(item for item in fresh["todos"] if item["todo_id"] == json.loads(added.stdout)["todo_id"])
    assert (current["text"], current["priority"]) == ("[P1] Fresh canonical requirement", "P1")
    assert fresh["authority_read"]["provider_revision"] != full["authority_read"]["provider_revision"]
    assert fresh["authority_read"]["decision_read_from_provider"]
    assert fresh["authority_read"]["legacy_fallback_used"] is False


def test_display_limits_report_complete_sources_and_actual_shown_items(read_cli):
    _, run, _, _ = read_cli
    for options, available in [((), 3), (("--agent-id", "agent-a"), 2),
                               (("--limit", "1"), 3), (("--thin",), 3)]:
        payload = run(*options)
        envelope = payload["projection_envelope"]
        coverage = envelope["coverage"]
        assert envelope["alert"] is False
        assert coverage["complete"] is True and coverage["omitted"] == []
        assert coverage["expected_count"] == coverage["included_count"] == available
        assert coverage["available_count"] == available
        assert coverage["shown_count"] == len(payload["todos"])
        assert coverage["truncated"] == (len(payload["todos"]) < available)


def test_read_lenses_report_the_actual_canonical_source(read_cli):
    provider, run, _, state = read_cli
    before = state.read_bytes()
    expected = f"{provider}_v0"
    for options in [(), ("--agent-id", "agent-a"), ("--limit", "1"),
                    ("--thin",), ("--summary",), ("--todo-id", OWN),
                    ("--todo-id", "todo_missing"), ("--status", "done")]:
        payload = run(*options)
        assert payload["source"] == expected
        assert payload["authority_read"]["source_authority"] == expected
        assert [source["source_id"] for source in payload["projection_envelope"]["sources"]] == [expected]
    assert f"- source: `{expected}`" in run(format_name="markdown")
    assert state.read_bytes() == before


def test_read_entrypoint_does_not_load_mutation_owners(read_cli):
    _, run, _, _ = read_cli
    assert run(isolated=True)["todo_count"] == 3
    thin = run("--thin", isolated=True)
    assert thin["todo_count"] == 3  # Source count is retained by the bounded view.
    assert len(thin["todos"]) == 2
    assert run("--todo-id", OWN, isolated=True)["todo"]["todo_id"] == OWN


def test_read_source_failure_and_recovery_do_not_reuse_a_snapshot(read_cli):
    provider, run, prefix, state = read_cli
    before = run()
    state_bytes = state.read_bytes()
    runtime = Path(prefix[prefix.index("--runtime-root") + 1])
    authority = runtime / "authority" / f"{provider}-v0"
    unavailable = runtime / "unavailable-provider"
    authority.rename(unavailable)
    try:
        failed = run(expected_exit=1)
        assert failed["ok"] is False and failed["error"]
        assert failed["error_code"]
        assert failed["source_authority"] == f"{provider}_v0"
        assert failed["legacy_fallback_used"] is False
        assert state.read_bytes() == state_bytes
    finally:
        unavailable.rename(authority)
    recovered = run()
    assert recovered["todos"] == before["todos"]
    assert recovered.get("authority_read") == before.get("authority_read")


def test_todo_help_does_not_load_mutation_owners():
    script = (
        "import sys;from loopx.entrypoint import main;"
        "\ntry: main(['todo','--help'])"
        "\nexcept SystemExit as exc: assert exc.code==0"
        "\nassert 'loopx.control_plane.todos.mutation_api' not in sys.modules"
        "\nassert 'loopx.state_refresh' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO,
                            capture_output=True, text=True, timeout=45, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


# Frozen from the public LoopX v1.3.1 wheel (tag 7445ae360aaf).
# Its canonical declaration has 70 fields and its domain form has 68.
_RELEASE_131_TODO_FIELDS = (
    "index done text schema_version todo_id role status priority title archive_state source_section "
    "task_class action_kind task_domain capability_binding_ref task_repository continuation_policy "
    "removed_continuation_policy required_write_scopes required_capabilities target_capabilities "
    "explore_result_node_refs decision_scope required_decision_scopes decision_outcome "
    "decision_scope_outcomes claimed_by created_by last_actor_agent_id bound_agent goal_bound "
    "blocks_agent excluded_agents global_gate unblocks_todo_id resume_when resume_monitor_generation "
    "resume_condition resume_ready no_followup successor_todo_ids completion_continuation "
    "completion_recovery completion_receipt_id replan_obligation_id target_key cadence next_due_at "
    "expires_at watch_only last_checked_at result_hash consecutive_no_change material_change "
    "material_change_generation max_no_change_before_replan monitor_effect_id note evidence reason "
    "completed_at completion_turn_key completion_result updated_at superseded_by "
    "completion_validation_required completion_validation_sha256 completion_validation_revision "
    "completion_validation_revision_history handoff_note"
).split()


# Synthetic wider retained declaration exercising the exact inspection opt-in.
_RETAINED_FIXTURE_FIELDS = (
    "index done text schema_version todo_id role status priority title archive_state "
    "source_section task_class action_kind task_domain capability_binding_ref "
    "task_repository pr_number pr_events continuation_policy removed_continuation_policy "
    "required_write_scopes required_capabilities needs target_capabilities "
    "explore_result_node_refs decision_scope required_decision_scopes decision_outcome "
    "decision_amendment_operation_id decision_scope_outcomes claimed_by created_by "
    "last_actor_agent_id bound_agent goal_bound blocks_agent excluded_agents global_gate "
    "unblocks_todo_id unblocks_todo_ids depends_on_todo_ids resume_when "
    "resume_monitor_generation resume_condition resume_ready no_followup successor_todo_ids "
    "completion_continuation completion_recovery completion_receipt_id retirement_receipt_id "
    "restored_from_todo_id restoration_receipt_id legacy_terminal_resolution replan_obligation_id "
    "target_key cadence next_due_at expires_at watch_only last_checked_at result_hash "
    "consecutive_no_change material_change material_change_generation max_no_change_before_replan "
    "monitor_effect_id note note_history review_round_history review_gate_paused review_epoch_id "
    "review_unresolved_findings next_action validation skip_reason evidence evidence_corrections "
    "latest_evidence reason completed_at completion_turn_key completion_result updated_at "
    "superseded_by completion_validation_required completion_validation_sha256 "
    "completion_validation_display completion_validation_revision "
    "completion_validation_revision_history handoff_note"
).split()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("record_kind", ["canonical", "domain"])
@pytest.mark.parametrize("released", [True, False], ids=["v1.3.1", "synthetic-retained"])
def test_list_preserves_retained_authority_without_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, record_kind: str, released: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    registry = tmp_path / "registry.json"
    state.write_text("# Synthetic historical Goal\n\n## Agent Todo\n\n- [ ] Display decoy\n")
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": [{"id": GOAL, "repo": str(tmp_path), "state_file": state.name,
                   "coordination": {"registered_agents": ["agent-a"]}}],
    }))
    records = [
        {
            "schema_version": "todo_item_v0", "todo_id": "todo_archive",
            "role": "agent", "status": "done", "done": True,
            "text": "[P2] Preserve the historical acceptance receipt.",
            "priority": "P2", "title": "Original archive title",
            "archive_state": "archive", "source_section": "Completed Work Archive", "index": 1,
            "completion_receipt_id": "synthetic-completion-receipt",
            "retirement_receipt_id": "synthetic-retirement-receipt",
            "evidence": "Synthetic acceptance evidence; never a live Goal.",
            "no_followup": True,
        },
        {
            "schema_version": "todo_item_v0", "todo_id": "todo_pending",
            "role": "agent", "status": "open", "done": False,
            "text": "[P0] Preserve dependencies and paused review before executing.",
            "priority": "P0", "title": "Original active title",
            "archive_state": "active", "source_section": "Agent Todo", "index": 1,
            "task_class": "advancement_task", "claimed_by": "agent-a",
            "depends_on_todo_ids": ["todo_archive"],
            "review_gate_paused": True, "review_epoch_id": "synthetic-review-epoch",
            "note": "Reading this requirement does not authorize execution.",
        },
    ]
    fields = list(_RELEASE_131_TODO_FIELDS if released else _RETAINED_FIXTURE_FIELDS)
    if released:
        records = [{key: value for key, value in row.items() if key in fields} for row in records]
    if record_kind == "domain":
        fields = [field for field in fields if field not in ("index", "source_section")]
        for record in records:
            record["schema_version"] = "todo_domain_record_v0"
            del record["index"]
            del record["source_section"]
    # Only encoding is shared with the persisted protocol, never expected output.
    records_digest = hashlib.sha256(json.dumps(
        records, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    initialized = initialize_canonical_authority(runtime, GOAL, {
        "schema_version": "loopx_coordination_runtime_shadow_projection_v0",
        "goal_id": GOAL, "handoff_mode": "soft_claim", "todos": records, "leases": [],
        "todo_read_model": {
            "schema_version": f"loopx_todo_{record_kind}_read_record_v0",
            "contract_fields": fields, "todo_count": 2, "records_sha256": records_digest,
        },
    }, state_path=state, provider=provider)

    def authority_contents() -> dict[str, bytes | tuple[str, ...]]:
        contents = {}
        for path in sorted(runtime.rglob("*")):
            if not path.is_file() or path.name.endswith(("-wal", "-shm")):
                continue
            data = path.read_bytes()
            if data.startswith(b"SQLite format 3\0"):
                # Compare every logical table, including receipts/events and head.
                # WAL checkpointing is storage maintenance, not authority mutation.
                connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                try:
                    contents[str(path.relative_to(runtime))] = tuple(connection.iterdump())
                finally:
                    connection.close()
            else:
                contents[str(path.relative_to(runtime))] = data
        return contents

    original_authority = authority_contents()
    original_state = state.read_bytes()

    def list_todos(todo_id: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", "todo", "list",
             "--goal-id", GOAL, "--todo-id", todo_id],
            cwd=REPO, capture_output=True, text=True, timeout=45,
            env={**os.environ, "PYTHONPATH": str(REPO)}, check=False,
        )
        # Check non-mutation even on refusal, before demanding compatibility.
        assert state.read_bytes() == original_state
        assert authority_contents() == original_authority
        assert result.returncode == 0, result.stdout + result.stderr
        listed = json.loads(result.stdout)
        assert listed["ok"] and listed["read_only"] and listed["dry_run"]
        assert listed["authority_read"]["source_authority"] == f"{provider}_v0"
        assert listed["authority_read"]["provider_revision"] == initialized["provider_revision"]
        assert listed["authority_read"]["legacy_fallback_used"] is False
        return listed

    try:
        for expected in records:
            exact = list_todos(expected["todo_id"])
            full_exact = list_goal_todos(
                registry_path=registry, runtime_root_arg=str(runtime),
                goal_id=GOAL, todo_id=expected["todo_id"], full_text=True,
            )
            assert full_exact["authority_read"] == exact["authority_read"]
            for listed in (exact, full_exact):
                assert listed["todo_detail_projection"]["source_complete"] is True
                for field, value in expected.items():
                    assert listed["todo"][field] == value, field

        searched = read_manager_goal_details(
            registry, runtime, GOAL, owner_scope=True, query="Original active title",
        )
        assert searched["status"] == "read"
        assert searched["authority_revision"] == initialized["provider_revision"]
        assert [row["todo_id"] for row in searched["todos"]] == ["todo_pending"]
        assert state.read_bytes() == original_state
        assert authority_contents() == original_authority
    finally:
        subprocess.run(
            [sys.executable, "-c",
             "from loopx.control_plane.effect_runtime import effect_runtime_result;"
             "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)"],
            cwd=REPO, capture_output=True, text=True, timeout=30, check=True,
        )
