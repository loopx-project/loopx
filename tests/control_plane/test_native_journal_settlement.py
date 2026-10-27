import json
import sys
import pytest
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.test_goal_mode_mcp_settlement import _run_cli, GOAL_ID, AGENT_ID
from loopx.control_plane.turn_driver.journal_store import (
    load_turn_journal,
    turn_journal_path,
    reconcile_settled_turn_journal,
)
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.chat_coordinator_checkpoint import CoordinatorCheckpoints


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_native_mcp_settlement_reconciles_managed_journal(
    provider, tmp_path, monkeypatch
):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    project = state.parent
    runtime = tmp_path / "runtime"
    host = project / "host.py"
    validator = project / "verify.py"
    host.write_text(
        "import json,pathlib,sys\nr=json.load(sys.stdin)\nif r.get('phase')=='checkpoint_direction':\n json.dump({'read_context_id':'deliberately-stale','vision_unchanged_reason':'Old direction rejected.'},sys.stdout)\nelse:\n assert not pathlib.Path('candidate.txt').exists()\n pathlib.Path('candidate.txt').write_text('valid');pathlib.Path('host-count.txt').write_text('1')\n json.dump({'schema_version':'loopx_turn_result_v0','turn_key':r['turn_key'],'result_kind':'validated_completion','completed_phases':['host_execute','typed_result'],'classification':'candidate_verified','recommended_action':'Read remaining work.','next_action':'Read remaining work.','delivery_batch_scale':'implementation','delivery_outcome':'outcome_progress','summary':'Candidate verified.','vision_unchanged_reason':'Pre-result judgment.'},sys.stdout)\n"
    )
    validator.write_text(
        "from pathlib import Path;assert Path('candidate.txt').read_text()=='valid'"
    )
    _, managed = _run_cli(
        registry,
        "turn",
        "run-once",
        "--host",
        "generic-cli",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--project",
        str(project),
        "--host-adapter-command-json",
        json.dumps([sys.executable, str(host)]),
        "--validation-command-json",
        json.dumps([sys.executable, str(validator)]),
        "--no-global-sync",
        "--execute",
    )
    assert managed["status"] == "failed" and managed["validation"]["ok"], managed
    path = turn_journal_path(
        runtime, goal_id=GOAL_ID, turn_key=managed["resume_turn_key"]
    )
    before = load_turn_journal(path)
    assert before["status"] == "failed" and before["completed_phases"] == [
        "host_execute",
        "typed_result",
        "validation",
    ]
    # An already committed result does not supply missing native settlement receipts.
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    pending_journal, pending_ledger = path.read_bytes(), index.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        reconcile_settled_turn_journal(
            runtime, registry_path=registry,
            settlement_identity=before["plan"]["transaction"]["settlement_plan"]["identity"],
        )
    assert pending_journal == path.read_bytes() and pending_ledger == index.read_bytes()
    old = next(
        t
        for t in read_canonical_todos_if_promoted(
            runtime_root=runtime, goal_id=GOAL_ID
        )["todos"]
        if t["todo_id"] == todo
    )
    checkpoint = CoordinatorCheckpoints(registry, runtime, GOAL_ID, AGENT_ID)
    context = checkpoint.read(todo)
    result = checkpoint.resume(
        todo,
        {
            "read_context_id": context["read_context_id"],
            "vision_unchanged_reason": "Current original result and obligation preserve the route.",
        },
    )
    assert result["ok"] and result["settlement_complete"], result
    after = load_turn_journal(path)
    assert after["status"] == after["receipt"]["status"] == "committed", after[
        "receipt"
    ]
    assert (
        after["turn_key"] == before["turn_key"]
        and after["receipt"]["settlement_effect_id"]
        == result["settlement_identity"]["effect_id"]
    )
    assert (
        after["receipt"]["next_phase"] is None
        and after["receipt"]["failed_phase"] is None
    )
    for k in ["plan", "host_result", "task_validation", "completion_intent"]:
        assert after[k] == before[k], k
    new = next(
        t
        for t in read_canonical_todos_if_promoted(
            runtime_root=runtime, goal_id=GOAL_ID
        )["todos"]
        if t["todo_id"] == todo
    )
    for k in [
        "completion_turn_key",
        "completion_receipt_id",
        "completed_at",
        "evidence",
        "status",
        "claimed_by",
    ]:
        assert old.get(k) == new.get(k), k
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    ledger = index.read_bytes()
    original = path.read_bytes()
    repeated = reconcile_settled_turn_journal(
        runtime,
        registry_path=registry,
        settlement_identity=result["settlement_identity"],
    )
    assert repeated["replayed"] and not repeated["appended"]
    assert ledger == index.read_bytes() and original == path.read_bytes()
    foreign = {**result["settlement_identity"], "agent_id": "foreign"}
    with pytest.raises((ValueError, RuntimeError)):
        reconcile_settled_turn_journal(
            runtime, registry_path=registry, settlement_identity=foreign
        )
    assert ledger == index.read_bytes() and original == path.read_bytes()
    assert (project / "host-count.txt").read_text() == "1" and (
        project / "candidate.txt"
    ).read_text() == "valid"
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert (
        sum(
            r.get("classification") == "quota_slot_spent"
            and r.get("settlement_identity") == result["settlement_identity"]
            for r in rows
        )
        == 1
    )
    (tmp_path / "journal-reconciliation-proof.json").write_text(
        json.dumps(
            {
                "provider": provider,
                "before": before,
                "after": after,
                "native_result": result,
                "original_todo_before": old,
                "original_todo_after": new,
                "host_calls": 1,
                "spend_calls": 1,
                "repeat_no_effects": True,
                "foreign_no_effects": True,
                "source_qualification_only": True,
            },
            indent=2,
        )
    )
