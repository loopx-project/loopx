"""Real provider and public-entrypoint checks; no live Goal or display repair."""
import json
import subprocess
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from test_goal_amendment_proposal import _write_fixture, _proposal, _admit, GOAL_ID
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
from loopx.control_plane.goals.shared_goal_alignment import project_shared_goal_alignment
from loopx.control_plane.goals import shared_goal_work_source
from loopx.control_plane.testing.canary_harness import run_json_cli_result


def _record(todo_id="todo_current", **fields):
    return {"schema_version": "todo_item_v0", "todo_id": todo_id, "role": "agent",
        "status": "open", "done": False, "text": "Canonical work", "task_class": "advancement_task",
        "archive_state": "active", "source_section": "Agent Todo", "index": 1, **fields}


def _canonical(tmp_path, records=None, *, events=None, leases=None, native=False):
    paths = _write_fixture(tmp_path)
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID,
        todos=[_record()] if records is None else records, leases=leases or [], handoff_mode="soft_claim")
    if native:
        from hashlib import sha256
        from loopx.control_plane.coordination.coordination_state_contract import (
            TODO_DOMAIN_RECORD_FIELDS, TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        )
        from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
        for record in projection["todos"]:
            record["schema_version"] = "todo_domain_record_v0"
            record.pop("index", None)
            record.pop("source_section", None)
        projection["todo_read_model"] = {"schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
            "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS), "todo_count": len(projection["todos"]),
            "records_sha256": sha256(canonical_bytes(projection["todos"])).hexdigest()}
    initialize_canonical_authority(paths["runtime"], GOAL_ID, projection, state_path=paths["state_file"])
    if events:
        paths["state_file"].with_name("events.jsonl").write_text("retired unrelated source\n")
    return paths


def _alignment(paths):
    return project_shared_goal_alignment(goal_id=GOAL_ID, agent_id="agent-a", project=paths["project"])


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("display", ["stale", "missing"])
def test_quota_diagnoses_exact_gate_conflict_from_complete_provider(tmp_path, native, display):
    scope = {"schema_version": "decision_scope_v0", "kind": "write_scope", "granularity": "action", "scope_key": "release"}
    records = [_record(f"todo_unrelated_{index}", claimed_by="agent-a") for index in range(40)]
    records.extend([
        _record("todo_required", claimed_by="agent-a", required_decision_scopes=[scope]),
        _record("todo_gate", role="user", source_section="User Todo", task_class="user_gate",
                blocks_agent="agent-a", decision_scope=scope, unblocks_todo_id="todo_unrelated_0"),
    ])
    paths = _canonical(tmp_path, records, native=native)
    if display == "missing":
        paths["state_file"].unlink()
    before = paths["state_file"].read_bytes() if paths["state_file"].exists() else None
    code, result = run_json_cli_result("quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", "agent-a", registry_path=paths["registry"])
    assert code == 0, result
    assert result["effective_action"] == "todo_decision_scope_projection_repair", result
    assert "required_decision_scope_target_mismatch" in json.dumps(result)
    assert (paths["state_file"].read_bytes() if paths["state_file"].exists() else None) == before


def _mutate_provider(paths):
    root = Path(__file__).resolve().parents[2]
    store = (root / "loopx/control_plane/coordination/file_authority_store.ts").as_uri()
    codec = (root / "loopx/control_plane/coordination/authority_store_codec.ts").as_uri()
    script = (f"import {{FileAuthorityStore}} from {json.dumps(store)};"
        f"import {{canonicalAuthoritySha256}} from {json.dumps(codec)};"
        "const s=new FileAuthorityStore(process.argv[1],process.argv[2]);const h=await s.loadAuthority();"
        "h.head.todos[0].text='Changed canonical work';"
        "h.head.todo_read_model.records_sha256=canonicalAuthoritySha256(h.head.todos);"
        "const r=await s.commitAuthority({expected_provider_revision:h.provider_revision,"
        "operation_id:'fixture-revision-change',events:[],receipts:[],next_projection:h.head});"
        "if(r.status!=='applied')throw Error(JSON.stringify(r));")
    subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script,
        str(paths["runtime"] / "authority/file-v0"), GOAL_ID], check=True, capture_output=True, text=True, timeout=30)


def test_empty_canonical_is_authoritative_and_missing_display_is_not_repaired(tmp_path):
    paths = _canonical(tmp_path, [])
    paths["state_file"].unlink()
    code, result = run_json_cli_result("shared-goal-alignment", "--goal-id", GOAL_ID,
        "--agent-id", "agent-a", "--project", str(paths["project"]), registry_path=paths["registry"])
    assert code == 0, result
    assert result["unclaimed_eligible_work"] == []
    assert result["source_basis"]["revision_basis"] == "canonical_todo_snapshot"
    assert result["source_basis"]["state_event_basis_sequence"] == 0
    assert result["frontier_basis"]["basis_source"] == "unbound"
    assert not paths["state_file"].exists()


@pytest.mark.parametrize("events", [False, True])
def test_canonical_revision_changes_proposal_basis_without_changing_event_sequence(tmp_path, events):
    paths = _canonical(tmp_path, events=events)
    proposal = _proposal(paths, {"affected_todo_ids": ["todo_current"]})
    before = _alignment(paths)["source_basis"]
    _mutate_provider(paths)
    after = _alignment(paths)["source_basis"]
    assert before["state_event_basis_sequence"] == after["state_event_basis_sequence"]
    assert before["source_basis_digest"] != after["source_basis_digest"]
    result = _admit(paths, proposal)
    assert result["admission"] == "needs_rebase"
    assert "base_source_basis_digest_mismatch" in result["admission_facts"]
    assert result["canonical_effect"] == "none"


def test_amendment_uses_one_snapshot_and_cannot_revive_a_display_only_todo(tmp_path, monkeypatch):
    paths = _canonical(tmp_path)
    proposal = _proposal(paths, {"affected_todo_ids": ["todo_stage2_a"]})
    with pytest.raises(ValueError, match="not open"):
        _admit(paths, proposal)
    proposal = _proposal(paths, {"affected_todo_ids": ["todo_current"]})
    original = shared_goal_work_source.read_canonical_todos_if_promoted
    reads = []
    def capture(**kwargs):
        result = original(**kwargs)
        reads.append(result["provider_revision"])
        # A concurrent change after this read must not cause a second snapshot
        # for inventory after alignment has already selected a different basis.
        _mutate_provider(paths)
        return result
    monkeypatch.setattr(shared_goal_work_source, "read_canonical_todos_if_promoted", capture)
    result = _admit(paths, proposal)
    assert len(reads) == 1
    assert result["admission"] == "admitted"
    assert result["canonical_effect"] == "none"  # Snapshot admission is not a commit-time CAS.


@pytest.mark.parametrize("native", [False, True])
def test_display_changes_do_not_change_canonical_basis(tmp_path, native):
    paths = _canonical(tmp_path, native=native)
    before = _alignment(paths)
    paths["state_file"].write_text("---\nstatus: completed\nupdated_at: 2099-01-01\n---\nnot valid Todo data")
    assert _alignment(paths) == before
    paths["state_file"].unlink()
    assert _alignment(paths) == before


def test_provider_failure_never_falls_back_to_readable_markdown(tmp_path):
    paths = _canonical(tmp_path)
    before = paths["state_file"].read_bytes()
    provider = paths["runtime"] / "authority/file-v0"
    provider.rename(provider.with_name("offline-fixture"))
    with pytest.raises(LocalCoordinationAuthorityUnavailable):
        _alignment(paths)
    assert paths["state_file"].read_bytes() == before


def test_canonical_lease_conflict_wins_over_obsolete_lease_file(tmp_path):
    lease = {"schema_version": "task_lease_v0", "goal_id": GOAL_ID, "todo_id": "todo_current",
        "status": "active", "expires_at": "2099-01-01T00:00:00Z", "owner": "agent-b",
        "lease_epoch": 2, "version": 1}
    paths = _canonical(tmp_path, [_record(claimed_by="agent-a")], leases=[lease])
    legacy_path = paths["runtime"] / "goals" / GOAL_ID / "task-leases/todo_current.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps({**lease, "owner": "agent-a"}))
    result = _alignment(paths)
    assert "lease_owner_mismatch" in result["conflict_facts"]
    legacy_path.write_text("malformed obsolete display")
    assert _alignment(paths) == result


def test_real_cli_reads_production_scale_snapshot_without_display(tmp_path):
    paths = _write_fixture(tmp_path)
    module = (Path(__file__).resolve().parents[2] / "tests/control_plane_ts/production_scale_coordination_fixture.ts").as_uri()
    process = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
        f"import {{productionScaleCoordinationFixture}} from {json.dumps(module)};"
        "process.stdout.write(JSON.stringify(productionScaleCoordinationFixture(process.argv[1])));", GOAL_ID],
        check=True, capture_output=True, text=True, timeout=30)
    fixture = json.loads(process.stdout)
    initialize_canonical_authority(paths["runtime"], GOAL_ID, fixture["projection"], state_path=paths["state_file"])
    paths["state_file"].unlink()
    code, result = run_json_cli_result("shared-goal-alignment", "--goal-id", GOAL_ID,
        "--agent-id", "agent-a", "--project", str(paths["project"]), registry_path=paths["registry"])
    assert code == 0, result
    assert result["frontier_counts"] == {"current_agent_claimed_advancement_count": 13,
        "unclaimed_advancement_count": 0, "other_agent_claimed_advancement_count": 24}
    assert result["read_only"] is True
    assert not paths["state_file"].exists()
